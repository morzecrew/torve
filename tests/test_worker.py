"""The worker's lifecycle (RFC 0044 §5.5).

Execution is injected, so these cases are about the lifecycle around an
attempt and not about sandboxes: what a worker records, in what order, and
what the board says after — including after a worker dies in the middle,
which is the property D-44.6 exists for.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.executors import outcome_of
from torve.application.manager import project
from torve.application.runstate import Escalation, RunState
from torve.application.worker import Outcome, Worker
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import Scope, Task

PARTITION = "morzecrew/torve"


def task(task_id: str, *, allow: list[str], depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        decisions=[],
        depends_on=depends_on or [],
        scope=Scope(allow=allow, deny=[]),
    )


def green(**overrides) -> Outcome:
    fields = {
        "attempt": 1,
        "exit_code": 0,
        "gates_exit_code": 0,
        "gate_outcomes": {"scope": "pass", "acceptance": "pass"},
        "landed_sha": "a" * 40,
        "digest": "d" * 12,
    }

    return Outcome(**{**fields, **overrides})


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, task_id):
    await log.record(
        EventKind.TASK_MINTED,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"title": task_id, "source_id": "0044"},
    )


def worker_over(log, outcome: Outcome, *, name: str = "w-1") -> Worker:
    async def execute(_task: Task) -> Outcome:
        return outcome

    return Worker(log=log, name=name, execute=execute)


def test_a_green_pass_claims_runs_and_lands():
    async def scenario(log):
        await mint(log, "T-1")
        tasks = {"T-1": task("T-1", allow=["src/**"])}
        handled = await worker_over(log, green()).once(tasks, PARTITION)
        history = await log.history("T-1")

        assert handled == "T-1"
        assert [event.kind for event in history] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
            EventKind.ATTEMPT_STARTED,
            EventKind.ATTEMPT_FINISHED,
            EventKind.GATES_EVALUATED,
            EventKind.LANDING_RECORDED,
        ]

        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.READY
        assert board.tasks["T-1"].landed_sha == "a" * 40

    run(scenario)


def test_nothing_to_claim_records_nothing():
    async def scenario(log):
        handled = await worker_over(log, green()).once({}, PARTITION)

        assert handled is None
        assert await log.since() == []

    run(scenario)


def test_an_escalation_hands_the_task_to_a_human():
    async def scenario(log):
        await mint(log, "T-1")
        outcome = green(
            landed_sha=None,
            gates_exit_code=1,
            gate_outcomes={"scope": "fail"},
            escalation=EscalationReason.POISON_CEILING,
            detail="3 attempts, ceiling 3",
        )
        await worker_over(log, outcome).once({"T-1": task("T-1", allow=["src/**"])}, PARTITION)
        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.ESCALATED
        assert board.tasks["T-1"].escalation == "poison_ceiling"
        assert board.tasks["T-1"].claimed_by is None

    run(scenario)


def test_an_attempt_that_neither_landed_nor_escalated_returns_to_the_queue():
    async def scenario(log):
        await mint(log, "T-1")
        tasks = {"T-1": task("T-1", allow=["src/**"])}
        await worker_over(log, green(landed_sha=None, detail="gates red")).once(tasks, PARTITION)
        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.QUEUED
        assert board.tasks["T-1"].claimed_by is None
        # And another worker can pick it straight up.
        assert await worker_over(log, green(), name="w-2").once(tasks, PARTITION) == "T-1"

    run(scenario)


def test_a_worker_killed_mid_attempt_leaves_a_log_that_says_where_it_stopped():
    """D-44.6: a kill costs the lease and nothing else. The facts recorded
    before the death stand, and no later fact is invented on its behalf."""

    async def scenario(log):
        await mint(log, "T-1")

        async def dies(_task: Task) -> Outcome:
            raise RuntimeError("the worker died")

        worker = Worker(log=log, name="w-1", execute=dies)
        claimed = await worker.claim({"T-1": task("T-1", allow=["src/**"])}, PARTITION)

        with pytest.raises(RuntimeError):
            await worker.run(claimed, PARTITION)

        board = project(await log.since(partition=PARTITION))

        assert [event.kind for event in await log.history("T-1")] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
            EventKind.ATTEMPT_STARTED,
        ]
        assert board.tasks["T-1"].state is TaskState.RUNNING
        assert board.tasks["T-1"].claimed_by == "w-1"

    run(scenario)


def test_two_workers_never_take_the_same_task():
    async def scenario(log):
        await mint(log, "T-1")
        tasks = {"T-1": task("T-1", allow=["src/**"])}

        assert await worker_over(log, green(), name="w-1").claim(tasks, PARTITION) is not None
        assert await worker_over(log, green(), name="w-2").claim(tasks, PARTITION) is None

    run(scenario)


def test_a_worker_takes_the_next_disjoint_task_beside_one_in_flight():
    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2")
        tasks = {
            "T-1": task("T-1", allow=["src/**"]),
            "T-2": task("T-2", allow=["web/**"]),
        }

        assert await worker_over(log, green(), name="w-1").claim(tasks, PARTITION) is not None

        second = await worker_over(log, green(), name="w-2").claim(tasks, PARTITION)

        assert second is not None
        assert second.id == "T-2"

    run(scenario)


def test_a_landed_run_state_maps_to_a_landing_fact():
    """The seam to v1's runner (D-44.12): a run state becomes the facts the
    log holds, and nothing in the mapping decides anything."""

    state = RunState(task_id="T-1", path=Path("/tmp/unused"))
    state.state = TaskState.READY
    state.attempts = 2
    state.history = [{"fact": "committed 3eeafb629e; pushed=False; pr deferred"}]
    outcome = outcome_of(state)

    assert outcome.landed_sha == "3eeafb629e"
    assert outcome.attempt == 2
    assert outcome.escalation is None


def test_an_escalated_run_state_carries_its_reason_through_the_enum():
    state = RunState(task_id="T-1", path=Path("/tmp/unused"))
    state.state = TaskState.ESCALATED
    state.escalation = Escalation(reason="poison_ceiling", detail="3 attempts, ceiling 3")
    outcome = outcome_of(state)

    assert outcome.escalation is EscalationReason.POISON_CEILING
    assert outcome.landed_sha is None
    assert "3 attempts" in outcome.detail
