"""Everything addressable derives from the task id (RFC 0003 §4, D-3.4).

The reaper's cleanup-by-convention depends on these derivations entirely, so
they use a stable digest, not Python's salted hash().

RFC 0003 §4 also derives an API port, a database name and a compose project
from the task id. Those were implemented and never wired to anything — a
sandbox reaches none of them today — and are gone (A-50). The derivation
rule stands; when a service needs a port, it derives one the same way.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# ----------------------- #

LABEL_TASK = "torve.task"
LABEL_RUN = "torve.run"
LABEL_ROOT = "torve.root"
WORKTREE_DIR = ".wt"


# ....................... #


def worktree(root: Path, task_id: str) -> Path:
    return root / WORKTREE_DIR / task_id


# ....................... #


def state_file(root: Path, task_id: str) -> Path:
    """Beside the worktree, not inside it — removing the worktree must not
    destroy the record of what happened to the task."""

    return root / WORKTREE_DIR / f"{task_id}.state.json"


# ....................... #


def trace_file(worktree: Path, attempt: int) -> Path:
    """Session trace, one per attempt (RFC 0004 §4) — beside the worktree for
    the same reason as the state file: triage outlives the workspace."""

    return worktree.parent / f"{worktree.name}.a{attempt}.trace.log"


# ....................... #


def sandbox_name(task_id: str, run_id: str) -> str:
    return f"torve-{task_id.lower()}-{run_id[:8]}"


# ....................... #


def root_key(root: Path) -> str:
    """The engine root's identity on a shared daemon (D-3.25, A-38): a
    stable digest of the resolved path — two engines on one machine, or
    two checkouts of one repository, never mistake each other's
    sandboxes for their own."""

    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


# ....................... #


def labels(task_id: str, run_id: str, root: Path) -> dict[str, str]:
    return {LABEL_TASK: task_id, LABEL_RUN: run_id, LABEL_ROOT: root_key(root)}


# ....................... #


def branch(task_id: str) -> str:
    return f"torve/{task_id}"


# ....................... #


def shadow_id(task_id: str) -> str:
    """The synthetic id shadow infrastructure derives from (RFC 0004 §5):
    worktree, state file and sandbox names all key on it, so a shadow run
    coexists with a live run of the same task and the reaper sweeps both."""

    return f"shadow-{task_id}"
