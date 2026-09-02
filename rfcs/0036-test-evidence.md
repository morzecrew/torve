---
id: "0036"
title: Test evidence
kind: design
status: draft
depends_on: ["0002", "0005"]
informed_by: ["0009", "0023", "0034"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Evidence that the tests test: a changed-lines coverage gate entering through the shadow lifecycle, a manifest lint refusing gates without sabotage twins, and post-merge escapes minting review-corpus entries.
schema_version: 1
---

# RFC 0036 — Test evidence

- **Scope:** Three additions to the verification battery's own honesty: a
  `coverage-delta` shell gate measuring whether an attempt's changed
  lines are exercised by the suite (entering shadow per D-2.18, new dev
  dependencies pytest-cov and diff-cover), a manifest lint refusing a
  gate declaration without a sabotage pair, and the doctrine plus
  mechanism for growing `.torve/review-corpus/` from every post-merge
  escape instead of its three static seeds. Deliberately not covered:
  mutation testing and performance baselines (§8 — each returns under a
  named condition), and property/fuzz suites (plain task work needing no
  doctrine).
- **Related:** RFC 0002 (gate battery, sabotage discipline, the
  shadow→blocking lifecycle, and its own line "everything else
  accumulates from observed leaks"), RFC 0005 §6 (reviewer measurement
  over the review corpus; `src/torve/cli/review.py`,
  `tests/test_review_corpus.py`), RFC 0023 (standing maintenance — the
  surface mutation testing would ride), RFC 0034 (the conviction audit
  whose numbers frame what gates are for).
- **Origin:** An operator-side gap analysis (2026-09-01, separate
  session) mapping an external field note against this corpus: the
  battery proves the suite green and proves the gates convict, but
  nothing proves an attempt's *new lines are exercised*, nothing refuses
  a gate that ships without its sabotage twin, and the review corpus
  does not grow from real escapes despite 0002's accumulate-from-leaks
  doctrine.

---

## 1. Summary

The battery answers "did the suite pass" and the sabotage suite answers
"do the gates convict"; this document adds the third question — "did the
tests actually meet the change" — and closes two honesty loops around
it. A `coverage-delta` gate measures the suite against the attempt's
changed lines and enters through the same shadow lifecycle every gate
earns blocking through. A manifest lint makes the sabotage discipline
structural: a declared gate without a proven red twin is a configuration
error, not a convention. And the review corpus stops being three seeded
defects: an escape — a defect fixed after its change shipped — mints a
corpus entry as part of fixing it, so the reviewer's regression suite
grows from exactly the failures that got past it.

## 2. Motivation

- Untested new code passes today silently: `acceptance` runs the suite,
  and a change whose new branches no test reaches is green. The 0034
  conviction audit sharpens the stake: functional convictions are 10% of
  reds — the gates convict grammar generously and logic rarely, and the
  cheapest way to catch more logic is to refuse unexercised lines.
- The repowise index independently lists untested hotspots
  (`src/torve/gates/source_layout.py`, `src/torve/cli/sandbox.py`,
  `src/torve/cli/review.py` at −2.0 impact each) — the gap is measured,
  not hypothetical.
- Sabotage coverage is convention, not structure: builtins carry shipped
  CASES, but shell gates (`rfc-valid`, `layering`) are sabotaged by
  hand-run suites, and nothing refuses a future gate that ships with no
  twin at all.
- `.torve/review-corpus/` holds three static seeds (`clean-rename`,
  `duplicated-helper`, `swallowed-exception`) while the bug-magnet files
  (`projections.py`, `runner.py` — 8 and 9 recorded fixes) accumulate
  real escapes that teach the reviewer nothing.

## 3. Current state

Verified at drafting time:

- The battery is manifest-driven (`.torve/gates.yaml`); shell gates run
  arbitrary commands in the sandbox, and the lifecycle (shadow →
  blocking, D-2.18) is live. Neither pytest-cov nor diff-cover is in the
  dependency tree yet.
- Sabotage twins live in `src/torve/gates/sabotage.py` CASES for
  builtins and in per-gate test suites otherwise; the manifest has no
  field linking a gate to its twin.
- The review corpus is consumed by `src/torve/cli/review.py` and pinned
  by `tests/test_review_corpus.py`; an entry is a directory with
  `case.yaml`, `diff.patch` and a `tree/` — the entry format needs no
  change, only a source of new entries.

## 4. Goals / Non-goals

**Goals**

- Changed-lines coverage measured per attempt, promoted to blocking only
  on soak evidence like every gate before it.
- A gate without a proven twin cannot be declared.
- Every escape leaves a corpus entry behind, as part of the fix landing.

**Non-goals**

- Raising whole-repo coverage or setting a global threshold — the gate
  judges the diff, never the inherited tree (0031's ratchet doctrine:
  new work only, no retroactive conviction).
- Test *strength* measurement — that is mutation testing, §8.
- Any model-judged verdict — all three additions are deterministic.

## 5. Design

### 5.1 The coverage-delta gate

A shell gate in the manifest, shadow state, origin `rfc/0036`:

```yaml
coverage-delta:
  run: "uv run pytest --cov=src --cov-report=xml -q && uv run diff-cover coverage.xml --compare-branch {base} --fail-under 80"
  input: worktree
  state: shadow
  origin: rfc/0036
```

diff-cover intersects the coverage report with the diff against the
gate base, so only the attempt's own added or changed lines are judged;
an untouched, untested legacy file convicts nobody. The threshold and
the exact command shape are the executor's to settle (D-36.2 OPEN) —
what is fixed is the judgment surface (changed lines only) and the
entry path (shadow first; promotion is a separate act under D-2.23's
soak evidence). Sabotage twins ship with the gate: a change adding an
untested branch reddens, its tested twin passes.

### 5.2 The sabotage-pair lint

The manifest loader learns to refuse a gate that names no twin: each
gate entry carries `sabotage:` naming either a shipped CASES family or
the test file that reddens it. The lint is a configuration check —
manifest load time, not gate runtime — so a battery with an unproven
gate fails before anything dispatches. The three-seed grace: existing
entries are backfilled in the same change, so the lint lands with zero
grandfather exceptions.

### 5.3 Escapes mint corpus entries

Doctrine (D-36.4): a fix for a defect that shipped — identified by the
fix touching behavior a previous landing introduced — owes a review
corpus entry beside the fix: the defective diff (from history), the
finding a reviewer should have produced, and the fixed tree. Mechanism
kept lean: `torve review corpus add <fixing-commit>` scaffolds the
entry from the commit pair (defective landing found by trailer, fix
commit's parent as the tree), and the operator writes the one paragraph
a scaffold cannot — what the finding should have said. The reviewer
measurement (0005 §6) then runs over a corpus that grows at exactly the
rate defects escape.

### Alternatives considered

- **Coverage threshold on the whole tree** — rejected: convicts history,
  violates the ratchet doctrine, and the number becomes a negotiation.
- **Auto-minting corpus entries from every bug-labeled commit** —
  rejected: the finding paragraph is the entry's value and only a person
  knows what the reviewer should have seen; a scaffold without judgment
  is corpus noise.
- **Blocking coverage-delta on arrival** — rejected: D-2.18 exists
  because unmeasured gates convict falsely; flaky coverage on async
  paths is a known genre of false red.

## 6. Tests

Gate: sabotage twins (untested-branch red, tested twin green, legacy
untouched-file case proving no retroactive conviction). Lint: manifest
with a twinless gate refuses to load; the shipped manifest passes with
all entries backfilled. Corpus verb: scaffolding from a synthetic
commit pair produces a loadable entry `tests/test_review_corpus.py`
accepts; a fix commit with no shipped defective ancestor is refused
with an instructive message.

## 7. Docs

The gates page documents the changed-lines-only judgment surface and
the promotion path; the review page documents the corpus-entry duty and
the verb. The rfc-writer template's battery examples gain the
`sabotage:` field.

## 8. Out of scope

- **Mutation testing on the diff** — test strength, not presence;
  expensive per attempt by design. Returns as a standing-maintenance
  job (RFC 0023's surface) once coverage-delta has soaked to blocking —
  mutation of uncovered lines is noise, so the coverage gate is its
  prerequisite.
- **Performance baselines** — the index's 158 open I/O-in-loop findings
  say the gap is real, but a benchmark gate needs committed baselines
  and variance discipline this document should not smuggle in; returns
  as its own RFC when a hot path regresses in a way a landing should
  have caught.
- **Property/fuzz suites** (hypothesis on the manifest loader, citation
  regexes, RFC front-matter parse) — suite additions, no doctrine
  needed; hand-mintable as routine tasks any time.

## 9. Risks

- **Coverage flake convicts honest work** — mitigated by the shadow
  entry and by judging lines, not percentages of files; promotion waits
  for soak numbers (D-2.23).
- **The corpus duty gets skipped under deadline** — the duty binds the
  operator, not a gate; accepted, revisited if the corpus stays static
  while fixes accumulate (the specquality attention line already counts
  fixes, so the drift is visible).
- **The lint blocks an emergency gate addition** — a twin can be one
  red case; writing it is minutes, and an untested emergency gate is
  exactly the thing the lint exists to refuse.

## 10. Unresolved questions

- The `--fail-under` threshold for changed lines and whether asserts on
  branch coverage join line coverage — the executor measures both on
  recent landings and logs the choice (D-36.2).
- Whether the corpus verb belongs under `torve review` or `torve rfc` —
  implementation settles by where the review corpus loader already
  lives.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-36.1 | `LOCKED` | The coverage-delta gate judges the attempt's changed lines only — never the inherited tree, never a whole-repo threshold — and enters shadow like every gate (D-2.18); promotion to blocking is a separate act on soak evidence | `.torve/gates.yaml` `src/torve/gates/sabotage.py` | No retroactive conviction of history; the number measures the change, so it cannot become a negotiation about the past |
| D-36.2 | `OPEN` | The changed-lines threshold and the exact pytest-cov/diff-cover invocation: the executor measures candidate values against recent landings and logs the settled command | `.torve/gates.yaml` | — |
| D-36.3 | `ASSUMED` | A manifest gate entry names its sabotage twin (`sabotage:` — a CASES family or a test path); manifest load refuses a twinless gate, with existing entries backfilled in the landing so the lint ships with zero exceptions | `src/torve/config/manifest.py` `.torve/gates.yaml` | A gate that cannot prove it convicts cannot be declared — the sabotage discipline becomes structure |
| D-36.4 | `LOCKED` | A fix for an escaped defect owes a review-corpus entry beside the fix: the defective diff from history, the finding a reviewer should have produced (written by a person, never generated), and the fixed tree — the corpus grows at the rate defects escape (0002's accumulate-from-leaks doctrine applied to the reviewer) | `.torve/review-corpus/**` `src/torve/cli/review.py` | The reviewer's regression suite is fed by exactly what got past it; a static corpus is a visible failure of this rule |
| D-36.5 | `ASSUMED` | `torve review corpus add <fixing-commit>` scaffolds the entry from the commit pair (defective landing by trailer, fix parent as tree); a commit with no shipped defective ancestor is refused with an instructive message | `src/torve/cli/review.py` | — |

## 12. Phasing

Phase 1's units are disjoint and parallel; the corpus verb waits for
neither.

```yaml
- phase: 1
  title: the coverage-delta gate
  intent: >-
    pytest-cov and diff-cover join the dev dependencies; the
    coverage-delta shell gate enters the manifest in shadow (D-36.1),
    judging the attempt's changed lines against the gate base; the
    executor measures and settles the threshold and invocation (D-36.2)
    and logs the choice; sabotage twins ship with it — untested new
    branch red, tested twin green, untouched legacy file unconvicted.
  scope:
    - .torve/gates.yaml
    - pyproject.toml
    - uv.lock
    - src/torve/gates/sabotage.py
    - tests/test_gates.py
  acceptance:
    - uv run pytest tests/test_gates.py
    - uv run torve gates check
    - uv run torve rfc check
    - uv run ruff check .
- phase: 1
  title: the sabotage-pair lint
  intent: >-
    Gate declarations carry `sabotage:` naming a CASES family or a test
    path; manifest load refuses a twinless gate (D-36.3); every
    existing entry in this repository's gates.yaml is backfilled in the
    same landing so the lint ships with zero grandfather exceptions. A
    red case pins the refusal, a green one pins the shipped manifest.
  scope:
    - src/torve/config/manifest.py
    - .torve/gates.yaml
    - tests/test_manifest.py
  acceptance:
    - uv run pytest tests/test_manifest.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
- phase: 2
  title: escapes mint corpus entries
  intent: >-
    The corpus duty (D-36.4) lands as mechanism: torve review corpus
    add <fixing-commit> scaffolds an entry from the commit pair
    (D-36.5) — defective landing located by its task trailer, fix
    parent as the tree, the finding paragraph left explicitly for the
    operator with a refusing placeholder the loader rejects until
    written. A commit with no shipped defective ancestor is refused
    with an instructive message. The review page documents the duty.
  scope:
    - src/torve/cli/review.py
    - tests/test_review_corpus.py
  acceptance:
    - uv run pytest tests/test_review_corpus.py
    - uv run mypy src
    - uv run basedpyright src
    - uv run ruff check .
  depends_on: []
```
