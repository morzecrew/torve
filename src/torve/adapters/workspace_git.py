"""Workspace port over git worktrees (RFC 0003 §4): `.wt/<task-id>`, on the
task's own branch, derived entirely from the task id (D-3.4)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from torve import naming


class WorkspaceError(RuntimeError):
    pass


class GitWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise WorkspaceError(proc.stderr.strip() or f"git {' '.join(args)} failed")
        return proc.stdout

    def create(self, task_id: str, base_ref: str | None) -> Path:
        path = naming.worktree(self.root, task_id)
        if path.exists():
            self.remove(task_id)
        base = base_ref or "HEAD"
        branch = naming.branch(task_id)
        current = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch == current:
            # The task's branch is checked out here (dogfooding the engine on
            # its own repository); a worktree cannot share it, so detach.
            self._git("worktree", "add", "--detach", str(path), base)
        else:
            self._git("worktree", "add", "-B", branch, str(path), base)
        return path

    def remove(self, task_id: str) -> None:
        path = naming.worktree(self.root, task_id)
        proc = subprocess.run(
            ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)
        subprocess.run(
            ["git", "-C", str(self.root), "worktree", "prune"],
            capture_output=True, check=False,
        )

    def list_worktrees(self) -> list[tuple[str, Path]]:
        wt_root = self.root / naming.WORKTREE_DIR
        if not wt_root.is_dir():
            return []
        return [(p.name, p) for p in sorted(wt_root.iterdir()) if p.is_dir()]
