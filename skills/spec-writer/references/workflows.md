# The four workflows, and the conventions they apply

`torve spec` enforces most of what follows (RFC 0007 §3a — the package owns
the format, D-7.12). This file is the procedure a human or an agent follows
around it.

## Directory

**Location.** Documents live in `.torve/specs/` by default — configurable as
`specs.path` in the runner's `.torve/config.yaml`, one path only, never a
list or a glob (D-A.16). Only `S-NNNN/` directories belong there, each
holding only `document.yaml`, `decisions.yaml`, `amendments.yaml` and
`execution.yaml`; a stray file, a leftover one-file document or a `schema/`
directory is a `torve spec check` problem naming what to do with it. The
archive is `.torve/archive/` beside it, the same shape; the schemas are
`.torve/schemas/` beside both.

**The schemas.** `torve init` writes one JSON Schema per file from the
model into `.torve/schemas/`, and `torve spec check` reddens when one lags.
Every file's first line names its own, so an editor validates a row as it
is typed. Run it once per repository and again whenever the engine's model
changes.

**Gitignore is the user's call, not yours.** Some projects commit documents;
others gitignore them as local working notes. Never add or remove a
`.gitignore` entry for the directory unless explicitly asked.

**Where execution's findings live: the task log, then `execution.yaml`.**
Every task's divergence entries record what execution found wherever the
code and these designs disagreed. At landing they are folded into the
document's `execution.yaml`, entry by entry, cited by row — never into
`document.yaml`. A row that execution proposed reaches the rows through
Workflow B.

## Numbering and directories

- Numbers are 4-digit, zero-padded, monotonically increasing: `0001`, `0002`, …
- To allocate: `torve spec new "Title"`. The next number is **derived** — the
  maximum that exists in the corpus path and the archive beside it, plus one
  (D-A.17, D-53.10). There is no counter file, and no way to pick a number by
  hand.
- Directory: `S-NNNN/`, the identifier and nothing else — the title lives in
  `document.yaml` and `torve spec list` shows it. The `id` inside must match
  the number; the check reddens when it does not.
- Never renumber existing documents. Numbers are identifiers, not an ordering
  to be tidied.
- **Never delete a document, never reuse a number** (D-A.19). A document
  leaves the corpus path through `torve spec archive NUMBER --superseded-by
  NNNN` into `.torve/archive/`, keeping its directory and identifiers; gaps
  in the numbering are fine, filling one is refused.

## Statuses

`status` is one of `draft` (proposed, not depended on — a "design locked,
demand-gated" document is still a draft), `accepted` (reviewed; contracts
may inherit from it) and `superseded` (only with `superseded_by`).
`implementation` is the author's judgement — `none`, `partial`, `complete`,
`abandoned` — which `torve context` checks against what the record says
shipped and reports as a disagreement, never corrects.

The bookkeeping — number allocation, the directory, the serializer, drift
detection — is mechanical, and `torve spec` does it without the collisions
hand-allocation produces:

```bash
torve spec check                    # every document loads; identifiers, citations, graph, comments, sections, schema drift
torve spec list                     # every document with status, implementation and dependencies — the index
torve spec new "Title"              # derive the next number and write the smallest document that checks
torve spec new "Title" --kind convention
torve spec show D-x.y | A-n | NNNN  # one identifier resolved, archived ones marked
torve spec graph                    # depends_on edges with statuses, plus inheritance hazards
torve init                          # write .torve/schemas/*.json from the models
torve spec add-decision NNNN        # append a row under the next free identifier, grade OPEN
torve spec amend NNNN --title T --row D-x.y --grade G   # the only way a row's grade or paths change
torve spec fix D-x.y "…"            # editorial: re-stamp a text-only edit, no amendment number
torve spec archive NNNN --superseded-by MMMM            # into .torve/archive/, under one transaction
torve spec check --fix-rot          # retire rows whose paths match nothing in the tree
torve spec fmt                      # report documents that differ from the serializer's form; writes nothing
torve spec render NNNN [--out PATH] # a markdown page for a person; never the source
```

(Add `--root DIR` if the repository isn't the cwd.) The thinking — what the
design says, what the description claims, when a status changes — is yours.

### A — Create a new document

1. Locate the directory (`.torve/specs/`, or whatever `specs.path` names);
   if none exists, run Workflow D first.
2. Allocate the number and the directory: `torve spec new "Title"`. It
   writes the header keys, one summary section and an empty decisions list;
   the rest is yours. Parallel creation on two branches produces the same
   number twice; that surfaces as a duplicate-id failure at merge, and the
   one merging second is renumbered before anything references it.
3. Grow the files along `references/spec-template.md`, scaled to the
   design's weight. Investigate the actual code before writing the
   current-state section — this is most of the work. Rows first, prose
   around them.
4. Write the `description`: one sentence saying which design this is, so a
   reader of `torve spec list` knows whether to open the document. The
   summary the document deserves goes in its summary section.
5. `torve spec check` until green; `torve spec fmt` if you want the
   serializer's form (optional — a hand-authored document is legal as it
   stands).

### B — Update an existing document

1. When work ships partially or fully, move `implementation`; `torve context`
   tells you when the assertion and the record disagree.
2. If execution diverged from the design, the divergence is already in that
   task's log and the document's `execution.yaml`; what lands in the rows is
   the row it proposed, appended with `torve spec add-decision` and graded
   with `torve spec amend --row`, citing the task in `rationale` or `cites`.
   Don't silently rewrite history, and don't hand-edit a row — the check
   reads a hand-edited grade or paths as a row with no history.
3. Leave the `description` alone unless the document's *subject* changed —
   shipping, phasing and amendments are the document's history, not its
   routing line.
4. Rejected designs are archived with `superseded_by` naming what stands
   instead, or kept as `draft` with an `abandoned` implementation; a recorded
   rejection prevents re-litigating.

### C — Maintain the corpus

Run `torve spec check` — it reports every document that does not load, a
key in the wrong file, a duplicate or reused identifier, a citation nothing
defines, a dependency on a draft, a cycle, a comment, a section carrying
what a typed list holds, a schema file that lags the model, and rows whose
paths match nothing in the tree. Fix what it names (the fixes are judgment:
which status is true, what the description should say), then re-run until
green. Report what was out of sync.

### D — Initialize a directory

1. Create `.torve/specs/` (unless the user wants another name or one already
   exists) and name it as `specs.path` in `.torve/config.yaml` if it is not
   the default.
2. `torve init` to write the schemas the files' first lines name.
3. Do not touch `.gitignore` — mention that committing vs. ignoring the
   directory is the user's choice.

## The description: routing, not summary

**The description exists to tell a reader which document to open, not what
it decided.** It has one job — discriminate this design from the others in
`torve spec list` — and that takes far less text than summarising it. "Get a
backup off the machine that took it" is forty characters and separates its
document from twenty others; the design, the rows and the trade-offs belong
in the document it points at.

- **One sentence. Aim for 200 characters, and treat 300 as the ceiling.**
- **State the problem and the shape of the answer.** Not the mechanism, not
  the alternatives, not the numbers.
- **It records what a document *is*, never what happened to it.** No
  "shipped 2026-08-04", no phase-by-phase progress, no amendment history.
- **Write it once.** Revisit it only when the document's *subject* changes.

## Reconciling what execution learned

Execution finds things the design could not. When it does, the executor
**proposes** rows — in its task log, with the evidence that produced them —
and the author appends them. Three rails:

- **The rows are append-only.** A superseded row stays until retired through
  an amendment, its `superseded_by` naming the row that replaced it. The
  history of a decision is the part that stops it being re-litigated.
- **Never amend the prose to match what was built.** It reads as tidying, and
  it destroys the only evidence that a decision changed at all — which is
  precisely what a later reader needs in order to trust the document. Record
  the change; don't erase the disagreement.
- **An accepted row cites the entry it came from** — the task id and the
  decision the entry cites, in `cites` or `rationale`. The row states the
  decision; the entry holds what was actually found, what was built instead,
  and what it cost. Without the link the row reads as something the author
  thought of, which loses the one fact that makes it credible: it was forced
  by contact with the code.

A document whose prose has been quietly retrofitted is worse than one that
is visibly out of date: the second tells you to check, the first does not.
