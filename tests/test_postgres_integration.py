"""The Postgres store against a real dockerized Postgres (D-3.6): torve's own
DDL provisions the tables (the substrate ships none), then the same lease /
fence / cancel semantics asserted over the mock are asserted over the real
adapter. Skipped where no Docker daemon is available."""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from datetime import timedelta

import pytest
from test_runtime_conformance import docker_available

from torve.runconfig import StoreConfig

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker daemon not available")

PORT = 15544
DSN = f"postgresql://postgres:torve-test@127.0.0.1:{PORT}/postgres"


@pytest.fixture(scope="module")
def pg_dsn():
    name = f"torve-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_PASSWORD=torve-test",
         "-p", f"127.0.0.1:{PORT}:5432", "postgres:16-alpine"],
        check=True, capture_output=True,
    )
    try:
        import psycopg

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(DSN, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                time.sleep(0.5)
        else:
            pytest.fail("postgres container never became ready")
        yield DSN
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture()
def store_config(pg_dsn, monkeypatch):
    monkeypatch.setenv("TORVE_PG_DSN", pg_dsn)
    return StoreConfig(
        adapter="postgres",
        lease_for=0.5,
        heartbeat_divisor=2,
        max_run_duration=30,
        run_relation=f"torve_run_{uuid.uuid4().hex[:8]}",  # isolated per test
        step_relation=f"torve_step_{uuid.uuid4().hex[:8]}",
    )


def test_provision_and_full_lifecycle_over_postgres(store_config):
    from forze.application.contracts.durable.function import DurableRunStatus

    from torve.adapters.durable_store import open_store, provision_postgres
    from torve.taskstore import TaskStore

    async def scenario():
        await provision_postgres(store_config)
        await provision_postgres(store_config)  # idempotent: IF NOT EXISTS throughout

        taskstore = TaskStore(await open_store(store_config), store_config)

        async def body(_ctx, input_json):
            return {"echo": input_json}

        taskstore.register(body)

        # Happy path: enqueue + claim + heartbeat + fenced complete.
        record = await taskstore.run_now({"task_id": "T-PG"}, idempotency_key="T-PG:r1")
        assert record.status is DurableRunStatus.COMPLETED
        assert record.output_json == {"echo": {"task_id": "T-PG"}}

        # Idempotent resubmit converges on the completed run.
        again = await taskstore.run_now({"task_id": "ignored"}, idempotency_key="T-PG:r1")
        assert again.run_id == record.run_id

        # A worker dies holding a lease; reap reclaims under an advanced fence.
        zombie = await taskstore.enqueue({"task_id": "T-PG-Z"})
        claimed = await taskstore.store.begin(zombie.run_id, lease_for=timedelta(milliseconds=50))
        assert claimed is not None
        await asyncio.sleep(0.2)
        expired = await taskstore.expire_abandoned()
        assert zombie.run_id in [r.run_id for r in expired]
        landed = await taskstore.load(zombie.run_id)
        assert landed.status is DurableRunStatus.FAILED
        assert "lease_expired" in (landed.error or "")

        # The backend declares cancellation support; the gate does not raise.
        pending = await taskstore.enqueue({"task_id": "T-PG-C"})
        recorded = await taskstore.request_cancel(pending.run_id)
        assert recorded  # a PENDING run stops at once

    asyncio.run(scenario())
