"""The manager's board (RFC 0044 §5.4).

The board is a fold over recorded facts, so every case here is written the
same way: record events, project, assert what the manager would decide. The
dispatch rules are v1's — dependency by landing, scope-disjoint in flight,
partitions independent — asserted against the new source of state.
"""

from __future__ import annotations

import asyncio

from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.manager import Board, TaskView, dispatchable, project
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import TaskState
from torve.domain.task import Scope, Task

PARTITION = "morzecrew/torve"
OTHER = "morzecrew/other"


def task(task_id: str, *, allow: list[str], depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        decisions=[],
        depends_on=depends_on or [],
        scope=Scope(allow=allow, deny=[]),
    )


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, task_id, *, partition=PARTITION, **payload):
    await log.record(
        EventKind.TASK_MINTED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"title": task_id, "source_id": "0044", **payload},
    )


async def land(log, task_id, *, partition=PARTITION):
    await log.record(
        EventKind.LANDING_RECORDED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"sha": "a" * 40, "attempt": 1},
    )


async def claim(log, task_id, *, partition=PARTITION, worker="w-1"):
    await log.record(
        EventKind.TASK_CLAIMED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={"worker": worker, "lease_seconds": 900},
    )


def test_an_empty_log_projects_an_empty_board():
    assert project([]) == Board()


def test_a_minted_task_is_queued_and_dispatchable():
    async def scenario(log):
        await mint(log, "T-1")
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.QUEUED
        assert dispatchable({"T-1": task("T-1", allow=["src/**"])}, board, PARTITION) == ["T-1"]

    run(scenario)


def test_a_claimed_task_is_no_longer_dispatchable_and_names_its_worker():
    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.CLAIMED
        assert board.tasks["T-1"].claimed_by == "w-1"
        assert dispatchable({"T-1": task("T-1", allow=["src/**"])}, board, PARTITION) == []

    run(scenario)


def test_a_dependency_is_satisfied_only_by_a_landing():
    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2")
        tasks = {
            "T-1": task("T-1", allow=["src/a/**"]),
            "T-2": task("T-2", allow=["src/b/**"], depends_on=["T-1"]),
        }

        assert dispatchable(tasks, project(await log.since()), PARTITION) == ["T-1"]

        # Reaching a claim, an attempt, even a review is not a landing.
        await claim(log, "T-1")

        assert dispatchable(tasks, project(await log.since()), PARTITION) == []

        await land(log, "T-1")

        assert dispatchable(tasks, project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_tasks_in_flight_hold_their_scope_against_new_dispatch():
    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2")
        await claim(log, "T-1")
        tasks = {
            "T-1": task("T-1", allow=["src/**"]),
            "T-2": task("T-2", allow=["src/**"]),
        }

        assert dispatchable(tasks, project(await log.since()), PARTITION) == []

        # Disjoint scope dispatches beside it.
        tasks["T-2"] = task("T-2", allow=["web/**"])

        assert dispatchable(tasks, project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_partitions_do_not_see_each_others_work():
    async def scenario(log):
        await mint(log, "T-1", partition=PARTITION)
        await mint(log, "T-2", partition=OTHER)
        tasks = {"T-1": task("T-1", allow=["src/**"]), "T-2": task("T-2", allow=["src/**"])}
        board = project(await log.since(partition=PARTITION))

        assert dispatchable(tasks, board, PARTITION) == ["T-1"]
        assert dispatchable(tasks, project(await log.since(partition=OTHER)), OTHER) == ["T-2"]

    run(scenario)


def test_an_escalated_task_waits_for_a_human_and_returns_when_resolved():
    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        await log.record(
            EventKind.ESCALATION_RAISED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"reason": "poison_ceiling", "detail": "3 attempts"},
        )
        tasks = {"T-1": task("T-1", allow=["src/**"])}
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.ESCALATED
        assert board.tasks["T-1"].escalation == "poison_ceiling"
        assert board.tasks["T-1"].claimed_by is None
        assert dispatchable(tasks, board, PARTITION) == []

        await log.record(
            EventKind.ESCALATION_RESOLVED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="misery7100",
            payload={"resolution": "requeued", "note": "contract amended"},
        )

        assert dispatchable(tasks, project(await log.since()), PARTITION) == ["T-1"]

    run(scenario)


def test_an_abandoned_task_never_returns():
    async def scenario(log):
        await mint(log, "T-1")
        await log.record(
            EventKind.ESCALATION_RAISED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"reason": "underspecified"},
        )
        await log.record(
            EventKind.ESCALATION_RESOLVED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="misery7100",
            payload={"resolution": "abandoned"},
        )
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.ABANDONED
        assert dispatchable({"T-1": task("T-1", allow=["src/**"])}, board, PARTITION) == []

    run(scenario)


def test_a_replayed_log_rebuilds_the_same_board():
    """D-44.5's restart transparency is a property of the data: a manager
    that died learns nothing by being handed state, because reading the log
    again is what it would have been handed."""

    async def scenario(log):
        await mint(log, "T-1")
        await mint(log, "T-2")
        await claim(log, "T-1")
        await land(log, "T-1")

        first = project(await log.since())
        second = project(await log.since())

        assert first == second
        assert first.landed() == {"T-1"}
        assert first.tasks["T-1"].landed_sha == "a" * 40

    run(scenario)


def test_facts_that_are_not_transitions_leave_the_state_alone():
    """A divergence, a message or a burn event says something about the
    work, never about whose turn it is (D-44.1)."""

    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        await log.record(
            EventKind.SEAT_CONSUMED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"seat": "executor", "tokens": 120, "cost_usd": 0.02},
        )
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.CLAIMED

    run(scenario)


def test_a_view_defaults_to_queued():
    assert TaskView(task_id="T-1").state is TaskState.QUEUED
