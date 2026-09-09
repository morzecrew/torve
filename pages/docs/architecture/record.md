# The record

One append-only, typed log is the system of record for intent and execution
([S-0044](https://github.com/morzecrew/torve/blob/main/rfcs/0044-the-manager-domain.md),
S-0044/D-1). Every other view of engine state — the board, the projections, the
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
| `partition` | the repository this fact belongs to — landings serialize within one, and partitions run independently (S-0044/D-7) |
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
domain before a record is built (S-0044/D-2). It is the load-bearing half of the
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
| `task.returned` | operator |
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
| `notification.sent` | manager |

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

Facts are written when they become true, and rebuild is replay (S-0044/D-3).
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

## The intent half: sources and decisions

Everything above is execution — what an attempt did, what a gate found, what
landed. The other half of the record is what the work was supposed to
honour.

A **source** is any provenance carrying zero or more decisions: a
specification document, an incident, an audit, a review finding, an
operator's ask. The corpus is one shape of this and not a privileged one —
before the record existed, an incident that settled something had to become
a document first or the settlement was lost. A source is identified by a
stable id — the corpus's is the document identifier, `S-0044` — so a
document renamed on disk keeps its identity and the decisions stay attached
to it; a task's source is the `spec` its contract names (S-0059/D-3).

A **decision** is a subject, and this is the one distinction worth reading
twice, because getting it backwards makes every count wrong and the error
invisible:

- **A second record on the same subject is a new version of that
  decision.** `S-0027/D-7` regraded from `ASSUMED` to `LOCKED` is a second
  record on `S-0027/D-7`. Its current state is the last one; its history is all
  of them — the question `git log -p` over the corpus answers today, by
  hand, from diffs.
- **`supersedes` is an edge to a different decision.** `S-0014/D-13` retired in
  favour of `S-0014/A-1` is one decision naming another. It is not how a regrade
  is expressed.

Retirement is recorded, never inferred from a row that stopped appearing.
Absence cannot tell a deliberate retirement from a table somebody broke, and
for a source that is an incident rather than a file it means nothing at all.

```bash
torve decisions import <repo>          # idempotent: an unchanged corpus appends nothing
torve decisions show <repo> S-0044/D-9     # what it says now, and every version behind it
torve decisions paths <repo> "src/torve/application/**"
```

What the record deliberately does **not** do here is mint. A task contract
still copies its grades at write time from the document a human committed:
putting an import between a signature and the contract that inherits it
creates a way for the two to disagree and buys the mint nothing. That
changes when tasks are records too, at which point the import stops being an
extra step and becomes the only one.

### The archive as a source

A document that leaves the corpus path is imported as a source like any
other, and every row it carries is recorded retired with the archive as
the reason — `archived in 0044-the-manager-domain.md, superseded by 0055`
— so an identifier cited from a log written months ago resolves in the
record exactly as it resolves through `show`. The corpus path is what
contracts inherit from; the archive is what the record remembers.

## What is projected from it

| Projection | Answers |
| --- | --- |
| the board (`torve manager board`) | what each task's recorded facts add up to: state, attempts, who holds it, what it landed, what it has burned |
| the divergence log (`.torve/tasks/<id>/log.yaml`) | the entries the record holds for a task, written into the worktree before each gate pass so the battery judges the record |
| the telemetry stream | the attempt rows the cost, regime and quality projections read |
| the decision graph (`torve decisions`) | what is in force, what each decision used to say, and which decisions govern a set of paths |
| the notification queue | escalations with no settled delivery recorded against them — the queue is the *absence* of a second event, so there is nothing to update and nothing to lose |
| one task's history (`torve why --partition`) | every attempt with its verdict, cost and convictions, the events and reviews around them, and the totals |
| what ran here (`torve status --partition`) | the run states of tasks a run actually touched — an attempt recorded, or the engine holding the task now |
| the planning view (`torve context --partition`) | contracts, attempts, gate health, cost, character and divergences from the record; findings, feedback and the corpus from files, and the report names which |

These three also answer from files, and which one answers is the caller's
choice: naming a partition reads the record, naming none reads this
repository's own telemetry stream and run-state files. Only one rule is
automatic, and it runs in the safe direction — a record that turns out not
to hold the run falls back to the files, never the reverse. A v1 run left a
state file and no log, so an empty record means *ask the files*, not
*nothing ever ran*.

`status` and the board answer different questions over the same rows. The
board is every contract this partition owns and what became of it, including
landings imported from the repository's own trailers. `status` is what ran:
on this repository the record reports one run and the files three — the
manager dispatched one, and the other two are a v1 escalation and a hand run
no partition ever saw. That gap is why both readers still exist.

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
