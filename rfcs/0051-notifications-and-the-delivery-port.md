---
id: "0051"
title: Notifications and the delivery port
status: accepted
implementation: complete
depends_on: ["0006", "0044"]
informed_by: ["0008", "0032", "0045"]
supersedes: []
superseded_by: null
amended_by: ["A-126"]
owner: misery7100
description: >-
  How a recorded escalation reaches a person who is not looking at the dashboard: the log is the queue, delivery is a recorded fact, and the destination is a port with adapters.
schema_version: 1
---

# RFC 0051 — Notifications and the delivery port

- **Scope:** How a fact the engine already records reaches a person who is not
  watching. Covers what makes an escalation notification-worthy, where the
  undelivered queue lives, the delivery port and its first adapter, who runs
  the relay, and what "delivered" means when the destination is unreachable.
  It does **not** cover inbound commands, a writable dashboard, or the
  presentation of notifications in the browser — the served surface already
  renders the queue, and making it writable is a change to its security
  posture that belongs to its own document.
- **Related:** RFC 0006 D-6.11 (the decision this implements, whose
  implementation left with the tracker — A-115), RFC 0044 (the record and its
  authority table), RFC 0008 (the outbox argument this inherits rather than
  repeats), RFC 0032 (the served surface, read-only by construction),
  `src/torve/application/worker.py`, `src/torve/application/ports.py`.
- **Origin:** The operator, 2026-09-06: *"we need to build it on top of forze
  with hexagonality — then we can wire whatever."*

---

## 1. Summary

A run escalates, the record says so, and nobody finds out until somebody
looks. This document closes that: an interrupt-class escalation becomes a
**notification** with a destination, delivered by a relay leg of the manager's
own pass, through a `Notifier` port whose first adapter is a webhook.

The queue of undelivered notifications is **the event log itself** — an
escalation with no matching delivery is undelivered, and delivery is recorded
like every other fact. No second store, no dual write, and the same replay
that rebuilds the board rebuilds the queue.

## 2. Motivation

D-6.11 has been unimplemented since September 2026. Its delivery rode
`tracker.notify` through the tracker's outbox, and the whole tracker
projection was deleted (RFC 0008 A-92) after being inert in every repository
torve runs. A-115 recorded the consequence and stopped there.

The gap is measurable in this repository. `T-0096` escalated on 2026-08-30
with reason `killed`. It sat un-triaged for **seven days**, and because
`loop.pause_escalations` defaults to 1 it silently suppressed dispatch and
standing evaluation for every one of them (RFC 0019 A-105). Nothing told
anybody. It was found by reading the board a week later, and the run-state
file it left behind — 8.8 MB — was still on disk until 2026-09-05.

The reading is not what is missing. `context_report` groups escalations by
reason with age and route, `torve status` shows them, the fleet's survey
counts them across roots (A-110), and the dashboard polls all of it every ten
seconds. **Every one of those requires somebody to be looking.** D-6.8 already
makes queue age the primary alert; an alert nobody is sent is a reading.

## 3. Current state

Verified against the tree at `f0151a0`:

- **The fact is recorded.** `Worker.release` writes `ESCALATION_RAISED` with
  `reason` and `detail` (`src/torve/application/worker.py`), and the board
  folds it to `TaskState.ESCALATED`. Nothing consumes it beyond projections.
- **Nothing delivers.** There is no `Notifier` port. The `Tracker` port,
  `tracker.notify`, and the outbox that carried it are deleted. Searching the
  tree for a delivery path finds the reading surfaces and nothing else.
- **The served surface is read-only by construction.** `torve serve` binds
  `127.0.0.1` with no host flag (D-32.2), has no auth, and exposes three GET
  endpoints. It gained `--partition` on 2026-09-06 (A-123) and now answers
  from the record.
- **forze ships the outbox machinery.** `forze.application.contracts.outbox`
  carries `OutboxCommandPort.stage/flush`, `OutboxQueryPort.claim_pending /
  mark_published / mark_failed / mark_retry / reclaim_stale_processing`, depth
  probes, and a Postgres adapter in `forze_postgres.adapters.outbox`. Its
  documented guarantee is **at-least-once with dedup on `event_id`** — not
  exactly-once — and `OutboxSpec.require_transaction` exists precisely because
  staging outside the business transaction is the classic dual write.
- **torve opens no transaction around `log.record`.** The event log's writer
  is a single document create per fact (`src/torve/application/eventlog.py`).
  There is no boundary a second store could be enrolled in today.

## 4. Goals / Non-goals

**Goals.** An escalation reaches a person without anyone looking. The
destination is pluggable without touching the domain. Delivery survives a
process death without duplicating a page. What has and has not been delivered
is answerable from the record alone.

**Non-goals.** Inbound anything — this is one direction only, and the
commander-approval path the tracker also carried stays absent (A-93). No
writable dashboard. No per-operator identity or routing rules: one destination
per repository until a second operator exists to need a second. No new store.

## 5. Design

### 5.1 A notification is a delivery, not a subject

The escalation is the fact and it is already recorded. A notification is not a
second fact about the work; it is an **act performed on** the first one. So
nothing new is minted, no `notification` subject type appears, and the
vocabulary grows by exactly one kind:

```
escalation.raised     (exists)  the fact, written by the worker
notification.sent     (new)     one delivery of one escalation, by the relay
```

`NotificationSent` carries `subject` (the escalation's own event id),
`destination` (the adapter name and its target, never its credential),
`attempt` (how many tries this took) and `detail` (empty on success, the
error's first line on a terminal failure).

This is the RFC 0044 shape read straight: facts are recorded when they become
true (D-44.3), and "this page went out at 10:02" is a fact.

### 5.2 The log is the queue

An escalation whose event id appears in no `notification.sent` is
**undelivered**. That is the entire queue, and it is a fold — the same read
that builds the board, filtered to two kinds.

This avoids the dual write that `OutboxSpec.require_transaction` exists to
refuse. Torve opens no transaction around `log.record` (§3), so staging a row
in a second store at escalation time would commit the escalation and lose the
notification on any crash between them. Deriving the queue from the record
cannot lose it: if the escalation committed, the queue has it.

What this gives up, honestly: the outbox's `available_at` backoff, its
`processing` lease, and its stale-reclaim. §5.5 replaces the first with a
recorded attempt count; the other two are unnecessary while one relay runs at
a time, and §10 asks the question that would change that.

### 5.3 The port

```python
@dataclass(frozen=True)
class Notification:
    """One escalation, addressed. Composed from records — never from a
    finding's own words, which the engine does not judge."""

    task_id: str
    partition: str
    reason: str
    detail: str
    at: datetime
    event_id: str          # the escalation's own id; the dedup key
    age_s: float           # how long it has been somebody's turn

class Notifier(Protocol):
    """One destination. Raises TransientDelivery for a retry, and
    RuntimeError for a refusal a retry will not fix."""

    name: str

    def deliver(self, notification: Notification) -> str: ...
```

`deliver` returns the destination's own receipt (a webhook's response id, a
message id) which lands in `notification.sent` — a delivery you cannot point
at afterwards is indistinguishable from one that did not happen.

The port lives in `torve.application.ports` beside `Runtime`, `Vcs` and
`Broker`; adapters live under `torve/adapters/notify/`, and the composition
root wires them (RFC 0015 §2.1, RFC 0042 D-42.1).

**First adapter: `webhook`.** It needs no account, no vendor SDK and no
credential beyond a URL the operator holds, so it can be tested against a
local server and it makes Slack, Discord and PagerDuty a configuration line
rather than a code change. **Second, deliberately: `none`** — the explicit
inert destination, following the broker's precedent (D-21.9), so "no
notifications" is a stated choice rather than an unconfigured accident.

### 5.4 The relay is a leg of the manager's pass

The manager already runs continuously, rebuilds its whole view from the record
every pass, and holds nothing between passes (D-44.5). A relay leg inherits
all of that: it reads the undelivered queue, delivers, records, and a process
killed mid-delivery re-reads the same queue on restart.

It runs **after** reclaim and **before** the mint, for the reason the standing
leg has that position (A-106): what a pass does first is the work that is
already owed.

A separate relay process is what a second one would be, and the port is shaped
so that is a wiring change — but a process whose only job is draining a queue
that one other process already visits every pass is a second thing to run,
supervise and reason about, for no throughput anybody needs (§10).

### 5.5 Delivery is at-least-once, and the destination deduplicates

D-6.11 says *exactly one delivered notification*. No transport gives that;
forze's own outbox documents at-least-once with dedup on `event_id`, and a
webhook cannot promise better. So the guarantee is assembled, not assumed:

- The relay records `notification.sent` **after** the adapter returns. A crash
  between delivery and recording redelivers.
- Every notification carries the escalation's `event_id` as an idempotency
  key, and the adapter passes it to the destination. A destination that
  honours it collapses the redelivery; one that does not sends a duplicate
  page, which is the failure mode this document accepts.
- A `TransientDelivery` failure records nothing and is retried on the next
  pass, with the attempt count derived from the failures already recorded.
- Past `notify.attempts` (default 5) the relay records `notification.sent`
  with a `detail` naming the error and stops trying. **A queue that retries
  forever is a queue that never drains**, and an escalation that could not be
  delivered is still on the board where it always was.

### Alternatives considered

**forze's outbox as the queue.** The obvious choice, and the operator's first
instinct. It has retry, backoff, leases and a Postgres adapter already. It
loses because torve opens no transaction around `log.record`: staging would be
a dual write, which is the exact failure `require_transaction` exists to
refuse, and closing that gap means giving the event log a transaction boundary
it does not have — a change to RFC 0044's write path, for a queue that is
already derivable. If retry semantics outgrow §5.5, adopting the outbox is a
substitution behind the same port, and this row is why.

**A `notification` subject with its own lifecycle.** Rejected: it makes the
same fact exist twice, which is what D-8.2 was retired for (D-44.3), and every
reader would then have to decide which copy is authoritative.

**The dashboard as the inbox — unread state, mark-as-read.** Deferred, and not
because it is hard. `torve serve` is read-only on loopback with no auth, and
that posture *is* its security model (D-32.2). A write endpoint changes what
an unauthenticated local process can do to the record. That is a decision
worth taking deliberately, in its own document, rather than smuggling in
behind a notification feature.

## 6. Tests

- The queue is a fold: escalations with and without a matching
  `notification.sent`, asserted over a built event list — including a
  redelivery after a recorded failure, and an escalation whose delivery is
  recorded being absent from the queue forever after.
- The relay records after the adapter returns, never before: a `Notifier` that
  raises leaves the queue unchanged, and the next pass retries.
- A transient failure retries and a terminal one parks with its reason, with
  the attempt count derived from the record rather than held in memory.
- The `none` adapter delivers nothing and records nothing, and a pass with it
  wired is a no-op.
- Authority: only the relay's actor may write `notification.sent`
  (`AUTHORITY`), asserted the way every other kind is.
- The webhook adapter against a local HTTP server: the idempotency key on the
  wire, a 5xx as transient, a 4xx as terminal.

## 7. Docs

`pages/docs/operating.md` gains the configuration and what happens when the
destination is down. `pages/docs/architecture/record.md` gains
`notification.sent` in the vocabulary table and the queue in the projections
table. No new page: this is one leg and one port, and a page per feature is
how a docs site stops being read.

## 8. Out of scope

- **Inbound commands and approvals.** They left with the tracker (A-93) and
  their return is a different design with a different security posture.
- **Per-operator routing.** One destination per repository until a second
  operator exists; a routing table with one entry is a table nobody maintains.
- **Batch-class escalations.** RFC 0006 §4 keeps them board-visible by design;
  paging on everything is how a pager stops being read.
- **Notifying on anything but escalations.** Landings, gate failures and cost
  anomalies are all recorded and all readable; each would need its own
  argument for interrupting a person, and none has one yet.

## 9. Risks

- **A duplicate page during a redelivery window.** Accepted and stated: the
  alternative is exactly-once, which no transport offers, and the destination
  holds the key to collapse it.
- **The relay makes the manager's pass do network I/O.** A hanging webhook
  would stall a pass that otherwise touches only the log and the filesystem.
  Mitigated by a timeout on the adapter, and named here because the failure
  would present as "the manager stopped working" rather than "the webhook is
  down".
- **A misread of §5.2.** "The log is the queue" invites someone to add a
  `pending` flag to an event and mutate it. Events are immutable (D-44.2); the
  queue is the *absence* of a second event, and there is nothing to update.
- **Notification fatigue.** Every escalation pages. On a busy fleet that is a
  lot of pages, and the honest answer is §10's first question rather than a
  filter nobody has evidence to shape yet.

## 10. Unresolved questions

- **Whether one destination is enough** once a fleet has more than one
  repository. The manifest already carries per-repository trust and a shared
  attention budget; whether it should carry per-repository destinations is a
  question the second repository answers.
- **Whether the relay belongs in the pass.** It does while one manager runs.
  Two managers over one partition would both drain the same queue and double
  the pages, which is a lease question the outbox already solves — and the
  point at which §5's alternative wins.
- **What a delivery receipt is worth.** Recording it costs nothing; whether
  anybody ever reads one back is unknown, and if the answer is nobody, the
  field goes.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-51.1 | `LOCKED` | A notification is a delivery of a recorded escalation, never a second fact about the work; the vocabulary grows by `notification.sent` and by nothing else | `src/torve/domain/events.py` | The same fact cannot exist in two places and disagree — the failure D-8.2 was retired for |
| D-51.2 | `LOCKED` | The undelivered queue is a fold over the log: an escalation whose id appears in no `notification.sent`. No second store holds it | `src/torve/application/notify.py` | No dual write is possible, because there is nothing to write twice; the cost is the outbox's backoff and leases, replaced by D-51.6 |
| D-51.3 | `LOCKED` | Delivery is a port with adapters; the domain never names a destination | `src/torve/application/ports.py` `src/torve/adapters/notify/**` | Webhook, email or pager is a wiring edit, and RFC 0015's layering keeps the adapter out of the application |
| D-51.4 | `ASSUMED` | The first adapters are `webhook` and `none`; `none` is explicit and is the default | `src/torve/adapters/notify/**` `src/torve/config/runconfig.py` | A repository that has not chosen a destination sends nothing *by decision*, following D-21.9's precedent for the broker |
| D-51.5 | `ASSUMED` | The relay is a leg of the manager's pass, after reclaim and before the mint | `src/torve/application/residency.py` | It inherits restart transparency for free; a separate process stays a wiring change (§10) |
| D-51.6 | `ASSUMED` | Delivery is at-least-once with the escalation's event id as the idempotency key; the relay records after the adapter returns | `src/torve/application/notify.py` | A crash redelivers rather than losing a page, and a destination that honours the key collapses it |
| D-51.7 | `ASSUMED` | A terminal failure records `notification.sent` with its reason after `notify.attempts` tries; the queue drains either way | `src/torve/application/notify.py` `src/torve/config/runconfig.py` | A queue that retries forever never drains, and the escalation is still on the board |
| D-51.8 | `LOCKED` | Only interrupt-class escalations notify; batch-class stays board-visible | `src/torve/application/notify.py` | RFC 0006 §4's distinction, inherited rather than re-decided — paging on everything is how a pager stops being read |
| D-51.9 | `OPEN` | Whether the served surface ever accepts a write. Not settled here: it changes what an unauthenticated loopback process may do (D-32.2) | — | — |

## 12. Phasing

```yaml
- phase: 1
  title: the port, the queue and the inert destination
  intent: >-
    `notification.sent` joins the event vocabulary with its payload model and its authority row; `application/notify.py` gains the queue fold (escalations with no recorded delivery) and the relay that walks it; `Notifier` joins the application's ports with the `none` adapter behind it, wired by the composition root and configured off by default. Nothing is delivered anywhere in this phase and that is the point: the queue, the recording and the authority are provable without a destination existing, and a repository that upgrades sees no behaviour change at all.
  scope:
    - "src/torve/domain/events.py"
    - "src/torve/application/notify.py"
    - "src/torve/application/ports.py"
    - "src/torve/adapters/notify/**"
    - "src/torve/config/runconfig.py"
    - "tests/test_notify.py"
    - "tests/test_events.py"  # A-133: a module in scope brings its test file
    - "tests/test_runconfig.py"  # A-133: a module in scope brings its test file
  acceptance:
    - "uv run pytest tests/test_notify.py tests/test_manager.py"
    - "uv run lint-imports --config pyproject.toml"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: the webhook adapter and the relay leg
  intent: >-
    The webhook adapter posts a composed notification with the escalation's event id as its idempotency key, mapping 5xx and timeouts to a retry and 4xx to a terminal failure; the relay becomes a leg of `residency.once`, after reclaim and before the mint, wired by the composition root from configuration. The retry count is derived from the record rather than held between passes, and a delivery that exhausts it parks with its reason. Verified against a local HTTP server, and once end to end against a real destination the operator holds.
  scope:
    - "src/torve/adapters/notify/webhook.py"
    - "src/torve/application/residency.py"
    - "src/torve/cli/manager.py"
    - "tests/test_notify.py"
    - "tests/test_residency.py"
    - "tests/test_manager.py"  # A-133: a module in scope brings its test file
  acceptance:
    - "uv run pytest tests/test_notify.py tests/test_residency.py"
    - "uv run torve rfc check"
  depends_on: [1]
```

---

## Amendments

### A-126 — 2026-09-06 — Every attempt is a fact, or the ceiling is unreachable
**Found executing phase 1, by the test that was supposed to pass.** §5.5
says a transient failure "records nothing at all" and is retried, and
D-51.7 says the relay parks a delivery "past `notify.attempts`" with the
count derived from the record (D-51.6, §5.2). Those two cannot both be
true. If a failed attempt records nothing, the record holds no attempts to
count, `attempts_so_far` returns zero forever, and the ceiling is never
reached — the relay retries a dead destination until somebody notices.

The test that caught it was written from the RFC, asserting that four
recorded failures plus one more park the delivery. It failed because there
was no way to have four recorded failures.

**Changed:** every attempt is recorded, not only the ones that worked.
`NotificationSent.delivered: bool` becomes
`outcome: "delivered" | "retrying" | "failed"`, and the queue drains on a
*settled* outcome — `delivered` or `failed` — while a `retrying` row leaves
the escalation owed. The retry count is the number of rows naming the
escalation, which is derivable precisely because the failures are there.

D-51.1 stands unbroken: the vocabulary still grows by `notification.sent`
and by nothing else. What changed is what that kind records — attempts
rather than successes — which is the RFC 0044 rule applied consistently
rather than partially: a delivery that was tried and failed is a fact about
the work, and §5.2's own argument for keeping the queue in the log is that
facts which committed cannot be lost.

The cost, stated: a destination that is down for an hour writes one row per
escalation per pass until the ceiling, rather than nothing. Bounded by
`notify.attempts` (default 5), and the rows are the evidence of the outage.

**Both phases landed 2026-09-06.** Verified end to end against real
Postgres and a live HTTP server: an escalation recorded on a fresh
partition appeared in the queue, was delivered with its own event id on the
wire as `Idempotency-Key`, recorded `delivered` with the destination's
receipt, drained the queue, and a second pass paged nothing.
