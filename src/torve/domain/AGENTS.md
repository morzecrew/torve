<!-- torve:managed src/torve/domain — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/domain/`

### S-0053/D-1 — `LOCKED` (The item model and the rebuilt corpus)

The specification the engine reads is one pydantic model in `domain/spec.py`, `extra="forbid"`; every reader — planner, importer, check, health, show, intake lint, standing inheritance — consumes the model and never a parser's rows

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Six consumers stop re-shaping rows; a field the author wrote cannot be dropped on the way to the record; S-0007/D-17 stands because the *format* still terminates at `config/` while the model may cross
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-3 — `ASSUMED` (The item model and the rebuilt corpus)

Five fenced kinds: `decision-details`, `invariants`, `alternatives`, `questions`, `changes`; prose sections become `DesignSection`s keyed by heading slug and are typed no further

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Typing an argument fragments it; what is typed is what was already a list

### S-0053/D-5 — `ASSUMED` (The item model and the rebuilt corpus)

Every row carries a content fingerprint; a mismatch between a contract's copied row and the row as it stands is reported as *suspect* by `decisions show` and `rfc health`, never as a conviction

- Paths: `src/torve/domain/spec.py` `src/torve/application/decisions.py`
- Consequence: Copy-at-mint becomes checkable; S-0031/D-3's no-retroactive rule is preserved by making the mismatch a reading, not a red

### S-0053/D-14 — `ASSUMED` (The item model and the rebuilt corpus)

`domain/spec.py` imports nothing but pydantic and `domain/rfc.py`, so extracting it into its own distribution is a packaging act and never a rewrite

- Paths: `src/torve/domain/spec.py`
- Consequence: The model is the reusable half; torve's grades, paths and checks are a profile over it

### S-0053/D-15 — `ASSUMED` (The item model and the rebuilt corpus)

`check` on a row is authored, never derived; a `LOCKED` row without one is reported *soft* by `rfc health`; nothing runs a check in this document

- Paths: `src/torve/domain/spec.py`
- Consequence: 15 of 19 derived checks in the probe were guesses; running checks as gates is the next document's design

### S-0053/D-16 — `ASSUMED` (The item model and the rebuilt corpus)

The fingerprint covers text, grade and paths; consequence, rationale and check are outside it, since a contract copies the first three at mint and a human reads the rest (S-0053/Q-1, settled in draft)

- Paths: `src/torve/domain/spec.py`
- Consequence: An edit to a consequence is free; an edit to what an executor is bound by is recorded

### S-0054/D-1 — `LOCKED` (Decisions as gates and the projections beside the code)

`InheritedDecision` gains `consequence` and `check`, copied at mint from the model, carried by `decision.recorded`, rendered after each row in the prompt; the fingerprinted set stays text, grade and paths

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: A gate reads the contract and never the corpus (S-0007/D-18 stands); the reason a row exists reaches the executor for the first time
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-1 — `LOCKED` (Standing decisions)

Models never decide what work exists or whether it is finished; the runner executes state transitions from facts — exit codes, gate outcomes, approvals — and an agent reports observations that never cause a transition

- Paths: `src/torve/application/runner.py` `src/torve/application/manager.py` `src/torve/domain/states.py`
- Consequence: A reviewer's severity is data whose consequence configuration sets; the planner invokes no model at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-6 — `ASSUMED` (Standing decisions)

Escalation reasons are a closed enum mapped to exit codes; `locked_conflict` and `underspecified` are halts on working judgement, one indicting the code and the other the contract

- Paths: `src/torve/domain/states.py` `src/torve/cli/options.py`
- Consequence: A new reason is an amendment and a code path, never a free string

### S-0055/D-7 — `LOCKED` (Standing decisions)

A task contract is immutable once minted: a changed contract is a new task, a re-mint is a version and never a transition, and a task in flight is never re-minted

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: An executor's contract cannot drift out from under it; `torve plan` refuses to re-mint over minted phases and leaves the decision to a person
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-9 — `LOCKED` (Standing decisions)

A contract copies its rows' grade, text and paths at mint time; a grade is never resolved at read time

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py`
- Consequence: A regrade never rewrites the judgement of a task that ran under the old grade
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-12 — `ASSUMED` (Standing decisions)

An empty decision list on a contract is legal and explicit; "none apply" is distinct from "field forgotten"

- Paths: `src/torve/gates/decisions_reported.py` `src/torve/domain/task.py`
- Consequence: The document-less lane is governed by standing inheritance, not by silence

### S-0055/D-37 — `LOCKED` (Standing decisions)

A fact is an event of a closed kind vocabulary with an authority table; an agent may write only `divergence.recorded` and `message.sent`; no update command exists on the record

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py`
- Consequence: An audit trail that can be edited cannot be trusted; the authority table refuses before any store sees the write
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-1 — `LOCKED` (Structure for everything) — implementation: none

A document is one YAML file, `rfcs/NNNN-slug.yaml`, in the `Document` model's own shape and key order; loading is the model's validator plus the corpus checks; `schema_version` 2, and 1 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py`
- Consequence: The markdown parser, `load_fences`, the heading and table regexes are deleted; every reader of the corpus is unchanged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-2 — `LOCKED` (Structure for everything) — implementation: none

`DecisionDetail` folds into `Decision`; the fenced kinds are gone; a row carries `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by` and its `fingerprint` on itself

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: `inherit_decisions` reads the row; the frontmatter fingerprint map is gone
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-3 — `LOCKED` (Structure for everything) — implementation: none

Prose is `sections[].md`, a string the engine never parses, with `key` and `heading` beside it; `level` and `order` are dropped

- Paths: `src/torve/domain/spec.py`
- Consequence: Markdown inside a body is welcome and invisible to every check
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-1 — `LOCKED` (The specification is a directory)

A specification is a directory `S-NNNN/` — the identifier and nothing else — of `document.yaml`, `decisions.yaml`, `amendments.yaml` and `execution.yaml`, split by who writes each; an absent file is an empty list; the loader joins them into the one `Document` every reader keeps reading; `schema_version` 3, and 2 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py` `src/torve/config/spec_emit.py`
- Consequence: The author's file changes only by the author; `amended_by` is derived and gone; `Document.path` names a directory
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-2 — `LOCKED` (The specification is a directory)

A section is `key` and `md`; the heading is rendered from the key and the number from the position; `check` refuses a section with an empty body, a typed-kind fence, the decisions table header or an amendment identifier as its key

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: A typed list exists in one place; a heading cannot disagree with its key
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-1 — `LOCKED` (One grammar and the anatomy)

Every item the corpus defines has one global identifier, `S-NNNN/<local>` — `D-n`, `I-n`, `Q-n`, `A-n`, `P-n` or a prose key — and is written inside its own document by the local half alone; the document is the namespace, amendments included, and tasks stay `T-NNNN`

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: One regex reads every citation; nothing is spelled twice; sections and phases become citable
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-4 — `LOCKED` (One grammar and the anatomy)

`document.yaml` carries the prose as typed keys — `summary`, `motivation`, `current_state`, `goals`, `non_goals`, `tests`, `risks` required of an accepted design, `docs` and `out_of_scope` optional, `design` a keyed list with at least one entry once a design is accepted, `sections` the extras capped at eight — with keys unique document-wide and never a family shape; an accepted convention owes its summary alone; `description` is dropped and the summary's first sentence routes

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py`
- Consequence: `yq .motivation` answers; a document without a motivation cannot be accepted; the routing line is written once
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-5 — `LOCKED` (One grammar and the anatomy)

`phasing.yaml` holds `phasing` and `contract_example`, author-written and planner-read; a scope amendment touches it and the amendments file alone

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: Six files per document; the planner's list diffs apart from the prose
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-6 — `LOCKED` (One grammar and the anatomy)

Execution is a directory, `execution/<task>-<attempt>-<instant>.yaml`, one landing per file, written once and never deleted; the loader reads it sorted by instant into `landings`; an identical replay is a no-op and a restarted attempt lands under a new instant; the scope gate exempts the directory

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: Two candidates of one document never conflict at merge; no landing is refused for a number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-7 — `LOCKED` (One grammar and the anatomy)

The engine writes one instant, `YYYY-MM-DDTHH:MM:SSZ` in UTC, from `torve.base.clock.stamp()`, for amendments, landings, entries, telemetry, run state and their display; the dates that exist convert once to midnight UTC with the loss stated

- Paths: `src/torve/base/clock.py` `src/torve/domain/spec.py` `src/torve/application/telemetry.py` `src/torve/application/runstate.py` `src/torve/application/decisions.py` `src/torve/application/projections.py` `src/torve/cli/spec.py` `src/torve/cli/decisions.py`
- Consequence: A timeline over amendments, landings and entries sorts on one string
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-12 — `LOCKED` (One grammar and the anatomy)

A landing names its `base` — the commit the attempt built on, read from the log's pin — beside the `commit` it rides in when the lander knows it; a landing made by hand is made after the work commit, with `torve log land --commit`, in a commit of its own

- Paths: `src/torve/domain/spec.py` `src/torve/application/decisions.py` `src/torve/cli/log.py`
- Consequence: Where an implementation started is on the landing, not only in a log git never carries; the trailer join stays for the runner's commit
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-1 — `LOCKED` (One word for the document, and the tree as the record)

A contract names its document as `spec: S-NNNN` — the identifier, never a path; a contract carrying `rfc` refuses to load with a hint naming the key; the same word and value stand on the drafts file, `torve intake --spec`, a standing job's `decisions_from`, the plan report, the projections' envelopes, the web tables and the pack; the contract's schema version is 2 and the local contracts are rewritten once

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py` `src/torve/application/intake.py` `src/torve/application/standing.py` `src/torve/application/review.py` `src/torve/application/projections.py` `src/torve/application/specquality.py` `src/torve/cli/**` `web/src/**` `.torve/tasks/**`
- Consequence: One lookup resolves a document from a contract; nothing regexes a number out of a path
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-3 — `LOCKED` (One word for the document, and the tree as the record)

The record's source id of a task is `spec or "operator"`, the document id; `CORPUS_NAMESPACE` goes; a mint whose contract carries `rfc` folds through one read shim in `minted_contract`, and the record is not rewritten

- Paths: `src/torve/application/residency.py` `src/torve/application/manager.py` `src/torve/domain/source.py`
- Consequence: A task's source joins the record's source without translation; three hundred recorded mints keep folding
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-5 — `LOCKED` (One word for the document, and the tree as the record)

Every closed vocabulary is defined once in `torve/domain/vocabulary.py` and imported — the corpus words, the entry words, the contract's role, tier and character, the gate words, the finding severity, the source kind — with the tuples the CLI lists; `domain/rfc.py` is deleted; a word two fields share, or the record reads, is never spelled inline; `Phase.character` and `Task.character` share `Character`

- Paths: `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py`
- Consequence: A word gains a member in one place; the parity test between the log's and the record's copies is deleted with the copies
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-6 — `LOCKED` (One word for the document, and the tree as the record)

One shared `ConfigDict` in `torve/base/model.py` — `extra="forbid"`, `use_attribute_docstrings=True` — configures every model that forbids extras; a field's words are its attribute docstring, never a comment beside it and never `Field(description=...)`; a test asserts every property of every schema `init` writes carries a description

- Paths: `src/torve/base/model.py` `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py` `src/torve/cli/init.py` `.torve/schemas/**`
- Consequence: The schema an editor shows carries the field's meaning; a field added without its words fails the suite
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-8 — `ASSUMED` (One word for the document, and the tree as the record)

The log's `base_sha` is `base`, the landing's word; the log's schema version is 2 and a log saying `base_sha` reads through a shim; the landing's `commit` stays; `spec new`'s hint and `spec show`'s label name the summary, not a description

- Paths: `src/torve/domain/spec.py` `src/torve/application/**` `src/torve/gates/decisions_reported.py` `src/torve/adapters/agent/harness.py` `src/torve/cli/**`
- Consequence: One word for the commit an attempt built on, in the log and the landing

### S-0059/D-10 — `LOCKED` (One word for the document, and the tree as the record)

The runner writes no `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` or `Torve-Decisions` trailer, retiring S-0010/D-4; the landing carries `decisions: [{id, grade}]`, the rows the contract carried; `Torve-Bypass`, `Torve-Fixes` and `Torve-Checkpoint` stay

- Paths: `src/torve/application/runner.py` `src/torve/domain/spec.py` `src/torve/config/spec_emit.py`
- Consequence: One record of a landing; the commit author stays the agent's identity (S-0010/D-2)
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-1 — `LOCKED` (A source is a file, and the contract names it)

A source is a file, `.torve/sources/<kind>/<slug>.yaml`, carrying `id` (which must equal `<kind>/<slug>` from its own path), `title`, `ref`, `at` and `summary`; its identifier is `<kind>/<slug>`, a document's stays `S-NNNN`, and `specification` is not a directory because a document is already a source; `torve init` writes `sources.json` and each file opens with its schema line

- Paths: `src/torve/domain/source.py` `src/torve/config/sources.py` `src/torve/cli/init.py` `.torve/sources/**` `.torve/schemas/**`
- Consequence: A source identifier resolves to something a person can open, which is what makes it worth putting on a contract
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-2 — `LOCKED` (A source is a file, and the contract names it)

A source carries no decisions: rows that stand are the corpus's alone, and a source that settled some names the document holding them in `settled_by`

- Paths: `src/torve/domain/source.py` `src/torve/config/sources.py`
- Consequence: One row-bearing artefact; a contract inherits from one place and `spec cites` has one answer
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-3 — `LOCKED` (A source is a file, and the contract names it)

`Task.source: str | None` is the provenance — a source identifier, `S-NNNN` or `<kind>/<slug>`, validated and never parsed for inheritance — beside `spec`, which stays whose rows the contract inherits; a contract may carry both, either or neither

- Paths: `src/torve/domain/task.py` `src/torve/gates/context.py`
- Consequence: A task can say what asked for it without pretending a document did
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-8 — `LOCKED` (A source is a file, and the contract names it)

A document's `kind` stays `design` or `convention`: it answers what prose an accepted document owes and nothing else, so a bug worth a document is a design whose motivation is the defect, an audit's standing rules are a convention, and where the work came from is the source

- Paths: `src/torve/domain/vocabulary.py` `src/torve/config/spec.py` `skills/spec-writer/**`
- Consequence: One axis per field; the corpus never grows a second way to say provenance
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0079/D-1 — `ASSUMED` (The night as a typed record)

A night is a subject in the event log — `night.opened` and `night.closed`, both the manager's authority — and never a file, a summary or a counter anybody maintains

- Paths: `src/torve/domain/events.py`
- Consequence: a manager killed at 04:00 has already written everything the morning report needs, and a night with an open and no close reads as unfinished rather than as lost

### S-0079/D-2 — `ASSUMED` (The night as a typed record)

`night.opened` carries the night's terms whole — the ready queue as it stood, the width, the budget in dollars and in attempts, the stop conditions, the lease, and the resolved value of each night knob — read once at the open and never re-read

- Paths: `src/torve/domain/events.py`
- Consequence: a night's report says what the night was started with even after the configuration was edited while it ran, and two nights are comparable because their terms are recorded rather than reconstructed from whatever the file says afterwards

### S-0085/D-1 — `ASSUMED` (A document builds on another document's tree)

A phasing file names the documents whose landed tree its work builds on under a top-level `after`, as document ids; the header's `depends_on` keeps its one meaning, decision inheritance, and says nothing about trees

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `.torve/schemas/phasing.json`
- Consequence: S-0009 declares `after: [S-0008]` and `depends_on: []` and both are true; a document may name one document in both lists or in either

### S-0085/D-5 — `ASSUMED` (A document builds on another document's tree)

`after` names documents, never phases; a case that needs a phase inside one is a case for an amendment, with the case written down

- Paths: `src/torve/domain/spec.py`
- Consequence: the grammar is one list of ids and the planner's lookup is one call per id; a dependent waits for the whole named document, which under S-0083/D-5 is when its tree reaches the base anyway

### S-0087/D-1 — `ASSUMED` (A document names its change, and its pull request wears the name) — implementation: none

A document's header may carry `change` — a Conventional Commits type from the operator's mapping, an optional scope, and `breaking`, default false; the field is optional on the model, and `torve spec check` warns when an accepted design document has none

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `.torve/schemas/document.json`
- Consequence: every document in the corpus loads as it is, and the one whose landing would be untyped is named at check, not discovered in `main`'s history

## Invariants holding over `src/torve/domain/`

- **S-0059/I-3**: Every property of every schema `torve init` writes carries a description
  - Paths: `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py`
  - Check: `uv run pytest tests/test_spec.py -k schema_descriptions`

<!-- /torve:managed -->
