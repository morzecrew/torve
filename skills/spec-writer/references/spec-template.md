# Document template

`torve spec new "Title"` writes the smallest document that checks — the
directory `S-NNNN/` under the next number, `document.yaml` with the header
keys and a summary, `decisions.yaml` with an empty list. What follows is
the filled shape to grow it into. The anatomy is typed (S-0058/D-4): an
accepted design says its `summary`, `motivation`, `current_state`,
`goals`, `non_goals`, `tests` and `risks` and designs at least one thing;
an accepted convention says its summary; `docs` and `out_of_scope` are
optional; extras stop at eight. Replace `<placeholders>`;
delete what the document does not need. Every key is validated against the
schema its file's first line names under `.torve/schemas/`, written by
`torve init`.

## `document.yaml` — yours

```yaml
# yaml-language-server: $schema=../../schemas/document.json
id: S-NNNN
title: <Title>
kind: design            # or convention
status: draft           # accepted once reviewed and depended on; superseded only with superseded_by
implementation: none    # none | partial | complete | abandoned — the author's judgement, checked against the record
depends_on: []          # documents whose rows this one inherits from; must be accepted
informed_by: []         # documents read, not inherited from
supersedes: []
superseded_by: null
owner: <name>
description: >-
  <One sentence, ~200 chars: which design this is, not what it decided.
  `torve spec list` shows it; it is how a reader picks the document to open.>
schema_version: 3
summary: |
  <What ships, in a few sentences; the first sentence routes — it is what
  `torve spec list` shows, so it says which design this is. Write it last.>
motivation: |
  <The problem, with evidence: measured numbers, real failure cases, the
  code paths that hurt. A motivation that cites nothing concrete is a
  reason to question the document.>
current_state: |
  <What exists today, verified against the code — not from memory. Name
  the files, ports and schemas involved; verified surprises belong here.>
goals: |
  <What this document achieves, explicitly.>
non_goals: |
  <Explicit exclusions with a reason each — "not X, that is Y's job".>
design:
  - key: the-first-workstream
    md: |
      <The design, one entry per workstream, in order — the heading is the
      key and the number is the position. Pin each with real artifacts —
      signatures, schemas, config shapes — in fenced blocks; prose alone
      drifts. State failure semantics: what raises, what is refused, what
      fails closed. A rejected alternative goes in `alternatives` below,
      with the trade-off that lost; never restate a typed list here as a
      fence or a table.>
tests: |
  <How the design is verified; what is explicitly not tested and why.>
docs: |
  <Optional: what documentation ships with it.>
out_of_scope: |
  <Optional, named and reasoned: why each item is excluded and what would change that.>
risks: |
  <Honest failure modes, including the document being misread.>
sections:                     # extras, at most 8, keys unique document-wide, never a typed name
  - key: exit-criteria
    md: |
      <Anything the anatomy has no key for.>
alternatives:
  - option: <what else could have been built>
    rejected_because: <the trade-off that lost, so it stays rejected>
questions:
  - id: Q-1
    text: <what must be settled, and by whom>
    status: open
## `phasing.yaml` — yours, read by the planner

```yaml
# yaml-language-server: $schema=../../schemas/phasing.json
phasing:
  - phase: 1
    title: <a short unit name>
    intent: >-
      One paragraph: what changes and why. This is the contract's intent.
    scope: [src/example/**, tests/test_example.py]   # a module and its existing test file
    acceptance: [uv run pytest tests/test_example.py]
    depends_on: []
    character: structural   # optional: structural | routine
  - phase: 2
    title: <the follow-up>
    intent: >-
      What this phase changes, and why it waits for phase 1.
    scope: [src/other/**]
    acceptance: [uv run pytest tests/test_other.py]
    depends_on: [1]
contract_example:             # optional; validated against the live task schema
  id: T-0142
  spec: S-NNNN
  role: implement
  intent: One paragraph: what changes and why.
  scope:
    allow: [src/example/**, tests/test_example.py]
  acceptance: [uv run pytest tests/test_example.py]
  decisions:
    - id: S-NNNN/D-1
      grade: LOCKED
      text: <the rule>
      paths: [src/example/**]
  tier: executor
```
```

## `decisions.yaml` — yours, stamped by the tool

```yaml
# yaml-language-server: $schema=../../schemas/decisions.json
decisions:
  - id: D-1              # local: the document is the namespace; cited as S-NNNN/D-1
    grade: LOCKED       # LOCKED | ASSUMED | OPEN — most rows are ASSUMED
    text: <the rule, as one sentence a contract can carry>
    paths: [src/example/**]     # every row that governs an area declares it; LOCKED rows must
    consequence: <what this constrains later, non-obviously>
    rationale: <why, in one or two sentences>
    cites: []           # what this row descends from: D-n of this document, or S-NNNN/D-n, S-NNNN/A-n, S-NNNN
    check: pytest tests/test_example.py   # optional: a command whose exit code judges the row
  - id: D-2
    grade: OPEN
    text: <a decision deliberately delegated to implementation — still a row, never an absence>
invariants:
  - id: I-1
    statement: <a rule that holds over these paths>
    paths: [src/example/**]
    check: uv run lint-imports
retired: []             # identifiers this document once defined; never reused
```

`amendments.yaml` appears with the first `torve spec amend` and
`execution/` with the first landing, one file per landing; neither is
written by hand. Every time the engine writes is the instant
`YYYY-MM-DDTHH:MM:SSZ`.

## Notes on filling it in

- **The corpus outranks this template.** If the project's documents already
  use a different section set, match it — section keys are what a log
  cites, and `§NN` cross-references in prose must stay unambiguous.
- **Status lives in the header.** `draft` until reviewed and depended on,
  then `accepted`; `superseded` only alongside `superseded_by`. Nuance about
  what shipped goes in a section, not into the status value — an annotated
  true status beats a clean false one, and the queryable field stays clean.
- **Amendments over rewrites.** Once a document leaves draft, the rows are
  append-only — a reversed decision gets a new row citing the row it
  reverses, through `torve spec amend`, never an edit. What execution found
  already lives in the task's log and reaches the document as an appended
  row citing its entry; restating the log's narrative here guarantees the two
  disagree later.
- **No comments but the first line.** A comment is meaning outside the
  model; `torve spec check` refuses it. The comments in this template are for
  reading the template, not for a document.
- **`fingerprint` is the tool's.** Never write it; `amend` and `fix` stamp it,
  and `check` reads a hand-changed grade or paths against it.
