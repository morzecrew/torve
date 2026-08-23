"""The standing loop (RFC 0019): one bounded tick over existing machinery
— poll, the lane under its approval switch, reap, dispatch of at most one
queued task, tracker sync last (D-19.3 as amended by A-27: the lane runs
before the reaper, because READY is sweepable and a reap ahead of the
lane destroys the lane's own input — merge-before-reap, A-26, applied
inside the tick). Never a daemon: cadence is
delivered by the environment, and every invocation exits (D-19.1). One
tick at a time per root, held by a lock whose stale break is loud
(D-19.2); intake pauses while the escalation queue is non-empty so the
queue may drain but not grow by the loop's hand (D-19.5); the tick never
creates work — it drains contracts and commands humans minted (D-19.8).

Selection (D-19.4, refined at execution): "no run state" alone would
re-dispatch every reaped task, because the reaper removes state files.
Queued therefore means no run *record* — neither a state file nor a
telemetry attempt record — which stays readable from the file system
alone; telemetry is append-only and survives the reaper.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState

# ----------------------- #

LOCK = "tick.lock"

# A leg callable returns (detail, moved): what happened, and whether
# anything actually changed — the difference between quiet and dead.
Leg = Callable[[], tuple[str, bool]]


@dataclass
class TickDeps:
    """The tick's legs, wired by the caller. None marks a leg the
    configuration turned off; the tick records the skip reason itself."""

    reap: Leg
    poll: Leg | None       # None: no tracker configured
    dispatch: Callable[[str], tuple[str, bool]]
    lane: Leg | None       # None: promotion.auto_merge is off
    sync: Leg | None       # None: no tracker configured
    landed: Callable[[str], bool]  # has this task id landed on the base?


@dataclass
class TickReport:
    legs: list[tuple[str, str]]
    noop: bool
    locked_out: bool = False


def _run_record_exists(root: Path, task_id: str) -> bool:
    if naming.state_file(root, task_id).exists():
        return True
    from torve.config.manifest import Manifest, load_manifest

    manifest_path = layout.gates_file(root)
    telemetry = root / (load_manifest(manifest_path).telemetry
                        if manifest_path.is_file() else Manifest(gates=[]).telemetry)
    if not telemetry.is_file():
        return False
    for line in telemetry.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast("dict[str, Any]", json.loads(line))
        if record.get("task_id") == task_id:
            return True
    return False


def _dependency_satisfied(root: Path, dep: str,
                          landed: Callable[[str], bool]) -> bool:
    path = naming.state_file(root, dep)
    if path.exists():
        return RunState.load(path).state is TaskState.READY
    return landed(dep)


def next_queued(root: Path, landed: Callable[[str], bool]) -> str | None:
    """The file-system rule (D-19.4): an executable-role contract with no
    run record and satisfied dependencies, ascending id first."""
    from torve.gates.context import load_task

    tasks_dir = root / layout.TORVE_DIR / "tasks"
    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            task = load_task(contract)
        except ValueError:
            continue  # an unreadable contract is not this leg's problem
        if task.role not in ("implement", "revert"):
            continue
        state_path = naming.state_file(root, task.id)
        if state_path.exists():
            # A QUEUED state is a board re-queue (T-0059): §4's re-entry
            # is the human act, and it already happened. Any other state
            # is a run the loop must not touch.
            if RunState.load(state_path).state is not TaskState.QUEUED:
                continue
        elif _run_record_exists(root, task.id):
            continue
        if not all(_dependency_satisfied(root, dep, landed)
                   for dep in task.depends_on):
            continue
        return task.id
    return None


def _now() -> datetime:
    return datetime.now(UTC)


def _acquire_lock(root: Path, budget_s: int) -> bool:
    lock = root / layout.TORVE_DIR / LOCK
    if lock.exists():
        try:
            row = cast("dict[str, Any]", json.loads(lock.read_text(encoding="utf-8")))
            held_at = datetime.strptime(
                str(row.get("at", "")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            age = (_now() - held_at).total_seconds()
        except (json.JSONDecodeError, ValueError):
            row, age = {}, float("inf")
        if age <= budget_s:
            return False
        # The stale break is loud, never a silent steal (D-19.2).
        engine_event(root, "tick_lock_broken", {
            "stale_holder": row.get("pid"), "age_s": age})
    lock.write_text(json.dumps({
        "pid": os.getpid(), "at": _now().strftime("%Y-%m-%dT%H:%M:%SZ")}),
        encoding="utf-8")
    return True


def _release_lock(root: Path) -> None:
    (root / layout.TORVE_DIR / LOCK).unlink(missing_ok=True)


def _escalated_count(root: Path) -> int:
    return sum(1 for state in RunState.load_all(root / naming.WORKTREE_DIR)
               if state.state is TaskState.ESCALATED)


def run_tick(root: Path, config: RunnerConfig, deps: TickDeps) -> TickReport:
    """One pass, fixed order, every leg's failure recorded rather than
    fatal — a bounded tick must reach its sync leg so the board reflects
    whatever did happen."""
    if not _acquire_lock(root, config.loop.tick_budget):
        engine_event(root, "tick", {"noop": True, "locked": True})
        return TickReport(legs=[("lock", "held by a running tick — no-op")],
                          noop=True, locked_out=True)
    legs: list[tuple[str, str]] = []
    moved = False

    def leg(name: str, call: Leg | None, skip_reason: str) -> None:
        nonlocal moved
        if call is None:
            legs.append((name, f"skipped: {skip_reason}"))
            return
        try:
            detail, did = call()
        except Exception as exc:  # a leg's failure is recorded, never fatal
            legs.append((name, f"error: {exc}"))
            return
        legs.append((name, detail))
        moved = moved or did

    try:
        leg("poll", deps.poll, "no tracker configured")
        # The lane before the reaper (A-27): READY is sweepable, so the
        # candidates must land while their states still exist.
        leg("lane", deps.lane, "auto_merge off")
        leg("reap", deps.reap, "")

        escalated = _escalated_count(root)
        if escalated >= config.loop.pause_escalations:
            # D-19.5: the queue may drain during a pause; it may not grow.
            legs.append(("dispatch", f"paused: escalation queue at {escalated}"))
        else:
            task_id = next_queued(root, deps.landed)
            if task_id is None:
                legs.append(("dispatch", "nothing queued"))
            else:
                leg("dispatch", lambda: deps.dispatch(task_id), "")

        leg("sync", deps.sync, "no tracker configured")
    finally:
        _release_lock(root)

    engine_event(root, "tick", {"noop": not moved, **dict(legs)})
    return TickReport(legs=legs, noop=not moved)
