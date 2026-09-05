"""The engine lock and the escalation queue's depth — what outlived the
standing loop (RFC 0019, abandoned by A-105).

The lock is what makes id assignment safe when two things adopt at once: a
human running `torve intake`, and a manager pass whose standing leg mints a
contract (RFC 0023 D-23.4). The tick it was named for is gone; the
contention it prevents is not, so the file keeps its name on disk — a lock
file whose name changes under a running process is a lock nobody holds.

The escalation count is read by the fleet before any root is served
(RFC 0024 §5.2) and by the pause rule, computed here once so the two cannot
drift by counting two different ways.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config import layout
from torve.domain.states import TaskState

# ----------------------- #

LOCK = "tick.lock"


# ....................... #


def _now() -> datetime:
    return datetime.now(UTC)


# ....................... #


def acquire_lock(root: Path, budget_s: int) -> bool:
    lock = root / layout.TORVE_DIR / LOCK

    if lock.exists():
        try:
            row = cast("dict[str, Any]", json.loads(lock.read_text(encoding="utf-8")))

            held_at = datetime.strptime(str(row.get("at", "")), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )

            age = (_now() - held_at).total_seconds()

        except (json.JSONDecodeError, ValueError):
            row, age = {}, float("inf")

        if age <= budget_s:
            return False

        # The stale break is loud, never a silent steal.
        engine_event(root, "tick_lock_broken", {"stale_holder": row.get("pid"), "age_s": age})

    lock.write_text(
        json.dumps({"pid": os.getpid(), "at": _now().strftime("%Y-%m-%dT%H:%M:%SZ")}),
        encoding="utf-8",
    )

    return True


# ....................... #


def release_lock(root: Path) -> None:
    (root / layout.TORVE_DIR / LOCK).unlink(missing_ok=True)


# ....................... #


def escalated_count(root: Path) -> int:
    """This root's escalation queue — the same count `torve status` shows,
    and the count a fleet's survey reads before any root is served."""

    return sum(
        1
        for state in RunState.load_all(root / naming.WORKTREE_DIR)
        if state.state is TaskState.ESCALATED
    )
