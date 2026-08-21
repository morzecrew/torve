# RFC 0008 — Tracker projection

- **Status:** 📝 Draft — depends on 0003; independent of 0005–0007
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

| # | Grade | Decision | Consequence |
| --- | --- | --- | --- |
| D-8.1 | `LOCKED` | The tracker holds no authoritative state; leases, status and history live in the store | Reversing loses transactions, fencing and append-only at once |
| D-8.2 | `LOCKED` | Projection rides the outbox, keyed on `(task_id, state, attempt)` for idempotency | At-least-once delivery without duplicate comments |
| D-8.3 | `LOCKED` | Inbound is a fixed command vocabulary validated against the store; a card move is an intent, not a state change | Two-way state sync is how these systems rot |
| D-8.4 | `LOCKED` | Task contracts are not editable from a tracker | A silently widened scope defeats the specification layer |
| D-8.5 | `LOCKED` | Tracker text is untrusted input and is never treated as specification | Injection surface with agent readers |
| D-8.6 | `ASSUMED` | `reflect` returns applied/refused/unsupported; refusal is a logged divergence | Required by Jira-style transition workflows |
| D-8.7 | `ASSUMED` | One comment per attempt, never per gate | Readability; revisit if triage needs finer granularity |
| D-8.8 | `OPEN` | Which tracker ships first | Whichever the team already lives in |

## 8. Risks

- **The board becomes the perceived source of truth.** People trust what they look at. Mitigation: refusals and divergences are visible, and `torve status` is the stated authority in documentation and in escalation comments.
- **Comment volume.** Even one per attempt is a lot across many tasks. Watch it; collapse retries into an edited summary comment if needed — the only place where editing rather than appending is acceptable, because it is a projection, not the record.
- **Rate limits shared with agents.** Projection is bursty at state transitions. Relay with backoff and a per-forge budget separate from the agents' own.

## 9. Exit criteria

- One tracker adapter projecting all states, with idempotency verified by deliberately replaying the relay.
- Four inbound commands working, including at least one refusal path exercised.
- A `reflect` refusal observed, logged, and surfaced without corrupting engine state.
