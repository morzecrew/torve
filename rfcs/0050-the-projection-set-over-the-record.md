---
id: "0050"
title: The projection set over the record
status: accepted
implementation: partial
depends_on: ["0044", "0049"]
informed_by: ["0022", "0032", "0040", "0047"]
supersedes: []
superseded_by: null
amended_by: ["A-95"]
owner: misery7100
description: >-
  The reports the engine answers planning questions with move onto the record, one at a time, each landing beside its file reader with a parity test rather than replacing it — because a reader moved today would read an empty log for every run the manager did not dispatch.
schema_version: 1
---

# RFC 0050 — The projection set over the record

- **Scope:** The read side. `why`, `status` and eventually `context` answered
  from the event log instead of from telemetry files, run-state files and a
  contract glob. Each record-backed reader lands **beside** its file reader,
  selected when the partition's log actually holds the run, with a parity
  test asserting the two agree where both can answer. It does **not** delete
  a file reader, does not change any report's shape, and does not touch what
  the engine records.
- **Related:** RFC 0044 A-86 (why a reader cannot simply move), RFC 0049
  (tasks as records — the last thing these joins were missing), RFC 0040
  (the why projection this re-answers), RFC 0022 (the specification-quality
  readings, deliberately last), RFC 0032 (the served surface that re-exposes
  all of it), `src/torve/application/projections.py`.
- **Origin:** The owner, 2026-09-05, on being shown the wall: *"Record-backed
  reader beside the file one."*

---

## 1. Summary

RFC 0049 put the last missing join in the record. Every fact `why`,
`status` and `context` read from files now also exists as an event — the
contract, the attempts, the gate outcomes, the landings, the escalations,
the divergences, and since RFC 0047 the decisions.

This moves the readers, carefully. Each report gains a record-backed
implementation that answers when the log holds the run and defers to the
file reader when it does not, plus a test asserting the two produce the same
envelope where both can answer. No file reader is deleted: a v1 run leaves
no log, and most runs are still v1.

## 2. Motivation

**The reports are the last thing that cannot leave the repository.** A
manager serving four partitions can dispatch, judge, land and escalate
without touching three of those repositories' filesystems, and then cannot
answer "why did T-0281 escalate" for any of them without one.

**Three surfaces re-expose one envelope.** `torve why`, the MCP tool and
`torve serve` all return `why_report` verbatim, which is exactly right and
means moving the reader moves all three at once.

**The joins are already written.** `_group_attempts`, `_why_events`,
`_why_reviews`, `_why_totals` and `_why_regime` are the shape of the answer;
what changes is where the rows come from. This is a source swap under a
stable envelope, not a redesign — which is what makes a parity test possible
and what makes the report's shape a non-goal.

**A-86's wall has not moved.** A reader pointed at the record today would
report `found: false` for every task a v1 `torve run` executed, which is the
worst available answer: not an error, not empty — *wrong*, and confidently.
That is why this lands beside rather than instead.

## 3. Current state

Verified against the tree at `09c5e96`:

- `why_report(root, task_id)` reads `_stream_rows(root)` (the telemetry
  JSONL), `feedback_records(root)` and the task's contract head. It already
  refuses to read run-state files, and it already returns a `found: false`
  envelope for an unknown id — both of which this preserves.
- `status_report(root)` reads run-state files under `.wt`.
- `context_report(root, rfc_dir)` is the corpus-wide planning view: tasks,
  proposals, findings, gate health, costs, harness populations, character
  calibration, document signals, the programme and the specification-quality
  block. It reads files for every one of them.
- `specquality.read_tasks` is a four-way filesystem join whose four legs all
  have recorded equivalents as of RFC 0049.
- `EventLog.history(subject_id)` returns one subject's events oldest-first
  in a single read, which is exactly the shape `why` needs.
- The board (`manager.project`) already *is* `status`, folded per partition.

## 4. Goals / Non-goals

**Goals**

- One report at a time, each with a record-backed reader and a parity test.
- No change to any envelope: the same keys, the same values, so the CLI, the
  MCP tool and the served endpoint are untouched.
- Selection that cannot silently answer from the wrong source.
- A file reader deleted only when the record demonstrably holds the run —
  which is the migration, not this document.

**Non-goals**

- **Not deleting a file reader.** §9 says what would change that.
- **Not a new report.** Anything the record makes newly answerable is
  someone else's document; this one moves what exists.
- **Not `context` in the first pass.** It is the largest and it joins the
  corpus; it goes last, on the pattern the first two establish.
- **Not the specification-quality readings.** RFC 0022's populations join
  landed-window semantics that deserve their own reading, and they are the
  least urgent.

## 5. Design

### 5.1 Selection is explicit, never guessed

```python
def why_report(root: Path, task_id: str, *, board: Board | None = None) -> dict[str, Any]: ...
```

A caller that has a log passes what the log gave it; a caller that does not,
does not. Selection is therefore a fact about the call site — the CLI knows
whether `--dsn` and `--partition` were given — rather than a probe that
guesses from an empty result.

The one automatic rule is the safe direction: **a record that does not hold
the task falls back to the files**, never the reverse. An empty log must
read as "ask the files", and a populated one must not be second-guessed by a
stale file.

### 5.2 The sources swap under a stable envelope

`why`'s five joins keep their signatures **and their code**. The row source
is what changes, and it changes by rendering rather than by re-implementing:
A-85 made the telemetry row a rendering of the event payload, so the record
is rendered into the rows those joins already read (`rows_from_events`).
Parity is then close to tautological — both sides are the same object
through the same `record_row` — and a difference is a defect in one
rendering rather than a disagreement between two readings.

The mapping the rendering performs:

| Envelope key | From files | From the record |
| --- | --- | --- |
| `rfc` | the contract head on disk | the minted contract (D-49.1) |
| `attempts` | telemetry rows for the task | `attempt.finished` and `gates.evaluated` payloads, which are the same record (A-85) |
| `events` | engine rows in the stream | `escalation.raised`, `task.released`, `divergence.recorded` |
| `reviews` | review rows in the stream | `review.recorded`, `blocker.raised` |
| `state` | derived from the rows | the board's own view |

The attempt row is the load-bearing one, and it is already solved: A-85 made
the telemetry row a *rendering* of the event payload, so the record-backed
reader is reading the same object the file reader parses back. That is why
this is a parity test rather than a reconciliation.

### 5.3 Parity is the acceptance

For a run the manager dispatched, both readers can answer, and the test
asserts they produce the same envelope key for key. That is the whole
verification strategy: a difference is a bug in the swap, because the
envelope is not being redesigned.

`test_manager_e2e.py` already drives one real task through the manager
against Postgres and reads the board back. It is the natural home for the
parity assertion, because it is the only place both carriers are populated
by the same run.

### 5.4 Order, and why `context` is last

`why` first: one subject, one read, five joins, three surfaces moved at
once. `status` second: the board already is it, so the work is reconciling
two vocabularies rather than writing a fold. `context` last: it joins the
corpus, the programme, the harness populations and the specification-quality
block, and every one of those is its own reading.

### Alternatives considered

- **Probe the record and fall back on empty.** No new parameter, no caller
  change — and it cannot tell "this task has no events" from "this log is
  not the one that has them", so it answers confidently from the wrong
  source exactly when a partition is misconfigured.
- **Move all three at once.** One diff, one review, one regression surface —
  across 1,780 lines of joins whose only shared property is that they read
  files. Three documents' worth of judgement in one commit.
- **Delete the file readers now.** The honest version of "replace", and it
  reports `found: false` for every v1 run. A wrong answer that looks like a
  correct one is worse than a missing feature.
- **A projection cursor and a stored view.** Faster reads, and it makes the
  report a thing that can be stale. These are on-demand folds and the whole
  point is that rebuilding is reading again.

## 6. Tests

- **Parity, on a real run**: the manager dispatches a task end to end, and
  both `why` readers produce the same envelope. Asserted key by key rather
  than as one dictionary comparison, so a failure names the key.
- **Fallback**: a task the log has never heard of reads from the files and
  answers as it does today.
- **The unknown task stays `found: false`** on both paths — a typo must not
  fake a taskless history.
- **The attempt block round-trips**: an `attempt.finished` payload folded
  into a why entry equals the entry built from the telemetry row rendered
  from the same payload, which is A-85 asserted from the reading end.
- Explicitly not tested: that the record is complete. It is not, that is the
  migration, and the fallback is what makes an incomplete record safe.

## 7. Docs

`pages/docs/architecture/distribution.md` item 4 loses its projections half
as each report moves; the item stays until run state and telemetry do. No
new page: the reports' own documentation is their envelope, which does not
change.

## 8. Out of scope

- **`context_report` and the specification-quality readings.** Phased here,
  built after the first two land — the pattern is what phase 1 and 2 exist
  to establish.
- **Deleting the telemetry stream.** It is a rendered carrier with readers
  outside this engine, and it survives the readers moving.
- **A projection cursor.** Named in RFC 0044 §5.7 and not needed while these
  are on-demand folds over one subject or one partition.

## 9. Risks

- **Two implementations of one report.** Real, and the reason for the parity
  test rather than an argument. It ends when the file reader is deleted,
  which is gated on the migration and named in §4.
- **Divergence by omission.** A key added to the envelope in one reader and
  not the other passes every existing test. Mitigated by the parity test
  comparing key sets, not only values.
- **The parity test needs Postgres and docker.** It is skipped without them,
  like `test_manager_e2e` already is — so a contributor without either gets
  a green suite that did not check the thing this document is about. Named
  rather than solved; the mock adapter covers the fold, and only the
  end-to-end run covers both carriers being written by one run.

## 10. Unresolved questions

- Whether `status` should keep its own report at all once the board exists,
  or whether `torve status` becomes `torve manager board` with a different
  renderer. Settled by writing it.
- What a report should say when the record holds *part* of a task's history —
  a run that started under v1 and continued under the manager. Today it
  falls back wholesale; merging the two carriers is a third behaviour nobody
  has asked for.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-50.1 | `LOCKED` | A record-backed reader lands beside its file reader, never replacing it, until the record demonstrably holds the runs being asked about | `src/torve/application/projections.py` | A v1 run keeps answering; the cost is two implementations and a parity test, which is cheaper than a report that is confidently wrong |
| D-50.2 | `LOCKED` | Selection is explicit at the call site; the only automatic rule is that a record without the task falls back to the files, never the reverse | `src/torve/application/projections.py` `src/torve/cli/why.py` | A misconfigured partition cannot make a report answer from the wrong source and look right doing it |
| D-50.3 | `ASSUMED` | No report's envelope changes: same keys, same values, so the CLI, the MCP tool and the served endpoint are untouched | `src/torve/application/projections.py` | The move is verifiable by parity rather than by review, which is the only practical check over joins this size |
| D-50.4 | `ASSUMED` | Parity is asserted on a real manager-dispatched run, key by key | `tests/test_manager_e2e.py` | The one place both carriers are populated by the same run; a failure names the key rather than the report |
| D-50.5 | `ASSUMED` | `why` moves first, `status` second, `context` and the specification-quality readings last | `src/torve/application/projections.py` `src/torve/application/specquality.py` | The smallest join establishes the pattern; the largest one does not get to invent it |
| D-50.6 | `OPEN` | What a report should say when the record holds part of a task's history. Settled by the first run that spans the migration | — | — |

## 12. Phasing

```yaml
- phase: 1
  title: why, over the record
  intent: >-
    `why_report` gains a record-backed source: one read of the task's own history folded into the same envelope it returns today — the minted contract for `rfc`, the attempt and gate payloads for `attempts`, the escalation and divergence records for `events`, the review records for `reviews`, and the board's own view for `state`. Selection is a parameter the caller passes, and a record that does not hold the task falls back to the files; the reverse never happens. The CLI grows the flags that make the choice explicit, and the MCP tool and the served endpoint are untouched because the envelope is not. Parity against the file reader is asserted on a real manager-dispatched run, key by key.
  scope:
    - "src/torve/application/projections.py"
    - "src/torve/cli/why.py"
    - "tests/test_why_record.py"
    - "tests/test_manager_e2e.py"
  acceptance:
    - "uv run pytest tests/test_why_record.py tests/test_context.py"
    - "uv run lint-imports --config pyproject.toml"
    - "uv run torve rfc check"
  depends_on: []
- phase: 2
  title: status, over the board
  intent: >-
    `status_report` gains a record-backed source over the board that already holds every fact it reports — state, attempts, holder, landing, escalation. The work is reconciling two vocabularies rather than writing a fold: the run-state file's states and the board's are the same enum reached by different paths, and where they disagree the disagreement is the finding. Same selection rule and same parity discipline as phase 1.
  scope:
    - "src/torve/application/projections.py"
    - "src/torve/cli/status.py"
    - "tests/test_why_record.py"
  acceptance:
    - "uv run pytest tests/test_why_record.py tests/test_reaper.py"
    - "uv run torve gates run"
    - "uv run torve rfc check"
  depends_on: [1]
- phase: 3
  title: context, over the record and the corpus
  intent: >-
    The planning view moves last and reads two records rather than one: the decision graph RFC 0047 imported for its corpus half, and the event log for its execution half. Its blocks land one at a time behind the same selection rule — tasks and programme first, since RFC 0049 made those a fold; costs and harness populations next, since the attempt payload already carries what they count; the specification-quality readings last, because their landed-window semantics are their own reading rather than a source swap.
  scope:
    - "src/torve/application/projections.py"
    - "src/torve/application/specquality.py"
    - "src/torve/cli/context.py"
    - "tests/test_context.py"
  acceptance:
    - "uv run pytest tests/test_context.py tests/test_specquality.py"
    - "uv run torve gates run"
    - "uv run torve rfc check"
  depends_on: [2]
```

---

## Amendments

### A-95 — 2026-09-05 — A run, over a board that also holds history
**Found executing phase 2.** §5.4 called `status` the easy one — "the board
already is it" — and the fold was indeed free. What was not free is the
population. The board carries every task a partition has ever minted, and
RFC 0049 D-49.1 mints landed history too: on this repository's first pass
the manager minted 184 contracts and recorded 184 landings from the trailer,
so a board-backed `status` that reported every non-queued row answered with
184 `ready` rows and buried the one run that was actually live.

The run-state files never had this problem because a state file exists only
where a run happened. The board's equivalent question is therefore not
"which rows are not queued" but "which rows did a run touch": an attempt is
recorded, or the engine is holding the task now. An escalation counts even
with no attempt behind it, because something is waiting on a person either
way. A `ready` row with no attempt is history the board imported, and it is
`torve board`'s to show, not this reader's.

That settles the first of §10's unresolved questions in the negative:
`status` keeps its own report, because the two answer different questions
over the same rows. The board is every contract this partition owns and
what became of it; `status` is what ran here. Their overlap is real but
partial, and collapsing them would cost the second question.

The second question is now measured rather than hypothetical. Against the
live log this repository reports one run from the record and three from the
files: the record's is the only task the manager dispatched, and the files
also hold a v1 escalation and a hand-run landing that no partition ever saw.
That is A-86's wall with a number on it, and it is why D-50.1 holds.

**Changed:** §5.4's account of `status` gains its population rule — a run is
an attempt recorded or a task the engine holds, plus any escalation — and
§10's first unresolved question is settled: `status` keeps its own report.

Phases 1 and 2 have landed. Phase 3 remains, and its first block —
`context`'s tasks and programme — is the one RFC 0049 already made a fold.
