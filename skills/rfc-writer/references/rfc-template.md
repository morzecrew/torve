# Document template

`torve rfc new "Title"` writes the smallest document that checks — the
header keys, one summary section, empty lists — under the next number.
What follows is the filled shape to grow it into, scaled to the design's
weight: a small design-lock document keeps the header, a scope section, a
design section, a non-goals section and the rows. Replace `<placeholders>`;
delete what the document does not need. Every key is validated against
`rfcs/schema/document.json`, which the first line names.

```yaml
# yaml-language-server: $schema=schema/document.json
id: 'NNNN'
title: <Title>
kind: design            # or convention
status: draft           # accepted once reviewed and depended on; superseded only with superseded_by
implementation: none    # none | partial | complete | abandoned — the author's judgement, checked against the record
depends_on: []          # documents whose rows this one inherits from; must be accepted
informed_by: []         # documents read, not inherited from
supersedes: []
superseded_by: null
amended_by: []          # written by `torve rfc amend`
retired: []             # identifiers this document once defined; never reused
owner: <name>
description: >-
  <One sentence, ~200 chars: which design this is, not what it decided.
  `torve rfc list` shows it; it is how a reader picks the document to open.>
schema_version: 2
sections:
  - key: scope
    heading: Scope
    md: |
      <One dense paragraph: what this document covers and what it deliberately
      does not — modules touched, contract changes or "no contract changes",
      the boundary of the blast radius. The paragraph a reader uses to decide
      whether to read the rest. Related code and documents by relative path
      and number; where the design was debated, if anywhere.>
  - key: summary
    heading: 1. Summary
    md: |
      <What ships, in a few sentences. Write it last.>
  - key: motivation
    heading: 2. Motivation
    md: |
      <The problem, with evidence: measured numbers, real failure cases, the
      code paths that hurt. A motivation that cites nothing concrete is a
      reason to question the document.>
  - key: current-state
    heading: 3. Current state
    md: |
      <What exists today, verified against the code — not from memory. Name
      the files, ports and schemas involved; verified surprises belong here.>
  - key: goals
    heading: 4. Goals / Non-goals
    md: |
      Goals: <...>

      Non-goals: <explicit exclusions with a reason each — "not X, that is
      Y's job">.
  - key: design
    heading: 5. Design
    md: |
      <The core. One subsection per workstream (### 5.1, ### 5.2 …) inside
      this body, pinned with real artifacts — signatures, schemas, config
      shapes — in fenced blocks; prose alone drifts. State failure semantics:
      what raises, what is refused, what fails closed. A rejected alternative
      goes in `alternatives` below, with the trade-off that lost.>
  - key: tests
    heading: 6. Tests
    md: |
      <How the design is verified; what is explicitly not tested and why.>
  - key: out-of-scope
    heading: 7. Out of scope
    md: |
      <Named and reasoned: why each item is excluded and what would change that.>
  - key: risks
    heading: 8. Risks
    md: |
      <Honest failure modes, including the document being misread.>
decisions:
  - id: D-NNNN.1
    grade: LOCKED       # LOCKED | ASSUMED | OPEN — most rows are ASSUMED
    text: <the rule, as one sentence a contract can carry>
    paths: [src/example/**]     # every row that governs an area declares it; LOCKED rows must
    consequence: <what this constrains later, non-obviously>
    rationale: <why, in one or two sentences>
    cites: []           # identifiers this row descends from: D-x.y, I-x.y, A-n, a document number
    check: pytest tests/test_example.py   # optional: a command whose exit code judges the row
  - id: D-NNNN.2
    grade: OPEN
    text: <a decision deliberately delegated to implementation — still a row, never an absence>
invariants:
  - id: I-NNNN.1
    statement: <a rule that holds over these paths>
    paths: [src/example/**]
    check: uv run lint-imports
alternatives:
  - option: <what else could have been built>
    rejected_because: <the trade-off that lost, so it stays rejected>
questions:
  - id: Q-NNNN.1
    text: <what must be settled, and by whom>
    status: open
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
  rfc: rfcs/NNNN-title.yaml
  role: implement
  intent: One paragraph: what changes and why.
  scope:
    allow: [src/example/**, tests/test_example.py]
  acceptance: [uv run pytest tests/test_example.py]
  decisions:
    - id: D-NNNN.1
      grade: LOCKED
      text: <the rule>
      paths: [src/example/**]
  tier: executor
```

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
  reverses, through `torve rfc amend`, never an edit. What execution found
  already lives in the task's log and reaches the document as an appended
  row citing its entry; restating the log's narrative here guarantees the two
  disagree later.
- **No comments but the first line.** A comment is meaning outside the
  model; `torve rfc check` refuses it. The comments in this template are for
  reading the template, not for a document.
- **`fingerprint` is the tool's.** Never write it; `amend` and `fix` stamp it,
  and `check` reads a hand-changed grade or paths against it.
