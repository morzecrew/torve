"""Vcs/Scm adapters (RFC 0010 §2, grown from the RFC 0003 skeleton). The
commit is the runner's artefact: author is the agent identity the runner
passes in (D-10.2 — never a human), committer is Torve, and when a signing
key path is configured the commit is SSH-signed here, at the runner
boundary, with a key no sandbox ever saw (D-10.3). Revert is mechanical
git — `revert --no-commit` staging the inverse tree for the normal landing
commit (one commit per attempt, D-10.8); a conflicted revert aborts and
returns False, the engine never resolves one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ----------------------- #


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False
    )


def repository_name(root: Path) -> str:
    """The name provider routing keys on (RFC 0004 §6b): `org/repo` from the
    origin remote when one exists, the directory name otherwise — stable
    across checkouts, which a path is not."""
    proc = _git(root, "remote", "get-url", "origin")
    if proc.returncode == 0:
        found = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?/?$", proc.stdout.strip())
        if found:
            return found.group(1)
    return root.resolve().name


class GitVcs:
    def commit_all(self, worktree: Path, message: str, author: str | None = None,
                   sign_key: str | None = None) -> str | None:
        _git(worktree, "add", "-A")
        status = _git(worktree, "status", "--porcelain")
        if not status.stdout.strip():
            return None
        config = ["-c", "user.name=Torve", "-c", "user.email=torve@local"]
        commit = ["commit", "-m", message]
        if sign_key:
            config += ["-c", "gpg.format=ssh", "-c", f"user.signingkey={sign_key}"]
            commit.append("-S")
        else:
            commit.append("--no-gpg-sign")
        if author:
            commit += ["--author", author]
        proc = _git(worktree, *config, *commit)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git commit failed")
        return _git(worktree, "rev-parse", "HEAD").stdout.strip()

    def landed_shas(self, worktree: Path, task_id: str) -> list[str]:
        """The commits a task landed, newest first — reconstructed from the
        Torve-Task trailer alone (D-10.4: git log is the surviving record)."""
        proc = _git(worktree, "log", "--format=%H", "--fixed-strings",
                    f"--grep=Torve-Task: {task_id}")
        return [line for line in proc.stdout.split() if line]

    def revert(self, worktree: Path, shas: list[str]) -> bool:
        """Stage the inverse of the given commits without committing — the
        landing commit carries the revert's own provenance. A conflict
        aborts, leaves the worktree clean, and returns False."""
        proc = _git(worktree, "-c", "user.name=Torve", "-c", "user.email=torve@local",
                    "revert", "--no-commit", *shas)
        if proc.returncode == 0:
            return True
        _git(worktree, "revert", "--abort")
        _git(worktree, "reset", "--hard")
        return False

    def push(self, worktree: Path, branch: str) -> bool:
        remotes = _git(worktree, "remote")
        if "origin" not in remotes.stdout.split():
            return False
        proc = _git(worktree, "push", "-u", "origin", f"HEAD:refs/heads/{branch}")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git push failed")
        return True


class GitLane:
    """The lane's git surface (RFC 0006 §1). The rebase happens in a
    disposable worktree so the operator's checkout never moves; a conflicted
    rebase aborts and removes it — the engine never resolves a conflict."""

    def tip(self, root: Path, ref: str) -> str | None:
        proc = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return proc.stdout.strip() or None if proc.returncode == 0 else None

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool:
        return _git(root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0

    def current_branch(self, root: Path) -> str:
        return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def is_clean(self, root: Path) -> bool:
        return not _git(root, "status", "--porcelain").stdout.strip()

    def rebase_in_worktree(self, root: Path, branch: str, onto: str, workdir: Path) -> bool:
        added = _git(root, "worktree", "add", str(workdir), branch)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or f"worktree add failed for {branch}")
        rebased = _git(workdir, "rebase", onto)
        if rebased.returncode != 0:
            _git(workdir, "rebase", "--abort")
            self.remove_worktree(root, workdir)
            return False
        return True

    def remove_worktree(self, root: Path, workdir: Path) -> None:
        _git(root, "worktree", "remove", "--force", str(workdir))

    def merge_ff(self, root: Path, ref: str) -> str:
        proc = _git(root, "merge", "--ff-only", ref)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"fast-forward to {ref} refused")
        return _git(root, "rev-parse", "HEAD").stdout.strip()

    def approver(self, root: Path) -> str:
        return _git(root, "config", "user.name").stdout.strip() or "unknown"


class GhScm:
    """Pull requests through the gh CLI — the runner speaks to the forge, the
    agent never does (D-10.1 ahead of its RFC)."""

    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str:
        proc = subprocess.run(
            ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
            capture_output=True, text=True, check=False, cwd=worktree,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "gh pr create failed")
        return proc.stdout.strip()


class NullScm:
    """The --no-pr mode: no remote exists yet, so the PR leg is recorded as
    deferred rather than silently skipped."""

    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str:
        return ""
