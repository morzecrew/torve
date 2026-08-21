---
id: "0011"
title: CLI contract
status: accepted
depends_on: []
informed_by: ["0002", "0003"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Output contract, exit codes and non-TTY behaviour — the three CLI surfaces
  consumed by CI, telemetry and scripts — plus where UX effort actually pays.
schema_version: 1
---

# RFC 0011 — CLI contract

What must be decided now because it is expensive to change later, and what can wait.

Companion to RFC 0002 (`torve gates`) and RFC 0003 (`torve run`).

## 1. Two decisions, very different urgency

**The library is settled: Typer plus Rich.** Typer sits on Click, the dependency is light, and migrating off `argparse` later costs more than adopting it now. Nothing here is worth deliberating.

**The UX can wait — except for three things.** Output contracts, exit codes and non-TTY behaviour are consumed by CI, telemetry and scripts. Retrofitting them means breaking whatever already depends on the old shape. Everything else — colour, tables, progress, live views — is presentation over the same data and can arrive whenever.

The rest of this document is those three things, then a note on where UX effort actually pays.

## 2. Output contract

Every command that produces a result supports `--format json`. Not eventually — from its first implementation.

The reason is not aesthetics. Telemetry ingests attempt records, CI steps branch on gate outcomes, and the tracker projection (RFC 0008) renders from the same data. If those arrive a month after the human output, something will already be parsing the human output, and it will break the first time a line is reworded.

```
--format text    default; for a person at a terminal
--format json    machine-readable; a single object, schema-versioned
```

Rules:

- **Human output and machine output never mix.** `--format json` emits exactly one JSON document on stdout and nothing else.
- **Diagnostics always go to stderr**, in both formats. Progress, warnings, and the reason a gate was skipped are not results.
- **JSON is schema-versioned**, same `schema_version` discipline as everything else in the corpus. A consumer that cannot read version N should say so rather than guess.
- **`--format json` never carries `miscusi`.** Per D-27, the pleasantry is for humans; parsers must never depend on it.
- **JSON output is the same shape as the persisted record** wherever one exists. `torve run --format json` emits an `Attempt`. Do not invent a parallel CLI-only schema — that is a second contract to keep in sync, and it will fall out of sync.

```json
{
  "schema_version": 1,
  "task": "T-0142",
  "outcome": "escalated",
  "escalation_reason": "scope_violation",
  "attempt": 2,
  "gates": [
    {"name": "scope", "status": "failed", "exit_code": 1, "duration_ms": 340},
    {"name": "typecheck", "status": "skipped"}
  ],
  "cost_usd": 0.31,
  "trace_ref": "…"
}
```

## 3. Exit codes

`torve run` and `torve gates run` are called from CI and from scripts. Zero-or-not is not enough resolution, and once a code is published it cannot be redefined without breaking callers.

| Code | Meaning | Typical caller response |
| --- | --- | --- |
| 0 | Success — work complete, gates green | proceed |
| 1 | Gate failed — the work is not acceptable | fail the step, show output |
| 2 | Escalated — needs a human decision | fail the step, notify |
| 3 | Configuration error — bad manifest, unknown gate, missing contract | fail loudly; this is a repository bug |
| 4 | Infrastructure failure — sandbox, store, network | retry may be appropriate |
| 5 | Budget or ceiling exhausted | do not retry |

Two properties worth stating:

- **This is the escalation vocabulary from charter §4, projected onto exit codes.** Do not invent a second taxonomy; a new exit code requires a new escalation reason, and vice versa.
- **3 and 4 must be distinguishable**, because one means "someone wrote the manifest wrong" and the other means "try again in a minute". Collapsing them into a generic failure is the single most common way a CLI becomes annoying to operate.

Codes above 5 stay unassigned. Reserve rather than reuse.

## 4. Non-TTY behaviour

Spinners and live tables render as escape-sequence noise in CI logs. Rich detects a non-TTY, but do not rely on autodetection alone — it is wrong often enough, and the failure is ugly and permanent in the log.

- `--plain` disables colour, spinners and any live redraw.
- `--plain` is implied when `CI` is set, when stdout is not a TTY, or when `--format json` is used.
- `NO_COLOR` is honoured.

This matters beyond CI: agent CLIs invoked inside a sandbox are frequently not attached to a terminal either.

## 5. Where UX effort actually pays

**Not in the CLI.** `torve run` is invoked once per task, usually by a machine. A gate's failure message is read by a human at every single escalation.

That makes the gate output the highest-leverage surface in the system, and Rich contributes almost nothing to it — the work is wording.

```
scope         ✗  packages/core/types.ts is outside allow
                 allowed: packages/api/**, tests/api/**
                 3 other files were within scope

typecheck     ∅  skipped (blocking gate failed)

miscusi. task parked at gated, nothing merged.
→ escalation: scope_violation   attempt 2/5   $0.31
```

versus `gate failed with exit code 1`. The difference is thirty seconds against ten minutes, every incident, forever.

Three rules for gate output:

1. **Name the specific thing.** Which file, which command, which decision — never "a check failed".
2. **Show what was expected next to what happened.** The allow-list beside the offending path costs one line and removes a lookup.
3. **Say what happens now.** Parked, escalated, or retryable — so the reader knows whether to act.

Apply the same to `torve doctor`: each check names what it looked for, what it found, and what to do about it.

## 6. Command surface for now

```
torve run <task-id> [--agent fake] [--format json] [--plain]
torve gates run [--base REF] [--only NAMES] [--format json]
torve gates check                      # sabotage suite
torve reap
torve status [--format json]
torve doctor
```

That is the whole of RFC 0002 and RFC 0003. `plan`, `context` and anything tracker-facing arrive with their own RFCs.

Two conventions to hold from the start, because they are cheap now and awkward later:

- **Verb-noun, consistently.** `gates run`, `gates check` — not `run-gates` alongside `check_sabotage`. Subcommand groups scale; flat verb soup does not.
- **`--dry-run` on anything that mutates.** `plan` already specifies it as the default. Extend the flag name, not a new one per command.

## 7. Deferred, deliberately

Until after Phase 3, when it is visible which commands are run by hand and which only by CI:

progress bars · spinners · colour themes · `status` with live refresh · a TUI · shell completion · pager integration · interactive prompts of any kind

**Interactive prompts deserve a specific caution.** A command that asks a question hangs forever in CI and inside a sandbox. If a prompt is ever added, it must fail with code 3 when stdin is not a TTY rather than wait — and given how many callers here are machines, the better answer is usually a flag instead.

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-11.1 | `ASSUMED` | Typer plus Rich | `src/torve/cli/**` `pyproject.toml` | Low cost to adopt, higher to migrate later |
| D-11.2 | `LOCKED` | `--format json` on every result-producing command from its first implementation | `src/torve/cli/**` | Retrofitting breaks whatever already parses human output |
| D-11.3 | `LOCKED` | JSON output matches the persisted record shape; no CLI-only schema | `src/torve/cli/**` `src/torve/domain/**` | A second contract falls out of sync |
| D-11.4 | `LOCKED` | Exit codes 0–5 as tabled; a new code requires a new escalation reason | `src/torve/cli/**` `src/torve/domain/states.py` | Published codes cannot be redefined |
| D-11.5 | `ASSUMED` | `--plain` implied by `CI`, non-TTY, or `--format json`; `NO_COLOR` honoured | `src/torve/cli/**` | Autodetection alone is not reliable enough |
| D-11.6 | `LOCKED` | Results on stdout, diagnostics on stderr, never mixed | `src/torve/cli/**` | The basis of every machine consumer |
| D-11.7 | `LOCKED` | No interactive prompts; if ever added, fail with code 3 on non-TTY stdin | `src/torve/cli/**` | A prompt hangs forever in CI and in a sandbox |
| D-11.8 | `ASSUMED` | Presentation polish deferred until after Phase 3 | — | Revisit when hand-run commands are known |
