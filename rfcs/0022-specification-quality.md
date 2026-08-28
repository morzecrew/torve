---
id: "0022"
title: Specification quality as a measured quantity
kind: design
status: accepted
implementation: partial
depends_on: ["0004", "0007"]
informed_by: ["0001", "0002", "0009", "0016", "0020"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  Reading the records the engine already writes to measure the corpus that
  produced them: per-decision and per-document attribution, grade calibration,
  and the decoration check — reported to a human who then writes an amendment,
  never applied by the engine.
schema_version: 1
---

# RFC 0022 — Specification quality as a measured quantity

- **Scope:** How the facts already recorded about execution are read back as
  evidence about the *specifications* that produced it. Covers the attribution
  join (telemetry to task to document), the grade-calibration report, the
  decoration check over declared paths, the document-level signals, and
  `torve rfc health` as the surface. Changes nothing about what is recorded,
  invokes no model, and never edits a decision table — the output is a report
  a human reads before writing an amendment. Excludes gate health (RFC 0002
  §7.6), skill evals (RFC 0009 §5) and harness comparison (RFC 0004 §5), all
  of which already exist and are the reason the gap here is visible.
- **Related:** [`0004`](0004-agents-tiering.md) §6 · [`0007`](0007-planner-context.md) §4 ·
  [`0016`](0016-corpus-conventions.md) · `src/torve/application/projections.py` ·
  `src/torve/application/telemetry.py` · `src/torve/config/rfc_parse.py`
- **Inherits:** D-2, D-25, D-27 from RFC 0001; D-2.10 from RFC 0002; D-4.6
  from RFC 0004; D-7.12, D-7.24, D-7.27 from RFC 0007; D-A.4, D-A.11 from
  RFC 0016.

---

## 1. Summary

The engine measures its gates, its skills and its harnesses. It has never
measured the corpus, which is the thing the charter names as the
differentiator. Every input already exists in the records — decisions are
denormalised into every telemetry row by a rule written in RFC 0004 §6
precisely so this would be possible later; the escalation enum was kept
separable so that `underspecified` and `stale_inheritance` would be distinct
populations; the log carries `drift_count`, actions and cited decision ids;
`torve feedback` carries `human_minutes` and `rework_after_review`. What is
missing is a reader and a doctrine for what its numbers mean. This document
supplies both, as a subcommand beside `torve rfc check`.

## 2. Motivation

A grade is a human's one-shot judgement about how expensive a decision would
be to reverse. It is made once, at authoring time, under no pressure, by
someone guessing. It is then copied into every task that inherits the
decision, and it dictates whether an executor halts, departs or decides.
Nothing has ever checked one.

The evidence to check them has been accumulating since the first task:

- The `ASSUMED` row that is departed from by most of the tasks that inherit it
  was `OPEN` in disguise, and every executor that departed from it paid the
  cost of discovering that separately.
- The `LOCKED` row that halts often is either a real boundary or an
  over-grade, and the two are distinguishable by what the human did next: an
  amendment citing the decision means the document was wrong; a re-queue means
  the code was.
- The `OPEN` row that every executor resolves the same way is a decision the
  author could have made, and the corpus is paying for the same deliberation
  repeatedly.
- A row **never cited by any log**, in tasks that touched the paths it
  declares, is decoration. It costs tokens in every contract that inherits it
  and constrains nothing.

None of those is a hunch that could be argued about; each is a query. And
RFC 0020 §5.2 already committed to a rule set that "is expected to grow from
escalation evidence, each rule citing the escalation that produced it" — with
no mechanism anywhere that produces that evidence in aggregate.

The absence is also asymmetric in a way worth naming. RFC 0002 §7.6 measures
whether a gate earns its minutes. RFC 0009 §5 retires a skill that does not
earn its tokens. RFC 0004 §5 replays a task to compare harnesses. The one
artefact with no measurement attached is the one the charter says is the whole
point (D-25).

## 3. Current state

- Telemetry is one JSONL file per root, `.torve/telemetry.jsonl`, host-local
  and gitignored. `build_record` writes `task_id`, `decisions` (full dumps,
  denormalised), `results`, `exit_code`, `config_hash`, `agent`,
  `bypass_count_by_gate` and `flaky_count_by_command`. Engine events ride the
  same stream as `kind: engine` records.
- Feedback is a second stream, `.torve/feedback.jsonl`, keyed by task id,
  append-only, latest wins at analysis time.
- Task logs are YAML under `.torve/tasks/<id>/log.yaml`, in git, carrying
  `entries` with `decision`, `grade`, `kind`, `action`, `claim`, `evidence`
  and optionally `proposal`, plus a top-level `drift_count`.
- `src/torve/config/rfc_parse.py` already parses every decision table in the
  corpus into rows with identifier, grade and paths — the corpus half of the
  join exists and is under test as part of `torve rfc check`.
- `src/torve/application/projections.py` already walks the logs: `_proposals`
  collects entries carrying a `proposal` and marks them `possibly_landed` by
  corpus citation (D-7.24). That is the only existing read of a log for
  anything but gating, and it covers one field of one entry kind.
- RFC 0004 §6 staged storage: JSONL, then DuckDB "when a query needs window
  functions or joins", then a port, then ClickHouse. Nothing has moved past
  stage 1, and nothing reads stage 1 for this purpose.

## 4. Goals / Non-goals

**Goals**

- Attribute every escalation, drift entry, finding and rework fact back to the
  decision and the document that governed the task.
- Make a mis-graded decision visible as a population, not as a memory.
- Make a decoration row visible, and distinguish "governs nothing" from
  "declares the wrong paths".
- Give RFC 0020 §5.2's rule growth the evidence it was promised.

**Non-goals**

- **A corpus quality score.** The charter §8a refuses success thresholds for
  an internal tool; a single number is a threshold with extra steps, and it
  would be defended rather than read.
- **Automatic amendments.** The report is input to a human writing an
  amendment. An engine that edits decision tables is an engine that decides
  what work exists (D-2).
- **New recorded fields.** Everything needed is already recorded, and the
  reason it is recorded is that denormalisation was made mandatory from the
  first record. Adding fields now would restart the comparable history.
- **Ranking documents against each other.** The populations differ in size,
  age and subject; a league table would be read as a verdict on authors.

## 5. Design

### 5.1 The join, and what it is keyed by

Three sources, joined on two keys:

```text
telemetry.jsonl   task_id ─┐
feedback.jsonl    task_id ─┼─→ task record ──rfc──→ corpus decision table
tasks/*/log.yaml  task    ─┘        decisions[].id ─┘
```

The task contract supplies `rfc` and the inherited `decisions` with their
grades as copied at mint time; the log supplies what happened to each; the
corpus supplies the row as it stands now. **The grade compared is the one on
the contract, never the one in the table today** — a decision regraded last
week must not retroactively rewrite the judgement of a task that ran under the
old grade. This is the charter's "grade is copied at write time, never
resolved at read time", applied to the reader instead of the writer.

Tasks with no `rfc` (RFC 0020's adopted intake work, hand-minted contracts)
join at the decision level only and are reported as their own population.

### 5.2 Decision-level report

Per decision identifier, over the tasks that inherited it:

| Column | Meaning |
| --- | --- |
| inherited | tasks carrying the row |
| touched | of those, tasks whose diff intersected the row's declared paths |
| cited | log entries naming the identifier |
| by action | halted / departed / decided counts |
| outcome | escalations whose reason is in the human-decision family, split |
| after | whether an amendment citing this identifier landed within the window |

Four readings, each falsifiable and each mapping to one act:

- `ASSUMED`, departed in a majority of tasks that touched its paths →
  propose `OPEN`.
- `OPEN`, decided identically by every executor → propose promoting the
  decision into the row and stop paying for the deliberation.
- `LOCKED`, halted repeatedly, **and** followed by amendments → an
  over-grade or a wrong boundary. Halted repeatedly and followed by re-queues
  → a real boundary doing its job, which is a healthy row, not a finding.
- `touched > 0` and `cited == 0` → decoration or a silence-check hole. The
  discriminator is already in the data: if the tasks touched the declared
  paths and wrote nothing, the silence check should have fired, so either the
  row's paths are wrong or the gate is not reaching it. Both are defects, and
  they are different defects.

That last reading is the one only this corpus can produce, because it needs
the Paths column (D-32) and the silence check to exist at all.

### 5.3 Document-level report

Per document: tasks minted, attempts to green (median), escalations by
reason, findings of `kind: spec-drift` raised against its tasks, `drift_count`
sum, `human_minutes` median, rework rate. Plus the two reasons that exist
specifically to indict a document rather than code — `underspecified` and
`stale_inheritance` — reported as their own line, since RFC 0001's amendments
A-21 and A-22 separated them precisely so this line could be read.

RFC 0004 §6a's warning is inherited whole and printed with the report, not
paraphrased: this is a quasi-experiment across different tasks under different
conditions, and it supports direction, never magnitude.

### 5.4 The surface

`torve rfc health [NNNN]` — a subcommand beside `check`, `index` and `graph`,
because it reads the same corpus with the same parser and a new top-level verb
would be a second front door to one subject. Text for a human under RFC 0018's
vocabulary, JSON for machines under RFC 0011's contract. With a number, one
document in full; without, the corpus summary plus the rows that met a reading
in §5.2.

The same data becomes one section of the `torve context` projection, which
means the existing MCP surface exposes it to a planning session without a new
tool — the section is the consumer that justifies computing it at all.

### 5.5 Storage stays at stage 1

RFC 0004 §6's move-on condition for DuckDB is "a query needs window functions
or joins". These queries are joins over three files of a few thousand rows
between them, expressible as a dictionary keyed by task id built in one pass.
A plain reader is smaller than the optional extra it would replace, needs no
dependency, and keeps `torve rfc health` runnable in the same environment as
`torve rfc check` — which matters, because the report is worth having in CI
the day someone wants it there. The staging table is unchanged and the
condition for moving is unchanged; this document records that the condition is
not yet met and that the reader must be written so that moving is a change of
reader, as §6 promised.

### Alternatives considered

- **A single corpus health score.** Its trade is legibility for
  defensibility: one number is easy to watch and impossible to act on, and
  under charter §8a it would become a target nobody chose. Populations with a
  named reading each are harder to glance at and are the only form that ends
  in an amendment.
- **DuckDB now, per RFC 0004 §6 stage 2.** Its trade is a dependency and an
  extra for queries that do not need it at this volume. Reconsidered the first
  time a query in this document is genuinely a window function.
- **Recording new fields to make the analysis easier.** Its trade is that the
  existing history becomes incomparable at the moment of the change; the whole
  reason denormalisation was made mandatory from the first record was to avoid
  paying that.
- **Feeding the report to a model that proposes amendments.** Refused under
  D-2, and separately unnecessary: the four readings in §5.2 each name their
  own act.

## 6. Tests

A seeded corpus fixture — the shape RFC 0005 already uses for review
calibration — carrying documents, contracts, logs and telemetry with each of
§5.2's four readings planted, plus one row that looks like decoration but is
not (declared paths never touched by any task, which is silence about nothing
and must not be reported). The report is asserted as a value, not as rendered
text. One property is worth its own case: a decision regraded in the corpus
after a task ran must not change that task's contribution to the population.

## 7. Docs

`pages/` gains a short reader's guide: what each reading means and what act it
implies, because a number without its act is the score this document refuses.
The `flag-dont-flip` skill needs one sentence, not more — an executor writing a
`resolved` close-out is already the behaviour the decoration check depends on,
and telling the executor it is being measured would change what it writes.

## 8. Out of scope

- **Acting on the report.** Amendments stay hand-written by the charter owner.
  Reopened by nothing: this is D-2.
- **Cross-repository aggregation.** One root, one corpus. The fleet view is
  RFC 0024's problem and needs this report to exist first.
- **Retention and log deletion.** D-A.15 requires promotion before deletion,
  and this report is the closest thing to a promotion ledger the corpus will
  have — but wiring deletion to it is a separate decision about what may be
  destroyed, and it should not ride in on a reporting change.
- **Author-level anything.** The corpus has one owner and the population is
  documents, not people. Naming this keeps a later multi-author corpus from
  assuming otherwise.

## 9. Risks

- **The report is read as a verdict on the author.** With one author it is a
  verdict on past judgement, which is the point; with two it becomes
  performance review by query. Mitigation is the §8 exclusion, written now
  while it costs nothing.
- **Small numbers, confident readings.** A "majority departed" over three
  tasks is noise. The report prints denominators beside every ratio and
  suppresses a reading below a floor, and the floor is itself printed.
- **Measuring what is easy instead of what matters.** Attempts-to-green is
  cheap to compute and confounded by task size. It stays in the document-level
  table with that caveat attached, and no reading in §5.2 depends on it.
- **The decoration check fires on the silence gate's own gaps.** That is a
  feature — both causes are defects — but a reader who assumes it always means
  "delete the row" will delete a row whose paths were merely wrong. The
  report names both causes on the same line.

## 10. Unresolved questions

- The window for "an amendment citing this identifier landed after the halt".
  Corpus history is short and amendments are batched; the honest answer is
  probably "any time after", refined once there are enough pairs to see the
  distribution.
- Whether a task's contribution should be weighted by whether it landed. An
  abandoned task's departures are evidence about the document too, but of a
  different kind. Left open; execution reports both and the shape of the data
  decides.
- Whether the `torve context` section should include the readings or only the
  raw populations. A planning session that receives conclusions is one step
  from a planning session that receives instructions.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-22.1 | `LOCKED` | The report never edits a decision table, proposes no text, and invokes no model; its output is evidence for a human writing an amendment | `src/torve/application/specquality.py` `src/torve/cli/rfc.py` | An engine that regrades its own decisions is an engine deciding what work exists (D-2) |
| D-22.2 | `LOCKED` | Grades are compared as copied onto the contract at mint time, never as they stand in the table today | `src/torve/application/specquality.py` | A reader that resolves grades at read time rewrites the past, which is the defect the log format exists to prevent |
| D-22.3 | `ASSUMED` | No single corpus score is computed or displayed; the output is populations with a named reading and a printed denominator | `src/torve/application/specquality.py` `src/torve/cli/rfc.py` | Charter §8a refuses thresholds for an internal tool, and a scalar becomes one by being watched |
| D-22.4 | `ASSUMED` | A decision inherited by tasks that touched its declared paths but cited by no log entry is reported as decoration-or-paths-defect, naming both causes | `src/torve/application/specquality.py` | The reading is only possible because Paths (D-32) and the silence check exist; conflating the two causes would delete correct rows |
| D-22.5 | `ASSUMED` | Storage stays at RFC 0004 §6 stage 1: a plain reader over JSONL and YAML, no new dependency, written so that moving to stage 2 is a change of reader | `src/torve/application/specquality.py` | §6's own move-on condition — window functions or joins — is not met at this volume, and an unused extra is a dependency with no evidence behind it |
| D-22.6 | `ASSUMED` | The surface is `torve rfc health`, a subcommand of the existing corpus verb, plus one section of the `torve context` projection | `src/torve/cli/rfc.py` `src/torve/application/projections.py` | One subject, one front door; the context section is the consumer that justifies computing the report |
| D-22.7 | `LOCKED` | RFC 0004 §6a's quasi-experiment caveat is printed with the report, not paraphrased in documentation | `src/torve/cli/rfc.py` | The first attractive number becomes a promise to someone unless its limits arrive attached to it |
| D-22.8 | `ASSUMED` | Readings are suppressed below a printed floor of observations; ratios always print their denominator | `src/torve/application/specquality.py` | A majority over three tasks is noise wearing a percentage |
| D-22.9 | `ASSUMED` | Tasks without an `rfc` join at the decision level and are reported as their own population, never merged into a document's numbers | `src/torve/application/specquality.py` | Adopted intake work and hand-minted contracts have no document to indict, and mixing them would indict one anyway |
| D-22.10 | `OPEN` | Whether an abandoned task's entries weigh the same as a landed task's; execution reports both and logs which the data supports | `src/torve/application/specquality.py` | Departures on work that never landed are evidence of a different kind, and guessing the answer here would bake it in before anyone has seen the distribution |

## Phasing

```yaml
- phase: 1
  title: attribution-and-decision-report
  intent: |
    The reader and the join: telemetry, feedback and task logs indexed by
    task id, joined to contracts for the grade as minted and to the corpus
    parser for the row as it stands. Per-decision populations — inherited,
    touched, cited, action counts, escalation outcomes — with denominators
    printed and readings suppressed below the floor. torve rfc health as a
    subcommand beside check and index, text and JSON. No corpus score, no
    proposed text, no model.
  scope:
    - "src/torve/application/specquality.py"
    - "src/torve/cli/rfc.py"
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
  title: document-signals-and-context-section
  intent: |
    The document-level half and the planning-session consumer: per-document
    attempts, escalation reasons with underspecified and stale_inheritance
    on their own line, spec-drift findings, drift_count, human_minutes and
    rework rate, each carrying RFC 0004 §6a's caveat printed with it. The
    same data joins the torve context projection as one section, which the
    existing MCP surface then exposes to a planning session without a new
    tool.
  scope:
    - "src/torve/application/projections.py"
    - "src/torve/cli/context.py"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
  depends_on: [1]
```

## 12. Exit criteria

- One decision regraded by amendment, citing this report as the evidence that
  produced the regrade.
- One row identified as decoration and either deleted or repathed, with the
  report distinguishing which of the two causes applied.
- One contract-lint rule added under RFC 0020 §5.2 citing an escalation
  population this report surfaced rather than a single remembered incident.
- The report run over the corpus with no reading fired on a decision whose
  tasks never touched its declared paths — silence about nothing stays quiet.

## Amendments
