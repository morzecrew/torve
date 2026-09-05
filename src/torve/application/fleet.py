"""The fleet (RFC 0024): one operator, many repositories.

Survey every root's escalation queue, decide the pause once for the fleet
rather than per root (D-24.2), check each root's own configuration against
its manifest trust class (§5.3, D-24.6) before serving it, and take the
roots in the manifest's deterministic order. No fleet lock (D-24.7) and no
fleet store (D-24.3): every function here reads roots and writes to roots,
never to a shared artefact of its own.

The pass itself is injected rather than built here, because wiring one
root's adapters is `torve.cli.fleet`'s job — `torve.application` may not
import `torve.adapters` (RFC 0015 §6).

The tick half of this module went with the standing loop (A-105). What it
surveyed, the manager serves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from torve.application.runstate import RunState
from torve.base import naming
from torve.config.fleet import FleetManifest, FleetRepository, TrustRefused, enforce_trust
from torve.config.runconfig import load_runner_config
from torve.domain.states import TaskState

if TYPE_CHECKING:
    from torve.application.manager import Board

# ----------------------- #

# Every repository's board by root, folded by the caller — the fleet reads
# roots and the record, and only the composition root holds the log.
Boards = Callable[[], Awaitable[dict[str, "Board"]]]


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


def survey(manifest: FleetManifest, boards: dict[str, Board] | None = None) -> dict[str, int]:
    """Leg 1 (§5.2): each root's escalation queue.

    Both carriers when a board is available, one when it is not (D-48.5).
    A manager records its escalations in the log and whatever v1 ran on the
    same root left them in run-state files; counting only the files makes a
    fleet blind to every escalation the manager has raised since, which is
    now all of them (A-110).
    """

    return {
        repo.root: len(escalated_tasks(repo.path, (boards or {}).get(repo.root)))
        for repo in manifest.ticking_order()
    }


# ....................... #


def decide_pause(manifest: FleetManifest, counts: dict[str, int]) -> tuple[int, bool]:
    """Leg 2 (§5.2, D-24.2): the pause is decided once, for the fleet
    total — never per root."""

    total = sum(counts.values())

    return total, total >= manifest.attention.pause_escalations


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
    boards: Boards | None = None,
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
        _, paused = decide_pause(manifest, survey(manifest, await boards() if boards else None))

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
