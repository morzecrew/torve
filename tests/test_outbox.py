"""RFC 0008 phase 1: the outbox — staged idempotently, relayed
at-least-once, replay a no-op, one broken destination never dams the queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.application.outbox import Effect, pending, relay, stage


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".torve").mkdir()
    return tmp_path


def effect(key: str) -> Effect:
    return Effect(key=key, kind="comment", payload={"body": key})


def test_staging_dedupes_by_key(root):
    assert stage(root, effect("T-1:ready:1")) is True
    assert stage(root, effect("T-1:ready:1")) is False
    assert [e.key for e in pending(root)] == ["T-1:ready:1"]


def test_relay_delivers_once_and_replay_is_a_noop(root):
    stage(root, effect("T-1:ready:1"))
    stage(root, effect("T-2:ready:1"))
    seen: list[str] = []

    first = relay(root, lambda e: seen.append(e.key))
    assert first.delivered == ["T-1:ready:1", "T-2:ready:1"]

    # The deliberate replay of the whole relay (§9): nothing redelivers.
    again = relay(root, lambda e: seen.append(e.key))
    assert again.delivered == [] and len(again.skipped) == 2
    assert seen == ["T-1:ready:1", "T-2:ready:1"]
    assert pending(root) == []


def test_a_failed_delivery_stays_pending_and_the_queue_flows_on(root):
    stage(root, effect("T-1:ready:1"))
    stage(root, effect("T-2:ready:1"))

    def flaky(e: Effect) -> None:
        if e.key.startswith("T-1"):
            raise RuntimeError("destination down")

    report = relay(root, flaky)
    assert report.failed == {"T-1:ready:1": "destination down"}
    assert report.delivered == ["T-2:ready:1"]  # the queue was not dammed
    # The failure is retried on the next relay; the delivered row is not.
    healed = relay(root, lambda e: None)
    assert healed.delivered == ["T-1:ready:1"]
    assert healed.skipped == ["T-2:ready:1"]


def test_a_crash_between_deliver_and_ledger_redelivers(root):
    stage(root, effect("T-1:ready:1"))
    attempts: list[str] = []

    class Crash(Exception):
        pass

    def deliver_then_die(e: Effect) -> None:
        attempts.append(e.key)
        raise Crash("runner died after the side effect landed")

    # The crash is modelled as delivery raising AFTER the effect landed:
    # the ledger was never written, so the row redelivers — at-least-once,
    # and the destination's key dedupe absorbs the duplicate.
    relay(root, deliver_then_die)
    relay(root, lambda e: attempts.append(e.key))
    assert attempts == ["T-1:ready:1", "T-1:ready:1"]
