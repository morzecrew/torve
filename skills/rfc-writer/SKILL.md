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

The mechanical half is a script: location, numbering, filenames, statuses,
index rows, decision-table shape and the checks below are applied by
`scripts/rfc_index.py` (`check` / `next` / `new "Title"`), and its `check` is
the gate. Anatomy, prose style and workflows live in `references/`.

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

4. **Decision identifiers are permanent.** Divergence logs cite `D-3` forever;
   renumbering orphans every entry that cites it. Append new rows; never
   renumber, never reuse an identifier.

5. **Amendments, not edits.** An accepted RFC is amended — a dated marker on
   the affected row or section pointing at the amendment record — never
   rewritten in place: the logs and telemetry that cite it assume the text
   they cited still exists.

## Reconciling what execution learned

The executor **proposes** rows in its task log (`logs/<task-id>.yaml`, the
`proposal:` field) with the evidence that produced them; the author appends
them. The table is append-only — a superseded row stays, marked, naming its
replacement. An accepted row cites the entry it came from (`Added by execution
… — see logs/T-0142.yaml`); without the link it reads as something the author
thought of, losing the one fact that makes it credible.

The index one-liner **routes** (which RFC to open — one sentence, ~200 chars,
300 ceiling); it never summarises and never records history.

## References

- `references/rfc-template.md` — skeleton with per-section guidance
- `references/index-template.md` — INDEX.md skeleton
- `references/authoring.md` — the sections and how the prose behaves
- `references/workflows.md` — create, update, maintain, initialize
