"""RFC 0051: the queue is the log, and delivery is a recorded fact.

What is worth testing here is not the delivery — an adapter's HTTP is its
own business — but the two properties the design rests on: an escalation
with no recorded delivery is the queue, and a delivery that was never
recorded is one that will happen again. Both are folds, so both are
testable without a store, a network or a clock.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.adapters.notify import NoNotifier, WebhookNotifier
from torve.application.eventlog import event_log
from torve.application.notify import attempts_so_far, relay, undelivered
from torve.application.ports import Notification, TransientDelivery
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType

PARTITION = "acme/one"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def event(kind, payload, *, subject="T-0900", at=NOW, event_id=None) -> EventRecord:
    return EventRecord(
        id=event_id or uuid.uuid4().hex,
        rev=1,
        created_at=at,
        last_update_at=at,
        kind=kind,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=subject,
        actor_kind=ActorKind.WORKER,
        actor_id="w-1",
        payload=payload,
    )


def escalation(reason="blocker_finding", **kw) -> EventRecord:
    return event(EventKind.ESCALATION_RAISED, {"reason": reason, "detail": "three blockers"}, **kw)


def sent(subject: str, **payload) -> EventRecord:
    body = {"subject": subject, "destination": "webhook", "outcome": "delivered", "attempt": 1}

    return event(EventKind.NOTIFICATION_SENT, {**body, **payload})


def run(scenario):
    async def main():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


# ....................... #


def test_the_queue_is_an_escalation_with_no_recorded_delivery():
    raised = escalation()

    assert [n.task_id for n in undelivered([raised], now=NOW)] == ["T-0900"]
    # One record naming it, and it is gone from the queue forever after.
    assert undelivered([raised, sent(raised.id)], now=NOW) == []


def test_a_terminal_failure_drains_the_queue_too():
    """D-51.7: the failure is recorded precisely so the queue drains. An
    escalation nobody could page about is still on the board."""

    raised = escalation()
    parked = sent(raised.id, outcome="failed", attempt=5, detail="gave up after 5: timeout")

    assert undelivered([raised, parked], now=NOW) == []


def test_a_batch_class_escalation_never_pages():
    """D-51.8, RFC 0006 §4: paging on everything is how a pager stops being
    read. Underspecification waits to be looked at."""

    assert undelivered([escalation("underspecified")], now=NOW) == []
    assert [n.reason for n in undelivered([escalation("killed")], now=NOW)] == ["killed"]


def test_an_unclassified_reason_interrupts():
    """A reason nobody has classified is more likely to be new than
    routine, and the cost of asking is one page."""

    assert len(undelivered([escalation("something-nobody-mapped")], now=NOW)) == 1


def test_the_notification_carries_the_escalations_own_id_and_age():
    raised = escalation(at=NOW - timedelta(hours=2))
    one = undelivered([raised], now=NOW)[0]

    # The dedup key is the escalation's id, which is what makes this a
    # delivery *of* something (D-51.6).
    assert one.event_id == str(raised.id)
    assert one.age_s == pytest.approx(7200)


def test_attempts_are_derived_from_the_record_not_held_in_memory():
    """The relay keeps nothing between passes, so a counter in memory
    resets exactly when a destination is most likely to be down."""

    raised = escalation()
    facts = [raised, sent(raised.id, outcome="retrying", attempt=1)]

    assert attempts_so_far(facts, str(raised.id)) == 1
    assert attempts_so_far(facts, "some-other-id") == 0


# ....................... #


class Recording:
    """A notifier that says what it was handed."""

    name = "test"

    def __init__(self, fail: Exception | None = None) -> None:
        self.seen: list[Notification] = []
        self.fail = fail

    def deliver(self, notification: Notification) -> str:
        self.seen.append(notification)

        if self.fail is not None:
            raise self.fail

        return "receipt-1"


def test_a_delivery_is_recorded_after_it_happens():
    notifier = Recording()

    async def scenario(log):
        raised = escalation()
        paged = await relay(
            log, notifier, partition=PARTITION, actor_id="manager-1", events=[raised]
        )

        assert paged == ["T-0900"]
        assert [n.event_id for n in notifier.seen] == [str(raised.id)]

        history = await log.history("T-0900", partition=PARTITION)
        recorded = [e for e in history if e.kind is EventKind.NOTIFICATION_SENT]
        assert len(recorded) == 1
        assert recorded[0].payload["subject"] == str(raised.id)
        assert recorded[0].payload["receipt"] == "receipt-1"
        assert recorded[0].payload["outcome"] == "delivered"

    run(scenario)


def test_a_transient_failure_records_the_attempt_and_stays_owed():
    """The attempt happened, so it is a fact — and a relay that recorded
    nothing here could never derive its own retry count (A-126). What must
    not change is that the escalation is still owed."""

    notifier = Recording(fail=TransientDelivery("connection refused"))

    async def scenario(log):
        raised = escalation()
        assert (
            await relay(log, notifier, partition=PARTITION, actor_id="manager-1", events=[raised])
            == []
        )

        history = await log.history("T-0900", partition=PARTITION)
        recorded = [e for e in history if e.kind is EventKind.NOTIFICATION_SENT]
        assert len(recorded) == 1 and recorded[0].payload["outcome"] == "retrying"
        # A `retrying` row is an attempt, not an answer: still in the queue.
        assert len(undelivered([raised, recorded[0]], now=NOW)) == 1

    run(scenario)


def test_a_transient_failure_parks_once_the_attempts_are_spent():
    notifier = Recording(fail=TransientDelivery("still down"))

    async def scenario(log):
        raised = escalation()
        # Four failures already recorded: this is the fifth and last.
        spent = [sent(raised.id, outcome="retrying", attempt=n) for n in (1, 2, 3, 4)]

        await relay(
            log,
            notifier,
            partition=PARTITION,
            actor_id="manager-1",
            events=[raised, *spent],
            max_attempts=5,
        )

        history = await log.history("T-0900", partition=PARTITION)
        recorded = [e for e in history if e.kind is EventKind.NOTIFICATION_SENT]
        assert len(recorded) == 1
        assert recorded[0].payload["outcome"] == "failed"
        assert "gave up after 5" in recorded[0].payload["detail"]
        # And now it drains: a failure nobody could deliver is settled.
        assert undelivered([raised, *spent, recorded[0]], now=NOW) == []

    run(scenario)


def test_a_refusal_a_retry_will_not_fix_is_recorded_immediately():
    notifier = Recording(fail=RuntimeError("404 no such channel"))

    async def scenario(log):
        await relay(log, notifier, partition=PARTITION, actor_id="manager-1", events=[escalation()])

        history = await log.history("T-0900", partition=PARTITION)
        recorded = [e for e in history if e.kind is EventKind.NOTIFICATION_SENT]
        assert len(recorded) == 1 and recorded[0].payload["outcome"] == "failed"
        assert "404" in recorded[0].payload["detail"]

    run(scenario)


def test_the_inert_destination_drains_without_delivering():
    """D-51.4: a repository that configured silence must not accumulate a
    queue forever — the delivery that went nowhere is recorded as what it
    was."""

    async def scenario(log):
        paged = await relay(
            log, NoNotifier(), partition=PARTITION, actor_id="manager-1", events=[escalation()]
        )

        assert paged == ["T-0900"]
        history = await log.history("T-0900", partition=PARTITION)
        recorded = [e for e in history if e.kind is EventKind.NOTIFICATION_SENT]
        assert recorded[0].payload["destination"] == "none"
        assert recorded[0].payload["receipt"] == ""

    run(scenario)


def test_only_the_manager_may_record_a_delivery():
    """A page is the loop's act, not an attempt's (D-51.1's authority row)."""

    from torve.domain.events import UnauthorizedWrite

    async def scenario(log):
        with pytest.raises(UnauthorizedWrite):
            await log.record(
                EventKind.NOTIFICATION_SENT,
                partition=PARTITION,
                subject_type=SubjectType.TASK,
                subject_id="T-0900",
                actor_kind=ActorKind.WORKER,
                actor_id="w-1",
                payload={"subject": "x", "destination": "none"},
            )

    run(scenario)


# ....................... #
# The webhook adapter (RFC 0051 phase 2), against a real local server —
# the wire is the contract, so a stub of it proves nothing.
# ....................... #


def serve_once(handler):
    """One HTTP server on an ephemeral port, torn down with the test."""

    import threading
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server, f"http://127.0.0.1:{server.server_port}/hook"


def notification(**kw) -> Notification:
    fields = {
        "task_id": "T-0900",
        "partition": PARTITION,
        "reason": "blocker_finding",
        "detail": "three blockers",
        "at": NOW,
        "event_id": "abc-123",
        "age_s": 42.0,
    }

    return Notification(**{**fields, **kw})


def test_the_webhook_posts_the_idempotency_key_on_the_wire():
    """D-51.6: the escalation's own id is what a destination dedups on, so
    it must actually reach the destination."""

    from http.server import BaseHTTPRequestHandler

    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen["body"] = json.loads(self.rfile.read(length))
            seen["key"] = self.headers.get("Idempotency-Key")
            self.send_response(200)
            self.send_header("X-Request-Id", "req-7")
            self.end_headers()

        def log_message(self, *_args):  # keep the test output quiet
            return

    server, url = serve_once(Handler)

    try:
        receipt = WebhookNotifier(url, timeout_s=5).deliver(notification())

    finally:
        server.shutdown()

    assert receipt == "req-7"
    assert seen["key"] == "abc-123"
    assert seen["body"]["event_id"] == "abc-123"
    assert seen["body"]["task"] == "T-0900"
    # Composed from records, saying what happened.
    assert seen["body"]["text"] == "T-0900 escalated: blocker_finding — three blockers"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, TransientDelivery), (503, TransientDelivery), (429, TransientDelivery)],
)
def test_a_destination_that_is_unwell_is_a_retry(status, expected):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *_args):
            return

    server, url = serve_once(Handler)

    try:
        with pytest.raises(expected):
            WebhookNotifier(url, timeout_s=5).deliver(notification())

    finally:
        server.shutdown()


def test_a_refusal_is_not_retried():
    """A 4xx is the destination refusing this notification, and it will
    refuse the same one again — retrying is how a queue never drains."""

    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return

    server, url = serve_once(Handler)

    try:
        with pytest.raises(RuntimeError) as caught:
            WebhookNotifier(url, timeout_s=5).deliver(notification())

    finally:
        server.shutdown()

    assert not isinstance(caught.value, TransientDelivery)
    assert "404" in str(caught.value)


def test_an_unreachable_destination_is_a_retry():
    # Port 1 on loopback: nothing listens, and the refusal is immediate.
    with pytest.raises(TransientDelivery):
        WebhookNotifier("http://127.0.0.1:1/hook", timeout_s=2).deliver(notification())


def test_a_webhook_without_a_url_refuses_to_be_built():
    """Silence by misconfiguration is the failure this document exists to
    end; `none` is how silence is chosen."""

    with pytest.raises(ValueError, match="needs a URL"):
        WebhookNotifier("")
