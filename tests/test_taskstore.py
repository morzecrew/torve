"""TaskStore semantics over the mock store: leases, fencing, cancellation and
the reaper's expiry verdict — the behaviors RFC 0003 §5 buys from the
substrate (D-5), asserted from torve's side of the seam."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

from forze.application.contracts.durable.function import DurableRunStatus

from torve.adapters.durable_store import open_mock_store
from torve.runconfig import StoreConfig
from torve.taskstore import TaskStore

FAST = StoreConfig(lease_for=0.2, heartbeat_divisor=2, max_run_duration=10)


def make_taskstore(body=None) -> TaskStore:
    taskstore = TaskStore(open_mock_store(), FAST)

    async def default_body(_ctx, input_json):
        return {"echo": input_json}

    taskstore.register(body or default_body)
    return taskstore


def test_run_now_completes_with_output():
    async def scenario():
        taskstore = make_taskstore()
        record = await taskstore.run_now({"task_id": "T-1"}, idempotency_key="T-1:r1")
        assert record.status is DurableRunStatus.COMPLETED
        assert record.output_json == {"echo": {"task_id": "T-1"}}
        return record

    asyncio.run(scenario())


def test_idempotent_resubmit_converges_on_one_run():
    async def scenario():
        taskstore = make_taskstore()
        first = await taskstore.run_now({"n": 1}, idempotency_key="same-key")
        second = await taskstore.run_now({"n": 2}, idempotency_key="same-key")
        assert first.run_id == second.run_id
        assert second.output_json == {"echo": {"n": 1}}  # the first submission won

    asyncio.run(scenario())


def test_cancel_rides_the_lease_heartbeat():
    async def scenario():
        started = asyncio.Event()
        observed_cancel = asyncio.Event()

        async def slow_body(_ctx, _input_json):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                observed_cancel.set()
                raise
            return {}

        taskstore = make_taskstore(slow_body)
        running = asyncio.ensure_future(taskstore.run_now({"task_id": "T-2"}))
        await started.wait()
        live = await taskstore.live_records()
        assert len(live) == 1

        recorded = await taskstore.request_cancel(live[0].run_id)
        assert recorded
        record = await running
        assert observed_cancel.is_set()
        assert record.status is DurableRunStatus.CANCELLED

    asyncio.run(scenario())


def test_expired_lease_is_reclaimed_and_fenced():
    async def scenario():
        taskstore = make_taskstore()
        record = await taskstore.enqueue({"task_id": "T-3"})
        # A worker claims the run and dies without a terminal write.
        claimed = await taskstore.store.begin(record.run_id, lease_for=timedelta(milliseconds=20))
        assert claimed is not None
        stale_fence = claimed.attempts
        await asyncio.sleep(0.05)

        expired = await taskstore.expire_abandoned()
        assert [r.run_id for r in expired] == [record.run_id]
        landed = await taskstore.load(record.run_id)
        assert landed.status is DurableRunStatus.FAILED
        assert "lease_expired" in (landed.error or "")

        # The dead worker's late write must not overwrite the verdict: the
        # reclaim advanced the fence, so the stale fence cannot land — whether
        # the store refuses loudly or discards silently, the verdict stands.
        with contextlib.suppress(Exception):
            await taskstore.store.complete(record.run_id, output_json={"late": True},
                                           fence=stale_fence)
        final = await taskstore.load(record.run_id)
        assert final.status is DurableRunStatus.FAILED
        assert final.output_json != {"late": True}

    asyncio.run(scenario())


def test_force_fail_running_is_the_operator_override():
    async def scenario():
        taskstore = make_taskstore()
        record = await taskstore.enqueue({"task_id": "T-4"})
        await taskstore.store.begin(record.run_id, lease_for=timedelta(minutes=5))
        assert len(await taskstore.live_records()) == 1

        forced = await taskstore.force_fail_running()
        assert [r.run_id for r in forced] == [record.run_id]
        assert await taskstore.live_records() == []

    asyncio.run(scenario())
