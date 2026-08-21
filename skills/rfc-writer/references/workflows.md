# The four workflows, and the conventions they apply

`torve rfc` enforces most of what follows (RFC 0007 §3a — the package owns
the format, D-7.12). This file is the procedure a human or an agent follows
around it.

## Directory and index

**Location.** RFCs live in a single flat directory, `rfcs/` by default — configurable as `rfcs.path` in the runner's `.torve/config.yaml`, one path only, never a list or a glob (D-A.16). Only `NNNN-slug.md` and `INDEX.md` belong there, with no subdirectories (D-A.18); anything else is routed by `torve rfc check` to `pages/` or `ops/`.

**Gitignore is the user's call, not yours.** Some projects commit RFCs; others gitignore them as local working notes. Never add or remove a `.gitignore` entry for the RFC directory unless explicitly asked. If the directory is gitignored, the `INDEX.md` header should say so (see the template) so readers know why it isn't in the repo history.

**INDEX.md is the source of truth for the collection.** It carries three things:

1. The **next free number**, stated explicitly — numbers collide when minted in parallel, so the index names the next one and every RFC creation updates it in the same change.
2. The **index table**: `| # | Title | Status | One-line |`, one row per RFC, number linked to the file.
3. The **status legend**.

If the directory exists but has a `README.md` in this role, treat it as the index. If asked to set up fresh, use `INDEX.md` — copy `references/index-template.md`.

**Where execution's findings live: `logs/<task-id>.md`, outside this directory.** `flag-dont-flip` writes one per task, holding what execution found wherever the code and these designs disagreed. They are not RFCs — no number, no status, no row in the index table — and they never live in the corpus directory (D-A.18). Once any of them exist, the index links to `logs/` in prose above the table, because a reader deciding which RFC to open needs to know the document they are about to trust has a companion recording where it turned out to be wrong. Do not create them here: a task log is written by the task that executed.

## Numbering and filenames

- Numbers are 4-digit, zero-padded, monotonically increasing: `0001`, `0002`, …
- To allocate: `torve rfc new "Title"`. The next number is **derived** — the maximum that exists, plus one (D-A.17). There is no counter file, and no way to pick a number by hand.
- Filename: `NNNN-kebab-case-title.md`. Keep the number in the filename and the `# RFC NNNN — Title` H1 in sync — they drift otherwise, and links break both ways.
- Never renumber existing RFCs. Numbers are identifiers, not an ordering to be tidied.
- **Never delete a document, never reuse a number** (D-A.19). A document leaves service through `superseded` or `implementation: abandoned`; gaps in the numbering are fine, filling one is refused.

## Statuses

- 📝 **Draft** — proposed, not started (a "design locked, demand-gated" RFC is still Draft)
- 🚧 **In progress** — partially shipped
- ✅ **Complete** — fully shipped
- ❌ **Rejected / withdrawn** — keep the file; a recorded rejection prevents re-litigating

Status lives in two places that must agree: the `**Status:**` line in the RFC header and the Status column of the index table. Update both in the same change.

The bookkeeping — number allocation, file creation from the template, index regeneration, drift detection — is mechanical, and `torve rfc` does it without the collisions hand-allocation produces:

```bash
torve rfc check                    # frontmatter, tables, links, graph, directory, index drift
torve rfc index                    # regenerate INDEX.md; --check compares without writing
torve rfc new "Title"              # derive the next number, instantiate the template, regenerate
torve rfc new "Title" --kind convention
torve rfc graph                    # depends_on edges with statuses, plus inheritance hazards
```

(Read-only except `new` and `index`; add `--root DIR` if the repository isn't the cwd.) The thinking — what the design says, what the one-liner claims, when a status changes — is yours.

### A — Create a new RFC

1. Locate the RFC directory (`rfcs/`, or whatever `rfcs.path` names); if none exists, run Workflow D first.
2. Allocate the next number and instantiate the file: `torve rfc new "Title"` — it derives the number, writes the template, and regenerates the index. Steps 3 and 4 stay yours: it leaves the template unfilled. Parallel creation on two branches produces the same number twice; that surfaces as a duplicate-id failure at merge, and the one merging second is renamed before anything references it.
3. Fill the file from `references/rfc-template.md`'s shape, scaled to the design's weight. Investigate the actual code before writing "Current state" — this is most of the work.
4. Replace the placeholder index one-liner with one sentence that says which design this is — see the one-liner rules above. The summary the RFC deserves goes in the RFC's Summary section.

### B — Update an existing RFC

1. When work ships partially or fully, update the `**Status:**` line — and annotate it with what shipped and when ("Shipped 2026-06-29: …; only P5 remains").
2. If execution diverged from the design, the divergence is already in that task's log; what lands here is the decision row it proposed, appended and citing its entry. Don't silently rewrite history, and don't restate the log's narrative in the RFC — the row is the contract, the entry is the evidence, and duplicating one into the other means they will disagree later.
3. Mirror the status in the index table. Leave the one-liner alone unless the RFC's *subject* changed — shipping, phasing and amendments are the RFC's history, not the index's.
4. Rejected designs get ❌ and stay in the directory.

### C — Maintain the index

Run `torve rfc check` — it reports every file without an index row and vice versa, H1-vs-filename mismatches, malformed frontmatter, decision-table problems, dependency-graph hazards, and index drift. Fix what it names (the fixes are judgment: which status is true, what the one-liner should say), then re-run until green. Report what was out of sync.

### D — Initialize an RFC directory

1. Create `rfcs/` (unless the user wants `rfc/` or one already exists).
2. Create `INDEX.md` from `references/index-template.md`, filling in the project name and setting the next free number to `0001`.
3. Do not touch `.gitignore` — mention that committing vs. ignoring the directory is the user's choice.
4. Do not create anything under `logs/`. Task logs are `flag-dont-flip`'s, written by the task that executed; the index gains its pointer once any of them exist.

## The index one-liner: routing, not summary

**The one-liner exists to tell a reader which RFC to open, not what it decided.** It has one job — discriminate this design from the others in the table — and that takes far less text than summarising it. "Get a backup off the machine that took it" is forty characters and separates its RFC from twenty others; the design, the decisions and the trade-offs belong in the file it points at.

The rules:

- **One sentence. Aim for 200 characters, and treat 300 as the ceiling.** A table of thirty rows is then a couple of thousand characters, which is what makes the index cheap enough to consult on every lookup.
- **State the problem and the shape of the answer.** Not the mechanism, not the alternatives, not the numbers.
- **The index records what an RFC *is*, never what happened to it.** No "shipped 2026-08-04", no phase-by-phase progress, no defects found, no amendment history. Status lives in the Status column; everything else lives in the RFC — its `**Status:**` annotation, its Decisions table, its execution notes. An entry that grows each time work lands has become a changelog, and the whole table is then re-read on every allocation.
- **Write it once.** Revisit it only when the RFC's *subject* changes — not when its state does.

This is the one place in the skill where completeness is the wrong target. An index entry dense enough to substitute for opening the file has stopped being an index: every future lookup pays for content that belongs to one document.

## Reconciling what execution learned

Execution finds things the design could not. When it does, the executor **proposes** rows — in its task log, with the evidence that produced them — and the author appends them. Three rails:

- **The decision table is append-only.** A superseded row stays, marked superseded, naming the row that replaced it. The history of a decision is the part that stops it being re-litigated.
- **Never amend the RFC's prose to match what was built.** It reads as tidying, and it destroys the only evidence that a decision changed at all — which is precisely what a later reader needs in order to trust the document. Record the change; don't erase the disagreement.
- **An accepted row cites the log entry it came from** — `Added by execution 2026-08-14 — see logs/T-0142.md (D-3, attempt 2)` at the end of the row. Task-log entries carry no identifiers of their own, so the handle is the file plus the decision the entry cites plus its attempt; that triple is unique and nothing about it has to be renumbered. The row states the decision; the entry holds what was actually found, what was built instead, and what it cost. Without the link the row reads as something the author thought of, which loses the one fact that makes it credible: it was forced by contact with the code.

An RFC whose prose has been quietly retrofitted is worse than one that is visibly out of date: the second tells you to check, the first does not.
