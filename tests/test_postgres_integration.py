"""The conformance battery over the migrated database (rfcs/0012-migrations.md §7,
D-12.9) — the schema contract is a test, and both runs are blocking:

1. From scratch: clean Postgres, `torve migrate` applies every substrate step
   from zero, then the same lease / fence / cancel semantics asserted over
   the mock run against the real adapter.
2. Populated: with rows in the tables, re-running migrate is a checksummed
   no-op (D-12.5) and the battery still holds — fresh installs and upgrades
   are different code paths, and this is the half people forget.

Skipped where no Docker daemon is available.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from forze.application.contracts.durable.function import DurableRunStatus
from forze.application.execution import DepsRegistry, ExecutionRuntime
from test_runtime_conformance import docker_available

from torve.application.migrate import apply as migrate_apply
from torve.config.runconfig import StoreConfig

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker daemon not available")

PORT = 15544
ADMIN_DSN = f"postgresql://postgres:torve-test@127.0.0.1:{PORT}/postgres"


@pytest.fixture(scope="module")
def pg_server():
    name = f"torve-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=torve-test",
            "-p",
            f"127.0.0.1:{PORT}:5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
    )
    try:
        import psycopg

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(ADMIN_DSN, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                time.sleep(0.5)
        else:
            pytest.fail("postgres container never became ready")
        yield ADMIN_DSN
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture()
def pg_dsn(pg_server):
    """A fresh database per test; migrations use the canonical table names."""
    import psycopg

    database = f"torve_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {database}")
    return pg_server.rsplit("/", 1)[0] + f"/{database}"


async def make_taskstore(store_config):
    from torve.adapters.store.durable import open_store
    from torve.application.taskstore import TaskStore

    taskstore = TaskStore(await open_store(store_config), store_config)

    async def body(_ctx, input_json):
        return {"echo": input_json}

    taskstore.register(body)
    return taskstore


async def battery(taskstore, suffix: str) -> None:
    """The differential conformance battery: the same properties asserted over
    the mock in test_taskstore.py, against the migrated real store."""
    record = await taskstore.run_now(
        {"task_id": f"T-PG-{suffix}"}, idempotency_key=f"T-PG-{suffix}:r1"
    )
    assert record.status is DurableRunStatus.COMPLETED
    assert record.output_json == {"echo": {"task_id": f"T-PG-{suffix}"}}

    again = await taskstore.run_now({"task_id": "ignored"}, idempotency_key=f"T-PG-{suffix}:r1")
    assert again.run_id == record.run_id  # idempotent resubmit converges

    zombie = await taskstore.enqueue({"task_id": f"T-PG-Z-{suffix}"})
    claimed = await taskstore.store.begin(zombie.run_id, lease_for=timedelta(milliseconds=50))
    assert claimed is not None
    await asyncio.sleep(0.2)
    expired = await taskstore.expire_abandoned()
    assert zombie.run_id in [r.run_id for r in expired]
    landed = await taskstore.store.load(zombie.run_id)
    assert landed.status is DurableRunStatus.FAILED
    assert "lease_expired" in (landed.error or "")

    pending = await taskstore.enqueue({"task_id": f"T-PG-C-{suffix}"})
    assert await taskstore.request_cancel(pending.run_id)  # capability declared, ask recorded


def test_doctor_store_check_tracks_migration_currency(pg_dsn, monkeypatch, tmp_path):
    """The doctor's store check over a real database: behind before
    migrate (with the instruction), current after (D-12.7's sibling)."""
    import json

    import yaml
    from typer.testing import CliRunner

    from torve.application.migrate import pending_count
    from torve.cli import app

    monkeypatch.setenv("TORVE_PG_DSN", pg_dsn)
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "runtime": {"adapter": "opensandbox"},
                "store": {"adapter": "postgres"},
            }
        ),
        encoding="utf-8",
    )

    def store_check():
        result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
        return {c["name"]: c for c in json.loads(result.stdout)["checks"]}["store"]

    assert pending_count("substrate", pg_dsn) > 0
    behind = store_check()
    assert behind["ok"] is False
    assert "torve migrate substrate" in behind["detail"]

    migrate_apply("substrate", pg_dsn)
    assert pending_count("substrate", pg_dsn) == 0
    current = store_check()
    assert current["ok"] is True
    assert "current" in current["detail"]


def test_migrated_database_passes_the_battery_fresh_and_populated(pg_dsn, monkeypatch):
    monkeypatch.setenv("TORVE_PG_DSN", pg_dsn)
    store_config = StoreConfig(
        adapter="postgres", lease_for=0.5, heartbeat_divisor=2, max_run_duration=30
    )

    # Run 1 — from scratch: every step applied to a clean database.
    assert migrate_apply("substrate", pg_dsn) == 1
    # Counted from the directory: the number of torve's own steps grows, and
    # the property under test is that a clean database takes all of them.
    torve_steps = len(list((Path(__file__).parents[1] / "migrations/torve/postgres").glob("*.sql")))

    assert migrate_apply("torve", pg_dsn) == torve_steps
    assert migrate_apply("telemetry", pg_dsn) == 0  # stage 1: a file has no schema

    async def scenario():
        taskstore = await make_taskstore(store_config)
        await battery(taskstore, "fresh")

        # Run 2 — populated: re-applying is a checksummed no-op, and the
        # battery holds over a database that already carries rows.
        assert migrate_apply("substrate", pg_dsn) == 0
        await battery(taskstore, "upgraded")

    asyncio.run(scenario())


def test_the_event_log_round_trips_through_the_migrated_relation(pg_dsn):
    """The document spec and the DDL are two halves of one schema — column
    names are field names — and only a real database can say they agree.
    The mock proves the service's rules; this proves the relation."""

    from torve.adapters.eventstore.document import postgres_module
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    migrate_apply("torve", pg_dsn)

    async def scenario():
        module = await postgres_module(pg_dsn)
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            recorded = await log.record(
                EventKind.TASK_MINTED,
                partition="morzecrew/torve",
                subject_type=SubjectType.TASK,
                subject_id="T-9100",
                actor_kind=ActorKind.MANAGER,
                actor_id="manager-1",
                payload={"title": "the event log", "source_id": "0044", "phase": 1},
            )

            assert recorded.id is not None

            history = await log.history("T-9100")

            assert [event.kind for event in history] == [EventKind.TASK_MINTED]
            assert history[0].typed_payload().model_dump()["source_id"] == "0044"

    asyncio.run(scenario())
