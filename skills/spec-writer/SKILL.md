---
name: spec-writer
description: When writing a torve specification document — an executable input to torve plan, a directory of YAML files split by who writes each, a typed anatomy, graded rows with paths on every one, mintable phasing whose scope owns everything its acceptance can turn red, and identifiers the divergence logs and the code cite forever.
roles: [author]
gate: spec-valid
---

> **Specialisation.** Derived from `agent-skills/rfc-writer`, specialised for
> artefacts that Torve reads. Divergence from upstream is expected and
> intentional — **do not reconcile**. Improvements of general value flow
> upstream, not the reverse.

# Specification authoring for Torve

A document here is not only a design record — it is an **executable input
to `torve plan`**: its decision rows mint into task contracts with inherited
grades and declared areas, its phasing mints into tasks with scopes, and its
identifiers are what every divergence log and every code comment cites.
Write it as a document a machine derives work from and a human can refuse
in a diff.

The document is a directory, `.torve/specs/S-NNNN/`, holding four YAML
files split by who writes each, and the landings beside them:

| File | Writer | Holds |
|---|---|---|
| `document.yaml` | you | the header facts (`change:` among them), the typed prose (`summary` … `risks`), the `design` list, the extras, `alternatives`, `questions` |
| `phasing.yaml` | you; the planner reads it | `phasing`, `after`, `contract_example` |
| `decisions.yaml` | you; the tool stamps it | `decisions`, `invariants`, `retired` |
| `amendments.yaml` | `torve spec amend` and `spec fix` | `amendments`, `editorial` |
| `execution/` | the landing | one file per landing — `<task>-<attempt>-<instant>.yaml` — what each task found, entry by entry |

A file that is absent is an empty list; `spec new` writes `document.yaml` and
`decisions.yaml`. Each
file's first line names its schema under `.torve/schemas/`, written by
`torve init`, so an editor with a YAML language server validates every key
as it is typed; `torve spec check` is the gate and reads the same model.
Prose lives in typed keys — `summary`, `motivation`, `current_state`,
`goals`, `non_goals`, `tests`, `docs`, `out_of_scope`, `risks` — in the
`design` list and in at most eight extra `sections`; every body is a
markdown string nothing parses, and a section's heading is its key. Everything else is a typed list, and an
unknown key — or a key in the wrong file — is refused by name, never
ignored. The mechanical half — numbering, the directory, the serializer,
the checks — belongs to the package, and the `torve spec` verbs apply it
(`torve spec --help` lists them). Anatomy, prose style and workflows live in
`references/`.

`change:` is a mapping of `type` and `scope` (`{type: feat, scope: lane}`):
the document's pull request and its squash commit are titled from it, as
`<gitmoji> <type>(<scope>): <title>`. `after:` in `phasing.yaml` names other
documents, never phases, whose landed tree this document builds on; the
planner makes each first phase wait on every task of each named document.
It differs from the header's `depends_on`, which is decision inheritance.

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
   # decisions.yaml
   decisions:
     - id: S-0003/D-1
       grade: LOCKED
       text: Sessions in Redis, not the database
       paths: [packages/api/session/**]
       consequence: a session survives an API restart; a Redis outage logs everyone out
       rationale: the database is the write path we protect first
       cites: [S-0001/D-4]
       check: pytest tests/test_session.py
   ```

   `check` is a command whose exit code judges the row; the runner appends
   it to the battery as a `decision:<id>` gate under the row's
   `check_state` (`shadow` until an amendment promotes it, naming the soak
   evidence), and a row whose check passed owes no divergence entry. Which
   check is honest is a fact about the rule, not about what is easy to write:

   - **A rule over inputs gets a property.** When the row governs a parser,
     a matcher, a state transition or a round trip — anything quantified
     over inputs — the check is a Hypothesis test that generates them and
     asserts the rule holds. `hypothesis` is a direct dev dependency, so the
     test imports it without a fixture. Assert the rule, never the one input
     the author pictured: a property that enumerates the cases an example
     would is an example wearing a generator, and a reviewer reads every
     check for exactly that.
   - **Where no input rule exists, an example test is the honest check.** A
     row about a fixed spelling, a wire format or a migration's one-shot
     effect is a statement about one case; a property over it asserts nothing
     an example does not, and leaves a reader no way to say what a wrong
     implementation would violate. Write the example.
   - **The phase that settles the row writes its check.** A check belongs
     beside the code it judges, in the task whose phase delivers the row —
     never invented by the planner, never deferred. A check with no phase to
     run it selects nothing, which the battery now reads as a vacuous
     failure rather than a quiet green.

   `check_state: blocking` needs a `check_twin` — the test that proves the
   check can fail: a plausible wrong implementation of the row, run against
   the check, which must turn it red. `torve gates check` runs every standing
   row's twin after the sabotage CASES, and `torve spec check` refuses a
   blocking row that names none. A row is never blocking on a check nobody
   has shown can fail.

   A check that passes is not yet a strong check. The battery's `mutation`
   gate edits the changed lines and reruns the task's paired tests — the tests
   a row's check usually selects from, not the check's own command — and
   **a check that leaves survivors on its row's lines is a weak check**: the
   tests behind it ran the wrong edit and still passed. A survivor is
   evidence, not a verdict — an equivalent mutant survives without any test
   being wrong — but an example that exercises a line and asserts nothing
   about it is exactly what leaves one, so read a survivor as a prompt to
   assert the line's behaviour in the tests the check selects.

A test that proves a row cites it in its own docstring: the row's full
identifier, `S-NNNN/D-n`, as a whole token. A module or class docstring proves
nothing for the tests beneath it, so each test carries its own. The battery's
`evidence` gate reads the citation from the function's own source — its
docstring, comments and body — and runs each citing test on the base tree and
on the candidate: a row is proved when one citing test is red on the base tree
and green on the candidate, so the citation belongs in the test the change
turns green.

2. **Phasing must be mintable.** A phase is an entry in the `phasing` list —
   number, title, one-paragraph intent, the file globs it will touch, its
   acceptance commands, the phases it waits on — not prose about sequence.
   If `torve plan` cannot derive a task from an entry, the entry was written
   wrong. **The intent names the public surface the phase's tests drive** —
   the functions, types or commands the phase introduces or changes. Where
   the verify pilot is on, a verifier writes that phase's tests from the rows
   and the intent alone, before any implementation exists; an intent naming
   only a file leaves it to invent an API, and a phase written for a verifier
   names what to test. A phase's clock and model are its seat's: a phase that
   needs more time than the repository's `agent_timeout`, or the stronger
   model, names a `tier_variant` the operator configures with its own
   `agent_timeout` and `sandbox_timeout`, or a `character` the seat's
   `character_routing` sends to one. The size estimate is advice and moves
   neither.

3. **Non-overlapping scope within a phase.** Entries with the same phase
   number must not share globs — overlapping tasks cannot run in parallel and
   the plan silently serialises. Say it while writing, when it is free to fix.
   Scopes read on the gitignore grammar, so a root file is written `/README.md`:
   bare `README.md` matches at any depth and overlaps every other scope.

3b. **A phase owns every file its acceptance can turn red.** A scope that is
   too narrow is the most common reason an attempt halts. Before you write a
   phase, grep for every caller of what it changes. The scope names the
   modules and, beside each, its existing test file (the planner refuses a
   contract that allows a module without its test), the tests that enumerate
   artifacts, generated snapshots, docs pages a test compares against, the lock
   file beside a manifest, and re-export modules. Acceptance commands judge
   the tree: the tests the phase touches, the type checker, `torve spec
   check`. A command that reads the host (a running service, a database, the
   network, the installed CLI) is refused at planning.

3a. **A decision's paths must fit inside one phase's scope.** A row whose
   paths span two phases mints a contract that contradicts itself: the
   executor inherits a decision it cannot satisfy without leaving
   scope.allow. Split the row into per-surface decisions at authoring.

3c. **The scope's neighbours are derived, not listed.** Do not pad a phase's
   `scope` with every caller, importer or paired test it might touch: the
   planner derives each contract a **contingent** set at mint and at refresh —
   the scoped files' importers in source and tests, their first-party imports,
   their tests paired at any depth, their packages' `__init__`, and the paths
   the document's other phases match. A contingent path is promoted on its
   first write, recorded and reported, and fenced against tasks in flight; one
   another task holds requeues the attempt rather than halting it. The globs
   still name what the phase *owns* — a contingent path is a likely reach, not
   a claim, and is never counted for disjointness or sizing. What the import
   graph cannot see is a generated file, and that you do name: the manifest's
   `scope.derived` map pairs each such file — a golden, a schema, a docs
   listing a test compares — with its source, in both directions.

4. **Decision identifiers are permanent.** Divergence logs and code comments
   cite `S-0003/D-1` forever; renumbering orphans every one of them. Append new
   rows; never renumber, never reuse an identifier — a retired one is
   recorded in `retired` and stays taken.

5. **Amendments, not edits.** An accepted document is amended — an entry in
   its `amendments.yaml` (numbered per document, `A-n`, cited as `S-NNNN/A-n`) carrying the
   typed diff of what changed — never rewritten in place: the logs and
   telemetry that cite it assume the text they cited still exists.

## The typed lists beside the rows

| List | File | Entry | Holds |
|---|---|---|---|
| `decisions` | decisions | `id`, `grade`, `text`, `paths`, `consequence`, `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by`, `fingerprint` | the rows; `fingerprint` is the tool's stamp, never written by hand |
| `invariants` | decisions | `id` (`I-n`), `statement`, `paths`, `check` | a rule with the command that proves it |
| `retired` | decisions | identifiers | what this document once defined; never reused |
| `alternatives` | document | `option`, `rejected_because`, `cites` | the negative space every executor re-proposes when nobody wrote the option was closed |
| `questions` | document | `id` (`Q-n`), `text`, `status` open/settled, `settled_by` | what the design leaves for someone to settle |
| `contract_example` | phasing | a task contract | optional: a runnable demonstration of what the rows and a phase mint into, validated against the live task schema |
| `design` | document | `key`, `md` | the design, one entry per workstream, in order; the heading is the key |
| `sections` | document | `key`, `md` | the extras the anatomy has no key for, at most eight |
| `phasing` | phasing | `phase`, `title`, `intent`, `scope`, `acceptance`, `depends_on`, `tier_variant`, `character` | the mintable units |
| `amendments` | amendments | `id`, `at`, `title`, `changes`, `md` | written by `torve spec amend`; the words are yours |

`cites` resolves over the corpus and the archive in the one grammar: a
document `S-NNNN`, or an item of one — `S-NNNN/D-n`,
`S-NNNN/I-n`, `S-NNNN/Q-n`, `S-NNNN/A-n`, a phase `S-NNNN/P-n`, a prose
section `S-NNNN/<key>`. Inside a document's own files its own items are
written by the local half alone (`id: D-3`, `cites: [D-1, S-0002/D-4]`);
the loader qualifies them, and code and prose cite the global form. Type
what was already a list; never
fragment an argument into fields, and never restate a typed list as prose:
a section carrying an `alternatives` fence, the decisions table or an
amendment's words is a `check` problem — the list exists once.

## Rows change through the tool, never by hand

A row's grade or paths change only through `torve spec amend NUMBER
--title T --row D-n --grade G` (or `--path GLOB` repeated, `--text
"…"`, `--retire --reason R`). The verb appends the amendment with the typed
diff and the prior value, re-stamps the row's `fingerprint`, and writes the
document through the one serializer. A grade or paths edited by hand
afterwards is a `torve spec check` problem: a row with no history. A text
edited by hand is editorial drift — a warning — and `torve spec fix S-NNNN/D-n
"…"` re-stamps it with the before and after recorded under `editorial`, no
amendment number. A typo costs one command; a rewording that changes the
rule's meaning is an amendment.

A file may carry no comment but its first line: a comment is meaning
outside the model, and a row that needs a note needs a `rationale`. A
hand-authored document is otherwise legal as written; `torve spec fmt`
reports what differs from the serializer's form and writes nothing.

`torve spec check` also names rows whose declared paths match nothing in
the tree; `torve spec check --fix-rot` retires them through an amendment
per document with the reason recorded. For a document nothing has landed
from, it lists each LOCKED row of a `depends_on` document that a phase's
scope touches. A contract carries its own document's rows only, so an
executor meets a contradiction there at its first attempt and halts: read
the list before the owner accepts, and settle each conflict in the phase or
by amending the inherited document. Coverage per path — governed,
ungoverned, retired — is `torve spec paths <path>`; ungoverned is the
ratchet's frontier, never a finding.

## The archive

A document that no longer stands leaves the corpus path through `torve
spec archive NUMBER --superseded-by NNNN` into `.torve/archive/` beside it,
directory name and identifiers kept, `status: superseded`. Nothing inherits
from the archive; everything in it still resolves — `torve spec show` on an
archived row answers marked archived, a citation into it checks clean, and the
record holds every archived row as retired with the archive named.
Document and amendment numbers derive over corpus and archive together, so
a number is never reused.

## Reconciling what execution learned

The executor **proposes** rows in its task log (the `proposal:` field of a
divergence entry) with the evidence that produced them; the author appends
them with `torve spec add-decision` and grades them with `torve spec amend
--row`, citing the task in the row's `cites` or `rationale`. The rows are
append-only — a retired row leaves its `retired` entry and the amendment
that retired it. Without the link a row reads as something the author
thought of, losing the one fact that makes it credible: it was forced by
contact with the code.

The summary's **first sentence routes** (which document to open — one
sentence, 300 characters at most, and `check` warns past it); `torve spec
list` renders it, and nobody maintains an index.

## References

- `references/spec-template.md` — the document skeleton with per-key guidance
- `references/authoring.md` — the sections and how the prose behaves
- `references/workflows.md` — create, update, maintain, initialize
