---
name: corpus-bootstrap
description: Drafting a brownfield adopter's standing-decisions baseline from the survey report — mostly ASSUMED, LOCKED only on defended boundaries, paths on every row, no phasing, one draft document for a human to edit, commit and accept.
roles: [author]
gate: rfc-valid
---

> **Specialisation.** Derived from `agent-skills/corpus-bootstrap`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Corpus Bootstrap — the baseline draft

This skill is the second half of first contact with a repository that has no
corpus. The engine's `torve survey` did the first half: it replayed the last
landings through the gate battery and produced a read-only report of what
would have fired and what stayed silent for want of a corpus. This skill —
run in a supervised session, **never by the engine** — reads that report and
the repository, and drafts the standing-decisions document that turns the
report's silence into governance.

The engine never reads this skill's output while it is a draft. It only ever
reads the committed, human-accepted document, through the standing
inheritance layer: from acceptance, every row whose declared paths intersect
a contract's scope rides into that contract at mint time, and nothing else
(D-31.2). There is no engine surface for the baseline at all — which is the
point: a skill can be supervised, edited and refused in a diff; an engine
surface would drift.

## Inputs and output

- **Input:** the survey report (the JSON `torve survey --format json`
  writes) and the repository working tree. The report is the evidence base;
  the tree is where the boundaries live and where their globs are read from.
- **Output:** one draft document in this corpus's format — frontmatter, an
  H1, a Decisions section holding the decision table, no Phasing section —
  written so a human can edit, commit and accept it in one review. The draft
  is `status: draft`; acceptance is the human flipping it to `accepted` in
  the same commit that signs it.

## The extraction doctrine

Four rules, in order of how much damage breaking them does:

1. **Mostly ASSUMED.** A grade is a human judgement about reversal cost, and
   a stranger's repository has no corpus — nobody has made that judgement.
   Default every candidate row to `ASSUMED` and let the human's acceptance
   edits do the real grading. An extraction that comes back mostly `LOCKED`
   poisons the entry: the acceptance review reads it as a takeover and
   refuses the whole document.

2. **LOCKED only on defended boundaries.** A `LOCKED` row claims reopening is
   expensive. The only evidence an extraction can cite for that is the
   repository's own history showing the boundary being **defended**: a
   firing followed by a correction landing, or a consistent clean record
   with the boundary visible in the tree (the layout is deliberate because
   the history holds it). A single firing in a short window is
   `ASSUMED`-grade evidence — the boundary exists as a check, but nobody has
   been shown defending it.

3. **Paths on every row.** A row without declared paths is never standing —
   the inheritance layer skips it and the row governs nothing. Every row
   names the globs it governs, read from the actual tree, not invented.

4. **No phasing.** The baseline is a set of standing rows, not work to
   sequence. A Phasing section would make the engine mint tasks from a
   baseline document — exactly the engine surface D-31.2 refuses. The
   baseline answers "how is this repository governed"; sequencing is another
   document's job, and the draft has no `## Phasing` section.

## Reading the survey

The report is the evidence base; read it before the tree.

- **Fired** (`outcome` in `fail`/`error`/`bypassed`) — a boundary was
  crossed in history. Name the landing, then go to the tree: was the
  crossing corrected later? That correction is the defended-boundary
  evidence rule 2 wants.
- **Clean** (`pass`/`flaky`) — measured nothing wrong. No row follows from a
  clean gate alone; the boundary it measures is already being held.
- **Skipped with `no_corpus: true`** — the gate did not run because no task
  contract existed: the corpus's absence made visible. These are the rows
  the baseline exists to write: a standing row that gives the gate
  something to measure once work is minted.
- **Skipped with `no_corpus: false`** — the runner's fail-fast "not run"
  after an earlier blocking gate fired, or acceptance's structural no-commands
  skip. Not a corpus gap; no row follows.
- **The summary's `corpus_adds`** — the gates that never measured a single
  landing and whose silence is the no-task skip. The report already names
  them; every one of them becomes a candidate `ASSUMED` row.

## The shape this skill records (D-31.6)

The baseline is **one document per adoption**, named
`NNNN-standing-decisions.md` in the corpus directory (`rfcs/` by default),
with `NNNN` the next free corpus number and the title "Standing decisions".
Rationale: a first adopter has no areas taxonomy to split by, a single
document keeps the acceptance ceremony to one commit, and the inheritance
layer already selects rows by path intersection — several-by-area buys
nothing until a corpus has enough rows to need it. The first real adoption
settles this shape; this paragraph is the skill recording the shape it
chose, and the recording is updated to what the adoption chose.

## The hand-off

The extraction produces a **draft**. The human then:

1. Edits — grading, wording, the paths, anything the history suggests.
2. Commits — the draft becomes a committed document.
3. Accepts — `status: accepted`, the commit that makes the rows stand.

Nothing the engine reads exists before that commit, and nothing the engine
reads after it is this skill's doing: standing inheritance copies grade and
paths at mint time and the battery convicts only work minted after — a
ratchet, never a purge (D-31.3).

## References

- `references/doctrine.md` — the extraction doctrine in detail: grading
  evidence, path discipline, what the survey's four outcomes mean
- `references/workflow.md` — the supervised-session procedure, inputs to
  accepted document
- `references/draft-template.md` — the corpus-format skeleton the draft is
  written from
- `fixtures/survey-report.json` — a sample survey report (input)
- `fixtures/0001-standing-decisions.md` — the checkable output shape: the draft
  the doctrine produces from the sample report
