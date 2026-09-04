"""`torve fleet tick` and `torve fleet status`'s mechanism (RFC 0024 §5.2,
§5.4): survey every root's escalation queue, decide the pause once for the
fleet, check each root's own configuration against its manifest trust class
(§5.3, D-24.6) before ticking it in the manifest's deterministic order under
its own lock with the pause decision passed down, and read every root's
queue into one table ordered by age. No fleet lock (D-24.7) — the per-root
lock inside `run_tick` is the only mutual exclusion. No fleet store (D-24.3):
every function here reads roots and writes to roots, never to a shared
artefact of its own.

`tick` is injected (a `TickRunner`) rather than built here: wiring one
root's `TickDeps` needs adapters, and `torve.application` may not import
`torve.adapters` (RFC 0015 §6) — that construction is `torve.cli.fleet`'s
job, exactly as `torve.cli.tick` builds it for a solo tick.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from torve.application.loop import TickReport, escalated_count
from torve.application.manager import Board
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config.fleet import FleetManifest, FleetRepository, TrustRefused, enforce_trust
from torve.config.runconfig import load_runner_config
from torve.domain.states import TaskState

# ----------------------- #

# One root's worth of tick, given the fleet's pause decision — built by the
# CLI, which alone may wire the adapters a real tick needs.
TickRunner = Callable[[FleetRepository, bool], TickReport]


# ....................... #


@dataclass
class RootOutcome:
    root: str
    trust: str
    escalated: int
    outcome: str  # "ticked" | "locked out" | "error: <detail>"
    noop: bool


# ....................... #


@dataclass
class FleetReport:
    escalated_total: int
    paused: bool
    outcomes: list[RootOutcome]


# ....................... #


def escalated_tasks(root: Path, board: Board | None = None) -> set[str]:
    """One repository's escalation queue, across both carriers (D-48.5).

    A partition the resident manager serves records its escalations in the
    log; whatever v1 ran on the same root left them in run-state files.
    Union by task id rather than a sum, because a task escalated under one
    and re-escalated under the other is one task the operator has to look
    at, and counting it twice pauses a fleet for work that does not exist.
    """

    from_files = {
        state.task_id
        for state in RunState.load_all(root / naming.WORKTREE_DIR)
        if state.state is TaskState.ESCALATED
    }

    return from_files | (board.escalated() if board is not None else set())


# ....................... #


def survey(manifest: FleetManifest) -> dict[str, int]:
    """Leg 1 (§5.2): each root's escalation queue, read the same way
    `torve status` and the tick itself already do (`escalated_count`) — no
    new source, and the count a fleet decision is based on is the same
    count each root's own tick will report."""

    return {repo.root: escalated_count(repo.path) for repo in manifest.ticking_order()}


# ....................... #


def decide_pause(manifest: FleetManifest, counts: dict[str, int]) -> tuple[int, bool]:
    """Leg 2 (§5.2, D-24.2): the pause is decided once, for the fleet
    total — never per root."""

    total = sum(counts.values())

    return total, total >= manifest.attention.pause_escalations


# ....................... #


def fleet_tick(manifest: FleetManifest, tick: TickRunner) -> FleetReport:
    """Legs 3 and 4: for each root in the manifest's order, its own runner
    configuration is checked against its trust class (§5.3, D-24.6) before
    the tick — a refusal is recorded and the pass continues, exactly like a
    locked-out or failing root (D-24.5), and never reaches `tick`. The pause
    decision is passed down to every root that is ticked. One fleet event —
    the queue total, the pause decision, and every root's outcome — appended
    to each ticked root's own telemetry (D-24.11: no fleet-side stream exists
    to hold it instead, per D-24.3)."""

    counts = survey(manifest)
    total, paused = decide_pause(manifest, counts)
    outcomes: list[RootOutcome] = []

    for repo in manifest.ticking_order():
        escalated = counts[repo.root]

        try:
            enforce_trust(repo, load_runner_config(repo.path))
            report = tick(repo, paused)
            outcome = "locked out" if report.locked_out else "ticked"
            noop = report.noop

        except TrustRefused as exc:  # D-24.6: refused before the root is ticked
            outcome, noop = f"refused: {exc}", True

        except Exception as exc:  # D-24.5: recorded, the pass continues
            outcome, noop = f"error: {exc}", True

        outcomes.append(
            RootOutcome(
                root=repo.root, trust=repo.trust, escalated=escalated, outcome=outcome, noop=noop
            )
        )

    event = {
        "escalated_total": total,
        "paused": paused,
        "roots": [asdict(o) for o in outcomes],
    }

    for repo in manifest.ticking_order():
        try:
            engine_event(repo.path, "fleet_tick", event)

        except OSError:
            continue  # this root's telemetry write failed; the pass already ran

    return FleetReport(escalated_total=total, paused=paused, outcomes=outcomes)


# ....................... #


@dataclass
class EscalationRow:
    root: str
    task_id: str
    reason: str
    detail: str
    age_s: float


# ....................... #


def fleet_escalations(manifest: FleetManifest) -> list[EscalationRow]:
    """`torve fleet status` (§5.4, D-24.8): every root's escalation queue in
    one table, oldest first — RFC 0006's primary alert (D-6.8) given its
    fleet form. Read-only over roots (D-24.3): nothing here writes."""

    rows: list[EscalationRow] = []

    for repo in manifest.repositories:
        for state in RunState.load_all(repo.path / naming.WORKTREE_DIR):
            if state.state is not TaskState.ESCALATED or state.escalation is None:
                continue

            rows.append(
                EscalationRow(
                    root=repo.root,
                    task_id=state.task_id,
                    reason=state.escalation.reason,
                    detail=state.escalation.detail,
                    age_s=state.heartbeat_age_s(),
                )
            )

    return sorted(rows, key=lambda row: row.age_s, reverse=True)


# ----------------------- #

# One repository's worth of pass, given the fleet's pause decision — built
# by the CLI for the same reason `TickRunner` is: wiring one root's worker
# needs adapters, and `torve.application` may not import them (RFC 0015 §6).
# Returns the task id it handled, or None when the pass was idle.
PartitionPass = Callable[[FleetRepository, bool], Awaitable[str | None]]

# How long a round in which nobody took a task waits before looking again.
IDLE_SECONDS = 15.0


# ....................... #


@dataclass
class PartitionOutcome:
    root: str
    partition: str
    trust: str
    outcome: str  # "handled <task>" | "idle" | "refused: <detail>" | "error: <detail>"


# ....................... #


@dataclass
class FleetServeReport:
    rounds: int
    handled: int
    outcomes: list[PartitionOutcome]  # the last round's, one row per repository


# ....................... #


async def _serve_one(
    repo: FleetRepository, paused: bool, run_pass: PartitionPass
) -> tuple[PartitionOutcome, bool]:
    """One repository's pass, with every refusal turned into a row.

    A manager that stops serving four healthy repositories because a fifth
    has a broken configuration is worse than one that says so and carries
    on (D-24.5, D-48.3).
    """

    def row(outcome: str) -> PartitionOutcome:
        return PartitionOutcome(
            root=repo.root, partition=repo.partition, trust=repo.trust, outcome=outcome
        )

    if not repo.partition:
        # D-48.2: a partition nobody wrote down is a board nobody chose.
        return row("refused: no partition declared in the fleet manifest"), False

    try:
        enforce_trust(repo, load_runner_config(repo.path))
        task_id = await run_pass(repo, paused)

    except TrustRefused as exc:  # D-24.6: refused before the root is served
        return row(f"refused: {exc}"), False

    except asyncio.CancelledError:
        raise

    except Exception as exc:  # D-24.5: recorded, the round continues
        return row(f"error: {exc}"), False

    if task_id is None:
        return row("idle"), False

    return row(f"handled {task_id}"), True


# ....................... #


async def serve_fleet(
    manifest: FleetManifest,
    run_pass: PartitionPass,
    *,
    idle_seconds: float = IDLE_SECONDS,
    rounds: int | None = None,
) -> FleetServeReport:
    """The resident manager over every repository the manifest names
    (RFC 0048 §5.2).

    One round is `fleet_tick`'s shape with a manager pass where the tick
    was: survey each queue, decide the pause once for the fleet total
    (D-24.2), then serve each repository in the manifest's deterministic
    order (D-24.4) under its own trust class.

    A round in which nobody took a task sleeps; a round that produced one
    goes straight round again, because a landing may have unblocked the next
    thing — the same rule one partition already follows.
    """

    handled = 0
    seen = 0
    outcomes: list[PartitionOutcome] = []

    while rounds is None or seen < rounds:
        seen += 1
        outcomes = []
        worked = False
        # Re-surveyed every round: the queue changes as the fleet runs, and
        # a pause decided once at startup is a pause that stops meaning
        # anything an hour later.
        _, paused = decide_pause(manifest, survey(manifest))

        for repo in manifest.ticking_order():
            outcome, took = await _serve_one(repo, paused, run_pass)
            outcomes.append(outcome)

            if took:
                handled += 1
                worked = True

        if not worked and (rounds is None or seen < rounds):
            # Cancellation lands here in an idle fleet, which is where a kill
            # is free: nothing is claimed, so nothing is left leased.
            await asyncio.sleep(idle_seconds)

    return FleetServeReport(rounds=seen, handled=handled, outcomes=outcomes)
