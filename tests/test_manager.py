"""The manager's board (RFC 0044 §5.4).

The board is a fold over recorded facts, so every case here is written the
same way: record events, project, assert what the manager would decide. The
dispatch rules are v1's — dependency by landing, scope-disjoint in flight,
partitions independent — asserted against the new source of state.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import event_log
from torve.application.manager import (
    Board,
    TaskView,
    dispatchable,
    expired,
    project,
    stalled,
)
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
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


def event(kind, task_id, payload=None, *, at=None, partition=PARTITION) -> EventRecord:
    """One record, built rather than written: these cases are about the fold
    and about clocks, and a real store stamps its own."""

    stamped = at or datetime.now(UTC)

    return EventRecord(
        id=uuid.uuid4().hex,
        rev=1,
        created_at=stamped,
        last_update_at=stamped,
        kind=kind,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload=payload or {},
    )


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, task_id, *, partition=PARTITION, allow=None, depends_on=None, **payload):
    """One mint carrying its contract (RFC 0049 D-49.1) — what the board
    reads for scope and dependencies, so a case that leaves it out is
    testing a mint written before A-91 and says so."""

    contract = task(
        task_id, allow=allow if allow is not None else ["src/**"], depends_on=depends_on
    )

    await log.record(
        EventKind.TASK_MINTED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id="manager-1",
        payload={
            "title": task_id,
            "source_id": "0044",
            "depends_on": contract.depends_on,
            "contract": contract.model_dump(mode="json"),
            **payload,
        },
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
        assert dispatchable(board, PARTITION) == ["T-1"]

    run(scenario)


def test_a_claimed_task_is_no_longer_dispatchable_and_names_its_worker():
    async def scenario(log):
        await mint(log, "T-1")
        await claim(log, "T-1")
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.CLAIMED
        assert board.tasks["T-1"].claimed_by == "w-1"
        assert dispatchable(board, PARTITION) == []

    run(scenario)


def test_a_dependency_is_satisfied_only_by_a_landing():
    async def scenario(log):
        await mint(log, "T-1", allow=["src/a/**"])
        await mint(log, "T-2", allow=["src/b/**"], depends_on=["T-1"])

        assert dispatchable(project(await log.since()), PARTITION) == ["T-1"]

        # Reaching a claim, an attempt, even a review is not a landing.
        await claim(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == []

        await land(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_tasks_in_flight_hold_their_scope_against_new_dispatch():
    async def scenario(log):
        await mint(log, "T-1", allow=["src/**"])
        await mint(log, "T-2", allow=["src/**"])
        await claim(log, "T-1")

        assert dispatchable(project(await log.since()), PARTITION) == []

        # Disjoint scope dispatches beside it — re-minted, because that is
        # the only way a contract changes now (D-49.4).
        await mint(log, "T-2", allow=["web/**"])

        assert dispatchable(project(await log.since()), PARTITION) == ["T-2"]

    run(scenario)


def test_partitions_do_not_see_each_others_work():
    async def scenario(log):
        await mint(log, "T-1", partition=PARTITION)
        await mint(log, "T-2", partition=OTHER)
        board = project(await log.since(partition=PARTITION))

        assert dispatchable(board, PARTITION) == ["T-1"]
        assert dispatchable(project(await log.since(partition=OTHER)), OTHER) == ["T-2"]

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
        board = project(await log.since())

        assert board.tasks["T-1"].state is TaskState.ESCALATED
        assert board.tasks["T-1"].escalation == "poison_ceiling"
        assert board.tasks["T-1"].claimed_by is None
        assert dispatchable(board, PARTITION) == []

        await log.record(
            EventKind.ESCALATION_RESOLVED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-1",
            actor_kind=ActorKind.OPERATOR,
            actor_id="misery7100",
            payload={"resolution": "requeued", "note": "contract amended"},
        )

        assert dispatchable(project(await log.since()), PARTITION) == ["T-1"]

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
        assert dispatchable(board, PARTITION) == []

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


# ....................... #

# Liveness, read from the burn stream and nowhere else (RFC 0045 D-45.4).


def test_burn_lands_on_the_board_as_a_rate_and_a_total():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.ATTEMPT_STARTED, "T-1", {"attempt": 1, "tier": "executor", "agent": "x"}
            ),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 900, "cost_usd": 0.02},
                at=now - timedelta(minutes=30),
            ),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 600, "cost_usd": 0.01},
                at=now - timedelta(minutes=25),
            ),
        ]
    )
    view = board.tasks["T-1"]

    assert round(view.burned_usd, 3) == 0.03
    assert view.last_burn == now - timedelta(minutes=25)
    # Twenty-five minutes of an in-flight attempt spending nothing.
    assert stalled(view, now=now) is True
    assert stalled(view, now=now, after=timedelta(hours=1)) is False


def test_a_task_nobody_is_running_is_not_stalled():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 1, "cost_usd": 0.0},
                at=now - timedelta(days=1),
            ),
            event(EventKind.LANDING_RECORDED, "T-1", {"sha": "a" * 40, "attempt": 1}),
        ]
    )

    # Landed a day after its last burn: idle, not wedged.
    assert stalled(board.tasks["T-1"], now=now) is False


def test_an_attempt_that_has_burned_nothing_yet_accuses_nobody():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1"),
            event(EventKind.TASK_CLAIMED, "T-1"),
            event(
                EventKind.ATTEMPT_STARTED, "T-1", {"attempt": 1, "tier": "executor", "agent": "x"}
            ),
        ]
    )

    # A sandbox still building, or a tier that calls no provider at all.
    assert stalled(board.tasks["T-1"], now=now) is False


# ....................... #

# The lease (RFC 0044 D-44.6): a worker holds nothing else, and something
# has to be it running out.


def test_a_claim_that_has_gone_silent_past_its_lease_is_expired():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(hours=2)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(hours=1)),
        ]
    )

    assert [view.task_id for view in expired(board, now=now)] == ["T-1"]
    # The window is the manager's rule, and a longer one forgives the same
    # silence.
    assert expired(board, now=now, lease=timedelta(hours=3)) == []


def test_a_working_claim_is_not_expired():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(hours=2)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(hours=1)),
            # Activity is any recorded fact, not a heartbeat the holder
            # sends: a wedged process can report itself healthy, and a burn
            # event is what the work actually produced.
            event(
                EventKind.SEAT_CONSUMED,
                "T-1",
                {"seat": "anthropic", "tokens": 10, "cost_usd": 0.01},
                at=now - timedelta(minutes=1),
            ),
        ]
    )

    assert expired(board, now=now) == []


def test_a_task_nobody_holds_never_expires():
    now = datetime.now(UTC)
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", at=now - timedelta(days=7)),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}, at=now - timedelta(days=7)),
            event(
                EventKind.LANDING_RECORDED,
                "T-1",
                {"sha": "a" * 40, "attempt": 1},
                at=now - timedelta(days=7),
            ),
        ]
    )

    # Landed a week ago and held by nobody: idle, not abandoned.
    assert expired(board, now=now) == []


def test_two_unconstrained_tasks_never_run_together():
    """The defect this shares a rule to prevent: an empty allow-set is
    unconstrained (RFC 0002 §6), and a glob intersection over two empty
    sets is empty — so the manager would have dispatched two tasks that may
    each touch anything, while the standing loop refused the same pair."""

    unconstrained = Task(id="T-1", decisions=[]).model_dump(mode="json")
    board = project(
        [
            event(EventKind.TASK_MINTED, "T-1", {"contract": unconstrained}),
            event(
                EventKind.TASK_MINTED,
                "T-2",
                {"contract": {**unconstrained, "id": "T-2"}},
            ),
            event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
        ]
    )

    assert dispatchable(board, PARTITION) == []


def test_an_oversize_contract_awaits_a_decomposition_and_not_a_worker(tmp_path):
    """D-26.7, ported off the scan (A-105): a contract too large to finish
    is not offered until something carries it as a parent, and the board is
    where that answer now lives — one fold, not a directory walk."""

    from torve.domain.task import Task

    def board_with(*contracts: Task) -> Board:
        return project(
            [
                event(EventKind.TASK_MINTED, one.id, {"contract": one.model_dump(mode="json")})
                for one in contracts
            ]
        )

    huge = Task(
        id="T-1",
        decisions=[],
        intent="x" * 4000,
        scope=Scope(allow=["src/**"]),
        acceptance=["a"] * 12,
    )
    child = Task(id="T-2", decisions=[], parent="T-1", scope=Scope(allow=["docs/**"]))

    assert dispatchable(board_with(huge), PARTITION) == []
    # Decomposed: the integration task has routed once and does not route
    # again, so it is offerable — and its child with it.
    assert dispatchable(board_with(huge, child), PARTITION) == ["T-1", "T-2"]


def test_resolving_an_escalation_returns_the_task_or_takes_it_off_the_board():
    """An escalation is the engine handing a task to a person; resolving is
    the person handing it back. Only an operator may write it (D-44.2)."""

    def board_after(resolution: str):
        return project(
            [
                event(EventKind.TASK_MINTED, "T-1"),
                event(EventKind.TASK_CLAIMED, "T-1", {"worker": "w-1"}),
                event(EventKind.ESCALATION_RAISED, "T-1", {"reason": "poison_ceiling"}),
                event(EventKind.ESCALATION_RESOLVED, "T-1", {"resolution": resolution}),
            ]
        ).tasks["T-1"]

    requeued = board_after("requeued")
    assert requeued.state is TaskState.QUEUED
    assert requeued.escalation is None
    assert requeued.claimed_by is None

    assert board_after("abandoned").state is TaskState.ABANDONED


def test_an_agent_may_not_close_its_own_escalation():
    from torve.domain.events import AUTHORITY, UnauthorizedWrite, check_authority

    assert AUTHORITY[EventKind.ESCALATION_RESOLVED] == frozenset({ActorKind.OPERATOR})

    # An agent that could close its own escalation could escalate its way
    # out of every rule it dislikes.
    for actor in (ActorKind.AGENT, ActorKind.WORKER, ActorKind.MANAGER):
        with pytest.raises(UnauthorizedWrite):
            check_authority(actor, EventKind.ESCALATION_RESOLVED)
