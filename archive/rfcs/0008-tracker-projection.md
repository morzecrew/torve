---
id: 0008
title: Tracker projection
status: superseded
implementation: abandoned
depends_on: ["0003"]
informed_by: []
supersedes: []
superseded_by: "0055"
amended_by: ["A-30", "A-33", "A-36", "A-40", "A-92", "A-143"]
retired: ["D-8.1", "D-8.2", "D-8.3", "D-8.4", "D-8.7", "D-8.8", "D-8.11", "D-8.12", "D-8.13", "D-8.14", "D-8.16", "D-8.17"]
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
| D-8.5 | `LOCKED` | Tracker text is untrusted input and is never treated as specification | `src/torve/application/tracker.py` `src/torve/adapters/tracker/**` | Injection surface with agent readers |
| D-8.6 | `ASSUMED` | `reflect` returns applied/refused/unsupported; refusal is a logged divergence | `src/torve/application/ports.py` `src/torve/adapters/tracker/**` | Required by Jira-style transition workflows |
| D-8.9 | `ASSUMED` | Commands are authorized before they are validated: only actors in `tracker.commanders` apply, an empty list refuses everyone, and refusals are answered on-thread. Added by execution 2026-08-24 — see .torve/tasks/T-0054 | `src/torve/application/tracker.py` `src/torve/config/runconfig.py` | The board is an unattended command channel once the standing loop polls it |
| D-8.10 | `ASSUMED` | The retry command completes its own re-queue: its apply runs the cleanup callable before the state transitions, and a failed cleanup refuses with the escalation left standing. The loop selects the QUEUED state; the runner admits it. Added by execution 2026-08-24 — see .torve/tasks/T-0059. *Amended by A-37 2026-08-25 (registered on RFC 0010, D-10.10): the cleanup is capture-only — the branch persists and the re-run's leased force-push supersedes it, dissolving this row's original stale-branch rationale* | `src/torve/application/tracker.py` `src/torve/adapters/vcs/git.py` | A retried task was stranded: the loop skipped its run record and the stale branch would have refused the re-run's push |
| D-8.15 | `ASSUMED` | A review issue nests under its first target's issue as a forge sub-issue. Added by execution 2026-08-24 — see .torve/tasks/T-0068. *Retired by A-33 2026-08-24: review issues no longer exist to nest — the decoration outlived its subject by a day, measured and deleted honestly* | — | The board's top level is the work; the machine's meta-work indents beneath it |
| D-8.18 | `ASSUMED` | `revise` is the commander's re-queue of a READY candidate whose review found something worth another attempt: the same capture-first cleanup as retry — the pull request's allow-listed threads and the superseded diff enter the revision record, the branch persists (D-10.10) — then `ready → queued`, an edge the state machine gains for exactly this command; reviews are never revised, and an approval of the superseded tip counts for nothing as always. Added by amendment A-40 2026-08-25 | `src/torve/application/tracker.py` `src/torve/domain/states.py` | A ready candidate with a confirmed Major finding offered only approve-the-bug or abandon-the-task — the live refusal on the board is this row's origin |

D-8.17 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.16 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.14 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.13 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.12 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.11 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.8 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.7 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.4 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.3 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.2 was retired 2026-09-09; path rot. The identifier is never reused.

D-8.1 was retired 2026-09-09; path rot. The identifier is never reused.

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
  intent: >-
    The transactional outbox from RFC 0003 §5, built for its first consumer: effects are staged in the same transaction as the state change they announce, relayed at-least-once by an explicit relay step, and every effect carries an idempotency key so a replay is a no-op rather than a duplicate. The engine's existing events keep flowing unchanged; the outbox is a new, durable leg beside them — staged rows survive a runner crash and relay later, which is the property the projection cannot live without.
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
  depends_on: []
- phase: 2
  title: The GitHub Issues projection
  intent: >-
    The Tracker port and its first adapter: reflect maps engine states to issue state and labels and returns applied, refused or unsupported — a refusal is a logged divergence, never an exception; comments are one per attempt, keyed on task, state and attempt through the idempotency rule; findings annotate; escalations label from the enumerated vocabulary and assign. Inbound is the fixed command vocabulary — retry, abandon, unblock — parsed allow-listed from comments, validated against the real store, refusals posted back. Tracker text is untrusted input everywhere. Proven against the lab repository: all states projected, idempotency verified by deliberately replaying the relay, one refusal path exercised.
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
  depends_on: [1]
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

### A-33 — 2026-08-24 — the board is for humans (amends D-8.14, retires D-8.15, adds D-8.16)
**Operator feedback**, after a day of live operation: review tasks
doubled the board and never asked for anything. The doubling fell out of
two sound rules composing badly — reviews are tasks (RFC 0005 D-5.9),
and every task projected an issue — and the day's earlier decorations
(the `review` label, the sub-issue nesting) treated the symptom while
the question was the row's existence. A board row exists to solicit
human input; a review runs unprompted, its findings matter only in
relation to the work it reviewed, and its escalations should interrupt
on the work's thread where the retry/abandon decision lives.

**Changed:** review-role tasks are not projected as issues (D-8.16).
A review's attempt summary posts as a comment on its target's issue; a
review escalation notifies on the target's thread. The run store keeps
the full review record as before — the board just stops mirroring it.
The review label and the sub-issue nesting retire with the rows they
decorated — built in the morning, measured by evening, deleted honestly.

**Deliberately unchanged:** D-5.9 (reviews are tasks — projection is
what changed, not the task model); the approve refusal for review-role
tasks (D-8.14's surviving half); D-8.11's close-out, which now also
tidies the legacy review issues out of the board; and the review
machinery itself — what runs is untouched, only what is shown.

### A-36 — 2026-08-25 — the label follows the gap (amends D-8.13, adds D-8.17)
**Found in operation** — after the first batch fully drained, the owner
reviewed the board and found landed, closed issues still wearing
`needs:approval` beside `state:landed`. The prompt's label had an
application path (D-8.13) and no removal path at all: not on the
approval it asked for, not on the requeue that superseded its tip, not
at the landing. A label that never retires stops carrying information
the day it is first applied.

**Changed:** the label is a claim about the present (D-8.17). Three
clears, all idempotent effects through the same outbox: an applied
approve command stages removal immediately; the projection stages a
clear for any run observed outside ready — keyed on the transition
ordinal like every state effect (A-30), and staged only for tasks the
ledger shows were ever prompted, so the outbox does not fill with
clears for labels never worn; the landings pass stages the backstop
clear. The tracker port gains `unlabel`, and a removal of an absent
label is absorbed at the destination like every other replay.

**Deliberately unchanged:** the prompt itself and its per-tip key
(D-8.13's core); the single-state-label rule (D-8.12), which was
working — `state:*` staleness during a busy drain is delivery lag that
self-corrects, not a defect; and the outbox's at-least-once contract.

### A-40 — 2026-08-25 — revise: the commander's re-queue of a ready candidate (adds D-8.18; the D-8.3 vocabulary grows)
**Found in operation** — the disjoint experiment batch produced the
corpus's first true line-anchored review finding: an allow-listed
reviewer flagged a Major correctness bug in a candidate the task-gated
review had passed, verified real against the code. The candidate sat
READY. `/torve retry` answered on the board: "retry needs an escalated
run; this one is ready" — and the commander's remaining options were
approve-the-bug or abandon-the-task. The state machine had no
`ready → queued` edge at all: external review findings on a passing
candidate had no path back into the loop, and the bug landed.

**Changed:** the vocabulary gains `revise` (D-8.18). Its apply demands
a READY implement-or-revert run, executes the same capture-first
cleanup as retry — the pull request's allow-listed review threads and
the superseded diff enter the revision record while the candidate
still stands, and the branch persists (D-10.10) — then transitions
`ready → queued`, the one new edge, minted for exactly this command.
The re-run revises on the same pull request; its fresh tip prompts for
its own sha-bound approval.

**Deliberately unchanged:** retry, which keeps its escalated-only
contract — the two verbs answer different situations and a shared
verb would blur the record; reviews, which are never revised (their
re-run rides their target's); approvals, sha-bound as always — a
superseded tip's approval counts for nothing; and the review's
advisory grade — `revise` is a human judgement about a finding, never
an automatic consequence of one.

### A-92 — 2026-09-05 — the projection is deleted, not deprecated (retires the implementation)
**The owner, 2026-09-05:** *"for a while we can completely wipe github
issues projector (tracker projection) — not necessary now (as it's
underdeveloped and we would need to rebuild it almost from scratch)."*

Deleted rather than left inert: `application/tracker.py`,
`application/outbox.py`, `cli/tracker.py`, `adapters/tracker/`, the `Tracker`
port and its three value objects, `TrackerConfig`, the tick's `poll` and
`sync` legs, and the tests for all of it — about 2,600 lines. The
`tracker_command` leg of RFC 0022's operator-attention projection goes with
them, since no surface produces those events any more.

**Why deletion rather than deprecation.** The projection was already inert
in every repository torve runs: `tracker.kind` is empty by default and this
one never set it. Inert code is not free — it is 2,600 lines that every
refactor has to be correct about, and this session moved the runner, the
board, the decision graph and the task record past it three times. A
subsystem nobody runs and everybody has to maintain is worse than one that
is gone and recorded.

**What the deletion also takes, which is the part worth reading.** The
tracker carried the *inbound* half: `/torve` commands, the commander
approvals D-8.9 authorized, and RFC 0020's intake requests. Deleting the
outbound projection without them was not available — they share the port and
the poll leg. `torve intake` from the command line survives untouched; what
is gone is a request arriving as an issue.

**What survives, deliberately.** This document, its decisions and its
identifiers. D-8.1's rule — the board is a view, never authority — is the
one thing a rebuild must not relearn by being burned, and it costs nothing
to keep written down. The implementation state is `abandoned`, which is the
D-A.11 judgement for exactly this: not a design that failed, a build that
stopped being worth its maintenance.

**A rebuild starts here, not from scratch.** §3's outbound mapping, §4's
idempotency rules and the restricted inbound command set are unchanged by
the deletion, and A-30/A-33/A-36/A-40 record what the first build learned.
The corpus check now warns that six LOCKED globs name modules that do not
exist. That is correct and should stay: for an abandoned document the globs
name where the build was and where a rebuild would go, and stripping them to
silence the warning would delete the one thing that says where.

### A-143 — 2026-09-09 — 12 path-rotted row(s) retired by `torve rfc check --fix-rot`
```yaml changes
- subject: D-8.1
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.2
  field: retired
  before: src/torve/application/outbox.py src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.3
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.4
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.7
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.8
  field: retired
  before: src/torve/adapters/tracker/github.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.11
  field: retired
  before: src/torve/application/tracker.py src/torve/adapters/tracker/github.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.12
  field: retired
  before: src/torve/adapters/tracker/github.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.13
  field: retired
  before: src/torve/application/tracker.py src/torve/cli/tick.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.14
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.16
  field: retired
  before: src/torve/application/tracker.py
  after: "path rot: every declared glob matches nothing in the tree"
- subject: D-8.17
  field: retired
  before: src/torve/application/tracker.py src/torve/adapters/tracker/github.py
  after: "path rot: every declared glob matches nothing in the tree"
```

*Archived 2026-09-09: superseded by 0055 (RFC 0053 D-53.8).*
