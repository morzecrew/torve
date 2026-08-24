---
id: "0008"
title: Tracker projection
status: accepted
implementation: complete
depends_on: ["0003"]
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-30"]
owner: Lev Litvinov
description: >-
  Any task tracker as a presentation surface: outbound projection over the outbox, restricted inbound commands, no authoritative state in the board.
schema_version: 1
---

# RFC 0008 — Tracker projection

- **Implementation state:** phases 1–2 executed 2026-08-23 (T-0049 the transactional outbox; T-0050 the GitHub Issues projection, live on the lab: issues created and labelled, the relay replay delivering nothing, a refused command answered on its thread). command authorization executed 2026-08-24 (T-0054, pulled forward of the first multi-writer board because RFC 0019's unattended poll leg made it live — D-8.9: a command applies only when its actor is in `tracker.commanders`, an empty list refuses everyone). `approve` executed 2026-08-24 (T-0061 — a commander's approval, bound to the branch tip at apply time, recorded on the run state for the lane's promotion requirement). The remaining item is condition-gated, not debt: a second adapter (Linear/Jira) arrives when a team lives there. Judged complete 2026-08-24
- **Scope:** Projecting engine state onto an external task tracker as a presentation surface, the outbound mapping, idempotency rules, the restricted inbound command set, and what each tracker's state vocabulary costs to adapt. Excludes storing any authoritative state in a tracker, and excludes editing task contracts from a tracker.
- **Inherits:** D-1, D-5, D-22 from RFC 0001 · outbox relay from RFC 0003 §5
- **Related:** RFC 0006 (escalation routing), RFC 0007 (planner read surface)

---

## 1. The rule this document exists to fix in place

**The tracker is an output port. It never holds authoritative state.**

Prior art does the opposite — leases live in issue fields, with owner and a UTC timestamp written back and verified by refetch before dispatch. It works, and it is tempting because it needs no database. It is still wrong here, for five specific reasons:

- **No transaction.** State change and outbox staging cannot be atomic if the state lives in an issue. That reopens the "escalated but nobody was told" window that D-5 closes.
- **No fencing.** Refetch-and-verify narrows the race; it does not eliminate it. A stale worker can still write over a new owner.
- **Shared rate limits.** Heartbeating every task against the forge spends the budget the agents need for work.
- **Append-only is inexpressible.** Issue bodies are edited in place and history is rewritten — exactly what D-22 forbids.
- **Permanent coupling** to one vendor's data model.

Authority stays in the durable run store and the document store. The tracker gets a projection.

## 2. Outbound: another outbox destination

The delivery mechanism already exists (RFC 0003 §5). Tracker updates are staged in the same transaction as the state change and relayed at-least-once, so projection survives a runner crash and needs no separate sync daemon.

*Execution note 2026-08-23 (T-0049):* in the local regime, effects derive from the run state file — itself written atomically with every transition — and re-stage idempotently, so a lost outbox is rebuilt from the states, never invented; that is what closes the "escalated but nobody was told" window here. Store-transactional staging joins with the durable-runner integration, behind the same API.

**At-least-once demands idempotency.** Every projected effect is keyed on `(task_id, state, attempt)` through the idempotency port. Without it, a relay retry posts a second identical comment, and within a week the board is landfill.

| Engine fact | Tracker effect |
| --- | --- |
| `Task` minted | issue or card created, linked to its RFC and phase |
| state transition | column or status field |
| `Attempt` completed | one comment: gate results, cost, duration, `trace_ref` |
| `Finding` | inline review comment at `location` |
| `escalated` + reason | label from the enumerated vocabulary, assigned to a human |
| `ready` | label; the merge lane (RFC 0006) still governs landing |

One comment per attempt, never per gate. A gate-level firehose is how a board becomes unreadable.

## 3. Inbound: commands, not state

Two-way synchronisation is where systems like this die. The rule:

**A human dragging a card does not change state — it submits an intent, which the executor may reject.**

Permitted inbound commands, and nothing else:

| Command | Effect | Rejected when |
| --- | --- | --- |
| `retry` | re-queue an escalated task | task is not in `escalated` |
| `abandon` | terminal, with reason | task is already terminal |
| `approve` | satisfies the approval requirement | review is stale against current head |
| `unblock` | clears a dependency hold | dependency is still unmet |

Each is validated against the real store and may return a refusal, which is posted back as a comment. The board is then wrong for a moment and corrected by the next projection — which is the correct failure mode, because the engine stayed right.

**Task contracts are never editable from a tracker.** `scope`, `acceptance`, `decisions` and `budget` live in git and change through a reviewed pull request. A field edited in Jira that silently rewidened a scope would defeat the entire specification layer.

## 4. Tracker content is untrusted input

Issue bodies and comments are editable by anyone with access, and agents read them. This is an injection surface, not a data source.

- Inbound command parsing is **structured and allow-listed** — a fixed command vocabulary, never free-text instruction interpretation.
- Text projected into an agent's context is marked as untrusted and never treated as specification. The specification is the task contract.
- Command authority is checked against the forge's permissions, not against who typed it.

## 5. The port

Not `set_status(x)`. State vocabularies are incompatible enough that a setter is a lie:

```python
class Tracker(Protocol):
    def reflect(self, task: Task, state: TaskState) -> ReflectResult: ...
    def comment(self, task_id: TaskId, body: str, key: str) -> None: ...
    def annotate(self, task_id: TaskId, finding: Finding, key: str) -> None: ...
    def poll_commands(self, since: datetime) -> list[TrackerCommand]: ...
```

`ReflectResult` is `applied`, `refused(reason)` or `unsupported`. **A refusal is a logged divergence, not an exception.** Torve's state remains correct whether or not the board accepted it, and persistent refusals surface in `torve context` as a configuration problem rather than as mysterious drift.

### Adapter cost by tracker

The API surface is small; the state vocabulary is where the work is.

- **Linear** — states are IDs and any state can be set directly. Cheapest.
- **GitHub Projects v2** — GraphQL with typed fields. Tolerable; the field-option IDs must be resolved once and cached.
- **Jira** — status cannot be set directly, only through a **transition**, which the project workflow may refuse by rule. The adapter maps states to transitions and must handle refusal, or the projection silently diverges from reality. This is the case that justifies `ReflectResult` existing.

## 6. Multiple repositories, one board

A phase spanning services produces tasks in several repositories. The projection groups them under the RFC as the parent unit, so the board shows the phase and its parts rather than a flat list nobody can read across.

Scheduling across repositories — weights, fair share, pausing a project — belongs to the runner, not here. The board displays the result; it does not decide it.

## 7. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-8.1 | `LOCKED` | The tracker holds no authoritative state; leases, status and history live in the store | `src/torve/application/tracker.py` | Reversing loses transactions, fencing and append-only at once |
| D-8.2 | `LOCKED` | Projection rides the outbox, keyed on `(task_id, state, attempt)` for idempotency. *Amended by A-30 2026-08-24: the state effect's key gains the transition ordinal — `(task_id, state, attempt, transitions)` — because a state revisited at the same attempt is a new fact the board must reflect* | `src/torve/application/outbox.py` `src/torve/application/tracker.py` | At-least-once delivery without duplicate comments |
| D-8.3 | `LOCKED` | Inbound is a fixed command vocabulary validated against the store; a card move is an intent, not a state change | `src/torve/application/tracker.py` | Two-way state sync is how these systems rot |
| D-8.4 | `LOCKED` | Task contracts are not editable from a tracker | `src/torve/application/tracker.py` | A silently widened scope defeats the specification layer |
| D-8.5 | `LOCKED` | Tracker text is untrusted input and is never treated as specification | `src/torve/application/tracker.py` `src/torve/adapters/tracker/**` | Injection surface with agent readers |
| D-8.6 | `ASSUMED` | `reflect` returns applied/refused/unsupported; refusal is a logged divergence | `src/torve/application/ports.py` `src/torve/adapters/tracker/**` | Required by Jira-style transition workflows |
| D-8.7 | `ASSUMED` | One comment per attempt, never per gate | `src/torve/application/tracker.py` | Readability; revisit if triage needs finer granularity |
| D-8.8 | `ASSUMED` | GitHub Issues ships first — the forge the team already lives in, proven on the lab repository. Resolved while draft 2026-08-23 | `src/torve/adapters/tracker/github.py` | Whichever the team already lives in; the credential and remote already exist |
| D-8.9 | `ASSUMED` | Commands are authorized before they are validated: only actors in `tracker.commanders` apply, an empty list refuses everyone, and refusals are answered on-thread. Added by execution 2026-08-24 — see .torve/tasks/T-0054 | `src/torve/application/tracker.py` `src/torve/config/runconfig.py` | The board is an unattended command channel once the standing loop polls it |
| D-8.10 | `ASSUMED` | The retry command completes its own re-queue: its apply deletes the task's stale remote branch — a ref deletion under the commander's authority, never a history rewrite, RFC 0010's no-force doctrine untouched — before the state transitions, and a failed cleanup refuses with the escalation left standing. The loop selects the QUEUED state; the runner admits it. Added by execution 2026-08-24 — see .torve/tasks/T-0059 | `src/torve/application/tracker.py` `src/torve/adapters/vcs/git.py` | A retried task was stranded: the loop skipped its run record and the stale branch would have refused the re-run's push |
| D-8.11 | `ASSUMED` | A landed task's issue is closed by a landings pass: the run state is swept after the landing, so sync consults the landing trailer for tasks with no live state and stages one close-out effect per task, ever; the adapter closes an existing issue and never creates one just to close it. A review's discharge is its every target's landing — a review never lands, so no targets or a pending target keeps it open (T-0066). Added by execution 2026-08-24 — see .torve/tasks/T-0065 | `src/torve/application/tracker.py` `src/torve/adapters/tracker/github.py` | Under unattended operation, landed and review issues lingered open with stale state labels — a board nobody grooms must groom itself |
| D-8.12 | `ASSUMED` | The board wears one state label at a time: setting a state label retires the stale state siblings; non-state labels and comment history are untouched. Added by execution 2026-08-24 — see .torve/tasks/T-0065 | `src/torve/adapters/tracker/github.py` | An issue wearing three states at once is a projection that stopped projecting |
| D-8.13 | `ASSUMED` | The board says where the human is needed: a candidate the lane refuses for want of approvals gains a `needs:approval` label and an on-thread prompt naming the tip and the count, keyed per tip — a superseded tip prompts afresh. Added by execution 2026-08-24 — see .torve/tasks/T-0067 | `src/torve/application/tracker.py` `src/torve/cli/tick.py` | Operator feedback: the approval wait was invisible — a board that needs a human must say so on the thread where the reply goes |
| D-8.14 | `ASSUMED` | Review-task issues wear a `review` label from projection, and `approve` refuses a review-role task outright — a review is never landed, so there is nothing to approve. Added by execution 2026-08-24 — see .torve/tasks/T-0067 | `src/torve/application/tracker.py` `src/torve/adapters/tracker/github.py` | Review issues read as peers of work issues; the machine's own work must be distinguishable from the work awaiting a human |

## 8. Risks

- **The board becomes the perceived source of truth.** People trust what they look at. Mitigation: refusals and divergences are visible, and `torve status` is the stated authority in documentation and in escalation comments.
- **Comment volume.** Even one per attempt is a lot across many tasks. Watch it; collapse retries into an edited summary comment if needed — the only place where editing rather than appending is acceptable, because it is a projection, not the record.
- **Rate limits shared with agents.** Projection is bursty at state transitions. Relay with backoff and a per-forge budget separate from the agents' own.

## Phasing

*(Added 2026-08-23 while draft, with the path relocation to RFC 0015's tree
— no `tracker/` top-level package. Phase 1 builds the transactional outbox
this document rides: RFC 0003 §5 specified it and deferred it for want of a
consumer; the projection is that consumer, so the leg lands here, in 0003's
tree. Phase 2 is the GitHub Issues adapter (D-8.8) against the lab
repository — its live criteria need the fine-grained token to gain
**Issues: Read and write** before execution, which is an operator step. The
inbound `approve` command stays deferred until promotion approvals exist as
engine state (RFC 0006's forge leg); the other three commands land here.)*

```yaml
- phase: 1
  title: The outbox the projection rides
  intent: |
    The transactional outbox from RFC 0003 §5, built for its first
    consumer: effects are staged in the same transaction as the state
    change they announce, relayed at-least-once by an explicit relay
    step, and every effect carries an idempotency key so a replay is a
    no-op rather than a duplicate. The engine's existing events keep
    flowing unchanged; the outbox is a new, durable leg beside them —
    staged rows survive a runner crash and relay later, which is the
    property the projection cannot live without.
  scope:
    - "src/torve/application/**"
    - "src/torve/adapters/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
- phase: 2
  title: The GitHub Issues projection
  depends_on: [1]
  intent: |
    The Tracker port and its first adapter: reflect maps engine states to
    issue state and labels and returns applied, refused or unsupported —
    a refusal is a logged divergence, never an exception; comments are
    one per attempt, keyed on task, state and attempt through the
    idempotency rule; findings annotate; escalations label from the
    enumerated vocabulary and assign. Inbound is the fixed command
    vocabulary — retry, abandon, unblock — parsed allow-listed from
    comments, validated against the real store, refusals posted back.
    Tracker text is untrusted input everywhere. Proven against the lab
    repository: all states projected, idempotency verified by
    deliberately replaying the relay, one refusal path exercised.
  scope:
    - "src/torve/application/**"
    - "src/torve/adapters/**"
    - "src/torve/cli/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
```

## 9. Exit criteria

- One tracker adapter projecting all states, with idempotency verified by deliberately replaying the relay.
- Four inbound commands working, including at least one refusal path exercised.
- A `reflect` refusal observed, logged, and surfaced without corrupting engine state.

## Amendments

### A-30 — 2026-08-24 — a revisited state is a new fact (amends D-8.2)

**Found in operation** — on the first organic retry under the standing
schedule. A candidate was reflected `ready` at attempt 1, escalated on a
merge conflict (the board correctly retired `state:ready` for
`state:escalated:merge_conflict`), was re-queued by the commander, and
came back `ready` at the same attempt count. The state effect's key
`(task_id, state, attempt)` had already been delivered, so the revisit
deduped away — the issue kept wearing the escalation label over a ready
candidate, a projection showing yesterday's state.

**Changed:** the state effect's key gains the run's transition ordinal —
the length of the state history that every transition already appends
to. Replays still deliver nothing (the ordinal is stable between
transitions), but a revisit is a longer history and therefore a new
effect. Escalation, notify and attempt effects keep their keys: attempts
move between their revisits by construction.

**Deliberately unchanged:** the outbox mechanism, the ledger, and the
at-least-once contract; on upgrade, each task's *current* state is
re-reflected once under the new key form — idempotent at the
destination, a label re-set and nothing more.
