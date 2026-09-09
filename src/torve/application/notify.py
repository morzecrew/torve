"""Notifications (S-0051): how a recorded escalation reaches a person who
is not looking at the dashboard.

The queue is the log (S-0051/D-2). An escalation whose event id appears in no
`notification.sent` is undelivered — a fold over two kinds, not a second
store — which is why no dual write is possible: if the escalation
committed, the queue has it. That matters here more than anywhere else,
because the failure mode of a notification system losing its queue is
silence, and silence is indistinguishable from nothing having happened.

What the fold gives up against a real outbox is `available_at` backoff and
a processing lease. The first is replaced by an attempt count derived from
what is already recorded; the second is unnecessary while one manager runs
a partition, and S-0051/unresolved-questions names the moment it stops being.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from torve.application.ports import Notification, TransientDelivery
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import EscalationReason

if TYPE_CHECKING:
    from collections.abc import Sequence

    from torve.application.eventlog import EventLog
    from torve.application.ports import Notifier
    from torve.domain.events import EventRecord

# ----------------------- #

# How many deliveries one escalation earns before the relay stops trying
# (S-0051/D-7). A queue that retries forever is a queue that never drains, and
# the escalation is still on the board either way.
MAX_ATTEMPTS = 5

# The escalation reasons that interrupt a person (S-0006/human-attention-is-the-scarce-resource, S-0051/D-8).
# Everything else is board-visible and waits to be looked at: paging on
# every escalation is how a pager stops being read.
INTERRUPT_REASONS = frozenset(
    {
        EscalationReason.BLOCKER_FINDING,
        EscalationReason.MERGE_CONFLICT,
        EscalationReason.LOCKED_CONFLICT,
        EscalationReason.GATE_INFRASTRUCTURE_FAILURE,
        EscalationReason.KILLED,
        EscalationReason.COST_ANOMALY,
    }
)


# ....................... #


def _interrupts(event: EventRecord) -> bool:
    """Whether this escalation is one that interrupts (S-0051/D-8). An unknown
    reason interrupts: a reason nobody has classified is more likely to be
    new than to be routine, and the cost of asking is one page."""

    reason = str(event.payload.get("reason") or "")

    try:
        return EscalationReason(reason) in INTERRUPT_REASONS

    except ValueError:
        return True


# ....................... #


def undelivered(
    events: Sequence[EventRecord], *, now: datetime | None = None
) -> list[Notification]:
    """Every escalation this partition has raised and not delivered, oldest
    first (S-0051/D-2).

    Delivered means a `notification.sent` naming the escalation's id with a
    settled outcome — `delivered`, or the terminal `failed` that is
    recorded precisely so the queue drains (S-0051/D-7). A `retrying` row is an
    attempt rather than an answer and leaves the escalation owed (S-0051/A-1).
    A resolved escalation is *not* filtered out: it
    was somebody's turn when it was raised, and a page that arrives after
    the resolution is late rather than wrong.
    """

    moment = now or datetime.now(UTC)
    # `retrying` is an attempt, not an answer: it records that a delivery
    # was tried and may be tried again, so it must not drain the queue
    # (S-0051/A-1). Only a settled outcome does.
    settled = {
        str(event.payload.get("subject") or "")
        for event in events
        if event.kind is EventKind.NOTIFICATION_SENT
        and str(event.payload.get("outcome") or "delivered") in ("delivered", "failed")
    }

    return [
        Notification(
            task_id=event.subject_id,
            partition=event.partition,
            reason=str(event.payload.get("reason") or ""),
            detail=str(event.payload.get("detail") or ""),
            at=event.created_at,
            event_id=str(event.id),
            age_s=(moment - event.created_at).total_seconds(),
        )
        for event in events
        if event.kind is EventKind.ESCALATION_RAISED
        and str(event.id) not in settled
        and _interrupts(event)
    ]


# ....................... #


def attempts_so_far(events: Sequence[EventRecord], event_id: str) -> int:
    """How many deliveries this escalation has already cost.

    Derived rather than held: the relay is a leg of a pass that keeps
    nothing between passes, so a retry counter in memory would reset every
    time the process restarted — which is exactly when a destination is
    most likely to be down.
    """

    return sum(
        1
        for event in events
        if event.kind is EventKind.NOTIFICATION_SENT
        and str(event.payload.get("subject") or "") == event_id
    )


# ....................... #


async def relay(
    log: EventLog,
    notifier: Notifier,
    *,
    partition: str,
    actor_id: str,
    events: Sequence[EventRecord] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> list[str]:
    """Deliver what is owed, and record what was delivered. Returns the
    task ids paged.

    The recording follows the delivery, never precedes it (S-0051/D-6): a crash
    in between redelivers, which is the failure this document chooses over
    the alternative of recording a page that never went out.

    A transient failure records a `retrying` attempt, which leaves the
    escalation owed and gives the next pass its count; once the count
    reaches the ceiling the attempt is recorded `failed` instead, and the
    queue drains (S-0051/D-7).
    """

    facts = list(events) if events is not None else await log.since(partition=partition)
    paged: list[str] = []

    for notification in undelivered(facts):
        attempt = attempts_so_far(facts, notification.event_id) + 1

        try:
            receipt = notifier.deliver(notification)

        except TransientDelivery as exc:
            # The attempt happened, so it is recorded — a relay that wrote
            # nothing here could never derive its own retry count (S-0051/A-1).
            spent = attempt >= max_attempts
            await _record(
                log,
                notification,
                partition=partition,
                actor_id=actor_id,
                destination=notifier.name,
                outcome="failed" if spent else "retrying",
                attempt=attempt,
                detail=(f"gave up after {attempt}: {exc}" if spent else str(exc))[:300],
            )
            continue

        except Exception as exc:  # a refusal a retry will not fix
            await _record(
                log,
                notification,
                partition=partition,
                actor_id=actor_id,
                destination=notifier.name,
                outcome="failed",
                attempt=attempt,
                detail=str(exc)[:300],
            )
            continue

        await _record(
            log,
            notification,
            partition=partition,
            actor_id=actor_id,
            destination=notifier.name,
            outcome="delivered",
            attempt=attempt,
            receipt=str(receipt)[:200],
        )
        paged.append(notification.task_id)

    return paged


# ....................... #


async def _record(
    log: EventLog,
    notification: Notification,
    *,
    partition: str,
    actor_id: str,
    destination: str,
    outcome: str,
    attempt: int,
    receipt: str = "",
    detail: str = "",
) -> None:
    """One delivery, recorded against the task the escalation was about —
    so `torve why` shows the page beside the escalation that caused it."""

    await log.record(
        EventKind.NOTIFICATION_SENT,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=notification.task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id=actor_id,
        payload={
            "subject": notification.event_id,
            "destination": destination,
            "outcome": outcome,
            "attempt": attempt,
            "receipt": receipt,
            "detail": detail,
        },
    )
