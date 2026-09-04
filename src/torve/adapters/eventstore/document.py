"""Event log persistence (RFC 0044 §5.7): the two deps modules the log's
document spec resolves through.

Nothing here decides anything. The authority table and the payload models
are enforced by the service above (`torve.application.eventlog`), and this
module only says where the records live: in memory for tests and
simulation, or in a Postgres relation whose name is configuration. Swapping
the two is this file's whole purpose, and it is why no other module in the
package imports `forze_mock` or `forze_postgres`.

The relation is provisioned by `migrations/torve/postgres`, torve's own
history — forze documents schemas and ships no migrations, so torve owns
them (A-6, D-12.1). Column names must match the model's field names; the
schema check `PostgresDocumentSchemaSpec` describes is the guard against
the two drifting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from torve.application.eventlog import ResourceName, TxRoute

if TYPE_CHECKING:
    from forze.application.execution import DepsModule

# ----------------------- #

SCHEMA = "public"
RELATION = "torve_event"


# ....................... #


def mock_module(state: Any | None = None) -> DepsModule:
    """Every port in memory. Passing a shared `state` is how two contexts in
    one test see the same log — a worker and the manager, say."""

    from forze_mock import MockDepsModule

    return MockDepsModule(state=state) if state is not None else MockDepsModule()


# ....................... #


async def postgres_module(
    dsn: str, *, schema: str = SCHEMA, relation: str = RELATION
) -> DepsModule:
    """The real log. The client is opened here rather than by a caller
    because a handler may never open a connection (forze's rule and RFC 0015
    §2.1's, arriving at the same place from different directions)."""

    from forze_postgres import PostgresClient, PostgresDepsModule, PostgresDocumentConfig

    client = PostgresClient()
    await client.initialize(dsn=dsn)

    return PostgresDepsModule(
        client=client,
        rw_documents={
            ResourceName.EVENTS: PostgresDocumentConfig(
                read=(schema, relation),
                write=(schema, relation),
                # `rev` is bumped by the writer, not by a trigger: the log is
                # append-only, so the strategy is inert either way, and the
                # application one is the one that needs no DDL to be true.
                bookkeeping_strategy="application",
            )
        },
        tx={TxRoute.DEFAULT},
    )
