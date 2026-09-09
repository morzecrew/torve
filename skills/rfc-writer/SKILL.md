---
name: rfc-writer
description: Writing a specification document as an executable input to torve plan — one YAML file in the Document model's shape, graded rows with paths on every one, mintable phasing with non-overlapping scope, and identifiers the divergence logs cite forever.
roles: [author]
gate: rfc-valid
---

> **Specialisation.** Derived from `agent-skills/rfc-writer`, specialised for
> artefacts that Torve reads. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Specification authoring for Torve

A document here is not only a design record — it is an **executable input
to `torve plan`**: its decision rows mint into task contracts with inherited
grades and declared areas, its phasing mints into tasks with scopes, and its
identifiers are what every divergence log cites. Write it as a document a
machine derives work from and a human can refuse in a diff.

The document is one YAML file, `rfcs/NNNN-slug.yaml`, in the shape of the
engine's own `Document` model (RFC 0056, D-56.1) — never markdown. Its
first line names `rfcs/schema/document.json`, the model's JSON Schema, so
an editor with a YAML language server validates every key as it is typed;
`torve rfc check` is the gate and reads the same model. Prose lives in
`sections[].md`, a markdown string nothing parses. Everything else is a
typed list, and an unknown key is refused by list, entry and field name —
never ignored. The mechanical half — numbering, filenames, the serializer,
the checks — belongs to the package (RFC 0007 §3a, D-7.12); `torve rfc`
(`new` / `check` / `list` / `show` / `amend` / `fix` / `archive` / `render`)
applies it. Anatomy, prose style and workflows live in `references/`.

## Decision grades

| Grade | Meaning | What it asks of an executor |
|---|---|---|
| `LOCKED` | Settled; reopening is expensive or reaches beyond this document. | Halt on conflict and surface it. |
| `ASSUMED` | Believed correct, not load-bearing. | Depart if building proves it wrong; log it. |
| `OPEN` | Deliberately delegated to implementation. | Decide it; log the decision and rationale. |

**Grade honestly — most rows are `ASSUMED`.** `LOCKED` is not emphasis; routine
halts get waved through, which costs the one signal the grade sends. `OPEN` is
not the same as leaving a row out: an absent row gets answered by whoever
arrives first, invisibly.

## What makes the rows executable

1. **`paths` on every decision row** — the single most important rule. A
   decision that governs an area must declare it, or the silence check in
   `decisions-reported` skips that decision and the strongest anti-drift
   guarantee quietly does nothing. `LOCKED` rows **must** carry paths; the
   check reddens without them.

   ```yaml
   decisions:
     - id: D-3.1
       grade: LOCKED
       text: Sessions in Redis, not the database
       paths: [packages/api/session/**]
       consequence: a session survives an API restart; a Redis outage logs everyone out
       rationale: the database is the write path we protect first
       cites: [D-1.4]
       check: pytest tests/test_session.py
   ```

   `check` is a command whose exit code judges the row; the runner appends
   it to the battery as a `decision:<id>` gate at shadow, and a row with a
   check owes no divergence entry (RFC 0054). `check_state: blocking` needs a
   `check_twin` — the test that proves the check can fail.

2. **Phasing must be mintable.** A phase is an entry in the `phasing` list —
   number, title, one-paragraph intent, the file globs it will touch, its
   acceptance commands, the phases it waits on — not prose about sequence.
   If `torve plan` cannot derive a task from an entry, the entry was written
   wrong.

3. **Non-overlapping scope within a phase.** Entries with the same phase
   number must not share globs — overlapping tasks cannot run in parallel and
   the plan silently serialises. Say it while writing, when it is free to fix.

3a. **A decision's paths must fit inside one phase's scope.** A row whose
   paths span two phases mints a contract that contradicts itself: the
   executor inherits a decision it cannot satisfy without leaving
   scope.allow. Split the row into per-surface decisions at authoring.

4. **Decision identifiers are permanent.** Divergence logs cite `D-3.1`
   forever; renumbering orphans every entry that cites it. Append new rows;
   never renumber, never reuse an identifier — a retired one is recorded in
   `retired` and stays taken.

5. **Amendments, not edits.** An accepted document is amended — an entry in
   its `amendments` list (globally numbered `A-n`, charter D-A.5) carrying the
   typed diff of what changed — never rewritten in place: the logs and
   telemetry that cite it assume the text they cited still exists.
   `amended_by` is checked against the entries.

## The typed lists beside the rows

| List | Entry | Holds |
|---|---|---|
| `decisions` | `id`, `grade`, `text`, `paths`, `consequence`, `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by`, `fingerprint` | the rows; `fingerprint` is the tool's stamp, never written by hand |
| `invariants` | `id` (`I-NNNN.n`), `statement`, `paths`, `check` | a rule with the command that proves it |
| `alternatives` | `option`, `rejected_because`, `cites` | the negative space every executor re-proposes when nobody wrote the option was closed |
| `questions` | `id` (`Q-NNNN.n`), `text`, `status` open/settled, `settled_by` | what the design leaves for someone to settle |
| `phasing` | `phase`, `title`, `intent`, `scope`, `acceptance`, `depends_on`, `tier_variant`, `character` | the mintable units |
| `contract_example` | a task contract | optional: a runnable demonstration of what the rows and a phase mint into, validated against the live task schema |
| `amendments` | `id`, `at`, `title`, `changes`, `md` | written by `torve rfc amend`; the words are yours |
| `sections` | `key`, `heading`, `md` | the prose, in order; markdown inside `md` is welcome and invisible to every check |

`cites` resolves over the corpus and the archive — a `D-x.y`, an `I-`, a
`Q-`, an `A-n`, a document number. Type what was already a list; never
fragment an argument into fields.

## Rows change through the tool, never by hand

A row's grade or paths change only through `torve rfc amend NUMBER
--title T --row D-x.y --grade G` (or `--path GLOB` repeated, `--text
"…"`, `--retire --reason R`). The verb appends the amendment with the typed
diff and the prior value, re-stamps the row's `fingerprint`, and writes the
document through the one serializer. A grade or paths edited by hand
afterwards is a `torve rfc check` problem: a row with no history. A text
edited by hand is editorial drift — a warning — and `torve rfc fix D-x.y
"…"` re-stamps it with the before and after recorded under `editorial`, no
amendment number. A typo costs one command; a rewording that changes the
rule's meaning is an amendment.

A document may carry no comment but its first line: a comment is meaning
outside the model, and a row that needs a note needs a `rationale`. A
hand-authored document is otherwise legal as written; `torve rfc fmt`
reports what differs from the serializer's form and writes nothing.

`torve rfc check` also names rows whose declared paths match nothing in
the tree; `torve rfc check --fix-rot` retires them through an amendment
per document with the reason recorded. Coverage per path — governed,
ungoverned, retired — is `torve spec paths <path>`; ungoverned is the
ratchet's frontier, never a finding.

## The archive

A document that no longer stands leaves the corpus path through `torve
rfc archive NUMBER --superseded-by NNNN` into `archive/rfcs/` beside it,
filename and identifiers kept, `status: superseded`. Nothing inherits from
the archive; everything in it still resolves — `torve rfc show D-44.12`
answers marked archived, a citation into it checks clean, and the record
holds every archived row as retired with the archive named. Document and
amendment numbers derive over corpus and archive together, so a number is
never reused.

## Reconciling what execution learned

The executor **proposes** rows in its task log (the `proposal:` field of a
divergence entry) with the evidence that produced them; the author appends
them with `torve rfc add-decision` and grades them with `torve rfc amend
--row`, citing the task in the row's `cites` or `rationale`. The rows are
append-only — a retired row leaves its `retired` entry and the amendment
that retired it. Without the link a row reads as something the author
thought of, losing the one fact that makes it credible: it was forced by
contact with the code.

The `description` **routes** (which document to open — one sentence, ~200
chars, 300 ceiling); it never summarises and never records history. `torve
rfc list` renders it; nobody maintains an index.

## References

- `references/rfc-template.md` — the document skeleton with per-key guidance
- `references/authoring.md` — the sections and how the prose behaves
- `references/workflows.md` — create, update, maintain, initialize
