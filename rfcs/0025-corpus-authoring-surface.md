---
id: "0025"
title: The corpus authoring surface
kind: design
status: accepted
implementation: none
depends_on: ["0007", "0016"]
informed_by: ["0011", "0020", "0022"]
supersedes: []
superseded_by: null
amended_by: []
retired: []
owner: Lev Litvinov
description: >-
  Mechanical writers over the corpus parser — a canonical emitter, torve rfc
  fmt, and transactional verbs for amendments, decisions, retirement and path
  relocation — so the structure-bearing edits stop being hand-typed.
schema_version: 1
---

# RFC 0025 — The corpus authoring surface

- **Scope:** How the mechanical parts of a corpus document get written, as
  opposed to validated. Covers a canonical emitter for the structures
  `src/torve/config/rfc_parse.py` already parses, `torve rfc fmt` as its
  surface, and transactional verbs for the edits that recur — `rfc amend`,
  `rfc add-decision`, `rfc retire`, `rfc relocate-paths` — each a
  parse-mutate-emit-check cycle that aborts on a red check. Excludes any
  change to storage (one `NNNN-slug.md` per document stands, D-A.18), any
  change to what `torve rfc check` validates, and any verb that chooses
  judgement — grades, decision text, status and implementation stay
  hand-written.
- **Related:** [`0016`](0016-corpus-conventions.md) §3–§7 · [`0007`](0007-planner-context.md) §3a ·
  `src/torve/config/rfc_parse.py` · `src/torve/cli/rfc.py` ·
  `skills/rfc-writer/`
- **Inherits:** D-A.2, D-A.3, D-A.4, D-A.5, D-A.6, D-A.17, D-A.18, D-16.1,
  D-16.2, D-16.4 from RFC 0016; D-7.12 from RFC 0007; D-11.1, D-11.11 from
  RFC 0011.

---

## 1. Summary

The corpus format is validated to the byte and written by hand, and the gap
between those two facts is where every recurring authoring defect lives. This
document adds the other half of D-7.12's "the package owns the format": a
canonical emitter beside the parser, `torve rfc fmt` to normalise a document's
structural surfaces without touching its prose, and one verb per mechanical
edit that history shows going wrong — appending an amendment, adding a
decision row, retiring an identifier, relocating Paths cells. Each verb
derives its identifiers the way `rfc new` already derives numbers, applies the
edit through the model, and refuses to leave a document the check would
redden. Storage does not change; a folder-per-document layout was considered
and rejected.

## 2. Motivation

The check catches structural mistakes; nothing prevents them. The evidence is
the repository's own history, and it is not subtle:

- **Amendment numbers collided three times** — the entries drafted as `A-8`,
  `A-13` and `A-16` each landed renumbered (`A-11`, `A-15`, `A-17`) because a
  number already taken elsewhere was chosen by hand. RFC numbers stopped
  colliding the day `torve rfc new` started deriving them (D-A.17); amendment
  numbers still rely on the author grepping.
- **Appending an amendment entry by anchored text edit** stranded a previous
  entry's `Also edits` line below the new entry — a defect class the corpus
  now checks for after the fact rather than making impossible.
- **The trap scalars keep biting**: a bare `on:` key parsing as boolean, a
  backticked `key: value` inside a plain scalar, a ` # ` sequence silently
  truncating a value. Each is a quoting decision an emitter makes correctly
  every time and a hand makes correctly most times.
- **Paths relocations are mechanical and hand-executed.** D-16.4 says Paths
  cells follow the code; every layout change since RFC 0015 has meant a hand
  sweep over decision tables, and the checker catching a missed cell is the
  backstop working, not the process.

None of these argues for a stricter format. The format is already exactly as
strict as validation can make it; the defects are all in the writing, which is
the half no tool owns.

## 3. Current state

Verified against the tree, not from memory:

- `src/torve/config/rfc_parse.py` parses everything this document needs:
  frontmatter, decision sections into `DecisionRow` values, phasing fences
  into `PhasingEntry` models, amendment headings, retired identifiers — and
  already computes `next_amendment` over the corpus (built for `rfc show`'s
  lookup surface). The model exists; only the emitter is missing.
- `src/torve/cli/rfc.py` carries `check`, `show`, `index`, `new`, `graph` and
  `health`. `new` is the only writer, and it derives its number as maximum
  plus one (D-A.17). `INDEX.md` is the only generated artefact (D-A.6).
- The corpus is uniform by habit: every accepted document carries the same
  section family, the exact five-column decision header (D-16.2), and an
  `## Amendments` container. The uniformity is enforced nowhere except where
  a specific check happens to overlap it.
- `torve rfc health` (RFC 0022) is a second reader over the same parser,
  which settles that the parse model is load-bearing enough to hang an
  emitter on.

## 4. Goals / Non-goals

**Goals**

- Make the recurring mechanical edits impossible to get wrong rather than
  merely detectable when wrong.
- Derive amendment and decision identifiers the way document numbers are
  already derived.
- Normalise the structural surfaces — frontmatter, tables, fences, amendment
  headings — to one canonical rendering, idempotently.
- Leave prose exactly alone.

**Non-goals**

- **Changing storage.** One `NNNN-slug.md` per document, no subdirectories
  (D-A.18). The folder-per-document alternative is examined and rejected in
  §5.5; this document is the answer to the fragility that motivated it.
- **Changing validation.** `torve rfc check` keeps its meaning; the verbs
  call it, they do not replace it.
- **Writing judgement.** No verb chooses a grade, drafts decision text,
  flips `status` or `implementation`, or composes amendment prose. The tool
  writes skeletons and identifiers; the author writes the words.
- **A markdown formatter.** `fmt` normalises the structures the parser owns
  and nothing else — it is not prettier, and body paragraphs pass through
  byte-for-byte.

## 5. Design

### 5.1 The canonical emitter

A companion to the parser — `src/torve/config/rfc_emit.py` — that renders the
parsed model back to text: frontmatter in fixed key order with trap scalars
quoted, decision tables at the exact validated header with aligned cells,
phasing fences re-serialised from their `PhasingEntry` models, amendment
headings in the dated form the check expects. Everything the parser does not
model — section prose, examples, header bullets — is carried through
verbatim, which makes the emitter a structure-preserving rewrite rather than
a renderer, and makes idempotence checkable: `emit(parse(emit(parse(text))))`
must equal `emit(parse(text))`, and a test pins it over the live corpus.

### 5.2 `torve rfc fmt`

`torve rfc fmt [NNNN]` — one document or the corpus — parses, emits, and
writes only when the result differs. `--check` reports without writing, for
CI symmetry with `index --check`. The verb refuses to write a document whose
parse already reports problems: formatting a broken document would launder
the breakage into a diff that looks deliberate.

### 5.3 The transactional verbs

Each verb is one cycle: parse the corpus, mutate the model, emit the touched
documents, regenerate the index, run the check in memory — and abort the
whole write on any problem, leaving the tree untouched. A verb that half-lands
is worse than no verb.

- **`torve rfc amend NNNN --title "..."`** — derives the next free amendment
  number corpus-wide via `next_amendment`, appends the dated
  `### A-nn — date — title` skeleton under the target's `## Amendments`
  container, and updates `amended_by` frontmatter. The author fills in the
  entry body and any row markers; the identifier, placement and frontmatter
  bookkeeping stop being theirs to get wrong.
- **`torve rfc add-decision NNNN`** — derives the next dotted identifier for
  the document, appends a row skeleton with a placeholder grade and empty
  Paths, and prints the identifier for the author to cite.
- **`torve rfc retire D-x.y`** — executes D-16.1 mechanically: removes the
  row, appends the identifier to the document's `retired:` list, and inserts
  a tombstone stub at the row's former position in the prose for the author
  to complete. Refuses when the identifier is cited anywhere the check would
  then fail to resolve.
- **`torve rfc relocate-paths OLD NEW`** — the D-16.4 sweep: rewrites every
  Paths cell carrying `OLD` to `NEW` across the corpus and prints the touched
  rows. Never touches decision text, so a decision whose *words* name the
  superseded location still gets its in-row marker by hand, as D-16.4
  specifies.

### 5.4 Typed example fences

The phasing fence set the pattern: a fenced block with a known info string is
parsed and validated, so the example cannot rot. This document extends it by
one: a fence tagged `yaml contract-example` validates against the task
contract schema, so a corpus document can carry a runnable contract example
that reddens when the schema moves. Other tags accrue one at a time, each
with a validator, never speculatively.

### 5.5 Alternatives considered

- **A folder per document** (`rfcs/0025/` with split sections and
  attachments). Its trade is authoring convenience against everything the
  single file carries: the citation shape (`rfc:` on every contract, Paths
  cells, commit trailers, `informed_by` links all name `rfcs/NNNN-slug.md`),
  one-file diff review, grep as the working motion, and per-file checks that
  would all need cross-file rewrites. It also amends three `LOCKED` rows
  (D-A.18, D-A.3, D-A.20) to buy a capability — attachments — that no
  document has needed in twenty-four tries. Rejected; reconsidered only when
  a document genuinely needs a non-markdown artefact, at which point the
  routing rule already points it to `pages/`.
- **Schema-validated section manifest now** (a `schema_version: 2` requiring
  a fixed section family per kind). Deferred, not rejected: the corpus is
  uniform by habit and the emitter makes the habit visible; hardening it into
  a check is cheap once `fmt` exists and premature before, since the manifest
  should be derived from what the emitter actually observes rather than
  legislated first. Left as D-25.9.
- **Fixing the format instead** (stricter markdown, or moving tables into
  frontmatter). Refused by D-A.3's standing reasoning: the rows carry prose
  people argue about in review, and splitting rows from rationale makes two
  sources of truth. The format is not the defect; the typing is.

## 6. Tests

Idempotence over the live corpus (§5.1's double-round-trip equality, run
against every committed document); a byte-equality case proving `fmt` on an
already-canonical document writes nothing; one case per verb proving the
transaction aborts whole on an injected check failure; a collision case
proving `rfc amend` under a corpus already carrying `A-nn` derives `A-nn+1`;
a retire case proving refusal while a citation still resolves through the
row. The trap scalars get a table-driven case each: `on:`, backticked
`key: value`, embedded ` # `.

## 7. Docs

The `rfc-writer` skill sheds its hand-instructions for the mechanical edits
and points at the verbs instead — the skill teaches judgement (grading,
phasing, prose) and the tool owns structure. One line in the skill's workflow
reference per verb; no new page.

## 8. Out of scope

- **A drafting run for corpus documents.** RFC 0020's pattern applied to RFC
  authoring — an agent iterating `new`/`fmt`/`check` in a sandbox, a human
  adopting — is the natural next consumer of this surface and is deliberately
  not designed here. It needs this document's verbs to exist first, and it
  raises adoption questions (what is the human signing when the artefact is a
  specification?) that deserve their own document.
- **Editing accepted decision text.** There is no verb for it because there
  is no legal edit: accepted rows change by amendment (D-A.5) or retirement
  (D-16.1), both of which have verbs.
- **INDEX changes.** The index stays generated and checked exactly as it is;
  the verbs regenerate it as part of their transaction, which is a call site,
  not a redesign.

## 9. Risks

- **The emitter fights the author's hand.** A `fmt` that rewrites more than
  the structural surfaces turns every corpus diff into noise and teaches
  people to skip it. Mitigation is D-25.1: prose passes through
  byte-for-byte, and the idempotence test doubles as a scope fence — any
  normalisation beyond the modelled structures shows up as a corpus-wide diff
  in review.
- **Verbs invite scripting judgement.** A verb that writes a skeleton makes
  it tempting to fill the skeleton by machine too. The non-goal is written
  now and D-25.3 grades it `LOCKED`, because the corpus's value is that a
  human meant every graded row.
- **`src/torve/cli/rfc.py` is already the repository's least-tested
  hotspot.** Adding four verbs there without moving logic down would deepen
  that. Mitigation: the verbs are thin — parse, mutate, emit and check all
  live in `config/`, and the CLI layer stays argument handling, matching the
  house layering.

## 10. Unresolved questions

- Whether `fmt` should also normalise section numbering (renumbering
  contiguous `## N.` headings after an insertion) or only validate it.
  Renumbering rewrites `§N` cross-references corpus-wide, which is either the
  feature or the hazard; execution proposes with evidence from the first
  documents it touches (D-25.8).
- Whether the amendment verb should also place cross-document row markers
  (the italic *amended by A-nn* notes) or leave them to the author. The
  markers carry judgement about which rows a reader must be warned at;
  starting hand-written and promoting later is the safe order.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-25.1 | `LOCKED` | The emitter and `fmt` normalise only the structures the parser models — frontmatter, decision tables, phasing fences, amendment headings; body prose passes through byte-for-byte | `src/torve/config/rfc_emit.py` `src/torve/cli/rfc.py` | A formatter that touches prose turns every corpus diff into noise and gets skipped, which is worse than no formatter |
| D-25.2 | `LOCKED` | Every authoring verb is one transaction — parse, mutate, emit, regenerate the index, check — and a red check aborts the whole write, leaving the tree untouched | `src/torve/cli/rfc.py` `src/torve/config/rfc_emit.py` | A half-landed structural edit is exactly the defect class this document exists to end |
| D-25.3 | `LOCKED` | Verbs write structure and identifiers, never judgement: no verb chooses a grade, drafts decision or amendment prose, or flips `status`/`implementation` | `src/torve/cli/rfc.py` | The corpus's value is that a human meant every graded row; a tool that fills skeletons is a planner nobody appointed |
| D-25.4 | `LOCKED` | Amendment numbers are derived at write time as maximum plus one corpus-wide, through the parser's `next_amendment`; the verb never accepts a chosen number | `src/torve/config/rfc_parse.py` `src/torve/cli/rfc.py` | D-A.17's doctrine applied to the identifier family that has collided three times by hand |
| D-25.5 | `ASSUMED` | Storage stays one `NNNN-slug.md` per document; the authoring surface is the answer to structural fragility, and D-A.18 stands unamended | `rfcs/**` | The folder alternative trades every citation shape and the one-file diff for attachments nothing needs (§5.5) |
| D-25.6 | `ASSUMED` | `rfc retire` executes D-16.1 whole — row removal, `retired:` frontmatter, tombstone stub — and refuses while any citation would stop resolving | `src/torve/cli/rfc.py` `src/torve/config/rfc_emit.py` | A partial retirement is the exact state A-20 was written to prevent |
| D-25.7 | `ASSUMED` | `rfc relocate-paths` sweeps Paths cells mechanically and never touches decision text; in-row markers stay hand-written per D-16.4 | `src/torve/cli/rfc.py` | The cell is mechanical and the marker is judgement; one verb doing both would write words nobody meant |
| D-25.8 | `OPEN` | Whether `fmt` renumbers sections or only validates numbering; execution proposes with evidence from the first documents touched | `src/torve/config/rfc_emit.py` | Renumbering rewrites `§N` cross-references corpus-wide — either the feature or the hazard, and the corpus should say which after seeing one real case |
| D-25.9 | `OPEN` | Whether a `schema_version: 2` section manifest per kind becomes checkable once the emitter exists; deferred until `fmt` has run over the corpus | `src/torve/config/rfc_parse.py` | The manifest should be derived from what the emitter observes, not legislated ahead of it |
| D-25.10 | `ASSUMED` | Typed example fences extend the phasing pattern one validator at a time, starting with `contract-example` against the task schema; no speculative tags | `src/torve/config/rfc_parse.py` | A validated example cannot rot; an unvalidated tag is decoration with syntax highlighting |

## Phasing

```yaml
- phase: 1
  title: emitter-and-fmt
  intent: |
    The canonical emitter beside the parser and torve rfc fmt as its
    surface: frontmatter in fixed key order with trap scalars quoted,
    decision tables at the validated header, phasing fences re-serialised,
    amendment headings in dated form — prose byte-for-byte. Idempotence
    pinned over the live corpus; fmt refuses documents whose parse already
    reports problems; --check reports without writing.
  scope:
    - "src/torve/config/**"
    - "src/torve/cli/**"
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
  title: transactional-verbs
  intent: |
    rfc amend, rfc add-decision, rfc retire and rfc relocate-paths as
    parse-mutate-emit-check transactions that abort whole on a red check:
    derived amendment and decision identifiers, D-16.1 retirement executed
    in one motion, mechanical Paths sweeps. Verbs write structure and
    identifiers only; grades, prose and status stay hand-written.
  scope:
    - "src/torve/config/**"
    - "src/torve/cli/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: [1]
- phase: 3
  title: typed-example-fences
  intent: |
    The contract-example fence validated against the task contract schema
    as part of torve rfc check, following the phasing-fence pattern, so a
    corpus document can carry a runnable contract example that reddens
    when the schema moves. The rfc-writer skill's template gains the fence;
    no other tags ship.
  scope:
    - "src/torve/config/**"
    - "skills/rfc-writer/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
  depends_on: [1]
```

## 12. Exit criteria

- `torve rfc fmt --check` green over the whole corpus in CI, with the
  idempotence property pinned by test.
- One real amendment landed through `rfc amend` with its number derived, not
  chosen — and no amendment renumbering incident since the verb shipped.
- One retirement executed through `rfc retire` leaving the check green in the
  same transaction.
- A layout change's Paths sweep executed by `rfc relocate-paths` with zero
  hand-edited cells.

## Amendments
