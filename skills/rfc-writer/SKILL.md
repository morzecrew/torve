---
name: rfc-writer
description: Writing an RFC as an executable input to torve plan — graded decision tables with Paths on every row, mintable phasing with non-overlapping scope, and identifiers the divergence logs cite forever.
roles: [author]
gate: rfc-index
---

> **Specialisation.** Derived from `agent-skills/rfc-writer`, specialised for
> artefacts that Torve parses. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# RFC Authoring for Torve

An RFC here is not only a design record — it is an **executable input to
`torve plan`**: its decision table mints into task contracts with inherited
grades and declared areas, its phasing mints into tasks with scopes, and its
identifiers are what every divergence log cites. Write it as a document a
machine derives work from and a human can refuse in a diff.

The mechanical half belongs to the package (RFC 0007 §3a, D-7.12): location,
numbering, filenames, frontmatter, decision-table shape and the checks below
are applied by `torve rfc` (`check` / `index` / `new "Title"` / `graph`), and
its `check` is the gate. Structured facts — id, status, dependencies, amendments,
owner, the routing description — live in YAML frontmatter (charter D-A.2);
`INDEX.md` is generated from it and CI-checked like a lockfile (D-A.6), never
edited by hand. Anatomy, prose style and workflows live in `references/`.

## Decision grades

| Grade | Meaning | What it asks of an executor |
|---|---|---|
| `LOCKED` | Settled; reopening is expensive or reaches beyond this RFC. | Halt on conflict and surface it. |
| `ASSUMED` | Believed correct, not load-bearing. | Depart if building proves it wrong; log it. |
| `OPEN` | Deliberately delegated to implementation. | Decide it; log the decision and rationale. |

**Grade honestly — most rows are `ASSUMED`.** `LOCKED` is not emphasis; routine
halts get waved through, which costs the one signal the grade sends. `OPEN` is
not the same as leaving a row out: an absent row gets answered by whoever
arrives first, invisibly.

## What makes the table executable

1. **`Paths` on every decision row** — the single most important rule. A
   decision that governs an area must declare it, or the silence check in
   `decisions-reported` skips that decision and the strongest anti-drift
   guarantee quietly does nothing. `LOCKED` rows **must** carry paths; the
   check reddens without them.

   ```markdown
   | # | Grade | Decision | Paths | Consequence |
   | --- | --- | --- | --- | --- |
   | D-3 | `LOCKED` | Sessions in Redis, not the database | `packages/api/session/**` | … |
   ```

2. **Phasing must be mintable.** A phase is a list of units — each with a
   name, its dependencies, and the file boundaries it will touch — not prose
   about sequence. If `torve plan` cannot derive a task from a phase entry
   without a human rewriting it, the phase was written wrong.

3. **Non-overlapping scope within a phase.** Tasks in one phase must not share
   `allow` globs — overlapping tasks cannot run in parallel and the plan
   silently serialises. Say it while writing, when it is free to fix.

3a. **A decision's Paths must fit inside one phase's scope.** A row whose
   paths span two phases mints a contract that contradicts itself: the
   executor inherits a decision it cannot satisfy without leaving
   scope.allow. Split the row into per-surface decisions at authoring
   (A-59: T-0099 hit exactly this and had to read D-22.6 down to its
   first clause).

4. **Decision identifiers are permanent.** Divergence logs cite `D-3` forever;
   renumbering orphans every entry that cites it. Append new rows; never
   renumber, never reuse an identifier.

5. **Amendments, not edits.** An accepted RFC is amended — an entry in its own
   `## Amendments` section (globally numbered `A-n`, charter D-A.5), with a
   dated marker on the affected row — never rewritten in place: the logs and
   telemetry that cite it assume the text they cited still exists. The
   frontmatter `amended_by` list is checked against the section headings.

## The typed sections beside the table (RFC 0053)

The table is what a contract inherits; five fenced YAML blocks carry what
the machine reads beside it. Each is validated against the model on load,
and an unknown key is refused by fence, entry and field name — never
ignored.

| Fence | Where | Holds |
|---|---|---|
| `yaml decision-details` | under Decisions, after the table | per row: `rationale`, `cites` (identifiers the row descends from — a `D-x.y`, an `A-n`, a document number, resolving over the archive too), `check` (a command whose exit code judges the row; authored, never derived), `superseded_by` |
| `yaml invariants` | under Design | `id` (`I-NNNN.n`), `statement`, `paths`, `check` — a rule with the command that proves it |
| `yaml alternatives` | under Alternatives considered | `option`, `rejected_because`, `cites` — the negative space every executor re-proposes when nobody wrote the option was closed |
| `yaml questions` | under Unresolved questions | `id` (`Q-NNNN.n`), `text`, `status` open/settled, `settled_by` |
| `yaml changes` | under an `### A-n` heading, written by the tool | the typed diff of what the amendment changed, with the prior value |

Prose stays prose: §Design is keyed by its headings and typed no further.
Type what was already a list; never fragment an argument.

## Rows change through the tool, never by hand

A row's grade or paths change only through `torve rfc amend NUMBER
--title T --row D-x.y --grade G` (or `--path GLOB` repeated, `--text
"…"`, `--retire --reason R`). The verb appends the dated heading, records
the typed diff with the prior value in a `changes` fence beneath it, and
re-stamps the row in the document's `fingerprints:` frontmatter. A grade
or paths edited by hand afterwards is a `torve rfc check` problem: a row
with no history. A text edited by hand is editorial drift — a warning —
and `torve rfc fix D-x.y "…"` re-stamps it with the before and after
recorded under the table, no amendment number. A typo costs one command;
a rewording that changes the rule's meaning is an amendment.

`torve rfc check` also names rows whose declared paths match nothing in
the tree; `torve rfc check --fix-rot` retires them through an amendment
per document with the reason recorded. Coverage per path — governed,
ungoverned, retired — is `torve decisions paths <partition> <glob>`;
ungoverned is the ratchet's frontier, never a finding.

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

The executor **proposes** rows in its task log (`.torve/tasks/<task-id>/log.yaml`,
the `proposal:` field) with the evidence that produced them; the author
appends them with `torve rfc add-decision` and grades them with `torve rfc
amend --row`. The table is append-only — a retired row leaves a tombstone
and its `retired:` entry, naming its replacement. An accepted row cites the
entry it came from in its `decision-details` (`cites`) and the words that
argued it in the amendment; without the link it reads as something the
author thought of, losing the one fact that makes it credible.

The frontmatter `description` **routes** (which RFC to open — one sentence,
~200 chars, 300 ceiling); it never summarises and never records history. The
index renders it; nobody edits the index.

## References

- `references/rfc-template.md` — skeleton with per-section guidance
- `references/index-template.md` — INDEX.md skeleton
- `references/authoring.md` — the sections and how the prose behaves
- `references/workflows.md` — create, update, maintain, initialize
