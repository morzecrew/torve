"""Minimal Vcs/Scm adapters for the RFC 0003 exit criterion (one task to an
open pull request). Full provenance — agent authorship, signing at the runner
boundary, complete trailers — belongs to RFC 0010; the trailers written here
are the forward-compatible subset.
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
    def commit_all(self, worktree: Path, message: str) -> str | None:
        _git(worktree, "add", "-A")
        status = _git(worktree, "status", "--porcelain")
        if not status.stdout.strip():
            return None
        proc = _git(worktree, "-c", "user.name=Torve", "-c", "user.email=torve@local",
                    "commit", "--no-gpg-sign", "-m", message)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git commit failed")
        return _git(worktree, "rev-parse", "HEAD").stdout.strip()

    def push(self, worktree: Path, branch: str) -> bool:
        remotes = _git(worktree, "remote")
        if "origin" not in remotes.stdout.split():
            return False
        proc = _git(worktree, "push", "-u", "origin", f"HEAD:refs/heads/{branch}")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git push failed")
        return True


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
