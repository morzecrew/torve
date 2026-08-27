"""Durable run store adapters (D-5, D-3.6): the forze mock for tests and
simulation, forze's Postgres store for real runs. Torve constructs the store
objects directly and hands the runner a three-line testing context — none of
the forze runtime is adopted.

Provisioning lives in `migrations/substrate/`, applied by `torve migrate
substrate` (rfcs/0012-migrations.md): the substrate documents schemas in adapter
docstrings and ships no migrations, so torve owns them (A-6).
"""

from __future__ import annotations

import os

from forze.application.contracts.durable.function import DurableRunStorePort
from forze_mock import MockDurableRunStore, MockState

from torve.config.runconfig import StoreConfig

# ----------------------- #


def resolve_dsn(config: StoreConfig) -> str:
    dsn = os.environ.get(config.dsn_env, "")
    if not dsn:
        raise RuntimeError(
            f"store.adapter is 'postgres' but ${config.dsn_env} is not set — "
            "the DSN is named by environment variable, never committed (D-4b)"
        )
    return dsn


# ....................... #


def open_mock_store() -> DurableRunStorePort:
    return MockDurableRunStore(state=MockState())


# ....................... #


async def open_postgres_store(config: StoreConfig) -> DurableRunStorePort:
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


# ....................... #


async def open_store(config: StoreConfig) -> DurableRunStorePort:
    if config.adapter == "mock":
        return open_mock_store()
    if config.adapter == "postgres":
        return await open_postgres_store(config)
    raise RuntimeError(f"unknown store adapter {config.adapter!r}")
