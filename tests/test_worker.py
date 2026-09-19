"""The worker's lifecycle (S-0044/workers-and-seats).

Execution is injected, so these cases are about the lifecycle around an
attempt and not about sandboxes: what a worker records, in what order, and
what the board says after — including after a worker dies in the middle,
which is the property S-0044/D-6 exists for.
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
        "landed_sha": "a" * 40,
    }

    return Outcome(**{**fields, **overrides})


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, task_id, *, allow=("src/**",), depends_on=()):
    """One mint carrying its contract (S-0049 S-0049/D-1) — the scope a
    worker's claim reads for disjointness comes off the board now."""

    await log.record(
        EventKind.TASK_MINTED,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={
            "title": task_id,
            "source_id": "0044",
            "contract": task(task_id, allow=list(allow))
            .model_copy(update={"depends_on": list(depends_on)})
            .model_dump(mode="json"),
        },
    )


def worker_over(log, outcome: Outcome, *, name: str = "w-1") -> Worker:
    async def execute(_task: Task) -> Outcome:
        return outcome

    return Worker(log=log, name=name, execute=execute)


def test_a_green_pass_claims_runs_and_lands():
    async def scenario(log):
        await mint(log, "T-1")
        handled = await worker_over(log, green()).once(PARTITION)
        history = await log.history("T-1")

        assert handled == "T-1"
        # The lifecycle and nothing else: what happened inside the run is
        # reported by the run itself (S-0044/D-3), and an injected execute
        # reports nothing — a worker that filled the gap here would be
        # writing a summary that claims to be a history.
        assert [event.kind for event in history] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
            EventKind.LANDING_RECORDED,
        ]

        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.READY
        assert board.tasks["T-1"].landed_sha == "a" * 40

    run(scenario)


def test_nothing_to_claim_records_nothing():
    async def scenario(log):
        handled = await worker_over(log, green()).once(PARTITION)

        assert handled is None
        assert await log.since() == []

    run(scenario)


def test_an_escalation_hands_the_task_to_a_human():
    async def scenario(log):
        await mint(log, "T-1")
        outcome = green(
            landed_sha=None,
            escalation=EscalationReason.POISON_CEILING,
            detail="3 attempts, ceiling 3",
        )
        await worker_over(log, outcome).once(PARTITION)
        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.ESCALATED
        assert board.tasks["T-1"].escalation == "poison_ceiling"
        assert board.tasks["T-1"].claimed_by is None

    run(scenario)


def test_an_attempt_that_neither_landed_nor_escalated_returns_to_the_queue():
    async def scenario(log):
        await mint(log, "T-1")
        await worker_over(log, green(landed_sha=None, detail="gates red")).once(PARTITION)
        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-1"].state is TaskState.QUEUED
        assert board.tasks["T-1"].claimed_by is None
        # And another worker can pick it straight up.
        assert await worker_over(log, green(), name="w-2").once(PARTITION) == "T-1"

    run(scenario)


def test_a_worker_killed_mid_attempt_leaves_a_log_that_says_where_it_stopped():
    """S-0044/D-6: a kill costs the lease and nothing else. The facts recorded
    before the death stand, and no later fact is invented on its behalf."""

    async def scenario(log):
        await mint(log, "T-1")

        async def dies(_task: Task) -> Outcome:
            raise RuntimeError("the worker died")

        worker = Worker(log=log, name="w-1", execute=dies)
        claimed = await worker.claim(PARTITION)

        with pytest.raises(RuntimeError):
            await worker.run(claimed, PARTITION)

        board = project(await log.since(partition=PARTITION))

        assert [event.kind for event in await log.history("T-1")] == [
            EventKind.TASK_MINTED,
            EventKind.TASK_CLAIMED,
        ]
        # Claimed and no further: the death produced no fact, and none is
        # invented for it. The task waits out its lease rather than being
        # released by a process that is no longer running.
        assert board.tasks["T-1"].state is TaskState.CLAIMED
        assert board.tasks["T-1"].claimed_by == "w-1"

    run(scenario)


def test_two_workers_never_take_the_same_task():
    async def scenario(log):
        await mint(log, "T-1")

        assert await worker_over(log, green(), name="w-1").claim(PARTITION) is not None
        assert await worker_over(log, green(), name="w-2").claim(PARTITION) is None

    run(scenario)


def test_a_worker_takes_the_next_disjoint_task_beside_one_in_flight():
    async def scenario(log):
        await mint(log, "T-1", allow=["src/**"])
        await mint(log, "T-2", allow=["web/**"])

        assert await worker_over(log, green(), name="w-1").claim(PARTITION) is not None

        second = await worker_over(log, green(), name="w-2").claim(PARTITION)

        assert second is not None
        assert second.id == "T-2"

    run(scenario)


def test_a_landed_run_state_maps_to_a_landing_fact():
    """The seam to v1's runner (S-0044/D-12): a run state becomes the facts the
    log holds, and nothing in the mapping decides anything."""

    state = RunState(task_id="T-1", path=Path("/tmp/unused"))
    state.state = TaskState.READY
    state.attempts = 2
    state.landed_sha = "3eeafb629e" + "0" * 30
    # The history abbreviates for the human reading it; the record carries
    # the whole sha, because a landing another system joins on cannot be a
    # prefix that was never checked for collisions.
    state.history = [{"fact": "committed 3eeafb629e; pushed=False; pr deferred"}]
    outcome = outcome_of(state)

    assert outcome.landed_sha == "3eeafb629e" + "0" * 30
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


def test_a_refused_dispatch_escalates_rather_than_stranding_the_claim():
    """Found by the first live dispatch (A-128). The regime check refused an
    unmeasured image, the ValueError escaped the worker, the pass died and
    the task sat `claimed` until its lease ran out — so what showed was "no
    work ran" rather than "your configuration refuses". A person is exactly
    who can fix that, and re-queuing would refuse again next pass forever."""

    async def refuse(task):
        raise ValueError("tier 'executor' now resolves an image digest nothing measured")

    async def scenario(log):
        await mint(log, "T-0001")
        worker = Worker(log=log, name="w-1", execute=refuse)

        assert await worker.once(PARTITION) == "T-0001"

        board = project(await log.since(partition=PARTITION))
        view = board.tasks["T-0001"]
        assert view.state is TaskState.ESCALATED
        # Never a document-indicting reason: the fault is the machine's, and
        # `underspecified` would blame the contract in the quality readings.
        assert view.escalation == "gate_infrastructure_failure"

        raised = [
            e
            for e in await log.history("T-0001", partition=PARTITION)
            if e.kind is EventKind.ESCALATION_RAISED
        ]
        assert "dispatch refused" in raised[0].payload["detail"]
        # And the claim is gone: an escalated task is a person's, not a
        # worker's.
        assert view.claimed_by is None

    run(scenario)


def test_a_claim_waits_for_its_dependency_to_reach_the_base():
    """The board says a dependency landed the moment its attempt went green;
    the lane puts that candidate on the base a pass later. A worker handed an
    `on_base` predicate claims nothing the base does not yet carry (bloomery
    T-0005 and T-0006 were cut without their predecessors, 2026-09-19)."""

    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2", depends_on=["T-1"])
        first = worker_over(log, Outcome(attempt=1, exit_code=0, landed_sha="a" * 40))
        assert await first.once(PARTITION) == "T-1"

        held = Worker(log=log, name="w-2", execute=first.execute, on_base=lambda task, board: False)
        assert await held.claim(PARTITION) is None

        free = Worker(log=log, name="w-3", execute=first.execute, on_base=lambda task, board: True)
        claimed = await free.claim(PARTITION)
        assert claimed is not None and claimed.id == "T-2"

    run(scenario)
