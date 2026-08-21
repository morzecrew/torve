"""Durable run store adapters (D-5, D-3.6): the forze mock for tests and
simulation, forze's Postgres store for real runs. Torve constructs the store
objects directly and hands the runner a three-line testing context — none of
the forze runtime is adopted.

Provisioning divergence, logged in logs/T-0004.md: RFC 0003 §7 assumes the
substrate ships its own provisioning path for these tables; in reality forze
documents the DDL only in adapter docstrings, so torve owns it here and
`torve store provision` applies it.
"""

from __future__ import annotations

import os

from forze.application.contracts.durable.function import (
    DurableRunAdminDepKey,
    DurableRunStoreDepKey,
)
from forze.application.execution import Deps, ExecutionContext
from forze.testing import context_from_deps
from forze_mock import MockDurableRunStore, MockState

from torve.runconfig import StoreConfig

# Schema transcribed from the PostgresDurableRunStore / step-adapter
# docstrings (the substrate's documented contract). Do not trim columns:
# every read projects cancel_requested_at / cancel_refused_at, and a missing
# column makes lease renewal raise, which a heartbeat reads as lease lost.
DDL_TEMPLATE = """\
CREATE TABLE IF NOT EXISTS {schema}.{run_relation} (
    run_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    idempotency_key text,
    input jsonb,
    output jsonb,
    error text,
    tenant_id uuid,
    attempts integer NOT NULL DEFAULT 0,
    leased_until timestamptz,
    available_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    cancel_requested_at timestamptz,
    cancel_refused_at timestamptz,
    PRIMARY KEY (run_id),
    UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS {run_relation}_status_created
    ON {schema}.{run_relation} (status, created_at);
CREATE INDEX IF NOT EXISTS {run_relation}_created_desc
    ON {schema}.{run_relation} (created_at DESC, run_id DESC);
CREATE TABLE IF NOT EXISTS {schema}.{step_relation} (
    run_id text NOT NULL,
    step_id text NOT NULL,
    result jsonb NOT NULL,
    tenant_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_id)
);
"""


def ddl_for(config: StoreConfig) -> str:
    return DDL_TEMPLATE.format(
        schema=config.schema_name,
        run_relation=config.run_relation,
        step_relation=config.step_relation,
    )


def resolve_dsn(config: StoreConfig) -> str:
    dsn = os.environ.get(config.dsn_env, "")
    if not dsn:
        raise RuntimeError(
            f"store.adapter is 'postgres' but ${config.dsn_env} is not set — "
            "the DSN is named by environment variable, never committed (D-4b)"
        )
    return dsn


def context_for(store: object) -> ExecutionContext:
    """The store registered under both the data plane and the control plane —
    forze's mock and Postgres stores each implement both."""
    return context_from_deps(
        Deps.plain({
            DurableRunStoreDepKey: lambda _ctx: store,
            DurableRunAdminDepKey: lambda _ctx: store,
        })
    )


def open_mock_store():
    return MockDurableRunStore(state=MockState())


async def open_postgres_store(config: StoreConfig):
    from forze_postgres import PostgresClient
    from forze_postgres.adapters.durable.run_store import PostgresDurableRunStore
    from forze_postgres.execution.deps.configs.durable import PostgresDurableRunConfig

    client = PostgresClient()
    await client.initialize(dsn=resolve_dsn(config))
    store = PostgresDurableRunStore(
        client=client,
        config=PostgresDurableRunConfig(relation=(config.schema_name, config.run_relation)),
    )
    return store


async def open_store(config: StoreConfig):
    if config.adapter == "mock":
        return open_mock_store()
    if config.adapter == "postgres":
        return await open_postgres_store(config)
    raise RuntimeError(f"unknown store adapter {config.adapter!r}")


async def provision_postgres(config: StoreConfig) -> None:
    """Apply torve's DDL for the durable tables. Migrations belong to the
    adapter (RFC 0003 §7); additive-only by construction."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(resolve_dsn(config)) as conn:
        await conn.execute(ddl_for(config))
        await conn.commit()
