"""Workspace port over git worktrees (S-0003/isolation): `.wt/<task-id>`, on the
task's own branch, derived entirely from the task id (S-0003/D-4)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from torve.application.ports import JUDGE_SUFFIX, judge_ref
from torve.base import naming

# ----------------------- #

# Worktree surgery serializes per process (S-0019/D-14, S-0019/A-6): concurrent
# `git worktree add` calls contend on the repository's own locks, and the
# slow half of a dispatch is the attempt, never the checkout.
_WORKTREE_LOCK = threading.Lock()

# Every git torve starts runs with the repository's hooks switched off
# (S-0110/D-7): configuration an agent wrote never executes on the host
# through a hook. `-c` is inherited by git's own child processes, so a
# `worktree add`'s checkout and the commits a fetch's server helper makes are
# covered by the one flag on the parent command.
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")

# The judge snapshot is an engine commit: no person is claimed for it, so it
# is committed under the same `Torve <torve@local>` identity `GitVcs` uses for
# an unsigned landing (S-0010).
COMMITTER = ("-c", "user.name=Torve", "-c", "user.email=torve@local")

# The upload-pack a judge fetch asks for: the snapshot is not an advertised
# ref, so the source must serve an arbitrary sha, exactly as a shadow clone's
# fetch does (S-0004/D-7).
_UPLOAD_PACK = "git -c core.hooksPath=/dev/null -c uploadpack.allowanysha1inwant=true upload-pack"


# ....................... #


class WorkspaceError(RuntimeError):
    pass


# ....................... #


def _git_at(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """One git call in `path`, raising on failure; the shape every helper
    here shares, so a judge snapshot's temporary-index calls read like the
    adapter's own."""

    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    if proc.returncode != 0:
        raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")

    return proc.stdout


def _snapshot_commit(worktree: Path) -> str:
    """The commit `git add -A` would carry in `worktree`, on its HEAD
    (S-0114/D-1). Staging goes through a temporary index, so the worktree's
    own index is exactly as it was, and no ref moves here."""

    head = _git_at(worktree, "rev-parse", "HEAD").strip()

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}

        def stage(*args: str) -> str:
            return _git_at(worktree, *COMMITTER, *args, env=env)

        stage("read-tree", head)
        stage("add", "-A")
        tree = stage("write-tree").strip()

        return stage("commit-tree", tree, "-p", head, "-m", "judge snapshot").strip()


def _standalone_checkout(path: Path, source: Path, snapshot: str, depth: int | None) -> Path:
    """A fresh repository at `path` holding `snapshot` fetched from `source`,
    checked out detached and verified before it is returned — the way a
    shadow clone stands alone (S-0004/D-7). `depth` bounds the fetch; None
    takes every commit the source holds behind the snapshot."""

    if path.exists():
        shutil.rmtree(path)

    path.mkdir(parents=True)

    _git_at(path, "init", "-q")
    bound = [f"--depth={depth}"] if depth is not None else []
    proc = subprocess.run(
        [
            "git",
            *NO_HOOKS,
            "-C",
            str(path),
            "fetch",
            "-q",
            *bound,
            "--upload-pack",
            _UPLOAD_PACK,
            str(source),
            snapshot,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise WorkspaceError(proc.stderr.strip() or f"judge fetch into {path.name} failed")

    _git_at(path, "checkout", "-q", "--detach", "FETCH_HEAD")

    head = _git_at(path, "rev-parse", "HEAD").strip()

    if head != snapshot:
        raise WorkspaceError(
            f"judge checkout {path.name} is at {head}, not the snapshot {snapshot}"
        )

    return path


# ....................... #


class GitWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    # ....................... #

    def _git(self, *args: str) -> str:
        return _git_at(self.root, *args)

    # ....................... #

    def create(
        self, task_id: str, base_ref: str | None, *, resume: bool = False, fetch: bool = False
    ) -> Path:
        with _WORKTREE_LOCK:
            path = naming.worktree(self.root, task_id)

            if path.exists():
                self._remove_locked(task_id)

            base = base_ref or "HEAD"
            branch = naming.branch(task_id)
            current = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

            # Continuation (S-0026 S-0026/D-9): cut from the previous attempt's
            # own candidate tip — whatever it checkpointed on its branch —
            # instead of resetting the branch back to base. A branch that
            # never diverged (nothing was ever checkpointed) has nothing to
            # resume from, so the normal base cut is the correct fallback.
            if resume and self._ref_exists(branch):
                if branch == current:
                    self._git("worktree", "add", "--detach", str(path), branch)
                else:
                    self._git("worktree", "add", str(path), branch)

                return path

            # S-0080/D-10: cut from the remote's tip rather than a local copy
            # that is stale from the first merge onward. After the resume
            # check: a continuation cuts from its own branch, which no fetch
            # moves.
            if fetch:
                self._git("fetch", "--quiet", "origin")

            # A recut resets the branch to base, and the branch may hold a
            # checkpoint no landing carried — a budget checkpoint (S-0026/D-9),
            # a convicted tree (S-0069/D-4), a tree an escalation left behind
            # (S-0086/D-2). Resetting over it orphaned the engine's own
            # commit (bloomery T-0020, 2026-09-19); it is kept under a ref
            # first, never pushed, so the work stays reachable by name.
            if self._ref_exists(branch):
                self._keep_checkpoint(task_id, branch, base)

            if branch == current:
                # The task's branch is checked out here (dogfooding the
                # engine on its own repository); a worktree cannot share
                # it, so detach.
                self._git("worktree", "add", "--detach", str(path), base)
            else:
                self._git("worktree", "add", "-B", branch, str(path), base)

            return path

    # ....................... #

    def _keep_checkpoint(self, task_id: str, branch: str, base: str) -> str | None:
        """The branch's tip under `refs/torve/checkpoints/<task>/<sha>` when the
        base does not already hold it; None when it does, or when git cannot
        say — a recut must not fail on its own bookkeeping."""

        tip = self._git("rev-parse", branch).strip()
        held = subprocess.run(
            ["git", *NO_HOOKS, "-C", str(self.root), "merge-base", "--is-ancestor", tip, base],
            capture_output=True,
            text=True,
            check=False,
        )

        if held.returncode == 0:
            return None

        ref = f"refs/torve/checkpoints/{task_id}/{tip[:12]}"

        try:
            self._git("update-ref", ref, tip)
        except WorkspaceError:
            return None

        return ref

    # ....................... #

    def _ref_exists(self, ref: str) -> bool:
        proc = subprocess.run(
            ["git", *NO_HOOKS, "-C", str(self.root), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            text=True,
            check=False,
        )

        return proc.returncode == 0

    # ....................... #

    def remove(self, task_id: str) -> None:
        with _WORKTREE_LOCK:
            self._remove_locked(task_id)

    # ....................... #

    def _remove_locked(self, task_id: str) -> None:
        path = naming.worktree(self.root, task_id)

        proc = subprocess.run(
            ["git", *NO_HOOKS, "-C", str(self.root), "worktree", "remove", "--force", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)

        subprocess.run(
            ["git", *NO_HOOKS, "-C", str(self.root), "worktree", "prune"],
            capture_output=True,
            check=False,
        )

    # ....................... #

    def list_worktrees(self) -> list[tuple[str, Path]]:
        wt_root = self.root / naming.WORKTREE_DIR

        if not wt_root.is_dir():
            return []

        return [(p.name, p) for p in sorted(wt_root.iterdir()) if p.is_dir()]

    # ....................... #

    def judge_checkout(self, task_id: str) -> Path:
        """The candidate the gates read (S-0114/D-1): commit what `git add -A`
        would stage in the task's worktree to `refs/torve/judge/<task>`, then
        build a standalone checkout at `.wt/<task>.judge/` — the way a shadow
        clone stands alone (S-0004/D-7) — detached at the snapshot and
        verified before any gate runs over it.

        The snapshot stages through a temporary index (`GIT_INDEX_FILE`), so
        the worktree's own index is exactly as it was and its branch never
        moves. The commit's parent is the worktree's HEAD, so its history is
        the candidate's real history, and the fetch at depth 1 transfers only
        the objects the snapshot reaches."""

        snapshot = _snapshot_commit(naming.worktree(self.root, task_id))
        # The ref is written directly, never through a checkout.
        self._git("update-ref", judge_ref(task_id), snapshot)
        path = self.root / naming.WORKTREE_DIR / f"{task_id}{JUDGE_SUFFIX}"

        return _standalone_checkout(path, self.root, snapshot, depth=1)

    # ....................... #

    def remove_judge(self, task_id: str) -> None:
        """Take the judge checkout and its ref (S-0114/D-5): the checkout is a
        standalone repository, not a worktree of the root, so it goes with
        `rmtree`; the ref is the one thing here the root owns."""

        path = self.root / naming.WORKTREE_DIR / f"{task_id}{JUDGE_SUFFIX}"
        shutil.rmtree(path, ignore_errors=True)

        if self._ref_exists(judge_ref(task_id)):
            self._git("update-ref", "-d", judge_ref(task_id))


# ....................... #


class ShadowWorkspace:
    """Shadow workspaces (S-0004/shadow-runs, S-0004/D-7): a self-contained clone at the
    replayed task's parent commit, holding truncated history and no refs
    beyond it — by construction, not by policy. A worktree cannot do this: it
    shares the repository's whole object store, and the fix being reachable
    in history is exactly the leak that makes shadow numbers flattering
    fiction (§6a). The fetch asks the source's upload-pack for the exact
    parent SHA at bounded depth, so later objects are never transferred."""

    def __init__(self, root: Path, depth: int = 50) -> None:
        self.root = root
        self.depth = depth

    # ....................... #

    def path_for(self, task_id: str) -> Path:
        return self.root / naming.WORKTREE_DIR / naming.shadow_id(task_id)

    # ....................... #

    def create(self, task_id: str, parent_sha: str) -> Path:
        return self._clone(naming.shadow_id(task_id), parent_sha)

    # ....................... #

    def create_at(self, label: str, sha: str) -> Path:
        """The survey's clone-at-landing variant (S-0031 S-0031/D-4): the same
        bounded-depth mechanics, but the clone is cut at the LANDING sha — the
        tree the battery runs over — with the landing's first parent as the
        gate base. Depth 2 is enough for that diff; the mechanics never
        transfer anything past the requested sha either way."""

        return self._clone(label, sha)

    # ....................... #

    def _clone(self, label: str, sha: str) -> Path:
        """The shared truncated-clone mechanics (S-0004/shadow-runs, S-0004/D-7): init a
        fresh repository under `.wt/<label>`, fetch the exact sha at bounded
        depth — so later objects are never transferred — and check it out on
        a `shadow` branch with no refs beyond it."""

        path = self.root / naming.WORKTREE_DIR / label

        if path.exists():
            shutil.rmtree(path)

        path.mkdir(parents=True)

        def run(*args: str) -> None:
            proc = subprocess.run(
                ["git", *NO_HOOKS, "-C", str(path), *args],
                capture_output=True,
                text=True,
                check=False,
            )

            if proc.returncode != 0:
                raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")

        run("init", "-q")

        run(
            "fetch",
            "-q",
            f"--depth={self.depth}",
            "--upload-pack",
            "git -c core.hooksPath=/dev/null -c uploadpack.allowanysha1inwant=true upload-pack",
            str(self.root),
            sha,
        )

        run("checkout", "-q", "-b", "shadow", "FETCH_HEAD")

        return path

    # ....................... #

    def remove(self, task_id: str) -> None:
        shutil.rmtree(self.path_for(task_id), ignore_errors=True)

    # ....................... #

    def judge_checkout(self, task_id: str) -> Path:
        """A replay's judge checkout (S-0114/D-1, D-2): what `git add -A` would
        stage in the replay's clone, written to the clone's own judge ref and
        fetched into `.wt/shadow-<task>.judge/`. The fetch is unbounded: the
        clone's history already ends at the replayed parent, so the checkout
        holds the base and nothing past it. Named for the replay's run state,
        so `torve reap` reads its liveness as it reads an attempt's."""

        clone = self.path_for(task_id)
        snapshot = _snapshot_commit(clone)
        _git_at(clone, "update-ref", judge_ref(task_id), snapshot)

        return _standalone_checkout(self._judge_path(task_id), clone, snapshot, depth=None)

    # ....................... #

    def remove_judge(self, task_id: str) -> None:
        # The judge ref is the clone's own and goes with the clone.
        shutil.rmtree(self._judge_path(task_id), ignore_errors=True)

    # ....................... #

    def _judge_path(self, task_id: str) -> Path:
        return self.root / naming.WORKTREE_DIR / f"{naming.shadow_id(task_id)}{JUDGE_SUFFIX}"

    # ....................... #

    def remove_at(self, label: str) -> None:
        """Remove one survey clone and the empty `.wt/` shell this class
        created around it (S-0031 S-0031/D-1: the target tree ends byte-identical
        — no workspace residue). rmdir only removes an empty directory, so a
        `.wt/` holding other work survives untouched."""

        shutil.rmtree(self.root / naming.WORKTREE_DIR / label, ignore_errors=True)

        # rmdir only removes an empty directory, so a `.wt/` holding other
        # work survives untouched.
        with suppress(OSError):
            (self.root / naming.WORKTREE_DIR).rmdir()


# ....................... #


def shipped_commit(root: Path, task_id: str, spec_dir: Path | None = None) -> str | None:
    """The commit that shipped a task, from the landings the tree holds
    (S-0059/D-12): the newest landing of that task naming a commit, or None.

    This grepped the `Torve-Task:` trailer, with a subject convention
    behind it for the hand-made landings that carried no trailer; both are
    gone with the trailer, and a tree without git answers now."""

    from torve.application.decisions import landed_commit
    from torve.config import layout

    return landed_commit(
        root, spec_dir if spec_dir is not None else root / layout.SPECS_DIR, task_id
    )


# ....................... #


def parent_of(root: Path, sha: str) -> str:
    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "rev-parse", "--verify", "--quiet", f"{sha}^"],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise WorkspaceError(f"no parent commit for {sha!r}")

    return proc.stdout.strip()


# ....................... #


def _parse_numstat(output: str) -> dict[str, object]:
    files: dict[str, dict[str, int]] = {}
    insertions = deletions = 0

    for line in output.splitlines():
        parts = line.split("\t")

        if len(parts) != 3:
            continue

        added = int(parts[0]) if parts[0].isdigit() else 0  # "-" for binary
        removed = int(parts[1]) if parts[1].isdigit() else 0
        files[parts[2]] = {"insertions": added, "deletions": removed}
        insertions += added
        deletions += removed

    return {
        "files_changed": len(files),
        "insertions": insertions,
        "deletions": deletions,
        "files": files,
    }


# ....................... #


def diff_range(root: Path, sha: str) -> dict[str, object]:
    """What actually shipped: the commit against its first parent."""

    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "diff", "--numstat", f"{sha}^", sha],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise WorkspaceError(proc.stderr.strip() or f"git diff for {sha!r} failed")

    return _parse_numstat(proc.stdout)


# ....................... #


def diff_worktree(workspace: Path, base: str) -> dict[str, object]:
    """What the shadow attempt produced relative to `base` (the replayed
    parent), untracked files included. Never against HEAD: a shadow clone
    carries a real `.git` the sandbox can reach, so an agent may commit its
    own work — which moves HEAD and would read as an empty diff. Stages
    everything first — the workspace is a throwaway measurement artefact. A
    stage or a diff that fails raises rather than measuring part of the work."""

    for args in (["add", "-A"], ["diff", "--cached", base, "--numstat"]):
        proc = subprocess.run(
            ["git", *NO_HOOKS, "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")

    return _parse_numstat(proc.stdout)
