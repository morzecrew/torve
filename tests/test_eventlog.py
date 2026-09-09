"""The event log over forze's document plane (S-0044/the-event-log, §5.2).

Every case runs against the in-memory adapter, which is the same document
port the Postgres adapter implements — the swap is a deps module, so what
holds here holds there. What is asserted is the service's own contract:
refusals happen before anything is persisted, the log has no way to change
a record once written, and reads come back in the order a projection
replays them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime
from pydantic import ValidationError

from torve.adapters.eventstore.document import mock_module
from torve.application.eventlog import EVENT_SPEC, EventLog, event_log
from torve.domain.events import ActorKind, EventKind, SubjectType, UnauthorizedWrite

PARTITION = "morzecrew/torve"


def run(scenario: Callable[[EventLog], Awaitable[None]]) -> None:
    """One runtime per case, torn down with the scope. The repository has no
    async-test plugin, and one helper is cheaper than adding one."""

    async def main() -> None:
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


async def mint(log, subject_id="T-0300", **overrides):
    fields = {
        "partition": PARTITION,
        "subject_type": SubjectType.TASK,
        "subject_id": subject_id,
        "actor_kind": ActorKind.MANAGER,
        "actor_id": "manager-1",
        "payload": {"title": "the event log", "source_id": "0044", "phase": 1},
    }

    return await log.record(EventKind.TASK_MINTED, **{**fields, **overrides})


def test_a_recorded_fact_reads_back_with_its_payload():
    async def scenario(log):
        recorded = await mint(log)

        assert recorded.kind is EventKind.TASK_MINTED
        assert recorded.typed_payload().model_dump()["title"] == "the event log"
        # Identity and the event's clock come from the document base, not from
        # anything torve stamped.
        assert recorded.id is not None
        assert recorded.created_at is not None

    run(scenario)


def test_history_is_one_subject_oldest_first():
    async def scenario(log):
        await mint(log)
        await log.record(
            EventKind.ATTEMPT_STARTED,
            partition=PARTITION,
            subject_type=SubjectType.TASK,
            subject_id="T-0300",
            actor_kind=ActorKind.WORKER,
            actor_id="w-1",
            payload={"attempt": 1, "tier": "executor", "agent": "harness"},
        )
        await mint(log, subject_id="T-0301")

        assert [event.kind for event in await log.history("T-0300")] == [
            EventKind.TASK_MINTED,
            EventKind.ATTEMPT_STARTED,
        ]

    run(scenario)


def test_the_tail_filters_by_partition():
    async def scenario(log):
        await mint(log)
        await mint(log, subject_id="T-0400", partition="morzecrew/other")

        assert len(await log.since()) == 2
        assert [event.subject_id for event in await log.since(partition=PARTITION)] == ["T-0300"]

    run(scenario)


def test_the_tail_resumes_after_a_moment():
    async def scenario(log):
        first = await mint(log)
        await mint(log, subject_id="T-0301")

        assert [event.subject_id for event in await log.since(first.created_at)] == ["T-0301"]

    run(scenario)


def test_an_unauthorized_write_never_reaches_the_store():
    async def scenario(log):
        with pytest.raises(UnauthorizedWrite):
            await log.record(
                EventKind.DECISION_ACCEPTED,
                partition=PARTITION,
                subject_type=SubjectType.DECISION,
                subject_id="S-0044/D-1",
                actor_kind=ActorKind.AGENT,
                actor_id="agent-1",
            )

        # The refusal is the domain's, so nothing was written on the way to it.
        assert await log.history("S-0044/D-1") == []

    run(scenario)


def test_a_malformed_payload_never_reaches_the_store():
    async def scenario(log):
        with pytest.raises(ValidationError):
            await log.record(
                EventKind.TASK_MINTED,
                partition=PARTITION,
                subject_type=SubjectType.TASK,
                subject_id="T-0302",
                actor_kind=ActorKind.MANAGER,
                actor_id="manager-1",
                payload={"title": "no source named"},
            )

        assert await log.history("T-0302") == []

    run(scenario)


def test_the_spec_declares_no_update_command():
    # S-0044 S-0044/D-1: append-only is enforced by the spec, so the adapter
    # exposes no update port for anything to call by accident.
    assert not EVENT_SPEC.supports_update()
