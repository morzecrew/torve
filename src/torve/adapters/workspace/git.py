"""Workspace port over git worktrees (S-0003/isolation): `.wt/<task-id>`, on the
task's own branch, derived entirely from the task id (S-0003/D-4)."""

from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import suppress
from pathlib import Path

from torve.base import naming

# ----------------------- #

# Worktree surgery serializes per process (S-0019/D-14, S-0019/A-6): concurrent
# `git worktree add` calls contend on the repository's own locks, and the
# slow half of a dispatch is the attempt, never the checkout.
_WORKTREE_LOCK = threading.Lock()


# ....................... #


class WorkspaceError(RuntimeError):
    pass


# ....................... #


class GitWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    # ....................... #

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False
        )

        if proc.returncode != 0:
            raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")

        return proc.stdout

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

            if branch == current:
                # The task's branch is checked out here (dogfooding the
                # engine on its own repository); a worktree cannot share
                # it, so detach.
                self._git("worktree", "add", "--detach", str(path), base)
            else:
                self._git("worktree", "add", "-B", branch, str(path), base)

            return path

    # ....................... #

    def _ref_exists(self, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--verify", "--quiet", ref],
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
            ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)

        subprocess.run(
            ["git", "-C", str(self.root), "worktree", "prune"],
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
                ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
            )

            if proc.returncode != 0:
                raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")

        run("init", "-q")

        run(
            "fetch",
            "-q",
            f"--depth={self.depth}",
            "--upload-pack",
            "git -c uploadpack.allowanysha1inwant=true upload-pack",
            str(self.root),
            sha,
        )

        run("checkout", "-q", "-b", "shadow", "FETCH_HEAD")

        return path

    # ....................... #

    def remove(self, task_id: str) -> None:
        shutil.rmtree(self.path_for(task_id), ignore_errors=True)

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
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{sha}^"],
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
        ["git", "-C", str(root), "diff", "--numstat", f"{sha}^", sha],
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
    everything first — the workspace is a throwaway measurement artefact."""

    subprocess.run(["git", "-C", str(workspace), "add", "-A"], capture_output=True, check=False)

    proc = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--cached", base, "--numstat"],
        capture_output=True,
        text=True,
        check=False,
    )

    return _parse_numstat(proc.stdout)
