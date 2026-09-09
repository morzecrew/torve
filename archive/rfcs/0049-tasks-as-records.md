---
id: 0049
title: Tasks as records
status: superseded
implementation: complete
depends_on: ["0044"]
informed_by: ["0001", "0007", "0022", "0047"]
supersedes: []
superseded_by: "0055"
amended_by: ["A-96", "A-97"]
owner: misery7100
description: >-
  The contract a task runs under travels in the record: minting carries the contract itself, re-minting records a changed one as a new version, and the repository's task directory becomes an importer rather than the thing every reader consults.
schema_version: 1
---

# RFC 0049 — Tasks as records

- **Scope:** The last thing the record does not hold. `task.minted` carries
  the contract it minted; a contract that changed on disk is re-minted as a
  new version and never as a state change; the board carries the contract, so
  dispatch reads it instead of a filesystem scan, and the scan becomes what
  brings new contracts *into* the record. It does **not** change how a
  contract is authored, does not move minting off the reviewed file, does not
  rewrite the projections this unblocks, and changes no task's behaviour.
- **Related:** RFC 0044 §5.3 and D-44.7 (minting places a contract on a
  partition), RFC 0047 (the same importer shape, for decisions), RFC 0022
  §5.1 (`read_tasks`, the four-way filesystem join this makes possible to
  replace), `src/torve/application/residency.py`,
  `src/torve/application/manager.py`.
- **Origin:** The owner, 2026-09-05: *"Let's do task as records."* Named by
  RFC 0044 A-90 as the one prerequisite the projection set and the tracker
  both wait on.

---

## 1. Summary

The record holds what happened to a task and not what the task *is*. The
board knows T-0279 was claimed, ran three attempts and landed; it does not
know its scope, its acceptance commands, the decisions it inherited or what
it was asked to do. Every reader that needs those re-reads
`.torve/tasks/<id>/contract.yaml`.

This puts the contract in the mint. `task.minted` carries the contract as
minted; the board carries it; dispatch reads it there. The repository's task
directory becomes an importer of the record — the same shape RFC 0047 gave
the corpus — rather than the thing every reader must be standing next to.

## 2. Motivation

**Everything else about a task is already recorded.** Its state, its
attempts, its convictions, its divergences, its landing, its spend. The
contract is the one field a projection cannot get from the log, which is why
`specquality.read_tasks` is a four-way filesystem join — contract file, log
file, run-state file, git trailers — of which three legs already have
recorded equivalents.

**A worker needs the repository to know what it is running.**
`worker.once(tasks, partition)` takes a dictionary the caller read off disk.
A worker on another machine would need the task directory before it could
claim anything, which makes "a worker holds nothing but a lease" true of
state and false of intent.

**A contract edited after minting silently changes what runs.** The scan
re-reads every contract on every pass, so an edit between the mint and the
dispatch takes effect with nothing recorded — the board says one thing and
the run is judged by another. That is the derive-don't-record hazard in the
one place it matters most, since scope and acceptance are what the gates
enforce.

**Two pieces of work are blocked on exactly this.** RFC 0044 §12's remaining
tail — the projection set that replaces `torve context`, and the tracker as
an ordinary projection consumer — are both joins against tasks. A-90
recorded that they wait on one prerequisite rather than three; this is it.

## 3. Current state

Verified against the tree at `c7edd18`:

- `TaskMinted` carries `title`, `source_id`, `phase` and `depends_on`. The
  contract itself is not recorded anywhere.
- `residency.contracts(root)` globs `T-*/contract.yaml`, parses each through
  `load_task`, skips unreadable ones and filters to `DISPATCHABLE_ROLES`.
  `residency.once` calls it every pass and threads the result into
  `worker.once`.
- `manager.dispatchable(tasks, board, partition)` needs the `Task` for
  `scope.allow` and `depends_on`; `blocked_by` and `overlaps` need it too.
- `_TRANSITIONS` maps `TASK_MINTED` to `QUEUED` unconditionally.
- `mint` skips a task the board already carries, so no second mint is
  written today under any circumstance.
- The live lab partition holds **192 `task.minted` events over 184 tasks**,
  none carrying a contract — so whatever this does must be true of a log
  that predates it.

## 4. Goals / Non-goals

**Goals**

- The contract a task runs under recorded where the task was placed on a
  partition.
- Dispatch answered from the board alone.
- A changed contract recorded as a change rather than taking effect quietly.
- Self-migrating: an existing log becomes complete by being read and
  re-minted, with nothing to run by hand.

**Non-goals**

- **Not a change to authoring.** `torve plan` and adoption write
  `contract.yaml` from a reviewed document exactly as they do; the grades a
  contract inherits still come from the file a human committed (D-47.3).
- **Not the projection set.** This unblocks `torve context`, the served
  tables and the tracker. Building them is their own document.
- **Not the worktree.** A worker still needs the repository's code to run
  an attempt; the work surface is item 1 on the distribution list and is
  untouched here.
- **Not a task the record invents.** Contracts enter the record from the
  repository and nowhere else.

## 5. Design

### 5.1 The contract travels in the mint

Minting is the act that places a contract on a partition (D-44.7), so the
contract as minted is exactly what belongs in that event.

```python
class TaskMinted(BaseModel):
    title: str
    source_id: str
    phase: int = 0
    depends_on: list[str] = Field(default_factory=list)
    contract: dict[str, Any] = Field(default_factory=dict)   # new
```

`contract` is `Task.model_dump()` — the whole contract, validated on the way
in and re-validated on the way out, so a payload that cannot be a `Task` is
a defect in the writer rather than a board row nobody can act on.

`title` and `source_id` stay because they are the board's own derivations
(`title` has a fallback chain, `source_id` is `rfc or "operator"`), not
contract fields copied. `phase` and `depends_on` *are* copies, and rather
than pretend otherwise the writer keeps writing them and a test pins them
equal to the contract's — the same "one record, both carriers, and a test
says so" the attempt record already uses (A-85). The fold prefers the
contract and falls back to the flat fields, which is what makes the 192
mints already in the log readable.

### 5.2 A re-mint is a version, and never a state change

`mint` currently skips any task the board carries. It now also re-mints one
whose recorded contract differs from the repository's — the same comparison
RFC 0047's importer makes, for the same reason: an unchanged contract
appends nothing, and a changed one is recorded rather than applied silently.

Two rules keep that from becoming a way to overrule a person:

- **A re-mint never transitions.** `TASK_MINTED` moves a task to `QUEUED`
  only on the first mint of that subject. A manager that could re-queue an
  escalated task by noticing an edited file would be writing an
  `escalation.resolved` it has no authority to write (D-44.2), by another
  name.
- **A task in flight is never re-minted.** Claimed, running, gated or
  reviewed, the contract it is being judged against is the one it started
  under. A contract changing beneath a running attempt is the hazard the
  corpus rule already names, arriving from the other direction.

The human workflow this makes work: a task escalates, the operator fixes the
contract, resolves the escalation, and the next pass re-mints the change —
recorded, visible on the board, and in force for the next attempt.

### 5.3 The scan becomes the importer

```python
def dispatchable(board: Board, partition: str) -> list[str]: ...

class Worker:
    async def claim(self, partition: str) -> Task | None: ...
    async def once(self, partition: str) -> str | None: ...
```

`TaskView` gains `contract: Task | None`, and everything that needed the
scan's dictionary reads it there. `residency.once` still scans, because that
is how a newly adopted contract reaches the record at all — but the scan's
only consumer is `mint`, which is the definition of an importer.

A view with no recorded contract is not dispatchable. That is not a new
exclusion: a task whose contract file the scan could not read was already
absent from the dictionary dispatch consulted. What changes is that the
board can now say *why* — the contract is missing from the record, rather
than the task simply not appearing.

### 5.4 The log completes itself

The first pass after this lands finds every task on the board carrying no
contract, sees that each differs from the repository's, and re-mints it —
recording the contract it has been running under all along. No migration
step, no backfill script, and by §5.2 nothing changes state on the way.

Measured against the live lab partition before writing anything: 184 board
rows, 184 contracts on disk, **0 rows with a recorded contract, 184 that
would re-mint, and 0 in flight** — so the first pass completes the log in
one go and there is no case where §5.3's in-flight refusal has to hold
something back.

A task whose contract file no longer exists is not re-minted and keeps a
view with no contract. It stops being dispatchable, which it already was
not, and it stays on the board with its history intact.

### Alternatives considered

- **A separate `contract.recorded` kind.** A second event at the same moment
  as the mint, carrying what the mint is about. Two events that must always
  appear together are one event.
- **A task document collection beside the log.** Mutable rows, current state
  by definition, no history — and then "what contract did attempt 2 run
  under" is unanswerable again, which is most of why this exists.
- **Freezing the contract at first mint, permanently.** Simplest rule, and
  it breaks the escalation workflow: the operator's whole recourse after a
  convicted run is to fix the contract, and a record that cannot accept the
  fix sends them to delete the task and mint a new id.
- **Keeping the scan as the reader and recording the contract only for
  projections.** Two readers of the same fact, one of which is authoritative
  and the other convenient — the arrangement every consolidation in RFC 0044
  has been removing.

## 6. Tests

- **Round trip**: a minted contract read back off the board equals the
  contract on disk, field for field, including decisions and scope.
- **Idempotence**: minting twice over an unchanged repository writes one
  event; a changed contract writes a second.
- **A re-mint does not transition**: an escalated task whose contract
  changed is re-minted and is still escalated; a released one is still
  queued; the first mint still queues.
- **A task in flight is never re-minted**, asserted for each in-flight
  state, because "in flight" is a set and a rule that holds for one member
  is not a rule.
- **The flat fields agree with the contract** at write time — `phase` and
  `depends_on` equal to the contract's, which is what lets the fold prefer
  one and fall back to the other.
- **Pre-amendment mints stay readable**: a fold over an event with no
  contract yields a view with none, and dispatch skips it rather than
  raising.
- **Parity**: `dispatchable` from the board alone equals what it returned
  from the scan's dictionary, over the same repository — the test that says
  the reader moved without the rule moving.
- Explicitly not tested: that a contract survives arbitrary YAML. `Task` is
  a pydantic model and its validation is not this document's to re-prove.

## 7. Docs

`pages/docs/architecture/state.md` says the repository holds what should be
and the record holds what happened. That stays true and gets more precise:
the contract is still authored in the repository, and the record holds which
contract was placed on which board, when, and what changed since.
`distribution.md` item 4 loses its task half.

## 8. Out of scope

- **The projection set.** `torve context`, the why report and the served
  tables become record reads once this lands. Their own document.
- **The tracker as a projection consumer.** Same.
- **`specquality.read_tasks`.** Three of its four legs already have recorded
  equivalents and the fourth arrives here; rewriting the join is the
  projection set's work, not this one's.
- **Contracts authored into the record directly.** A task minted by an
  operator with no file is expressible once tasks are records, and is not
  built: nothing asks for it, and the review discipline that makes a
  contract trustworthy is the commit it arrives in.

## 9. Risks

- **Payload size.** A contract with a long intent and twenty inherited
  decisions is a few kilobytes of JSON per mint, and a re-mint writes
  another. Accepted: mints are rare compared to attempts and burn, and the
  history is the point.
- **A contract that stops validating.** A `Task` model change that rejects
  an old contract makes a recorded mint unreadable. The fold treats a
  payload it cannot validate as a view with no contract — undispatchable and
  visible — rather than raising and taking the board down with it.
- **The re-mint comparison being wrong in the safe direction.** A comparison
  that finds a difference where there is none re-mints on every pass and
  fills the log. Mitigated by comparing the validated round trip rather than
  raw dictionaries, and by the idempotence test.
- **Two dispatch answers during the change.** Phase 1 records the contract
  and changes no reader; phase 2 moves them. Between the two the scan is
  still authoritative, which is the same order every consolidation in
  RFC 0044 has used.

## 10. Unresolved questions

- Whether a re-mint should carry what changed, rather than the whole
  contract again. A diff is smaller and needs a diff format nothing else in
  the record has; settled by whether mint volume ever matters.
- Whether `contracts(root)`'s role filter belongs in the importer or the
  reader. It currently drops non-dispatchable roles before minting, so a
  review or draft contract never reaches a board at all — correct today,
  and worth revisiting when a projection wants to see them.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-49.1 | `LOCKED` | `task.minted` carries the contract it minted; the board carries it, and dispatch reads it from there | `src/torve/domain/events.py` `src/torve/application/manager.py` | A worker needs the record and a worktree, not the repository's task directory, to know what it is running |
| D-49.2 | `LOCKED` | A re-mint never transitions a task: `task.minted` moves a subject to queued only the first time it is seen | `src/torve/application/manager.py` | A manager cannot re-queue an escalated task by noticing an edited file — that would be an `escalation.resolved` it has no authority to write |
| D-49.3 | `LOCKED` | A task in flight is never re-minted | `src/torve/application/residency.py` | The contract an attempt is judged against is the one it started under; a contract changing beneath a running attempt is the corpus hazard from the other direction |
| D-49.4 | `ASSUMED` | A contract that differs from the recorded one is re-minted as a new version; an unchanged one appends nothing | `src/torve/application/residency.py` | The operator's recourse after an escalation — fix the contract, resolve, requeue — works and is recorded; an existing log completes itself on the first pass |
| D-49.5 | `ASSUMED` | The fold prefers the recorded contract and falls back to the mint's flat fields; a payload that cannot validate as a `Task` yields a view with no contract rather than an error | `src/torve/application/manager.py` | Events written before this stay readable, and one bad payload cannot take a board down |
| D-49.6 | `ASSUMED` | A view with no recorded contract is not dispatchable | `src/torve/application/manager.py` | Unchanged behaviour stated as a rule — the scan already excluded what it could not read — and the board can now say why |
| D-49.7 | `OPEN` | Whether the role filter belongs to the importer or the reader. Settled by the first projection that wants to see a review contract | `src/torve/application/residency.py` | — |

## 12. Phasing

```yaml
- phase: 1
  title: the contract in the record
  intent: >-
    `task.minted` gains the contract it minted, by amendment to RFC 0044's closed vocabulary, and the writer fills it from the same `Task` the scan parsed — with the flat `phase` and `depends_on` still written and pinned equal to the contract's, so the fold can prefer one and fall back to the other. The board's view carries the contract, validated on read and left absent when a payload cannot be one, which is what keeps the mints already in the log readable. Minting re-mints a task whose recorded contract differs from the repository's, never one in flight, and a re-mint transitions nothing — so an existing log completes itself on the first pass without changing any task's state. No reader moves in this phase.
  scope:
    - "src/torve/domain/events.py"
    - "src/torve/application/manager.py"
    - "src/torve/application/residency.py"
    - "tests/test_task_records.py"
    - "tests/test_events.py"  # A-133: a module in scope brings its test file
    - "tests/test_manager.py"  # A-133: a module in scope brings its test file
    - "tests/test_residency.py"  # A-133: a module in scope brings its test file
  acceptance:
    - "uv run pytest tests/test_task_records.py tests/test_manager.py tests/test_residency.py tests/test_events.py"
    - "uv run lint-imports --config pyproject.toml"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: dispatch reads the board
  intent: >-
    `dispatchable`, `blocked_by`, `overlaps` and the worker's claim take the board instead of a dictionary somebody read off disk, so what a partition can start is answered from the record alone. `residency.once` still scans, because that is how a newly adopted contract enters the record, and the scan's only consumer becomes the mint — the definition of an importer. The rules do not move: a parity test asserts the board-read answer equals the scan-read one over the same repository, which is what says the reader changed and the rule did not.
  scope:
    - "src/torve/application/manager.py"
    - "src/torve/application/worker.py"
    - "src/torve/application/residency.py"
    - "pages/docs/architecture/state.md"
    - "tests/test_task_records.py"
    - "tests/test_manager.py"  # A-133: a module in scope brings its test file
    - "tests/test_worker.py"  # A-133: a module in scope brings its test file
    - "tests/test_residency.py"  # A-133: a module in scope brings its test file
  acceptance:
    - "uv run pytest tests/test_task_records.py tests/test_worker.py tests/test_manager.py"
    - "uv run torve rfc check"
  depends_on: [1]
```

---

## Amendments

### A-96 — 2026-09-05 — The importer takes every role; dispatch is what filters
**Found starting RFC 0050 phase 3.** The scan imported only the roles a
worker executes, because the mint's only consumer was dispatch. RFC 0050
phase 3 gives it a second consumer with a different appetite: the planning
projections read the whole population, and this repository's is 273
contracts — 184 implement, 85 review, 4 draft. A record holding 184 of them
cannot answer `context`'s task block, its decompositions (which key on a
draft's `parent`) or its character calibration; it would answer over two
thirds of the corpus and say nothing about the third it dropped.

**Changed:** D-49.1's scan takes every contract the repository carries.
Dispatch is where the role filter belongs and now lives: `dispatchable`
refuses a contract whose role is not `implement` or `revert`, so a review
or draft row is on the board, carries its contract, and is offered to
nobody. That is also the truthful shape — the run that mints a review is
the only thing that ever executes one (D-5.2, D-20.2), and a board row for
it is a record of work that happened, not an offer.

Two consequences worth stating. The board grows by every non-dispatchable
contract, all of them reading `queued` forever, so `torve manager board`
gains a `role` column: the role is what tells a queue entry from a record.
And a re-mint pass has to be able to run without a worker taking the first
thing it finds — on a repository with a queue that is a real agent and real
money — so `torve manager serve` gains `--no-dispatch`, which imports and
reclaims and claims nothing.

The 184 rows already on this repository's board carry no contract at all:
every one of them was minted before A-91, and no pass has run since to
re-mint. The dry run RFC 0049 measured predicted exactly that. Filling them
is one `--no-dispatch` pass, and it is a prerequisite of phase 3 rather
than part of it.

One more thing the first pass found: the `ran` guard — a contract that ran
here and landed nothing stays off the board — was written to stop a worker
being handed the same unfinished work twice. It is therefore about being
offered, and applies only to what can be. Left as it was it skipped 87 of
the 89 review and draft contracts, which is the same subset problem one
level down. Measured after the change: 273 rows, 184 implement, 85 review,
4 draft, 40 of them dispatchable.

### A-97 — 2026-09-05 — One landing oracle, and the landing a mint could not see
**Found comparing the two carriers.** With every contract imported (A-96),
`context` answered from the record over the same 273 tasks as the files —
and disagreed about the state of 204 of them. The cause was two oracles for
one question. The manager asked `landed_shas`, which reads the engine's own
`Torve-Task` trailer and nothing else (D-10.4); the projections ask
`shipped_ids`, which also counts a human's citation — `(T-0019)`, a
`torve/T-0006` merge (D-7.26). 111 tasks carry the trailer; 202 are shipped.
The other 91 were minted as queued, and 40 of them were dispatchable: a
manager pass would have handed a worker work somebody finished months ago,
which is the exact failure the landing import exists to prevent.

**Changed:** one oracle, `shipped_landings`, returning id to the commit
that shipped it in a single log pass. `shipped_ids` is now its key set, and
the manager reads it instead of the trailer alone. The question every
caller was really asking is whether the task is finished, and the manager
asking it more narrowly than the projections is not a stricter standard —
it is a different answer to the same question.

**Changed:** the landing import reaches a row that is already minted. A
first mint records the landing the repository proves; a row minted before
the partition could see that landing never got one, and re-minting does not
write one (D-49.2). The scan now records it for a row the record has *only
ever minted* — queued, no attempt, no landing. Once a task has run here the
board outranks the repository, so a human who requeued a landed task is not
sent back to `ready` by a scan that found an old commit.

Measured after the pass: 204 landed on both sides, and two rows left
disagreeing. T-0280 ran as a review here and the record only has it minted.
T-0096 escalated under v1 — the record never saw it, a commit cites the id,
so the record reads it shipped and RFC 0007 phase 4 reads `shipped` where
the files read `blocked`. That is A-86's wall in one row, and it is the
argument for RFC 0050 D-50.1 rather than a defect in this import.

*Archived 2026-09-09: superseded by 0055 (RFC 0053 D-53.8).*
