"""Everything addressable derives from the task id (RFC 0003 §4, D-3.4).

Never search for a free port at runtime — two workers race for the same one.
The reaper's cleanup-by-convention depends on these derivations entirely, so
they use a stable digest, not Python's salted hash().
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# ----------------------- #

LABEL_TASK = "torve.task"
LABEL_RUN = "torve.run"
WORKTREE_DIR = ".wt"


def offset(task_id: str) -> int:
    return int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:4]) % 100


def api_port(task_id: str) -> int:
    return 4000 + offset(task_id)


def _digits(task_id: str) -> str:
    found = re.search(r"\d+", task_id)
    return found.group(0) if found else "0"


def db_name(task_id: str) -> str:
    return f"task_{_digits(task_id)}"


def compose_project(task_id: str) -> str:
    return f"t{_digits(task_id)}"


def worktree(root: Path, task_id: str) -> Path:
    return root / WORKTREE_DIR / task_id


def state_file(root: Path, task_id: str) -> Path:
    """Beside the worktree, not inside it — removing the worktree must not
    destroy the record of what happened to the task."""
    return root / WORKTREE_DIR / f"{task_id}.state.json"


def trace_file(worktree: Path, attempt: int) -> Path:
    """Session trace, one per attempt (RFC 0004 §4) — beside the worktree for
    the same reason as the state file: triage outlives the workspace."""
    return worktree.parent / f"{worktree.name}.a{attempt}.trace.log"


def sandbox_name(task_id: str, run_id: str) -> str:
    return f"torve-{task_id.lower()}-{run_id[:8]}"


def labels(task_id: str, run_id: str) -> dict[str, str]:
    return {LABEL_TASK: task_id, LABEL_RUN: run_id}


def branch(task_id: str) -> str:
    return f"torve/{task_id}"


def shadow_id(task_id: str) -> str:
    """The synthetic id shadow infrastructure derives from (RFC 0004 §5):
    worktree, state file and sandbox names all key on it, so a shadow run
    coexists with a live run of the same task and the reaper sweeps both."""
    return f"shadow-{task_id}"
