---
id: "0034"
title: Task character and conviction-routed retries
kind: design
status: accepted
depends_on: ["0007", "0027", "0029"]
informed_by: ["0004", "0009", "0026"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Three task-character axes — structural and routine declared per phase, compliance measured from gate convictions — routing equipment at mint and retries at the failing gate, calibrated by a projection.
schema_version: 1
---

# RFC 0034 — Task character and conviction-routed retries

- **Scope:** A `character` field on Phasing entries (parsed by the format
  reader, copied verbatim by the planner, routed to a tier variant by
  configuration), an `axis` label on gate declarations, a retry rung chosen
  by the failing gate's axis instead of one fixed rung, a calibration
  projection tabling declared character against realized conviction
  profiles, and an eval candidate arm that overrides a tier variant the way
  it already overrides an image. Touches `src/torve/config/rfc_parse.py`,
  `src/torve/domain/task.py`, `src/torve/application/planner.py`,
  `src/torve/config/manifest.py`, `src/torve/application/runner.py`,
  `src/torve/application/projections.py`, `src/torve/application/evals.py`
  and their CLI surfaces. Deliberately not covered: any change to the
  gates' own rules (§8), any model-derived signal in routing (D-34.5), and
  the log-writing doctrine (D-34.11 names the tension and stops).
- **Related:** RFC 0007 (phasing format, D-7.21), RFC 0027 (variants and
  the single retry rung, D-27.2/D-27.11; displacement doctrine, D-27.7),
  RFC 0029 (equipment on variants), RFC 0009 §5 (the eval loop this
  extends).
- **Origin:** The repowise shadow campaign of 2026-09-01 (a 2×2 over two
  harnesses and an indexed-equipment candidate, `torve eval` records in
  the telemetry of this repo and the campaign archive) and a full-ledger
  conviction audit, both summarized in §2. Recorded as proposed decisions
  b88acfca66da (fence repairs as disclosed chores) and 953268eeff32
  (the axes themselves) before drafting.

---

## 1. Summary

Every task has a character, and the engine currently treats all of them
the same. This document names three axes — **structural** work that is
comprehension-bound, **routine** work that is prescription-bound, and
**compliance** work that is grammar-bound — and gives each a home: the
first two are declared per phase in the specification and route to an
equipped tier variant at mint; the third is never declared, because it is
a property of the run, and is attacked where it lives — the retry after a
conviction, which now resolves a rung keyed on the failing gate's axis
instead of one fixed rung. A calibration projection tables what was
declared against what the gates actually convicted, so declarations that
keep lying get corrected the way sizing estimates already are. The eval
loop learns to replay a tier-variant candidate so every routing choice
this document introduces is displaceable only by a measured verdict,
per D-27.7.

## 2. Motivation

Three measured facts, from this repository's own ledger.

**The conviction economy is administrative.** Of 266 live agent attempts
recorded to date, 106 were red. By gate: `decisions-reported` 47,
`scope` 29, `user-facing-text` 16, `acceptance` 12, `source-layout` 4,
`self-audit` 3, `rfc-valid` 3. Grouped: compliance-grammar gates carry
58% of all convictions; functional failures (`acceptance`) carry 10%.
The engine's dominant failure mode is not wrong code — it is the grammar
around right code.

**Grammar convictions are model-independent.** `decisions-reported`
convicted 28 attempts in the early sonnet era, 17 in the recent sonnet
era, and 2 of deepseek-v4-flash's 35; the 2026-09-01 shadow campaign
added three sonnet-at-xhigh escalations on the same evidence-format
class that has now cost three poison ceilings across two models.
Capability does not buy grammar compliance.

**Equipment pays by character.** The campaign's paired replays showed the
repowise-indexed variant turning T-0190 (a comprehension-bound task) from
2 attempts and 20 minutes into 1 attempt and 9, and alone surviving
T-0198's broken fence — while on T-0196 (a fully prescribed task) it
saved zero attempts and cost 20% more. The same equipment is an
accelerant on one character and a tax on another, and today nothing in
the system can tell them apart at dispatch time.

A fourth observation shapes a non-goal: at least two of the studied
`scope` convictions (T-0198, T-0200) were operator phasing defects, not
agent errors. A boundary conviction is as much a contract-quality signal
as an agent failure, which is why boundary retries stay on the same rung
(D-34.7) and the repair path is the fence-discipline decision already
accepted by the owner.

## 3. Current state

Verified against the tree at drafting time:

- A Phasing entry may carry `tier_variant`, copied verbatim by the
  planner onto the minted contract (RFC 0029 §1; landed via T-0156,
  rendered by `torve rfc emit` via T-0158). `character` follows exactly
  this path.
- A tier may name `retry_variant` — one rung, resolved after any
  gate-red (D-27.11, `src/torve/application/runner.py`). The rung does
  not know which gate convicted.
- Gate declarations (`config/manifest.py`, `.torve/gates.yaml`) carry
  `state` and `origin` but nothing that classifies what a conviction
  *means*.
- The costs projection already exposes per-attempt gate results, tier,
  token shape and wall clock (`application/projections.py`); the raw
  material for calibration exists, ungrouped.
- `torve eval` overrides only a tier's *image* in the candidate arm
  (`application/evals.py::candidate_config`, D-27.7); an equipped-variant
  candidate — the exact thing the campaign needed — required baking the
  prompt into the image as a workaround.

## 4. Goals / Non-goals

**Goals**

- Character declared where phasing already lives, routed where equipment
  already lives, with zero new places to look.
- Retry selection that reads only recorded gate outcomes — auditable
  from telemetry alone.
- A calibration surface that makes wrong declarations visible without
  blocking anything.
- The eval loop able to measure every routing choice this document adds.

**Non-goals**

- Changing any gate's rules or thresholds — D-34.9 names the one
  candidate and leaves it to a measurement window; this document only
  classifies gates.
- Inferring character mechanically from intent text or scope shape —
  the campaign showed fence breadth carries no signal beyond the
  declared intent; inference would launder a guess into a routing key.
- Automatic displacement of any default — D-27.7 stands; everything
  here lands as measurable configuration, not as a new regime.

## 5. Design

### 5.1 Character, declared

The Phasing format (D-7.21) gains an optional `character` key with the
closed vocabulary `structural | routine`:

```yaml
- phase: 2
  title: standing inheritance
  character: routine
  tier_variant: null
  ...
```

This document's own §12 fence deliberately omits the field it defines:
the parser refuses unknown keys until phase 1 lands, so the first
declared characters arrive with the first post-0034 document.

`parse_phasing` validates the vocabulary; `torve plan` copies the value
verbatim onto the minted contract (`Task.character`, optional, default
absent), exactly as `tier_variant` travels. Absent means *undeclared* —
never inferred, rendered as such everywhere. Compliance is deliberately
not in the vocabulary: a phase cannot know in advance that its run will
fumble a log grammar, and a declarable compliance axis would invite
pre-excusing it.

### 5.2 Character, routed

Dispatch resolves a contract with `character` and **no** explicit
`tier_variant` through a configuration mapping:

```yaml
tiers:
  executor:
    character_routing:
      structural: executor.indexed
```

An explicit `tier_variant` on the contract always wins (the operator
spoke); an absent mapping entry falls through to the seat default. The
mapping lives in configuration, not the corpus, because which variant
serves a character is a regime choice — `config_hash` already separates
it, and D-27.7 already governs displacing it.

### 5.3 Convictions, classified

A gate declaration gains an optional `axis` with the closed vocabulary
`functional | boundary | compliance | form`. This repository's manifest
labels its battery: `acceptance` functional; `scope` boundary;
`decisions-reported`, `user-facing-text`, `self-audit` compliance;
`source-layout`, `rfc-valid` form. An unlabeled gate reads as
`functional` — the fail-safe that routes its retry up, never sideways.

### 5.4 Retries, routed on the conviction

The tier's single `retry_variant` generalizes to a mapping keyed by
axis, with the old scalar kept as sugar for `{functional: ...}`:

```yaml
tiers:
  executor:
    retry_variants:
      functional: executor.heavy
      compliance: executor          # same seat; the gate's own repair
                                    # text (T-0203) is the equipment
```

After a red attempt the runner groups the attempt's failing gates by
axis and resolves the retry rung for the *most severe* axis present, in
the fixed order `functional > boundary > compliance > form`. Selection
reads only the recorded gate outcomes of the attempt — never the trace,
never model output — so the routing of every retry is reproducible from
telemetry alone. Boundary convictions resolve no rung by default
(D-34.7): a broken fence is repaired by the operator's disclosed chore,
not escaped by a heavier model. Every attempt's telemetry row already
stamps the tier it actually ran under (D-27.11's second clause), which
is what keeps a multi-rung regime enumerable.

### 5.5 Calibration, projected

A `character` section joins the context projection: one row per task
carrying a declaration or any conviction — declared character, realized
conviction profile grouped by axis, attempts, and token shape. The serve
surface re-exposes it verbatim per D-32.1. The section is measurement,
not enforcement: a `routine` declaration whose runs keep drawing
functional convictions is corrected in the document by its author, the
way sizing estimates already earn D-26.7 observations.

### 5.6 Evals, extended

`candidate_config` accepts a tier-*variant* override beside the existing
image override — `torve eval --tier executor --variant indexed` replays
every named task with the candidate arm resolving `executor.indexed`
where the incumbent resolves the seat default. The eval record carries
the variant name and both config hashes. This retires the campaign's
workaround of baking prompt equipment into a candidate image so the
image override could smuggle it.

### Alternatives considered

- **Degrees (0–10) instead of an enum** — rejected: nothing consumes a
  gradient, and an uncalibrated scalar invites false precision; the enum
  can grow by amendment when a consumer for a third value exists.
- **Character on the whole RFC instead of per phase** — rejected: the
  campaign's six tasks came from four documents whose phases mixed
  characters (0031's survey is structural, its bootstrap skill routine).
- **Retry keyed on the specific gate name** — rejected: per-gate rungs
  explode the enumerable regime space D-27.11 protects, and the axis is
  the level at which the evidence differentiates.

## 6. Tests

Phasing: vocabulary validation red/green, verbatim copy onto the
contract, absent-stays-absent. Routing: explicit `tier_variant` beats
mapping, mapping beats default, unmapped character falls through.
Retry: axis grouping, severity order, boundary-resolves-nothing, sugar
compatibility for the scalar form, and a telemetry-replay test asserting
the chosen rung is derivable from the recorded gate outcomes alone.
Projection: content-only assertions per D-18.1. Evals: variant candidate
arm produces a distinct config hash and a record naming the variant;
image and variant overrides refuse to combine in one invocation until a
need is shown.

## 7. Docs

The rfc-writer template's Phasing section documents `character` beside
`tier_variant`. The equipment page (0029's docs) gains the
character-routing and retry-variants shapes. No migration notes: every
field is optional and absent behaves exactly as today.

## 8. Out of scope

- Rewording any gate's grammar — D-34.9 defers the evidence-format
  question to a measurement window over post-T-0203 tasks; this
  document must not smuggle a rules change under a classification.
- The scribe log post-pass (a second agent writing `log.yaml` from the
  finished diff) — collides with D-4.6's self-report doctrine; named in
  D-34.11 as the escalation if routing and gate-taught repair fail to
  bend the 58%.
- Dashboard rendering of the calibration section beyond the verbatim
  re-exposure D-32.1 already mandates.

## 9. Risks

- **Declarations rot.** Mitigated by §5.5 being cheap to read and by
  absent-means-undeclared: nothing forces a guess.
- **Compliance retries loop on the same conviction.** The compliance
  rung defaults to the same seat, so a task that cannot satisfy the
  grammar still meets the poison ceiling unchanged; the mitigation is
  T-0203's repair-teaching output, and D-34.9's window measures whether
  that suffices.
- **Axis mislabeling routes retries wrong.** The vocabulary is four
  words and the manifest diff is reviewed; the fail-safe default is the
  heaviest rung, so a missing label costs money, never correctness.

## 10. Unresolved questions

- The size of D-34.9's measurement window (proposal: the next 20
  implement tasks after T-0203's landing) — the owner settles it when
  the window closes.
- Whether `character_routing` belongs on the seat only or also on
  variants (a variant routing to a variant) — implementation settles it
  by refusing the recursive form until a need is shown, and logs.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-34.1 | `LOCKED` | Task character has exactly three axes: structural and routine are declarable; compliance is measured from gate convictions and never declarable | `src/torve/config/rfc_parse.py` `src/torve/application/projections.py` | The axis vocabulary is the routing key everywhere; adding an axis is a corpus amendment, and no phase can pre-excuse a grammar failure |
| D-34.2 | `ASSUMED` | A Phasing entry may carry `character: structural\|routine`, validated at parse, copied verbatim by the planner onto the contract; absent means undeclared and is never inferred | `src/torve/config/rfc_parse.py` `src/torve/application/planner.py` `src/torve/domain/task.py` | — |
| D-34.3 | `ASSUMED` | `character_routing` on a seat maps a declared character to a tier variant; an explicit contract `tier_variant` always wins; an unmapped character falls through to the seat default | `src/torve/config/runconfig.py` `src/torve/application/runner.py` | Routing lives in configuration, so `config_hash` separates it and D-27.7 governs displacing it |
| D-34.4 | `ASSUMED` | A gate declaration may carry `axis: functional\|boundary\|compliance\|form`; an unlabeled gate reads as functional | `src/torve/config/manifest.py` `.torve/gates.yaml` | The fail-safe default routes an unlabeled gate's retry to the heaviest rung — a missing label costs money, never correctness |
| D-34.5 | `LOCKED` | Retry-rung selection is deterministic over the red attempt's recorded gate outcomes alone — never the trace or model output — resolving the most severe axis present in the fixed order functional > boundary > compliance > form | `src/torve/application/runner.py` | Every retry's routing is reproducible from telemetry alone; a smarter selector is a new RFC, not a patch |
| D-34.6 | `ASSUMED` | `retry_variants` maps axes to rungs, generalizing D-27.11's single rung; the scalar form stays as sugar for the functional key; every attempt's row stamps the tier it actually ran under | `src/torve/config/runconfig.py` `src/torve/application/runner.py` | The regime space stays enumerable — bounded by the axis vocabulary, not the gate roster |
| D-34.7 | `ASSUMED` | A boundary conviction resolves no retry rung by default: fence defects are repaired by the operator's disclosed chore commit before the landing, never escaped by a heavier model | `src/torve/application/runner.py` | — |
| D-34.8 | `ASSUMED` | The context projection gains a character section — declared character against realized conviction profile by axis, attempts, token shape — re-exposed verbatim by serve per D-32.1; measurement, never enforcement | `src/torve/application/projections.py` | — |
| D-34.9 | `OPEN` | Whether the evidence-format grammar itself gets simplified is settled by a measurement window over post-T-0203 implement tasks: if gate-taught repair does not materially cut `decisions-reported` convictions, the owner decides on the grammar | `src/torve/gates/decisions_reported.py` | — |
| D-34.10 | `ASSUMED` | The eval candidate arm may override a tier variant beside the existing image override; the record carries the variant and both config hashes; combining both overrides in one invocation is refused until a need is shown | `src/torve/application/evals.py` `src/torve/cli/evals.py` | — |
| D-34.11 | `OPEN` | The scribe log post-pass (a second agent writing the divergence log from the finished diff) stays out: it collides with D-4.6's self-report doctrine — revisited only if D-34.9's window shows routing plus gate-taught repair failing to bend the compliance share | `src/torve/application/runner.py` | — |

## 12. Phasing

Phase 1's two units are disjoint and parallel. Phase 2 needs nothing
from phase 1 but lands the axis vocabulary phase 3 routes on.

```yaml
- phase: 1
  title: character declared and routed
  intent: >-
    The character field end to end: parse_phasing validates the
    structural|routine vocabulary (D-34.2), the planner copies it
    verbatim onto the minted contract beside tier_variant, Task carries
    it optionally, and dispatch resolves character_routing (D-34.3) —
    explicit tier_variant wins, unmapped character falls through to the
    seat default. The rfc-writer template documents the field.
  scope:
    - src/torve/config/rfc_parse.py
    - src/torve/domain/task.py
    - src/torve/application/planner.py
    - src/torve/config/runconfig.py
    - src/torve/application/runner.py
    - skills/rfc-writer/**
    - tests/test_plan.py
    - tests/test_rfc_check.py
    - tests/test_runner.py
  acceptance:
    - uv run pytest tests/test_plan.py tests/test_rfc_check.py tests/test_runner.py
    - uv run torve rfc check
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 1
  title: eval variant override
  intent: >-
    candidate_config accepts a tier-variant override beside the image
    override (D-34.10): torve eval --tier <seat> --variant <name>
    replays with the candidate arm resolving the dotted variant; the
    eval record carries the variant name and both config hashes;
    combining --image and --variant in one invocation is refused with
    an instructive message.
  scope:
    - src/torve/application/evals.py
    - src/torve/cli/evals.py
    - tests/test_evals.py
  acceptance:
    - uv run pytest tests/test_evals.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 2
  title: axis vocabulary and calibration
  intent: >-
    Gate declarations carry the optional axis field with the four-word
    vocabulary, unlabeled reads as functional (D-34.4); this
    repository's gates.yaml labels its battery per §5.3; the context
    projection gains the character calibration section (D-34.8) —
    declared character against realized conviction profile grouped by
    axis, attempts and token shape — rendered in context and re-exposed
    verbatim by serve.
  scope:
    - src/torve/config/manifest.py
    - .torve/gates.yaml
    - src/torve/application/projections.py
    - src/torve/cli/context.py
    - tests/test_context.py
    - tests/test_gates.py
  acceptance:
    - uv run pytest tests/test_context.py tests/test_gates.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
- phase: 3
  title: conviction-routed retries
  intent: >-
    retry_variants keyed by axis with the scalar form kept as sugar for
    functional (D-34.6); after a red attempt the runner groups the
    attempt's failing gates by axis and resolves the rung for the most
    severe axis present in the fixed order functional > boundary >
    compliance > form, reading only recorded gate outcomes (D-34.5);
    boundary resolves no rung (D-34.7). A replay-shaped test derives
    the chosen rung from telemetry records alone.
  scope:
    - src/torve/config/runconfig.py
    - src/torve/application/runner.py
    - tests/test_runner.py
  acceptance:
    - uv run pytest tests/test_runner.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: [2]
```
