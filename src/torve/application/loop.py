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
alone; telemetry is append-only and survives the reaper. And the
repository outranks the host (A-29): both records are host-local, so a
task whose landing trailer is already in base history is never queued —
a fresh clone must not re-run what the repository already knows.
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


# ....................... #


@dataclass
class TickDeps:
    """The tick's legs, wired by the caller. None marks a leg the
    configuration turned off; the tick records the skip reason itself."""

    reap: Leg
    poll: Leg | None  # None: no tracker configured
    dispatch: Callable[[list[str]], tuple[str, bool]]
    lane: Leg | None  # None: promotion.auto_merge is off
    sync: Leg | None  # None: no tracker configured
    landed: Callable[[str], bool]  # has this task id landed on the base?
    # RFC 0020 phase 2: claim intake requests, run pending drafting
    # tasks, project drafts. None: no tracker configured.
    intake: Leg | None = None
    # RFC 0023: evaluate every committed standing job's predicate and mint
    # what is due, bounded by cooldown, max_open and
    # loop.standing_max_per_tick. None: no standing leg wired.
    standing: Leg | None = None


# ....................... #


@dataclass
class TickReport:
    legs: list[tuple[str, str]]
    noop: bool
    locked_out: bool = False


# ....................... #


def _run_record_exists(root: Path, task_id: str) -> bool:
    if naming.state_file(root, task_id).exists():
        return True

    from torve.config.manifest import Manifest, load_manifest

    manifest_path = layout.gates_file(root)

    telemetry = root / (
        load_manifest(manifest_path).telemetry
        if manifest_path.is_file()
        else Manifest(gates=[]).telemetry
    )

    if not telemetry.is_file():
        return False

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        record = cast("dict[str, Any]", json.loads(line))

        if record.get("task_id") == task_id:
            return True

    return False


# ....................... #


def _dependency_satisfied(dep: str, landed: Callable[[str], bool]) -> bool:
    # A-31: only a landing satisfies — a ready-but-unlanded dependency is
    # not on the base the dependent's worktree is cut from, and under the
    # approvals regime ready can dwell unlanded while a human deliberates.
    return landed(dep)


# ....................... #

# The states whose scopes fence the dispatch batch (D-19.14): a run the
# loop must not touch is also a run whose files nothing else may claim.
INFLIGHT = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})


# ....................... #


def _scopes_clash(left: list[str], right: list[str]) -> bool:
    # An empty allow-set is unconstrained (RFC 0002 §6): a task that may
    # touch anything can prove itself disjoint from nothing.
    if not left or not right:
        return True

    from torve.application.planner import globs_intersect

    return globs_intersect(left, right)


# ....................... #


def _inflight_scopes(root: Path) -> list[list[str]]:
    from torve.gates.context import load_task

    scopes: list[list[str]] = []

    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        if state.state not in INFLIGHT:
            continue

        contract = layout.task_file(root, state.task_id)

        if not contract.is_file():
            continue

        try:
            scopes.append(load_task(contract).scope.allow)

        except ValueError:
            continue

    return scopes


# ....................... #


def queued_batch(root: Path, landed: Callable[[str], bool], limit: int = 1) -> list[str]:
    """The file-system rule (D-19.4 as amended by A-39): executable-role
    contracts with no run record and satisfied dependencies, ascending id
    first — admitted up to *limit* while their scopes stay pairwise
    disjoint and disjoint from every in-flight run's (D-19.14). The
    intersection test is the planner's conservative one: what is provably
    shared serializes; only the provably disjoint runs together."""

    from torve.application import sizing
    from torve.gates.context import load_task

    admitted: list[str] = []
    claimed_scopes = _inflight_scopes(root)
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            task = load_task(contract)

        except ValueError:
            continue  # an unreadable contract is not this leg's problem

        if task.role not in ("implement", "revert"):
            continue

        # RFC 0026 D-26.7: a too_large verdict routes to decomposition —
        # the contract awaits it and the tick's own dispatch skips it. The
        # only way past this is the explicit, recorded operator override
        # (`torve run <id> --oversize`), a different dispatch path entirely.
        if sizing.awaiting_decomposition(root, task):
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

        # A-29: the repository outranks the host. A landed task is never
        # queued — state files and telemetry are host-local, so a fresh
        # clone holds neither and would otherwise re-run landed history.
        # Asked here so the common tick still reads local files alone.
        if landed(task.id):
            continue

        if not all(_dependency_satisfied(dep, landed) for dep in task.depends_on):
            continue

        if any(_scopes_clash(task.scope.allow, held) for held in claimed_scopes):
            continue

        admitted.append(task.id)
        claimed_scopes.append(task.scope.allow)

        if len(admitted) >= limit:
            break

    return admitted


# ....................... #


def next_queued(root: Path, landed: Callable[[str], bool]) -> str | None:
    found = queued_batch(root, landed, limit=1)
    return found[0] if found else None


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

        # The stale break is loud, never a silent steal (D-19.2).
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
    and (RFC 0024 §5.2) the count a fleet's survey leg reads before any
    root ticks, so the two never drift by computing it two different ways."""

    return sum(
        1
        for state in RunState.load_all(root / naming.WORKTREE_DIR)
        if state.state is TaskState.ESCALATED
    )


# ....................... #


def run_tick(
    root: Path,
    config: RunnerConfig,
    deps: TickDeps,
    *,
    fleet_pause: bool | None = None,
) -> TickReport:
    """One pass, fixed order, every leg's failure recorded rather than
    fatal — a bounded tick must reach its sync leg so the board reflects
    whatever did happen.

    `fleet_pause`, set only by a fleet pass (RFC 0024 D-24.2), is the
    fleet-wide decision and *replaces* this root's own
    `loop.pause_escalations` check rather than adding to it (D-24.10):
    both applying would pause a root beneath its own threshold for a
    reason its own configuration cannot explain, and the fleet threshold
    is what the operator now carries instead. `None` is a solo tick —
    this root's own threshold applies exactly as before.
    """

    if not acquire_lock(root, config.loop.tick_budget):
        engine_event(root, "tick", {"noop": True, "locked": True})

        return TickReport(
            legs=[("lock", "held by a running tick — no-op")], noop=True, locked_out=True
        )

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
        # Intake after poll (RFC 0020 §5.4): a revise or adopt the poll
        # just applied is what this leg acts on — re-running a re-queued
        # drafter, or skipping a run adoption just consumed.
        leg("intake", deps.intake, "no tracker configured")
        # The lane before the reaper (A-27): READY is sweepable, so the
        # candidates must land while their states still exist.
        leg("lane", deps.lane, "auto_merge off")
        leg("reap", deps.reap, "")

        escalated = escalated_count(root)
        paused = escalated >= config.loop.pause_escalations if fleet_pause is None else fleet_pause

        if paused:
            # D-19.5: the queue may drain during a pause; it may not grow.
            # RFC 0023 D-23.6's first bound places the standing leg inside
            # this same conditional: a paused tick evaluates no predicate.
            reason = (
                f"escalation queue at {escalated}"
                if fleet_pause is None
                else f"fleet-wide pause in force (this root's queue at {escalated})"
            )
            legs.append(("dispatch", f"paused: {reason}"))
            legs.append(("standing", f"paused: {reason}"))
        else:
            # Standing before dispatch (RFC 0023 §5.4, the intake
            # precedent): whatever it mints this pass is a queued
            # contract the batch below can already consider.
            leg("standing", deps.standing, "no standing leg wired")
            batch = queued_batch(root, deps.landed, max(1, config.loop.dispatch_workers))

            if not batch:
                legs.append(("dispatch", "nothing queued"))
            else:
                leg("dispatch", lambda: deps.dispatch(batch), "")

        leg("sync", deps.sync, "no tracker configured")

    finally:
        release_lock(root)

    engine_event(root, "tick", {"noop": not moved, **dict(legs)})

    return TickReport(legs=legs, noop=not moved)
