"""The engine lock and the escalation queue's depth — what outlived the
standing loop (S-0019, abandoned by S-0019/A-8).

The lock is what makes id assignment safe when two things adopt at once: a
human running `torve intake`, and a manager pass whose standing leg mints a
contract (S-0023 S-0023/D-4). The tick it was named for is gone; the
contention it prevents is not, so the file keeps its name on disk — a lock
file whose name changes under a running process is a lock nobody holds.

The escalation count that used to live beside it is gone: it counted one
carrier, and the pause rule now asks `fleet.escalated_tasks`, which counts
both (S-0048/D-5, S-0048/A-1).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from torve.application.telemetry import engine_event
from torve.config import layout

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
