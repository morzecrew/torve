# State and truth

One boundary organizes everything: **git holds what SHOULD be, the store
holds what HAPPENED** (D-27, LOCKED). Projection across it is one-way,
git → store; nothing in the store ever becomes an input to planning without
a human moving it through a reviewed commit.

![State boundary](../assets/diagrams/state.svg)

## The git side

- `rfcs/` — decision tables and phasing; the only planning input.
- `.torve/tasks/<id>/contract.yaml` — the minted contract, committed.
- `.torve/tasks/<id>/log.yaml` — the agent's divergence journal, landed
  *with* the work. The log is part of the deliverable: `decisions-reported`
  convicts work whose journal does not account for the decisions it touched.
- Landing commits with `Torve-Task` trailers — **the** record of completion.
  A dependency is satisfied only by a landing on `main` (A-29/A-31); run
  states saying "ready" count for nothing across tasks.

## The store side

- Run states: leases, fences, attempt history — the durable-function
  substrate (forze) underneath `torve run`, mock (JSONL) or Postgres.
- Telemetry records per gate run and per attempt, attempt verdict rows
  (RFC 0038), durable escalation events, durable traces (RFC 0039) — the
  full agent stream, kept because the exec boundary clips output at 8KB.
- Everything here is *derived record*: droppable in principle, rebuildable
  in aggregate, never authoritative for what the repo should contain.

## Read models

Projections (`torve context`, the why projection, `torve serve`'s tables)
join both sides read-only. The tracker board is the one projection with
side effects — it writes GitHub issues and labels, which is why it goes
through the [outbox](tracker-outbox.md) rather than calling GitHub directly
from the sweep.

## Two honest gaps

- **Continuation ceilings** (RFC 0026 §5.5 vs code): each dispatch builds a
  fresh `RunState`, so attempts and budgets reset across re-dispatches of
  the same task. Recorded in 0037 §3; unreconciled.
- **Ready-without-ancestry**: a run can record `ready` while the landing
  never reached `main` (the merge lane declines fast-forward over a dirty
  tree). The dirty-tree *cause* was retired on 2026-09-04 (the index file
  that a post-commit hook kept rewriting is untracked now), but the engine
  still does not verify ancestry before recording `ready` — the operator
  chain cherry-picks as a fallback.
