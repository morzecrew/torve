---
id: "0030"
title: The document threshold
status: draft
depends_on: ["0007", "0020"]
informed_by: ["0016", "0022", "0026"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  When work must carry its own document and when it may ride the standing
  corpus: paths-intersection inheritance for document-less contracts and a
  deterministic threshold that routes large or LOCKED-crossing work to
  authoring.
schema_version: 1
---

# RFC 0030 — The document threshold

- **Scope:** How a contract that has no source document stays governed, and
  when the engine refuses to let work proceed without one. Covers a standing
  inheritance function beside the planner's `inherit_decisions` — rows from
  every accepted document whose declared paths intersect the contract's
  `scope.allow`, copied at mint time — applied at intake adoption and
  surfaced by the contract lint; and a deterministic threshold verdict that
  routes a draft or adoption to "this needs a document" by counting the
  LOCKED rows and distinct documents its scope crosses, joined with the size
  verdict that already exists. No new ports, no gate changes, no change to
  `torve plan`'s full-table inheritance. Excludes any model judgement of
  risk, any engine-written document, and any retroactive re-minting of
  contracts that already exist.
- **Related:** [`0007`](0007-planner-context.md) §3, §6a (A-47's one-reader
  rule) · [`0020`](0020-intake-and-the-drafting-run.md) §5 ·
  [`0022`](0022-specification-quality.md) D-22.9 ·
  `src/torve/application/planner.py` · `src/torve/application/intake.py` ·
  `src/torve/application/sizing.py`
- **Origin:** The adoption research of 2026-08-31: every surveyed
  spec-first tool's first documented rejection reason is fixed-cost ceremony
  on small work, and this engine's own history is the internal half of the
  same evidence — dozens of hand-minted maintenance tasks that legally
  carried `decisions: []` through areas the corpus governs.

---

## 1. Summary

A contract minted without a source document — adopted intake work, a
hand-minted maintenance task — inherits the standing rows of every accepted
document whose declared paths intersect its `scope.allow`, through the same
copy-at-write-time discipline `torve plan` uses. A deterministic threshold,
computed at the same authoring surfaces from what that inheritance found and
the size verdict, refuses the document-less lane to work that crosses too
much settled ground: the refusal names the crossings and routes the author
to `torve rfc new`. Ceremony becomes proportional to blast radius, and the
document-less lane stops being the ungoverned one.

## 2. Motivation

The corpus is all-or-nothing today. Work either arrives through a document —
table, phasing, admission — or it arrives with whatever decisions someone
hand-copied, which in practice is none:

- Intake adoption inherits decisions only when the request body carries an
  `rfc:` line naming one document (`RFC_LINE` in
  `src/torve/application/intake.py`); a prose request without it adopts into
  a contract with `decisions: []`.
- Hand-minted contracts are YAML written by hand; nothing checks their
  `decisions` against the corpus at all.
- `decisions-reported` runs silence over an empty list by design (D-7.5:
  empty is legal and explicit) — so a document-less task that edits inside a
  LOCKED row's declared paths halts nothing and logs nothing. The strongest
  anti-drift guarantee quietly does not apply to exactly the lane that has
  no other guarantee.

RFC 0022 already reports document-less tasks as their own population
(D-22.9) — the reader exists; the governance does not. And the population is
not small: this repository's own history hand-minted its maintenance batches
(T-0139–T-0141, T-0148–T-0149 among others) precisely because a document per
small fix is ceremony nobody pays — the correct instinct, currently paid for
with inherited silence.

## 3. Current state

- `inherit_decisions(text, name)` (`src/torve/application/planner.py`) is
  the one reader (A-47): the full table of one document, grade and paths
  copied at write time. Both `torve plan` and adoption mint from it.
- Adoption's inheritance is gated on `RFC_LINE`; `lint_drafts` checks batch
  mechanics (refs, scopes, acceptance, the T-0113 rule) and consults no
  decision table.
- `globs_intersect` (planner) is the conservative intersection the same-
  phase disjointness check already uses.
- The size verdict (`src/torve/application/sizing.py`, `SizeVerdict`)
  already classifies a contract's scope at mint and dispatch; `too_large` is
  advisory.
- `torve lint-contract` (RFC 0020) is the standalone surface a hand-minter
  can already run.

## 4. Goals / Non-goals

**Goals**

- Small work stays document-less and becomes governed: standing rows arrive
  in the contract without anyone writing a document.
- Work that crosses too much settled ground is refused the document-less
  lane by a deterministic verdict that names its evidence.
- The document-minted flow is untouched.

**Non-goals**

- **Engine-written documents.** The threshold routes a human to authoring;
  it never drafts the document itself (D-2 — the drafting run of RFC 0020
  §5 remains the only machine assist, and adoption remains the signature).
- **Risk scoring by model.** The verdict is arithmetic over declared paths
  and the size class; a model's opinion of risk is a model deciding what
  work needs a human, which is D-2 with a scarier face.
- **Loosening any gate.** `decisions-reported` and the battery are
  unchanged; this document only changes what a contract carries into them.
- **Retroactive re-minting.** Contracts that already exist keep the
  decisions they were minted with (D-22.2's discipline, applied forward).

## 5. Design

### 5.1 Standing inheritance

```python
def standing_decisions(
    corpus: RfcsConfig, scope_allow: list[str]
) -> list[InheritedDecision]: ...
```

Beside `inherit_decisions` in the planner: every accepted document's table
is read through the same parse, and a row is inherited when any of its
declared paths intersects `scope_allow` (`globs_intersect`, conservative —
a false inclusion costs a few contract lines, a false exclusion costs the
silence check). Grade and paths are copied at write time, the same
discipline as D-7.22. Rows without paths are never standing — they govern
their own document's work only.

Consumers:

- **Adoption** (`adopt` in `intake.py`) always calls it and merges the
  result with any `RFC_LINE` inheritance, deduplicated by identifier —
  the cited document wins on conflict, since its copy is the one the
  request was written against.
- **`torve lint-contract`** warns when a contract's `scope.allow`
  intersects standing rows the contract does not carry, naming them — the
  hand-minter's surface, advisory because a hand-minted contract is already
  a human's signature.

`torve plan` is unchanged: a document's own table is inherited whole
(D-7.22), and whether document-minted contracts should *also* carry
intersecting standing rows from other documents is deliberately left open
(D-30.6).

### 5.2 The threshold verdict

```python
def document_threshold(
    standing: list[InheritedDecision], size: SizeVerdict
) -> ThresholdVerdict:  # rides | document_required
```

Deterministic arithmetic over what §5.1 already computed: the number of
distinct `LOCKED` rows intersected, the number of distinct source documents
they come from, and the size class. The rule (D-30.3, tunable in
configuration): `document_required` when the scope crosses LOCKED rows from
two or more documents, or when the size verdict is `too_large`. One
document's LOCKED ground with a bounded scope rides — the standing rows it
inherits are exactly the protection a document would have copied.

Enforcement is authoring-time only:

- **Intake lint**: a draft whose verdict is `document_required` is a lint
  error naming the crossings — the drafter's next iteration can narrow the
  scope, or the commander routes the request to authoring.
- **Adoption**: refuses `document_required` with the same message and exit 3
  — adoption is the signature, and a signature over that much settled
  ground belongs on a document.
- **Execution and gates: never.** A contract that exists was signed; the
  threshold is a router, not a gate, and a second enforcement point would
  convict work a human already accepted.

### Alternatives considered

- **Require a document for everything.** The corpus's own history refutes
  it: maintenance batches were hand-minted because the cost was real, and
  the workaround (empty decisions) was worse than the rule's intent.
- **Inherit the whole corpus into every contract.** Every row rides every
  prompt; decoration at scale, and exactly what the Paths column (D-32)
  exists to avoid.
- **A model-assessed routing verdict.** Refused under D-2; also
  unnecessary — the signal wanted ("how much settled ground does this
  cross") is arithmetic the corpus format already encodes.

## 6. Tests

- `standing_decisions`: intersection in and out, pathless rows never
  standing, grade copied as it stood, draft and superseded documents never
  read.
- Adoption: a prose request with no `rfc:` line adopts into a contract
  carrying the intersecting standing rows; the `RFC_LINE` copy wins on a
  duplicated identifier.
- Threshold: each clause of D-30.3 in isolation, plus the ride-through case
  (one document's LOCKED ground, bounded size).
- Lint: the `document_required` refusal message names rows and documents;
  `lint-contract` warns on missing standing rows without failing.

## 7. Docs

The rfc-writer skill's "what deserves an RFC at all" section gains the
threshold as its mechanical floor: below it the engine will carry your work
without a document, above it the engine itself will send you here. README's
intake section: one sentence on the refusal and what it routes to.

## 8. Out of scope

- **Standing inheritance for `torve plan` contracts** — named as D-30.6,
  not built; the evidence that settles it is decisions-reported silence
  over cross-document areas in document-minted work.
- **Threshold-driven decomposition** — a `document_required` verdict could
  suggest splitting the scope instead; that is RFC 0026's territory and a
  drafter behaviour, not an engine rule.

## 9. Risks

- **The threshold becomes a target.** Authors shave scopes to duck the
  verdict. Accepted: a shaved scope still inherits the standing rows it
  crosses, and the scope gate convicts the diff that exceeds it — ducking
  the router does not duck the governance.
- **Conservative intersection over-inherits.** A broad glob in an old
  document rides into many contracts as noise. Mitigated by RFC 0022's
  decoration reading — over-inherited rows that are never cited surface in
  `torve rfc health` as paths defects, which is the amendment loop working.
- **Two inheritance behaviours confuse authors.** Document-minted work
  inherits one full table; document-less work inherits intersections. The
  asymmetry is real and deliberate (a document is a scope commitment; a
  maintenance task is not) — recorded here so it is argued with rather than
  discovered.

## 10. Unresolved questions

- D-30.6 (below): whether document-minted contracts also carry intersecting
  standing rows from other accepted documents.
- Whether the threshold's counts should weight by grade age — a LOCKED row
  amended last week arguably counts for more than one untouched for months.
  Left to evidence from `torve rfc health`'s calibration report.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-30.1 | `LOCKED` | A contract without a source document inherits standing rows from every accepted document whose declared paths intersect its `scope.allow`, copied grade-and-paths at write time through the planner's one reader; pathless rows are never standing | `src/torve/application/planner.py` `src/torve/application/intake.py` | The document-less lane stops being the ungoverned one; a second reader here is the drift A-47 removed |
| D-30.2 | `LOCKED` | The threshold verdict is deterministic — arithmetic over intersected rows, distinct documents and the size class; no model opinion of risk anywhere in the routing | `src/torve/application/planner.py` `src/torve/application/intake.py` | A model deciding what needs a human is D-2 by another door |
| D-30.3 | `ASSUMED` | The rule: `document_required` when the scope crosses LOCKED rows from two or more accepted documents, or the size verdict is `too_large`; thresholds live in configuration beside intake's | `src/torve/config/runconfig.py` | A starting rule to calibrate against RFC 0022's evidence, not a truth |
| D-30.4 | `ASSUMED` | Enforcement is authoring-time only — intake lint error and adoption refusal (exit 3), `lint-contract` advisory for hand-minters; execution and gates never re-check the verdict | `src/torve/application/intake.py` `src/torve/cli/intake.py` | A signed contract was a human's call; convicting it later punishes the signature |
| D-30.5 | `ASSUMED` | `torve plan` inheritance is unchanged: the source document's table whole, per D-7.22; standing inheritance is the document-less lane's mechanism | `src/torve/application/planner.py` | Two lanes, one reader, different selections — the asymmetry is the design |
| D-30.6 | `OPEN` | Whether document-minted contracts also inherit intersecting standing rows from other accepted documents; decisions-reported silence over cross-document areas in minted work would settle it | `src/torve/application/planner.py` | Silence here fills itself the first time cross-document drift lands unlogged |

## 12. Phasing

```yaml
- phase: 1
  title: standing-inheritance
  intent: >-
    standing_decisions beside inherit_decisions in the planner — accepted
    documents only, paths-intersection via globs_intersect, grade copied at
    write time, pathless rows excluded (D-30.1); adoption always merges its
    result with RFC_LINE inheritance deduplicated by identifier, and
    lint-contract warns on standing rows a contract does not carry. Tests
    pin intersection, copy-at-write, the dedup preference and the
    adoption-without-rfc-line case.
  scope:
    - "src/torve/application/planner.py"
    - "src/torve/application/intake.py"
    - "tests/test_plan.py"
    - "tests/test_intake.py"
  acceptance:
    - "uv run pytest tests/test_plan.py tests/test_intake.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
    - "uv run lint-imports"
  depends_on: []
- phase: 2
  title: threshold-verdict
  intent: >-
    document_threshold over the standing result and the size verdict, with
    the D-30.3 rule in configuration; intake lint gains the
    document_required error naming the crossings, adoption refuses it with
    exit 3, and the refusal text routes to authoring without corpus
    coordinates in user-facing strings. Tests pin each clause, the
    ride-through case and both enforcement surfaces.
  scope:
    - "src/torve/application/intake.py"
    - "src/torve/config/runconfig.py"
    - "src/torve/cli/intake.py"
    - "tests/test_intake.py"
  acceptance:
    - "uv run pytest tests/test_intake.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: [1]
```
