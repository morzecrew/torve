---
id: "0039"
title: Durable traces and the burn profile
status: accepted
depends_on: ["0002", "0004"]
informed_by: ["0005", "0017", "0021", "0023"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Session traces moved from reap-swept worktree siblings into a
  retention-capped local store with relative references, plus a best-effort
  per-turn burn profile on the attempt row — where the tokens went becomes
  readable after the run is gone.
schema_version: 1
---

# RFC 0039 — Durable traces and the burn profile

- **Scope:** Two bounded changes to what survives an attempt: the trace
  file's home moves from the worktree's sibling (swept at reap) to
  `.torve/traces/` under the host root with a root-relative `trace_ref`
  and an age/size retention cap enforced at the reaper's existing sweep
  (`src/torve/base/naming.py`, `src/torve/application/reaper.py`,
  `src/torve/adapters/agent/harness.py`, a `traces:` block in
  `src/torve/config/runconfig.py`); and the harness adapter derives a
  best-effort `burn` sub-block — turns, tool calls, heaviest turns —
  from stream-formatted harness output when a profile emits one
  (`src/torve/adapters/agent/harness.py`,
  `src/torve/application/telemetry.py`). No prompt changes, no gate
  changes, no engine read of trace *content* for control flow.
  Deliberately not covered: making any profile emit a stream format
  (that is tier-command configuration, separated by `config_hash`),
  feeding traces to any agent (D-5.3 stands), and committing traces
  (they stay local artifacts, §5.4).
- **Related:** RFC 0004 §4 (the trace doctrine: "a trace is not gate
  evidence", `trace_ref`), RFC 0005 (D-5.3 — review input assembled
  without the author's trace), RFC 0021 (D-21.7 — the broker keeps no
  bodies; §5.4 records why the trace store may), RFC 0023 (standing
  maintenance, whose reap this rides);
  `src/torve/base/naming.py::trace_file`,
  `src/torve/application/reaper.py`,
  `src/torve/adapters/agent/harness.py::parse_metadata`.
- **Origin:** The 2026-09-01 execution-introspection gap analysis:
  T-0213's executor attempts burned 343–515K input tokens per attempt
  with totals as the only record, and the reaper unlinks the one
  artifact that could say where.

---

## 1. Summary

A trace outlives the workspace only until the reaper sweeps the run;
then the attempt's only record of what the agent actually did is gone,
and the `trace_ref` in telemetry dangles as an absolute path into a
directory that no longer holds it. This document moves traces into a
retention-capped store under `.torve/`, records the reference
root-relative so it stays resolvable, and — where a harness profile
emits a per-turn stream — folds an engine-derived burn profile into the
attempt row, so "why did this attempt cost 500K input tokens" is
answerable first from the row and then, when the row is not enough, from
a trace that still exists.

## 2. Motivation

- **Triage's shelf life is the reap.** `naming.trace_file` places
  traces beside the worktree "because triage outlives the workspace" —
  but `reaper.py` unlinks them at the terminal sweep, so triage
  outlives the workspace only until the run lands or is abandoned. The
  dogfood host currently holds 66 traces in a 526MB `.wt/`; every
  landed task before them kept nothing.
- **`trace_ref` is an absolute path.** The row citing
  `/home/.../GitLibrary/Morze/torve/.wt/T-0213.a1.trace.log` is
  machine-specific while it lives and dangling after the sweep; the
  telemetry stream is durable and portable, its one pointer into the
  filesystem is neither.
- **Totals are the only token record.** `parse_metadata`
  (`src/torve/adapters/agent/harness.py`) reads the last JSON object
  line of the harness output — the final envelope. A profile emitting a
  stream format carries per-turn usage and tool events through the same
  stdout, and the adapter discards everything above the last line.
  T-0213's four executor attempts each burned 343–515K input tokens and
  9–16M cache-read tokens; nothing recorded can say whether that was
  ten heavy turns or three hundred small ones.

## 3. Current state

Verified against the tree at drafting time:

- `trace_file(worktree, attempt)` returns
  `worktree.parent / f"{worktree.name}.a{attempt}.trace.log"`
  (`src/torve/base/naming.py`); the harness adapter writes
  `result.output` there verbatim and stamps the absolute path as
  `trace_ref` (`src/torve/adapters/agent/harness.py::run`).
- The reaper's terminal sweep globs `{task_id}.a*.trace.log` beside the
  state file and unlinks every match
  (`src/torve/application/reaper.py`).
- The trace's content is whatever the tier command's output format
  produced: the dsh reporter emits narration lines plus a final JSON
  envelope (T-0097's a1 trace is 8KB of narration); `claude -p
  --output-format json` emits the envelope alone. Nothing anywhere
  reads a trace except a human.
- The agent block's token counts are the envelope's four totals
  (T-0186), absent-stays-absent (D-4.6).
- The review lane composes its input without the author's trace by
  doctrine (D-5.3); no prompt anywhere embeds trace content.

## 4. Goals / Non-goals

**Goals**

- A red or expensive attempt's trace is still there when a human asks
  why, for a bounded and configurable window.
- `trace_ref` resolves from the stream alone, on the host that owns the
  store, for as long as the file exists — and says so plainly when it
  no longer does.
- Where the harness stream carries per-turn facts, the attempt row
  answers the first burn question without opening the trace.

**Non-goals**

- Engine reads of trace content for control flow — D-34.5's fence
  applies; the burn profile is derived at capture time by the adapter
  that already parses this output, and no selector reads it.
- Trace content entering any prompt — D-5.3 stands untouched.
- A trace *format* — the store keeps what the profile emitted; imposing
  a format would couple torve to one harness's stream schema (D-4.1).

## 5. Design

### 5.1 The store

`naming.trace_file` moves the home to
`<root>/.torve/traces/<task>.a<attempt>.trace.log`. The harness adapter
keeps writing at attempt end exactly as today; `trace_ref` is recorded
root-relative (`.torve/traces/T-0213.a1.trace.log`). The reaper's
terminal sweep stops unlinking traces; sweeping the state file is
unchanged.

### 5.2 Retention

A `traces:` block joins the runner configuration:

```yaml
traces:
  keep_days: 30
  max_mb: 512
```

Enforced where sweeping already lives — the reaper's pass deletes
oldest-first beyond either bound. Deletion is the only mutation; a
missing trace behind a recorded `trace_ref` is the defined outcome of
retention, and every renderer says "reaped" rather than erroring. Both
bounds are operator knobs because trace volume is a property of the
fleet, not the engine.

### 5.3 The burn profile

`parse_metadata` already scans the harness output; it gains a sibling
that scans *all* JSON lines instead of the last, and when they carry
per-turn usage or tool events, derives:

```json
"burn": {
  "turns": 41,
  "tool_calls": 87,
  "top_turns": [
    {"turn": 12, "output_tokens": 9120},
    {"turn": 33, "output_tokens": 7004}
  ]
}
```

The block rides the agent block beside the token totals, best-effort
under exactly D-4.6's regime: a profile whose output format carries no
stream stays visibly unprofiled — absent, never zeroed or inferred.
Which profiles emit a stream is tier-command configuration and therefore
already part of the regime `config_hash` separates; this document does
not change any profile.

### 5.4 Bodies, named

The broker keeps counts and never bodies (D-21.7). The trace store
keeps bodies. These do not conflict: D-21.7 governs the egress
middleman, which must stay incapable of becoming a shadow archive of
provider traffic; the trace is the operator's own local artifact with
the same standing as the worktree it narrates. What the doctrine does
demand is that the store stay local — traces are never committed and
never leave the host by any torve mechanism. Whether the directory is
gitignored is the operator's call, as with the corpus (rfc-writer
doctrine); the docs say plainly that committing traces publishes model
output wholesale.

### Alternatives considered

- **Move traces at reap instead of writing them home** — rejected: two
  homes means every reader handles both, and a crash between write and
  move loses the file; one home from the first byte is strictly
  simpler.
- **Keep traces only for red attempts** — rejected: the expensive-green
  attempt is exactly the "why did this burn so much" case, and verdicts
  (RFC 0038) are not known to be the only selector a human wants;
  retention bounds the cost either way.
- **A structured trace schema of torve's own** — rejected: transcoding
  every harness's stream into a house format is a standing maintenance
  burden that D-4.1's harness-agnosticism exists to avoid; the burn
  profile extracts the few facts with cross-harness meaning and leaves
  the rest verbatim.

## 6. Tests

Naming: the new path and relative `trace_ref`. Reaper: terminal sweep
leaves traces; the retention pass deletes oldest-first past `keep_days`
and past `max_mb`, and touches nothing within bounds. Harness: the burn
scanner over a fixture stream (turns, tool calls, top turns), over an
envelope-only output (absent block), and over garbage lines (absent
block, no error); trace still written verbatim in all three. Not
tested: any assertion about real harness stream schemas beyond the
fixtures — the block is best-effort by grade.

## 7. Docs

RFC 0004 §4's trace doctrine paragraph gains the store location,
relative reference, retention semantics, and the never-committed
warning worded as §5.4 words it. The configuration docs gain the
`traces:` block with its defaults.

## 8. Out of scope

- Profiles switching to stream output formats — a tier-command edit
  under RFC 0027's measured-evolution discipline, adoptable one profile
  at a time once this lands.
- Rendering burn profiles and trace pointers per task — RFC 0040.
- Compression or archival of the store — returns if retention bounds
  prove too blunt for a fleet host.
- Scrubbing trace content — traces hold model output that never left
  the sandbox's own view; the `never_send` withholding already keeps
  named secrets out of that view before the agent runs. A scrubber
  returns only with evidence of a leak class it would catch.

## 9. Risks

- **The store grows on fleet hosts.** Bounded by both knobs; the
  reaper's pass is the single enforcement point, and `max_mb` is the
  hard stop — accepted.
- **Someone commits the store.** Mitigated by the docs' plain wording
  and the operator-owned gitignore decision; torve itself never stages
  the directory.
- **Burn profiles mislead across harnesses** (one harness's "turn" is
  another's "step"). Mitigated: the block records what the stream said
  under the profile the regime hash already names; cross-regime
  comparison of burn shapes carries the same quasi-experiment caveat
  cost comparison already does.

## 10. Unresolved questions

- The retention defaults (30 days / 512MB are drafting guesses) — the
  owner settles them against observed dogfood volume before the phase
  lands.
- Whether review-attempt traces (the reviewer's own session, RFC 0005)
  follow the same retention or a shorter one — implementation decides
  and logs; nothing in the design distinguishes them today.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-39.1 | `LOCKED` | Traces live under `<root>/.torve/traces/` from the first byte, referenced root-relative; the reaper's terminal sweep never deletes them — only the retention pass does | `src/torve/base/naming.py` `src/torve/application/reaper.py` `src/torve/adapters/agent/harness.py` | Triage actually outlives the workspace; every future reader may assume one home and a resolvable relative ref or an honest "reaped" |
| D-39.2 | `LOCKED` | The trace store is local: torve never commits, uploads or transmits a trace; trace content never enters a prompt (D-5.3) and never drives control flow | `src/torve/application/reaper.py` `src/torve/adapters/agent/harness.py` | D-21.7's no-bodies doctrine stays about egress; a mechanism that ships traces off-host is a new document with a trust story |
| D-39.3 | `ASSUMED` | Retention is `traces.keep_days` and `traces.max_mb` in runner configuration, enforced oldest-first at the reaper's existing pass; defaults 30 and 512 pending the owner's read of dogfood volume | `src/torve/config/runconfig.py` `src/torve/application/reaper.py` | — |
| D-39.4 | `ASSUMED` | The burn profile is derived by the harness adapter from all JSON lines of the output it already holds — turns, tool calls, top turns by output tokens — riding the agent block under D-4.6's absent-stays-absent regime; no stream, no block | `src/torve/adapters/agent/harness.py` `src/torve/application/telemetry.py` | — |
| D-39.5 | `ASSUMED` | Torve imposes no trace format: the store keeps profile output verbatim, and the burn scanner extracts only facts with cross-harness meaning | `src/torve/adapters/agent/harness.py` | — |

## 12. Phasing

Phase 2 shares the harness adapter with phase 1 and therefore waits on
it rather than running beside it.

```yaml
- phase: 1
  title: the durable store
  intent: >-
    Traces move home to .torve/traces/ with a root-relative trace_ref
    (D-39.1): naming.trace_file returns the new path, the harness
    adapter records the relative reference, the reaper's terminal
    sweep stops unlinking traces, and the retention pass enforces
    traces.keep_days / traces.max_mb oldest-first (D-39.3) from the
    new configuration block. Tests pin the path, the relative ref, the
    sweep's new restraint, and both retention bounds.
  scope:
    - src/torve/base/naming.py
    - src/torve/adapters/agent/harness.py
    - src/torve/application/reaper.py
    - src/torve/config/runconfig.py
    - tests/test_reaper.py
    - tests/test_tiering.py
    - tests/test_layout.py
  acceptance:
    - uv run pytest tests/test_reaper.py tests/test_tiering.py tests/test_layout.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
- phase: 2
  title: the burn profile
  intent: >-
    A sibling of parse_metadata scans every JSON line of the harness
    output and derives the burn block — turns, tool calls, top turns
    by output tokens — when per-turn facts are present, absent
    otherwise, never zeroed (D-39.4, D-39.5); the runner carries it
    into the agent block beside the token totals. Fixture streams
    cover a per-turn stream, an envelope-only output, and garbage
    lines.
  scope:
    - src/torve/adapters/agent/harness.py
    - src/torve/application/runner.py
    - src/torve/application/telemetry.py
    - tests/test_tiering.py
    - tests/test_runner.py
  acceptance:
    - uv run pytest tests/test_tiering.py tests/test_runner.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [1]
```
