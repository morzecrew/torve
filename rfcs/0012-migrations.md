---
id: "0012"
title: Migrations
status: accepted
implementation: complete
depends_on: ["0003"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Owner-grouped, forward-only SQL migrations: torve, substrate (pinned to a
  forze version), telemetry from stage 3; yoyo behind the migrate extra,
  torve doctor, and the conformance battery as the gate.
schema_version: 1
---

# RFC 0012 — Migrations

Who owns which schema, how each is versioned, and what must be decided now.

Follows from amendment A-6: substrate schema provisioning is ours, because forze documents its schemas in docstrings and ships no migrations.

---

## 1. Group by owner, not by engine

The obvious split is by database engine. The harder and more consequential split is by **who decides when the schema changes**.

| Owner | Tables | Changes when |
| --- | --- | --- |
| **Torve** | `tasks_ref`, `attempts`, `gate_results`, `findings`, `review_feedback` | our models change |
| **Substrate** | outbox, inbox, run store, step store, schedules, idempotency, distributed locks | **forze is upgraded** |
| **Telemetry** | attempts, engine events, review feedback | the record shape changes |

The middle row is the reason this document exists. Under A-6 the DDL is ours to write, but *what it must contain* is dictated by a version of somebody else's library. Interleaved with our own migrations, a forze upgrade becomes an edit in the middle of our numbering, and "which substrate version is deployed" stops being answerable.

```text
migrations/
  torve/postgres/           0001_documents.sql …
  substrate/postgres/       0001_outbox.sql …          # pinned to forze x.y
  telemetry/duckdb/         (stage 3+, see §4)
  telemetry/clickhouse/     (stage 4+, see §4)
```

Three separate histories, three `schema_migrations` tables, three independent version counters. Engine is the second level, not the first.

**Do not create directories for engines that do not exist.** `torve/postgres/` is justified because telemetry already lives on a different engine — multi-engine is real here, not hypothetical. A directory for a speculative backend is an invitation to put something in it "just in case".

---

## 2. Tooling

**Plain SQL, not a migration DSL.** Alembic pays for itself through autogeneration from SQLAlchemy models; there is nothing to generate from here. The domain is Pydantic, and half the schema belongs to a library that has no models we can introspect. What remains is versioning and ordering, which is the easy part.

More decisively: the interesting statements — `SELECT … FOR UPDATE SKIP LOCKED`, partial indexes for leases, fencing-token constraints — are not expressible through ORM abstractions and would end up inside `op.execute("""…""")` anyway. That is SQL inside Python inside a framework. Drop two of the three layers.

**Runner: `yoyo-migrations`**, as an optional dependency:

```toml
[project.optional-dependencies]
migrate = ["yoyo-migrations>=9"]
```

Not a dev dependency — migrations are applied in production. Not in the base install either: most installs of Torve are `torve gates run` in a consuming repository's CI, where there is no database and never will be, since gates work without a store at all (RFC 0002).

Two implementation notes:

- **`migrations/**/*.sql` ships as package data.** Otherwise a wheel-installed Torve knows how to migrate but has nothing to migrate with, and that is discovered at first deployment. Verify by installing the built wheel into a clean virtualenv and running `torve migrate` against an empty database.
- **Import yoyo lazily**, inside the command rather than at module level, so a missing extra produces "install `torve[migrate]`" with exit code 3 rather than an `ImportError` on every command.

yoyo stays an implementation detail behind `torve migrate`. Replacing it with dbmate later leaves the `.sql` files untouched.

**No `downgrade`.** Rolling a schema back over live data almost never behaves as written, and having a downgrade path creates false confidence that one exists. Forward-compatible migrations plus a backup is the honest arrangement.

**Checksums on.** yoyo records a hash of each applied migration and fails when an applied file has been edited after the fact — the most common cause of dev and production diverging.

---

## 3. Why this stays easy

All three aggregates are immutable and carry `schema_version` (D-22). Old rows are read by the old shape, so there is no backfill and nothing to rewrite. The overwhelming majority of migrations are `ALTER TABLE … ADD COLUMN` and new indexes.

This is a direct consequence of having no update commands, not luck. If an update command is ever added to an aggregate, migrations get hard in the same release — worth remembering when the proposal arrives.

---

## 4. Telemetry is not like the other two

"Telemetry obviously needs migrations" is true only from stage 3 onward.

| Stage | Storage | Migrations |
| --- | --- | --- |
| 1 | JSONL file | **none — a file has no schema** |
| 2 | DuckDB reading the same files | none; views created on demand |
| 3 | DuckDB as a store | views and typed tables; mostly `CREATE OR REPLACE VIEW`, idempotent by construction |
| 4 | ClickHouse | real migrations — table engines, partitioning keys, TTL |

At stages 1 and 2 versioning is carried by `schema_version` inside each record, and mixed-version rows are read by different revisions of the model. That is not a stopgap; it is what append-only buys.

**ClickHouse will not fit yoyo.** DDL is non-transactional and `ALTER` is asynchronous, so a runner that wraps each migration in a transaction is wrong there. It needs either a dedicated tool or a small purpose-built runner.

**Therefore: write no telemetry migrations now.** Create the directory when stage 3 actually arrives, and pick the tool then — for ClickHouse it will not be the same one.

---

## 5. Decide now: `torve migrate` takes a target

Cheap today, breaking later — the same reasoning as `--format json` in the CLI contract.

```text
torve migrate torve
torve migrate substrate
torve migrate telemetry          # no-op at stage 1
torve migrate --all
torve migrate --status           # three versions, one table
```

Ship the argument even though only the first two do anything. Adding a required positional later breaks every existing caller.

`--status` pays off sooner than expected: the first question during a forze upgrade is "which substrate version is applied", and without it that means inspecting the database by hand.

---

## 6. The forze pin

Substrate migrations are written against a specific forze version. That version lives in a file beside them:

```text
migrations/substrate/FORZE_VERSION      # e.g. 0.6.3
```

- It is part of `config_hash` (A-6), so a substrate schema change registers as a different regime in telemetry.
- **`torve doctor` compares the installed forze version against the pin and fails on mismatch.** Otherwise a schema mismatch is discovered through adapter behaviour rather than through a check — which is the expensive way to find out.
- A forze upgrade that changes a substrate schema is a migration task in Torve, not a silent `pip install -U`. This is the real operational cost of A-6 and it should be written into the upgrade routine.

---

## 7. The conformance battery is the gate

Under A-6 the schema contract is expressed as a test rather than as a file. That makes running it non-optional.

**Two runs, and the second is the one people forget:**

1. **From scratch.** Clean Postgres from testcontainers, apply every migration from zero, run the differential conformance battery. DDL that does not match what the adapters expect fails here.
2. **Upgrade path.** Restore a database at the previous release's schema, apply the new migrations, run the battery again. Fresh installs and upgrades are different code paths, and it is the second that breaks.

Both are blocking gates in the Torve repository's own CI. A migration that has never been applied to a populated database has not been tested.

---

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-12.1 | `LOCKED` | Migrations are grouped by schema owner first, engine second | `migrations/**` | Substrate versioning is driven by forze, not by us; interleaving makes the deployed version unknowable |
| D-12.2 | `ASSUMED` | Plain SQL, no migration DSL | `migrations/**` | Revisit only if a SQLAlchemy model ever appears to generate from |
| D-12.3 | `ASSUMED` | `yoyo-migrations` behind the `migrate` extra, imported lazily | `src/torve/application/migrate.py` `pyproject.toml` | Portable to dbmate without touching the `.sql` files |
| D-12.4 | `LOCKED` | No `downgrade`; forward-only plus backups | `migrations/**` | A downgrade path that does not work is worse than none |
| D-12.5 | `LOCKED` | Checksums enabled; editing an applied migration fails | `src/torve/application/migrate.py` | The main cause of environment divergence |
| D-12.6 | `LOCKED` | `torve migrate` takes a target from the first release | `src/torve/cli/**` | Adding a required positional later breaks callers |
| D-12.7 | `LOCKED` | Substrate migrations pin a forze version; `torve doctor` enforces it | `migrations/substrate/FORZE_VERSION` `src/torve/cli/**` | A schema mismatch must be a check, not a symptom |
| D-12.8 | `ASSUMED` | No telemetry migrations before stage 3; ClickHouse gets its own runner | `migrations/**` | A file has no schema; non-transactional DDL breaks transactional runners |
| D-12.9 | `LOCKED` | Conformance battery runs against both a from-scratch and an upgraded database, blocking | `tests/test_postgres_integration.py` | The schema contract is a test; an unrun test is not a contract |
| D-12.10 | `ASSUMED` | Migrations hardcode the canonical relation names (`public.torve_durable_run`, `public.torve_durable_step`), which are `StoreConfig`'s defaults; the config fields remain for embedders and tests, and a deployment that renames them owns its own DDL. Added by execution 2026-08-21 | `migrations/substrate/postgres/0001_durable.sql` `src/torve/config/runconfig.py` | Static SQL files cannot parametrize table names |
| D-12.11 | `ASSUMED` | The `torve` owner directory exists from day one with a README and no steps, so `torve migrate torve` is a stable no-op rather than an unknown target; §1's prohibition stays about speculative engines. Added by execution 2026-08-21 | `migrations/torve/postgres/**` | D-12.6 needs every target resolvable from the first release |
| D-12.12 | `ASSUMED` | Until a second migration exists, §7's run 2 is the populated form — rows present, re-apply a checksummed no-op, battery re-run; the restore-from-previous form becomes real with the first genuine schema change. Added by execution 2026-08-21 | `tests/test_postgres_integration.py` | One migration has no previous release's schema to restore |
| D-12.13 | `ASSUMED` | `torve migrate` replaces `torve store provision` outright — no dual support, and nothing was released to deprecate. Added by execution 2026-08-21 | `src/torve/cli/**` | Two ways to create one schema diverge |
| D-12.14 | `ASSUMED` | `config_hash` digests the `FORZE_VERSION` pin file — the schema regime the migrations were written against — not the installed forze version; `torve doctor` is what compares pin to installed. Added by execution 2026-08-21 | `src/torve/application/telemetry.py` | A mismatch is a failing check, not a silently different hash |
| D-12.15 | `ASSUMED` | The migrate module normalizes DSNs to `postgresql+psycopg://` before handing them to yoyo; operator DSNs stay in the standard form everywhere else. Added by execution 2026-08-21 | `src/torve/application/migrate.py` | yoyo routes bare `postgresql://` through psycopg2, which torve does not ship |
