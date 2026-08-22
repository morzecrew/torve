---
id: "0018"
title: CLI presentation
status: accepted
implementation: partial
depends_on: ["0011"]
informed_by: ["0007", "0016"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  One visual vocabulary for the human side of every verb — components, colour
  semantics, formats and live status — now that the hand-run commands are
  known. The machine contracts of RFC 0011 are untouched.
schema_version: 1
---

# RFC 0018 — CLI presentation

- **Scope:** How human-facing output looks: shared components, colour
  semantics, verdict marks, output formats, live status for long waits, and
  what tests may assert about any of it. Excludes everything RFC 0011 already
  settled — output contracts, exit codes, stdout/stderr separation and
  non-TTY behaviour are load-bearing and stay exactly as published.
- **Inherits:** D-11.2, D-11.5, D-11.6, D-11.7 from RFC 0011; D-15.6 from
  RFC 0015.

---

## 1. The deferral is discharged

D-11.8 deferred presentation polish "until after Phase 3, when it is visible
which commands are run by hand and which only by CI." That condition is met:
usage shows `torve context`, `torve status`, `torve gates run`, `torve plan`,
`torve rfc check` and `torve shadow` run by hand, while `torve run` and CI
consume JSON. The evidence is concrete — the first live `torve context` was a
wall of unstructured bullets where five of its six sections are tables
wearing hyphens, and a 28-second acceptance gate renders nothing at all while
it runs.

The order of work RFC 0011 §5 fixed still holds: wording pays more than
styling, and the three gate-output rules (name the thing, expected beside
actual, say what happens now) are content rules this document does not touch.
This is the layer under them: the same content, presented once, consistently.

## 2. What is already settled and stays settled

`--format json` emits one document, byte-shape versioned, the only surface a
machine may parse (D-11.2, D-11.3). Diagnostics go to stderr in both formats
(D-11.6). `--plain` is implied by `CI`, a non-TTY stdout, or `--format json`,
and `NO_COLOR` is honoured (D-11.5). No interactive prompts, ever (D-11.7).
Every rule in this document operates strictly inside that frame: presentation
changes the pixels of the human rendering and nothing else.

## 3. One component vocabulary

Every verb renders through a small set of components in `torve.cli.console`,
never through bespoke per-verb string assembly:

- **Header line** — `torve <verb> · <subject> · <regime>`: what ran, on
  what, under which `config_hash` where one exists. Already the shape of
  `gates run`; becomes the shape of everything.
- **Table** — the default for enumerable rows: gate results, tasks by state,
  the programme view, costs, stale tasks, minted contracts. Right-sized to
  the terminal; a table that cannot fit truncates rows with an explicit
  `… N more` line rather than wrapping into noise — the full data is always
  in the JSON.
- **Verdict marks** — the published vocabulary stays: `✓` pass, `✗` fail,
  `∅` skipped, `≈` flaky, `⤳` bypassed, `!` error. Marks are stable across
  releases for the same reason exit codes are: people learn them.
- **Failure detail** — a failing row expands into an indented block directly
  under it (the wording rules of 0011 §5 apply to its content), never
  interleaved with other rows.
- **Closing line** — outcome and what happens now: `exit N`, `dry run —
  nothing written`, `parked at gated`.

Presentation lives in `cli/` only (D-15.6): application modules return data,
and a Rich renderable never crosses that boundary — the moment a `Table`
appears in `application/`, the JSON and the human view can drift.

## 4. Colour and emphasis

Colour semantics are fixed once: green for pass/ready, red for a blocking
failure or escalation, yellow for warnings and shadow-state findings, dim
for skipped rows and provenance detail, cyan for identifiers (`T-nnnn`,
`D-x.y`, RFC numbers, hashes). Two rules make it safe:

- **Colour is never the only carrier.** Every distinction colour draws is
  also drawn by a mark, a word or a position — `--plain` and `NO_COLOR`
  strip styling, never information.
- **No inline markup in strings.** Styling is applied through renderables
  and style parameters, not `[red]...[/red]` text — the current consoles run
  `markup=False` precisely so that data containing brackets cannot inject
  styling, and that stays.

## 5. Formats

`--format` grows one member, and only where it means something:

```
--format text        default; Rich-rendered for a human at a terminal
--format json        unchanged; the machine contract (D-11.2)
--format markdown    document-producing commands only: pasteable markdown
```

`torve context` is the motivating case: its §4 purpose is a document a
planning session consumes, so the pasteable markdown rendering is not a
degraded mode but a first-class format — while the default `text` rendering
becomes tables and sections for reading in place. A command whose output is
not a document (`gates run`, `status`) does not grow the option: an
unavailable format is better than a meaningless one.

## 6. Live status for long waits

A hand-run `gates run` sits silent for tens of seconds inside the acceptance
gate. One narrow lift of 0011 §7's deferral: a **single-line, TTY-only
status** ("running acceptance… 12s") during steps longer than a moment,
erased when the step completes. Absent under `--plain`, absent in CI, absent
under `--format json` — it is presentation, so it obeys every rule
presentation obeys. Multi-line progress, spinner zoos, live tables and TUIs
remain deferred; interactive prompts remain forbidden (D-11.7).

## 7. What tests may assert

The human rendering is not a contract — that is the whole reason `--format
json` exists from first implementation (0011 §2). Tests therefore pin the
JSON byte-shape and assert *content* of human output (a task id present, a
verdict mark, a section heading), never layout, spacing or colour. A test
that breaks because a column widened was testing the wrong surface.

## 8. Non-goals

Colour themes, configurable styles, shell completion, pagers, TUIs, live
refresh of `status`, progress bars beyond §6's one line — still deferred,
still deliberately. The presentation layer is finished when every verb
speaks the same visual language, not when it speaks a prettier one.

## 9. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-18.1 | `LOCKED` | The JSON byte-shape is the only machine contract; tests assert human output by content, never layout | `tests/**` | A layout-pinned test turns every visual improvement into a false regression |
| D-18.2 | `LOCKED` | Presentation lives in `cli/` only; application returns data and no Rich renderable crosses the boundary | `src/torve/cli/**` `src/torve/application/**` | The JSON and the human view must not be able to drift |
| D-18.3 | `ASSUMED` | One component vocabulary in `torve.cli.console` — header, table, verdict marks, failure detail, closing line — used by every verb | `src/torve/cli/**` | Bespoke per-verb styling is how ten commands grow ten dialects |
| D-18.4 | `LOCKED` | Colour is never the only carrier of a distinction; styling is applied via renderables, never inline markup in data strings | `src/torve/cli/**` | `--plain` strips styling, never information; bracketed data must not inject styling |
| D-18.5 | `ASSUMED` | Verdict marks (`✓ ✗ ∅ ≈ ⤳ !`) are stable vocabulary, kept across releases | `src/torve/cli/**` | People learn them the way they learn exit codes |
| D-18.6 | `ASSUMED` | `--format markdown` exists only on document-producing commands; `torve context` keeps its pasteable rendering there while `text` becomes rich | `src/torve/cli/context.py` | An unavailable format beats a meaningless one |
| D-18.7 | `ASSUMED` | One single-line TTY-only live status for long steps; absent under `--plain`, CI and JSON; everything larger stays deferred | `src/torve/cli/**` | A silent 28-second wait reads as a hang |
| D-18.8 | `ASSUMED` | Tables truncate to the terminal with an explicit `… N more` line; the full data is always in the JSON | `src/torve/cli/**` | Wrapped tables are noise wearing structure |

## 10. Phasing

```yaml
- phase: 1
  title: console-components
  intent: >-
    One component vocabulary lands in torve.cli.console — header line, table,
    verdict marks, failure detail, closing line — and the three highest-read
    surfaces (gates run, status, context) render through it; torve context
    gains --format markdown carrying today's pasteable rendering while its
    default becomes tables and sections. Every JSON byte-shape is unchanged,
    pinned by test.
  scope: ["src/torve/cli/**", "tests/test_cli.py", "tests/test_context.py"]
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
- phase: 2
  title: verb-sweep-and-live-status
  intent: >-
    Every remaining verb — plan, shadow, rfc, migrate, doctor, feedback,
    reap, cancel — renders through the shared components, and steps longer
    than a moment show the single-line TTY-only live status, absent under
    --plain, CI and JSON. No interactive prompt appears anywhere, and the
    verdict-mark vocabulary is byte-identical to phase 1's.
  scope: ["src/torve/cli/plan.py", "src/torve/cli/shadow.py",
          "src/torve/cli/rfc.py", "src/torve/cli/migrate.py",
          "src/torve/cli/doctor.py", "src/torve/cli/feedback.py",
          "src/torve/cli/status.py", "tests/test_plan.py",
          "tests/test_shadow.py", "tests/test_rfc_check.py"]
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
  depends_on: [1]
```

## 11. Exit criteria

- `torve context` and `torve gates run` render through the shared
  components; the context wall-of-bullets is tables and sections.
- Every command's `--format json` output is byte-identical before and after,
  held by test.
- Phase 1 of this document is minted by `torve plan 0018` with no manual
  editing of the result — the first document planned by the machinery it
  improves.
