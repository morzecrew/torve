---
id: "0038"
title: The attempt verdict
status: accepted
depends_on: ["0002", "0004", "0006"]
informed_by: ["0021", "0026", "0034", "0037"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Every attempt ends in exactly one durable telemetry row stamped with its
  attempt number and an engine-derived verdict, and every escalation lands an
  engine event — how a run ended stops dying with the state file.
schema_version: 1
---

# RFC 0038 — The attempt verdict

- **Scope:** Three additive changes to the one telemetry stream (D-6.7): an
  `attempt` number stamped into the agent block of every record an attempt
  appends, a closed-vocabulary `verdict` field derived by the engine from
  facts it already holds at attempt end, and one `kind: engine,
  event: escalation` record appended from the single place escalations are
  set (`RunState.escalate`). Touches
  `src/torve/application/telemetry.py`, `src/torve/application/runner.py`,
  `src/torve/application/runstate.py` and their tests. No new stream, no new
  store, no schema_version bump — every key is additive and absent on old
  rows (D-4.6's regime). Deliberately not covered: any change to what the
  agent self-reports, any router input (D-34.5 stands — the verdict is
  derived from the same recorded facts routing already reads, it adds no new
  input), and the per-turn burn breakdown (RFC 0039's subject).
- **Related:** RFC 0002 §8 (telemetry from day one, D-2.4), RFC 0004 §6
  (the agent block, self-report doctrine D-4.6), RFC 0006 §5b (engine
  events, D-6.7), RFC 0021 §5.5 (broker refusal mid-run, D-21.6),
  RFC 0037 §3 (the state-file overwrite finding this leans on);
  `src/torve/application/runner.py`, `src/torve/application/telemetry.py`,
  `src/torve/domain/states.py`.
- **Origin:** The 2026-09-01 execution-introspection gap analysis over this
  repository's own ledger, T-0213's timed-out attempt as the type specimen.

---

## 1. Summary

The stream records what an attempt produced; it barely records how an
attempt *ended*. This document makes the ending a first-class recorded
fact: every attempt appends exactly one row, that row carries its attempt
number and a one-word engine-derived verdict from a closed vocabulary, and
every escalation — today a field in a state file that the next dispatch
overwrites and the reaper eventually deletes — lands one durable engine
event with its reason and detail. Nothing new is measured; facts the
runner already holds in local variables stop evaporating.

## 2. Motivation

Four verified facts from this repository's ledger:

- **A timed-out attempt's row is nearly mute.** T-0213's attempt of
  2026-09-01T19:23:40Z carries `exit_code: null`, `results: []`,
  `config_hash: null`, `timed_out: true` — the flag exists (added when
  four ~$4 first attempts were found missing from cost-and-iterations,
  see the comment at the red-path append in
  `src/torve/application/runner.py`), but it exists only on the
  red-agent record shape, and "how did this attempt end" has no single
  field on any row. Of 352 gate-run rows in the current stream, 12
  carry `timed_out`; the other 340 answer the question only by joint
  inference over `exit_code`, `results` and absence.
- **No row knows its attempt number.** The agent block stamps tier,
  model, image digest and trace_ref — but not which attempt it was.
  Joining a row to its trace file (`<task>.aN.trace.log`) or to RFC
  0026's continuation chain is timestamp archaeology.
- **Escalations are not durable.** All 22 `state.escalate` call sites
  (runner, reaper, planner, intake, lane, `cli/run`) write only the
  state file. The current stream holds 85 engine events and not one
  escalation record, while the 0034 audit counts three poison ceilings
  across two models. `run_task` constructs a fresh `RunState` per
  dispatch and its first save overwrites the prior history (RFC 0037
  §3), and the reaper deletes the file at terminal sweep — the reason a
  task needed a human is unrecoverable precisely after the human is
  done with it.
- **An attempt can end with no row at all.** A broker budget refusal
  escalates mid-attempt (D-21.6) and stops the loop before the gates
  run; the red-agent record fires only on `timed_out or exit_code != 0`,
  so an agent that exits 0 into a refused escalation leaves spend with
  no record — the same class of hole the red-path record was added to
  close.

## 3. Current state

Verified against the tree at drafting time:

- `build_record` (`src/torve/application/telemetry.py`) shapes the
  gate-run row; the agent block (`agent_meta` in
  `run_routing`/`_run_task_async`, `src/torve/application/runner.py`)
  is restamped per attempt and carries no attempt number and no
  verdict.
- The red-agent record is a second, inline shape in the attempt hook
  (`src/torve/application/runner.py`, the `timed_out or exit_code != 0`
  branch) carrying `gates_run: false` and `timed_out` — the only rows
  that name their ending.
- `RunState.escalate` (`src/torve/application/runstate.py`) sets a
  field and nothing else; `engine_event`
  (`src/torve/application/telemetry.py`) already exists as the durable
  path for exactly this kind of fact (D-6.7) and already resolves the
  stream location manifest-or-default.
- The escalation vocabulary is closed and projected onto exit codes
  (`src/torve/domain/states.py`, D-11.4) — the reason taxonomy this
  document reuses rather than invents.
- Retry-rung selection reads the attempt's recorded gate outcomes
  (D-34.5); the verdict adds a summary of facts already recorded, never
  a new routing input.

## 4. Goals / Non-goals

**Goals**

- "How did attempt N of T-XXXX end" is one jq expression over one
  stream, for every ending: green, gates red, timeout, agent error,
  broker refusal, halt, infrastructure failure.
- Every attempt's row joins deterministically to its trace file and its
  continuation chain by attempt number.
- Escalation reasons and details survive re-dispatch and reap.
- RFC 0040's per-task timeline can be built from the stream alone.

**Non-goals**

- No change to routing — D-34.5 stands; the verdict is derived from the
  same recorded facts, and no selector reads it.
- No mirroring of the full `history[]` transition list into the stream —
  the verdict and the escalation event carry the triage-bearing facts;
  wholesale mirroring is noise until a reader demonstrates the need
  (named in §8).
- No new stream or store — D-2.4 stands: JSONL until a query demands
  otherwise, and this document's queries do not.

## 5. Design

### 5.1 The attempt number

The agent block gains `attempt: <int>` — stamped where the block is
already restamped per attempt, so it rides every record the attempt
appends: the gate-run row, the red-agent row, and the shadow row's
attempt records alike. This is the join key the trace filename
(`<task>.a<attempt>.trace.log`) has carried alone since RFC 0004 §4.

### 5.2 The verdict

Every attempt ends in **exactly one** row, and that row carries a
top-level `verdict` from a closed vocabulary, derived by the engine from
values already in hand at attempt end:

| verdict | derived from |
| --- | --- |
| `green` | agent exited 0, gate report exit 0 |
| `gates_red` | agent exited 0, gate report nonzero |
| `agent_timeout` | `result.timed_out` |
| `agent_error` | agent exited nonzero |
| `broker_refused` | the attempt hook escalated on the broker's budget refusal (D-21.6) |
| `halted` | the halted divergence entry (RFC 0001 §4) |
| `gate_infrastructure` | the gates hook raised (`GATE_INFRASTRUCTURE_FAILURE`) |

Where the loop today ends an attempt without appending anything (the
broker-refused and halted endings, the gates-hook exception), the
attempt appends a row of the red-agent shape — `results: []`,
`gates_run: false`, the agent block with whatever the adapter reported —
so the one-attempt-one-row invariant holds for every path out of the
attempt hook. The existing inline red-agent append is subsumed by the
same code path rather than duplicated beside it.

The verdict is engine-authored — derived from exec results, gate report
exit codes and escalation state, never from model output — so it does
not extend the self-reported regime D-4.6 governs; it summarizes what
the engine itself observed.

### 5.3 The escalation event

`RunState.escalate` appends one durable record as it sets the field:

```json
{"schema_version": 1, "kind": "engine", "event": "escalation",
 "at": "...", "task": "T-0213", "reason": "poison_ceiling",
 "detail": "...", "run_id": "6fdbe1a8"}
```

One call site instead of twenty-two: `escalate` derives the repository
root structurally from its own state-file location (the worktree-parent
walk `_write_regime_preimage` already performs), and the append is
best-effort in the same sense — an unwritable stream must not turn an
escalation into a crash, because the state-file write is the one that
gates correctness. `reason` is the existing `EscalationReason` value
verbatim; no new taxonomy.

### Alternatives considered

- **Verdict inside the agent block** — rejected: the block is the
  adapter's self-reported territory (D-4.6); the verdict is the
  engine's observation and sits beside `exit_code` at the top level,
  where the gate report's facts already live.
- **Appending the escalation event at each call site** — rejected:
  twenty-two sites is twenty-one chances to miss one; the invariant
  belongs where the field is set. Execution may depart (D-38.4's grade)
  if a state without a resolvable root turns up.
- **A schema_version bump** — rejected: every key is additive, readers
  already treat absence as unreported, and a bump would force every
  reader to branch for no informational gain.

## 6. Tests

Runner: one test per verdict value asserting the row's verdict and the
one-attempt-one-row invariant (a fake agent driven to each ending), the
attempt number on every appended record, and the broker-refused ending
now producing a row where today it produces none. Runstate: `escalate`
appends the event with reason/detail/run_id, is best-effort on an
unwritable stream, and still sets the field first. Replay: a test
asserting the verdict is derivable from the row's other recorded fields
— the vocabulary adds convenience, never information the row does not
already imply (keeps D-34.5's determinism argument honest).

## 7. Docs

RFC 0004 §6's record documentation gains the `attempt` and `verdict`
keys and the escalation event shape. No migration notes: old rows simply
lack the keys, and every reader named in this document treats absence as
pre-0038.

## 8. Out of scope

- Mirroring `history[]` transitions into the stream — the escalation
  event carries the triage-bearing endpoint; full mirroring returns if
  RFC 0040's timeline demonstrably starves without it.
- The per-task join and rendering of these facts — RFC 0040.
- Trace retention — RFC 0039; this document only makes the join key
  durable.
- A `COST_ANOMALY` detector — the reason exists unraised in
  `src/torve/domain/states.py`; wiring a detector is actuation, its own
  future document, and it will want 0040's rollup first.

## 9. Risks

- **Vocabulary too coarse.** An ending not in the table lands as the
  nearest value plus the row's existing fields; the vocabulary grows by
  amendment when a reader needs a distinction, and the replay test keeps
  each value derivable — accepted.
- **Double rows from the subsumed inline append.** The red-agent path
  moves rather than duplicates; the one-attempt-one-row test is the
  tripwire.
- **Escalation events from non-run states** (planner minting, reaper
  sweeps of orphans) may lack a run_id — the key is optional, the event
  still lands; accepted.

## 10. Unresolved questions

- Whether the reaper's `lease_expired` sweep of a run it did not own
  should also stamp a verdict row for the orphaned attempt, or only the
  escalation event — implementation decides against the reaper's actual
  knowledge at sweep time, and logs.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-38.1 | `LOCKED` | Every attempt ends in exactly one telemetry row, whatever the ending: the endings that today append nothing (broker refusal, halt, gates-hook failure) append the red-agent shape | `src/torve/application/runner.py` | Spend and endings are never invisible again; any new way out of the attempt hook must append its row or redden the invariant test |
| D-38.2 | `LOCKED` | The verdict is engine-derived from facts the runner already recorded or observed — exec results, gate report, escalation state — never from model output, and no router reads it (D-34.5 untouched) | `src/torve/application/runner.py` `src/torve/application/telemetry.py` | The verdict can never smuggle a model signal into routing; a smarter summary is a new document |
| D-38.3 | `ASSUMED` | The vocabulary is `green`, `gates_red`, `agent_timeout`, `agent_error`, `broker_refused`, `halted`, `gate_infrastructure`, carried top-level beside `exit_code`; it grows by amendment | `src/torve/application/telemetry.py` | — |
| D-38.4 | `ASSUMED` | The agent block stamps `attempt`, restamped where tier/adapter/model already are, on every record the attempt appends | `src/torve/application/runner.py` | — |
| D-38.5 | `ASSUMED` | `RunState.escalate` appends one `kind: engine, event: escalation` record (task, reason verbatim from `EscalationReason`, detail, run_id when known), best-effort, root derived structurally from the state-file location — one call site, not twenty-two | `src/torve/application/runstate.py` | — |
| D-38.6 | `ASSUMED` | All keys are additive; no schema_version bump; readers treat absence as pre-0038 | `src/torve/application/telemetry.py` | — |

## 12. Phasing

One phase, one unit — the three changes share the runner's attempt loop
and separating them would manufacture a dependency.

```yaml
- phase: 1
  title: the attempt verdict
  intent: >-
    The agent block stamps its attempt number (D-38.4); every path out
    of the attempt hook appends exactly one row (D-38.1) carrying an
    engine-derived verdict from the closed vocabulary (D-38.2, D-38.3),
    subsuming the inline red-agent append; RunState.escalate appends
    the durable escalation engine event best-effort from its single
    call site (D-38.5). All keys additive (D-38.6). Tests: one per
    verdict value, the one-attempt-one-row invariant, the
    broker-refused row that today does not exist, the escalate append
    and its best-effort failure mode, and the replay test deriving each
    verdict from the row's other fields.
  scope:
    - src/torve/application/runner.py
    - src/torve/application/runstate.py
    - src/torve/application/telemetry.py
    - tests/test_runner.py
    - tests/test_run_loop.py
    - tests/test_reaper.py
  acceptance:
    - uv run pytest tests/test_runner.py tests/test_run_loop.py tests/test_reaper.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
```
