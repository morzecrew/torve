"""The event log service (RFC 0044 §5.1, §5.2): the one way a fact enters
the system of record.

The rules live here rather than in a store because they are the domain's,
not any backend's (D-44.2): the authority table and the kind's payload model
are checked before a record is built, so an unauthorized or malformed write
never reaches persistence to be refused — or, worse, accepted — there. A
second store adapter cannot be more permissive than the first, because
neither adapter is asked to decide anything.

Persistence is forze's document plane. The spec below names a logical
resource and nothing physical (no table, no schema): the deps module maps
that name onto a Postgres relation or the in-memory mock, and swapping the
two is a wiring edit. The spec deliberately declares no update command —
without one the adapter exposes no update port, which is how append-only is
enforced rather than merely intended.

Ordering is `created_at` then `id`. The document base stamps the first and
generates the second, and both are needed: a timestamp alone is not a total
order under concurrency, and a stable tiebreak is what keeps a cursor page
from repeating or skipping a row.

The service holds its ports, never an execution context: `event_log(ctx)`
resolves them once at construction, and what the object keeps is the two
things it actually calls. A service that folds the context away instead
carries a live resolution surface into every method and outlives the scope
that made it valid.
"""

from __future__ import annotations

from asyncio import run_coroutine_threadsafe
from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import attrs
from forze.application.contracts.document import (
    DocumentCommandPort,
    DocumentQueryPort,
    DocumentSpec,
)

from torve.application.ports import BurnEvent, BurnSink
from torve.domain.events import (
    ActorKind,
    CreateEventCmd,
    EventDoc,
    EventKind,
    EventRecord,
    SubjectType,
    check_authority,
    validate_payload,
)

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop
    from datetime import datetime

    from forze.application.execution import ExecutionContext

# ----------------------- #


class ResourceName(StrEnum):
    """Logical resource names (RFC 0044 §5.7). One name is one route: the
    deps module keys its physical configuration by these members, and a
    mismatch between the two is the wiring bug worth naming once here."""

    EVENTS = "torve-events"


# ....................... #


class TxRoute(StrEnum):
    DEFAULT = "default"


# ....................... #

EVENT_SPEC = DocumentSpec(
    name=ResourceName.EVENTS,
    read=EventRecord,
    write={"domain": EventDoc, "create_cmd": CreateEventCmd},
)

# The log's read order, and the reason it names two fields: `created_at`
# alone repeats under concurrent writes, and a cursor over a non-unique key
# either repeats a row or skips one.
ORDER: Mapping[str, Literal["asc", "desc"]] = {"created_at": "asc", "id": "asc"}


# ----------------------- #


@attrs.define(slots=True, kw_only=True, frozen=True)
class EventLog:
    """Append and read the system of record.

    There is no update and no delete, here or on the port beneath: a
    correction is another event, and a history that can be rewritten answers
    no question reliably. The write port is the command port for a spec that
    declares no update command, so "no update" is a fact about what this
    object can do rather than a rule it follows.
    """

    reader: DocumentQueryPort[EventRecord]
    writer: DocumentCommandPort[EventRecord, EventDoc, CreateEventCmd, Any]

    # ....................... #

    async def record(
        self,
        kind: EventKind,
        *,
        partition: str,
        subject_type: SubjectType,
        subject_id: str,
        actor_kind: ActorKind,
        actor_id: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventRecord:
        """Record one fact. Refuses before persisting: an actor outside the
        kind's authority row, or a payload its kind's model rejects."""

        body = payload or {}

        check_authority(actor_kind, kind)
        validate_payload(kind, body)

        command = CreateEventCmd(
            kind=kind,
            partition=partition,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            payload=body,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        return await self.writer.create(command)

    # ....................... #

    async def history(
        self, subject_id: str, *, partition: str | None = None, limit: int = 1000
    ) -> list[EventRecord]:
        """Everything recorded about one subject, oldest first — the read a
        projection of a single task or decision replays."""

        values: dict[str, Any] = {"subject_id": subject_id}

        if partition is not None:
            values["partition"] = partition

        page = await self.reader.find_many(
            filters={"$values": values},
            pagination={"limit": limit, "offset": 0},
            sorts=ORDER,
        )

        return list(page.hits)

    # ....................... #

    async def since(
        self,
        after: datetime | None = None,
        *,
        partition: str | None = None,
        limit: int = 1000,
    ) -> list[EventRecord]:
        """The log's tail after a moment, oldest first — the read a
        projection resumes with, and the manager's view of one partition."""

        values: dict[str, Any] = {}

        if after is not None:
            values["created_at"] = {"$gt": after}

        if partition is not None:
            values["partition"] = partition

        page = await self.reader.find_many(
            filters={"$values": values} if values else None,
            pagination={"limit": limit, "offset": 0},
            sorts=ORDER,
        )

        return list(page.hits)


# ....................... #


def event_log(ctx: ExecutionContext) -> EventLog:
    """Build the service for one context (forze's handler-factory shape):
    the context is used here and not kept, so the object cannot outlive the
    scope that resolved its ports."""

    return EventLog(
        reader=ctx.document.query(EVENT_SPEC),
        writer=ctx.document.command(EVENT_SPEC),
    )


# ....................... #


def burn_sink(
    log: EventLog,
    loop: AbstractEventLoop,
    *,
    partition: str,
    task_id: str,
    seat: str,
    correlation_id: str | None = None,
) -> BurnSink:
    """A sink that records the broker's metering as it happens (RFC 0045
    §5.1).

    The broker calls this on the thread serving the run's egress, so the
    append is handed to the loop rather than awaited: the wire must not wait
    on a database, and a run must not stall because the log is slow. The
    result is deliberately not collected — a failed append loses an
    observation, and losing the run to an observer would be worse.
    """

    def sink(event: BurnEvent) -> None:
        run_coroutine_threadsafe(
            log.record(
                EventKind.SEAT_CONSUMED,
                partition=partition,
                subject_type=SubjectType.TASK,
                subject_id=task_id,
                actor_kind=ActorKind.WORKER,
                actor_id=seat,
                payload={
                    "seat": event.provider,
                    "tokens": event.tokens,
                    "cost_usd": event.cost_usd,
                },
                correlation_id=correlation_id,
            ),
            loop,
        )

    return sink
