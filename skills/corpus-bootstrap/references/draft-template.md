# Draft template

The shape every baseline draft takes — one document per adoption, named
`NNNN-standing-decisions.md` in the corpus directory, `NNNN` the next free
corpus number. Copy the skeleton, replace `<placeholders>`, delete the
guidance blockquotes. The Decisions table is the document: every row carries
paths, grades follow the doctrine (mostly `ASSUMED`, `LOCKED` only on
defended-boundary evidence), and there is no Phasing section. The fixture
(`fixtures/0001-standing-decisions.md`) is this shape filled from the sample
survey report — check your draft against it.

---

```markdown
---
id: "NNNN"
title: Standing decisions
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: <the adopter's owner>
description: >-
  <One sentence: the brownfield baseline for this repository — the standing
  rows extracted from the survey of its history, for a human to edit, commit
  and accept.>
schema_version: 1
---

# RFC NNNN — Standing decisions

- **Scope:** <One dense paragraph: this is the baseline — the standing rows
  the survey implies, extracted from the survey report and the repository's
  history. It governs work minted after acceptance, never the tree as it
  stands. No phasing: this is not a plan.>
- **Related:** <The survey report it was extracted from; the paths it cites.>
- **Origin:** <Extracted by the corpus-bootstrap skill from the survey of the
  last N landings on <branch>.>

---

## 1. Summary

<What the survey found, in a few sentences: which boundaries fired, which
gates never measured a single landing for want of a corpus, and what the
rows below therefore declare. Write it last, once the table has settled.>

## 2. The survey findings

<Per finding, the evidence the rows cite: the landing that fired and the
files it named, the correction that defended a boundary (if any), the gates
that never measured anything. This section is where the human checks the
extraction's reading of history.>

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-NNNN.1 | `ASSUMED` | <The standing rule> | `<glob>` `<glob>` | <What breaks or changes if the row is crossed; the evidence the row stands on> |
```

---

Fill the table with one row per finding, in the order the doctrine
extracts them: the defended boundary first (the `LOCKED` row, when there is
one), then the fired-but-uncorrected boundaries, then the corpus-gap rows
named by the survey's `corpus_adds`.

Every cell of the Paths column carries at least one glob, read from the
actual tree — a pathless row is never standing. The identifiers are the
document's own dotted family (`D-NNNN.n`, `D-NNNN.n+1`, …), unique
corpus-wide.
