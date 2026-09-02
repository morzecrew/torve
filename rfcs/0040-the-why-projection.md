---
id: "0040"
title: The why projection
status: draft
depends_on: ["0007", "0032", "0038"]
informed_by: ["0008", "0034", "0037", "0039"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  One on-demand per-task projection — torve why — joining attempt rows,
  verdicts, convictions, reviews, escalations and costs into a chronological
  timeline, rendered by CLI, MCP and serve from one reader.
schema_version: 1
---

# RFC 0040 — The why projection

- **Scope:** One new projection function beside the context and status
  reports (`src/torve/application/projections.py`) that assembles a
  single task's execution history from the telemetry stream, and three
  thin renderers of its one envelope: a `torve why <task-id>` CLI
  command (`src/torve/cli/why.py`, registered in `src/torve/cli/main.py`),
  a `why(task_id)` MCP tool beside `context` and `show`
  (`src/torve/cli/mcp.py`), and a serve endpoint re-exposing the
  envelope verbatim (`src/torve/cli/serve.py`). Everything read already
  exists or lands with RFC 0038; this document adds no record, no
  stream, no store, and no write of any kind. Deliberately not covered:
  browser rendering beyond serve's verbatim JSON (D-32.1's re-exposure
  is the whole web surface), any anomaly detector or threshold (§8),
  and any read of trace *content* or run-state files (D-40.2).
- **Related:** RFC 0007 §4 (the context projection this sits beside;
  D-7.11, D-A.12), RFC 0032 §5.2 (D-32.1 — one reader, all renderers),
  RFC 0038 (the attempt number and verdict this orders by), RFC 0039
  (the trace refs this displays), RFC 0008 (the tracker projection,
  whose attempt bodies this deliberately does not replace);
  `src/torve/application/projections.py` (`status_report`,
  `context_report`, `_costs`, `QUASI_EXPERIMENT_CAVEAT`).
- **Origin:** The 2026-09-01 execution-introspection gap analysis:
  reconstructing why T-0213 needed six attempts took a dozen hand-built
  jq joins over four record kinds plus a state file that the next
  dispatch overwrites.

---

## 1. Summary

Every fact needed to answer "why did this task fail, cost this much,
take this long" is already on the durable stream — spread across
gate-run rows, red-agent rows, engine events, review records and the
costs projection, joined today by hand. This document adds the join as
a projection: `torve why T-0213` renders one chronological timeline —
per attempt: verdict, the tier that actually ran, gate convictions,
tokens, cost, wall clock, trace ref; between attempts: escalations,
blocked and oversize dispatches, review findings; at the bottom: totals
and the task's cost against its regime's other attempts. Computed on
demand from the stream, stored nowhere, rendered identically by CLI,
MCP and serve.

## 2. Motivation

- **The join exists only as labor.** The stream holds five record kinds
  keyed three ways (`task_id`, `task`, review `target`); the state
  file's transition history is overwritten per dispatch (RFC 0037 §3)
  and swept at reap. Reconstructing T-0213 — six attempts, two
  compliance convictions, one 1200-second timeout, one review pass,
  $1.6 — took a dozen ad-hoc queries, each re-derived from memory of
  the record shapes.
- **The costs section answers "what", never "why".** `_costs` lists
  attempts newest-first across all tasks; per-gate health aggregates
  across all tasks; escalations show live state only. No surface groups
  the stream by task and orders it by attempt — the one shape a human
  triaging a specific task actually wants.
- **Every consumer already exists.** The operator asks in the terminal,
  the planning session asks over MCP (RFC 0007's projection doctrine),
  and the browser asks serve; D-32.1 already settled that these read
  one envelope. Only the envelope is missing.

## 3. Current state

Verified against the tree at drafting time:

- `projections.py` holds the pattern this copies: `status_report` and
  `context_report` compute from files the engine already writes, on
  demand, stored nowhere (D-A.12), with `render_markdown` beside them;
  `QUASI_EXPERIMENT_CAVEAT` already words the cross-regime comparison
  warning this document reuses.
- The MCP server exposes exactly two read-only tools, `context(section)`
  and `show(identifier)` (`src/torve/cli/mcp.py`); serve renders the
  status envelope the CLI renders (D-32.1).
- Attempt rows carry tier-actually-ran (D-27.11), token counts
  (T-0186), and — with RFC 0038 — attempt numbers and verdicts;
  engine events carry escalations (0038), blocked/oversize dispatches
  and lane outcomes (D-6.7); review records carry findings per target;
  the feedback stream carries human minutes per task.
- Rows older than RFC 0038 lack `attempt` and `verdict`; T-0186-era
  rows lack token counts; the earliest rows lack the agent block —
  the projection must render partial history honestly.

## 4. Goals / Non-goals

**Goals**

- "Why did T-XXXX end where it did" is one command, one MCP call, one
  URL — the same envelope.
- The timeline is complete for post-0038 history and honest about
  older rows ("pre-verdict record" beats a guess).
- The cost question gets its comparator: task totals beside the same
  regime's per-attempt distribution, caveated as the quasi-experiment
  it is.

**Non-goals**

- Not a replacement for the tracker projection's attempt bodies (RFC
  0008) — the tracker narrates outward to a team surface; this answers
  an operator's pointed question inward.
- Not judgement — the projection emits data and the human reads it
  (RFC 0007's doctrine); no severity ranking, no advice.
- Not an anomaly detector — the comparator displays; wiring
  `COST_ANOMALY` to it is actuation and a separate document (§8).

## 5. Design

### 5.1 The reader

`why_report(root, task_id) -> dict[str, Any]` in `projections.py`,
reading the telemetry stream, the feedback stream and the task's
contract head — never run-state files (overwritten and swept), never
trace content (model output; the ref is displayed, not read):

```json
{
  "schema_version": 1,
  "task": "T-0213",
  "rfc": "rfcs/0034-...md",
  "state": "ready",
  "attempts": [
    {
      "attempt": 1,
      "at": "2026-09-01T17:40:43Z",
      "verdict": "gates_red",
      "tier": "executor", "model": "deepseek-v4-flash",
      "convictions": ["decisions-reported", "self-audit"],
      "input_tokens": 392421, "output_tokens": 74099,
      "cost_usd": 0.348, "wall_time_s": 897.3,
      "trace_ref": ".torve/traces/T-0213.a1.trace.log",
      "trace_present": false
    }
  ],
  "events": [
    {"at": "...", "event": "oversize_dispatch", "reasons": ["..."]},
    {"at": "...", "event": "escalation", "reason": "poison_ceiling", "detail": "..."}
  ],
  "reviews": [
    {"at": "...", "verdict_findings": 2, "blockers": 0}
  ],
  "totals": {"attempts": 6, "cost_usd": 1.62, "input_tokens": 2130000,
             "output_tokens": 414000, "wall_time_s": 5261.0,
             "human_minutes": null},
  "regime": {"config_hash": "ff0e5e331a5c",
             "attempt_cost_median_usd": 0.35,
             "attempt_cost_p90_usd": 1.05,
             "caveat": "<QUASI_EXPERIMENT_CAVEAT>"}
}
```

Attempts are grouped by the 0038 `attempt` stamp; rows without one
(pre-0038 history) are grouped by timestamp order and marked
`"attempt": null, "pre_verdict": true` — rendered as what they are,
never retrofitted. Events and reviews interleave chronologically in the
rendered form; the envelope keeps them separate so renderers choose.
The regime block compares against the same `config_hash`'s attempt
rows, carrying the existing caveat verbatim — a comparator, not a
verdict. An unknown task id returns an envelope with `"found": false`
and the CLI exits 3 (configuration problem, D-11.4's family), because
a typo should not read as a taskless history.

### 5.2 The renderers

- **CLI:** `torve why <task-id>` in `src/torve/cli/why.py`, following
  the presentation contract (RFC 0018): markdown timeline on a TTY,
  `--format json` emits the envelope. Exit 0 whatever the history says
  — a red history read successfully is a successful read (the survey's
  doctrine).
- **MCP:** a third read-only tool `why(task_id)` beside `context` and
  `show`, returning the envelope verbatim — a planning session
  triaging its own escalations reads the same facts the operator does.
- **Serve:** one endpoint re-exposing the envelope verbatim per
  D-32.1; browser presentation beyond JSON is out of scope.

### Alternatives considered

- **A `task:` section inside `context()`** — rejected: context is the
  whole-department report a planning session loads once; a per-task
  drill-down parameterizes it into a different tool wearing the same
  name, and the MCP surface is cheaper to grow by one honest tool.
- **Reading `RunState` history for richer transitions** — rejected:
  overwritten per dispatch and swept at reap, so any projection
  leaning on it is complete only for the currently-live run; the
  stream is the one source that is always as complete as it will ever
  be (D-40.2).
- **Ranking or flagging outliers in the envelope** — rejected: the
  projection emits data, judgement stays with the reader (RFC 0007);
  a flag is a threshold, a threshold is a detector, and that document
  should own its calibration.

## 6. Tests

Projection: a fixture stream with mixed record kinds pins the grouping
by attempt stamp, the pre-0038 fallback grouping and marking, the
chronological event interleave, totals arithmetic, the regime
comparator against same-hash rows only, `trace_present` against a
present and an absent file, and the `found: false` envelope. CLI:
content-only assertions per D-18.1, the json format emitting the
envelope unchanged, exit 3 on unknown id, exit 0 on a red history.
MCP and serve: the tool and endpoint return the projection's envelope
byte-identical (the D-32.1 parity assertion the status pair already
has).

## 7. Docs

The CLI contract's command table gains `torve why`; the MCP server
docstring gains the third tool. The regime block's documentation
carries the caveat wording verbatim — the comparator must not be
described as a benchmark.

## 8. Out of scope

- A `COST_ANOMALY` detector wired to the regime comparator — the
  reason exists unraised in `src/torve/domain/states.py`; a detector
  is actuation with its own calibration story, and this projection is
  the surface it would calibrate against.
- Browser rendering of the timeline — returns with the serve surface's
  own roadmap, on the verbatim envelope.
- Cross-task and per-RFC rollups ("why is phase 2 expensive") — the
  programme view's territory (D-7.11); a per-task reader lands first.
- Reading 0037 `state.yaml` snapshots into the timeline — display of
  agent-authored state is 0037's own triage surface; joining the two
  waits until both exist.

## 9. Risks

- **The stream grows and the reader is O(stream) per call.** Accepted
  at current volume (the dogfood stream is hundreds of rows); D-2.4
  names the exit — a query that hurts is the demand that justifies a
  store, and this projection is exactly the query that would surface
  it.
- **Pre-0038 history renders thin and someone reads it as data loss.**
  Mitigated by explicit `pre_verdict` marking and the docs saying the
  keys' birthdays.
- **The regime comparator gets read as a benchmark.** Mitigated the
  way the costs section already handles it: the caveat rides the
  envelope itself, not just the docs.

## 10. Unresolved questions

- Whether the rendered CLI timeline shows engine events inline between
  attempts or as a trailing section — implementation lays both out and
  the owner picks on sight; the envelope is unaffected.
- Whether `why` should accept a bare RFC id and list its tasks'
  one-line summaries as a navigation aid — deferred until the per-task
  form has been used in anger.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-40.1 | `LOCKED` | One reader, every renderer: `why_report` computes the envelope and CLI, MCP and serve re-expose it verbatim (D-32.1's doctrine applied to the per-task question) | `src/torve/application/projections.py` `src/torve/cli/why.py` `src/torve/cli/mcp.py` `src/torve/cli/serve.py` | The three surfaces can never disagree; a renderer wanting different facts changes the projection, in one place, for all of them |
| D-40.2 | `LOCKED` | The projection reads durable streams only — telemetry, feedback, the contract — never run-state files or trace content; computed on demand, stored nowhere (D-A.12) | `src/torve/application/projections.py` | The answer is equally complete for a live run, a landed task and a reaped one; a richer answer requires making the fact durable first (0038/0039's route), never reaching into ephemera |
| D-40.3 | `ASSUMED` | Attempts group by the 0038 `attempt` stamp; unstamped history groups by timestamp order and is marked `pre_verdict: true` — rendered as partial, never retrofitted | `src/torve/application/projections.py` | — |
| D-40.4 | `ASSUMED` | The regime block compares the task against same-`config_hash` attempt rows only and carries `QUASI_EXPERIMENT_CAVEAT` in the envelope itself — a comparator, never a verdict or threshold | `src/torve/application/projections.py` | — |
| D-40.5 | `ASSUMED` | The envelope emits data without judgement: no severity ranking, no advice, no anomaly flags; a detector is a separate document that consumes this surface | `src/torve/application/projections.py` | — |
| D-40.6 | `ASSUMED` | Unknown task id: `found: false` envelope, CLI exit 3; a readable red history exits 0 | `src/torve/cli/why.py` | — |

## 12. Phasing

One phase: the reader and its three thin renderers are one unit —
splitting them would mint a projection nobody can call.

```yaml
- phase: 1
  title: the why projection
  intent: >-
    why_report in projections.py assembles the per-task envelope from
    the telemetry stream, feedback stream and contract head (D-40.1,
    D-40.2): attempts grouped by the 0038 stamp with the pre_verdict
    fallback (D-40.3), chronological events and reviews, totals, and
    the same-regime cost comparator carrying the quasi-experiment
    caveat in-envelope (D-40.4, D-40.5). Three renderers re-expose it
    verbatim: torve why <task-id> per the presentation contract with
    json format and the D-40.6 exit codes, the why(task_id) MCP tool
    beside context and show, and a serve endpoint. Tests pin grouping,
    fallback marking, totals, comparator scoping, trace_present, the
    found:false envelope, and byte-identical parity across the three
    surfaces.
  scope:
    - src/torve/application/projections.py
    - src/torve/cli/why.py
    - src/torve/cli/main.py
    - src/torve/cli/mcp.py
    - src/torve/cli/serve.py
    - tests/test_context.py
    - tests/test_mcp.py
    - tests/test_serve.py
    - tests/test_cli.py
  acceptance:
    - uv run pytest tests/test_context.py tests/test_mcp.py tests/test_serve.py tests/test_cli.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
```
