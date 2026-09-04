# The record

One append-only, typed log is the system of record for intent and execution
([RFC 0044](https://github.com/morzecrew/torve/blob/main/rfcs/0044-the-manager-domain.md),
D-44.1). Every other view of engine state — the board, the projections, the
telemetry stream — is rebuildable from it. Source code stays git's; only
intent and record moved.

There is no update and no delete, in the service or in the port beneath it.
A correction is another event, because a history that can be rewritten
answers no question reliably.

## What a fact looks like

An event carries an envelope and a payload. The envelope says who, when and
what it is about; the payload says what happened, and its shape is fixed per
kind:

| Envelope | Meaning |
| --- | --- |
| `partition` | the repository this fact belongs to — landings serialize within one, and partitions run independently (D-44.7) |
| `subject_type`, `subject_id` | what the fact is about: a task, a decision, a source |
| `actor_kind`, `actor_id` | who wrote it, checked against the authority table below |
| `created_at`, `id` | the store's clock and its identity — ordering is both, because a timestamp alone is not a total order |
| `correlation_id`, `causation_id` | the run a fact belongs to, and the fact that caused it |
| `schema_version` | on every event, so replay across a schema change is a test rather than a surprise |

Each kind has exactly one payload model with `extra="forbid"`. A payload the
model rejects is refused before anything reaches a store, which is why a
second store adapter cannot be more permissive than the first: neither
adapter is asked to decide anything.

## Who may write what

Write authority is a table over (actor kind, event kind), enforced in the
domain before a record is built (D-44.2). It is the load-bearing half of the
old git-holds-truth rule, kept without git holding truth: **nothing becomes
executable intent without a human signature.**

<!-- authority-table:start -->

| Event kind | May be written by |
| --- | --- |
| `source.imported` | manager, operator |
| `decision.recorded` | manager, operator |
| `decision.accepted` | operator |
| `decision.retired` | manager, operator |
| `task.minted` | manager |
| `task.adopted` | operator |
| `task.claimed` | manager |
| `task.released` | manager |
| `attempt.started` | worker |
| `attempt.finished` | worker |
| `gates.evaluated` | worker |
| `divergence.recorded` | agent |
| `review.recorded` | worker |
| `blocker.raised` | worker |
| `landing.recorded` | manager |
| `escalation.raised` | manager, worker |
| `escalation.resolved` | operator |
| `message.sent` | agent, manager, operator |
| `seat.consumed` | worker |

<!-- authority-table:end -->

Read the rows that are *narrow*. An agent may write two kinds and cannot
write an acceptance, a landing, or another agent's record, whatever its
prompt says. An agent cannot close its own escalation either — one that
could would be able to escalate its way out of every rule it dislikes. And
`task.minted` is manager-only, which is what makes minting the act that
places a task on a partition rather than a note somebody left.

!!! note "This table is generated"

    The rows above are checked against `AUTHORITY` in
    `src/torve/domain/events.py` by a test. A page that disagrees with the
    code is worse than no page.

## Recorded, never derived

Facts are written when they become true, and rebuild is replay (D-44.3).
This retired the previous engine's derive-don't-record rule, which
reconstructed effects from artifacts afterwards and drifted whenever the
artifacts and the reconstruction disagreed.

The rule has teeth in two places worth naming:

- **Attempts report themselves.** One dispatch is up to `poison_ceiling`
  attempts, each possibly under a different tier and each with its own gate
  verdict. The worker sees one outcome, so a worker writing the attempt
  record would be writing a summary that claims to be a history. Each
  attempt is recorded from where it happens, under the tier that actually
  ran it.
- **One record, two carriers.** The attempt record is one object. The
  telemetry row every projection reads is *rendered* from the event
  payload, so a field added to one carrier and not the other is not
  expressible.

## What is projected from it

| Projection | Answers |
| --- | --- |
| the board (`torve manager board`) | what each task's recorded facts add up to: state, attempts, who holds it, what it landed, what it has burned |
| the divergence log (`.torve/tasks/<id>/log.yaml`) | the entries the record holds for a task, written into the worktree before each gate pass so the battery judges the record |
| the telemetry stream | the attempt rows the cost, regime and quality projections read |

A projection is never edited. Rebuilding one is reading the log again, which
is also what a manager does when it restarts — restart transparency is a
property of the data, not machinery someone wrote.

## Where it lives

Persistence is [forze](https://github.com/morzecrew/forze)'s document plane.
The spec names a logical resource and nothing physical, so the same code
runs against the in-memory adapter in tests and Postgres in production; the
swap is a wiring edit. The spec deliberately declares no update command —
without one the adapter exposes no update port, which is how append-only is
enforced rather than merely intended.

```bash
just pg-up          # postgres on 127.0.0.1:15433
just migrate        # torve owns its own migrations
torve manager board <repo> --dsn "$TORVE_PG_DSN"
```
