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
from typing import TYPE_CHECKING

from torve.application.runner import run_task
from torve.application.worker import Execute, Outcome
from torve.domain.states import EscalationReason, TaskState

if TYPE_CHECKING:
    from pathlib import Path

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
        landed_sha=_landing_sha(state) if landed else None,
        # The run state carries the reason as the string it recorded; the
        # vocabulary is closed either way, and reading it back through the
        # enum is what keeps an unknown word from reaching the log.
        escalation=(
            EscalationReason(state.escalation.reason) if state.escalation is not None else None
        ),
        detail=state.escalation.detail if state.escalation is not None else _last_fact(state),
    )


# ....................... #


def _landing_sha(state: RunState) -> str | None:
    """The landing's sha, as the run recorded it. The history is the run's
    own account, so the sha comes from the fact that announced it rather
    than from asking git afterwards — what happened is what was recorded."""

    for entry in reversed(state.history):
        fact = str(entry.get("fact") or "")

        if fact.startswith("committed "):
            return fact.split()[1].rstrip(";")

    return None


# ....................... #


def _last_fact(state: RunState) -> str:
    return str(state.history[-1].get("fact") or "") if state.history else ""


# ....................... #


def runner_execute(root: Path, config: RunnerConfig, deps: RunDeps) -> Execute:
    """An `Execute` the worker can call: the runner in a thread, because it
    is synchronous and the worker's loop is not."""

    async def execute(task: Task) -> Outcome:
        state = await asyncio.to_thread(run_task, root, task, config, deps)

        return outcome_of(state)

    return execute
