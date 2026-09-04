"""Binding v1's runner to the worker (RFC 0044 D-44.12).

The machinery that runs an agent in a sandbox, drives the battery, mints the
review and lands the work is v1's and stays v1's: the second architecture
changes where state lives and who decides what runs next, not how an attempt
is executed. This module is the whole of the seam between them — it hands
the runner a task and turns the run state it returns back into the facts the
worker records.

Nothing here decides anything either. A run that escalated says so, a run
that landed carries its sha, and everything else is a task the board will
offer again; the mapping is deliberately total, so an outcome the runner can
produce always has a fact the log can hold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from torve.application import divergence
from torve.application.eventlog import RunLogChannel, attempt_sink, burn_sink
from torve.application.runner import run_task
from torve.application.worker import Execute, Outcome
from torve.domain.states import EscalationReason, TaskState

if TYPE_CHECKING:
    from torve.application.eventlog import EventLog
    from torve.application.runner import RunDeps
    from torve.application.runstate import RunState
    from torve.config.runconfig import RunnerConfig
    from torve.domain.task import Task

# ----------------------- #


def outcome_of(state: RunState) -> Outcome:
    """One run state as the facts it leaves behind."""

    landed = state.state is TaskState.READY

    return Outcome(
        attempt=state.attempts,
        exit_code=0 if landed else 1,
        gates_exit_code=0 if landed else 1,
        gate_outcomes={},
        landed_sha=state.landed_sha if landed else None,
        # The run state carries the reason as the string it recorded; the
        # vocabulary is closed either way, and reading it back through the
        # enum is what keeps an unknown word from reaching the log.
        escalation=(
            EscalationReason(state.escalation.reason) if state.escalation is not None else None
        ),
        detail=state.escalation.detail if state.escalation is not None else _last_fact(state),
    )


# ....................... #


def _last_fact(state: RunState) -> str:
    return str(state.history[-1].get("fact") or "") if state.history else ""


# ....................... #


def _log_root(state: RunState, root: Path) -> Path:
    """Where this run's divergence log ended up. The worktree is the run's
    own tree and holds the log whether or not the work landed — which is the
    case that matters, because a run that failed is exactly the one whose
    account of why is worth reading. A run that never got a worktree leaves
    the host root, where a landed log lives."""

    return Path(state.worktree) if state.worktree else root


# ....................... #


# What a dispatch needs resolved per task rather than per root: the tier a
# character routes to (D-34.3), the providers that tier is permitted, and
# the agent built for it. The worker holds one of these, not a dep bundle,
# because a bundle built once would pin every task to one tier's agent.
Prepare = Callable[["Task"], "tuple[Task, RunDeps]"]


# ....................... #


def runner_execute(
    root: Path,
    config: RunnerConfig,
    prepare: Prepare,
    *,
    log: EventLog | None = None,
    partition: str = "",
    seat: str = "worker",
) -> Execute:
    """An `Execute` the worker can call: the runner in a thread, because it
    is synchronous and the worker's loop is not.

    With a log, the run is also observed. Three things go in before it
    starts: each attempt reports itself as it happens, under the tier that
    actually ran it (D-44.3); the broker's metering lands per response (RFC
    0045 D-45.4); and the journal sync records the attempt's divergences and
    rewrites the worktree's log from the record before each gate pass reads
    it (A-82) — host-side, because the sandbox that wrote them has no route
    to the store. Without a log the runner behaves exactly as v1 does: the
    observation is wiring, not a dependency of execution.
    """

    async def execute(task: Task) -> Outcome:
        task, bound = prepare(task)
        journal = None

        if log is not None:
            loop = asyncio.get_running_loop()
            journal = divergence.journal_sync(
                log, loop, partition=partition, task_id=task.id, seat=seat
            )
            bound = replace(
                bound,
                sink=burn_sink(log, loop, partition=partition, task_id=task.id, seat=seat),
                facts=attempt_sink(log, loop, partition=partition, task_id=task.id, seat=seat),
                journal=journal,
                channel=RunLogChannel(
                    log=log, loop=loop, partition=partition, task_id=task.id, seat=seat
                ),
            )

        state = await asyncio.to_thread(run_task, root, task, config, bound)

        if journal is not None:
            # One more, for whatever the last attempt wrote after its gate
            # pass — or wrote without ever reaching one, which is the case a
            # per-gate sync alone would lose.
            await asyncio.to_thread(journal, _log_root(state, root))

        return outcome_of(state)

    return execute
