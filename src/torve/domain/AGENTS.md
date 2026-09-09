<!-- torve:managed src/torve/domain — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/domain/`

### D-53.1 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

The specification the engine reads is one pydantic model in `domain/spec.py`, `extra="forbid"`; every reader — planner, importer, check, health, show, intake lint, standing inheritance — consumes the model and never a parser's rows

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Six consumers stop re-shaping rows; a field the author wrote cannot be dropped on the way to the record; D-7.17 stands because the *format* still terminates at `config/` while the model may cross
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.3 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Five fenced kinds: `decision-details`, `invariants`, `alternatives`, `questions`, `changes`; prose sections become `DesignSection`s keyed by heading slug and are typed no further

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Typing an argument fragments it; what is typed is what was already a list

### D-53.5 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Every row carries a content fingerprint; a mismatch between a contract's copied row and the row as it stands is reported as *suspect* by `decisions show` and `rfc health`, never as a conviction

- Paths: `src/torve/domain/spec.py` `src/torve/application/decisions.py`
- Consequence: Copy-at-mint becomes checkable; D-31.3's no-retroactive rule is preserved by making the mismatch a reading, not a red

### D-53.14 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

`domain/spec.py` imports nothing but pydantic and `domain/rfc.py`, so extracting it into its own distribution is a packaging act and never a rewrite

- Paths: `src/torve/domain/spec.py`
- Consequence: The model is the reusable half; torve's grades, paths and checks are a profile over it

### D-53.15 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

`check` on a row is authored, never derived; a `LOCKED` row without one is reported *soft* by `rfc health`; nothing runs a check in this document

- Paths: `src/torve/domain/spec.py`
- Consequence: 15 of 19 derived checks in the probe were guesses; running checks as gates is the next document's design

### D-53.16 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

The fingerprint covers text, grade and paths; consequence, rationale and check are outside it, since a contract copies the first three at mint and a human reads the rest (Q-53.1, settled in draft)

- Paths: `src/torve/domain/spec.py`
- Consequence: An edit to a consequence is free; an edit to what an executor is bound by is recorded

### D-54.1 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

`InheritedDecision` gains `consequence` and `check`, copied at mint from the model, carried by `decision.recorded`, rendered after each row in the prompt; the fingerprinted set stays text, grade and paths

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: A gate reads the contract and never the corpus (D-7.18 stands); the reason a row exists reaches the executor for the first time
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.1 — `LOCKED` (RFC 0055 — Standing decisions)

Models never decide what work exists or whether it is finished; the runner executes state transitions from facts — exit codes, gate outcomes, approvals — and an agent reports observations that never cause a transition

- Paths: `src/torve/application/runner.py` `src/torve/application/manager.py` `src/torve/domain/states.py`
- Consequence: A reviewer's severity is data whose consequence configuration sets; the planner invokes no model at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.6 — `ASSUMED` (RFC 0055 — Standing decisions)

Escalation reasons are a closed enum mapped to exit codes; `locked_conflict` and `underspecified` are halts on working judgement, one indicting the code and the other the contract

- Paths: `src/torve/domain/states.py` `src/torve/cli/options.py`
- Consequence: A new reason is an amendment and a code path, never a free string

### D-55.7 — `LOCKED` (RFC 0055 — Standing decisions)

A task contract is immutable once minted: a changed contract is a new task, a re-mint is a version and never a transition, and a task in flight is never re-minted

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: An executor's contract cannot drift out from under it; `torve plan` refuses to re-mint over minted phases and leaves the decision to a person
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.9 — `LOCKED` (RFC 0055 — Standing decisions)

A contract copies its rows' grade, text and paths at mint time; a grade is never resolved at read time

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py`
- Consequence: A regrade never rewrites the judgement of a task that ran under the old grade
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.12 — `ASSUMED` (RFC 0055 — Standing decisions)

An empty decision list on a contract is legal and explicit; "none apply" is distinct from "field forgotten"

- Paths: `src/torve/gates/decisions_reported.py` `src/torve/domain/task.py`
- Consequence: The document-less lane is governed by standing inheritance, not by silence

### D-55.37 — `LOCKED` (RFC 0055 — Standing decisions)

A fact is an event of a closed kind vocabulary with an authority table; an agent may write only `divergence.recorded` and `message.sent`; no update command exists on the record

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py`
- Consequence: An audit trail that can be edited cannot be trusted; the authority table refuses before any store sees the write
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.1 — `LOCKED` (RFC 0056 — Structure for everything)

A document is one YAML file, `rfcs/NNNN-slug.yaml`, in the `Document` model's own shape and key order; loading is the model's validator plus the corpus checks; `schema_version` 2, and 1 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py`
- Consequence: The markdown parser, `load_fences`, the heading and table regexes are deleted; every reader of the corpus is unchanged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.2 — `LOCKED` (RFC 0056 — Structure for everything)

`DecisionDetail` folds into `Decision`; the fenced kinds are gone; a row carries `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by` and its `fingerprint` on itself

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: `inherit_decisions` reads the row; the frontmatter fingerprint map is gone
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.3 — `LOCKED` (RFC 0056 — Structure for everything)

Prose is `sections[].md`, a string the engine never parses, with `key` and `heading` beside it; `level` and `order` are dropped

- Paths: `src/torve/domain/spec.py`
- Consequence: Markdown inside a body is welcome and invisible to every check
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.1 — `LOCKED` (RFC 0057 — The specification is a directory)

A specification is a directory `S-NNNN/` — the identifier and nothing else — of `document.yaml`, `decisions.yaml`, `amendments.yaml` and `execution.yaml`, split by who writes each; an absent file is an empty list; the loader joins them into the one `Document` every reader keeps reading; `schema_version` 3, and 2 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py` `src/torve/config/spec_emit.py`
- Consequence: The author's file changes only by the author; `amended_by` is derived and gone; `Document.path` names a directory
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.2 — `LOCKED` (RFC 0057 — The specification is a directory)

A section is `key` and `md`; the heading is rendered from the key and the number from the position; `check` refuses a section with an empty body, a typed-kind fence, the decisions table header or an amendment identifier as its key

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: A typed list exists in one place; a heading cannot disagree with its key
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.1 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

Every item the corpus defines has one global identifier, `S-NNNN/<local>` — `D-n`, `I-n`, `Q-n`, `A-n`, `P-n` or a prose key — and is written inside its own document by the local half alone; the document is the namespace, amendments included, and tasks stay `T-NNNN`

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: One regex reads every citation; nothing is spelled twice; sections and phases become citable
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.4 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

`document.yaml` carries the prose as typed keys — `summary`, `motivation`, `current_state`, `goals`, `non_goals`, `tests`, `risks` required of an accepted document, `docs` and `out_of_scope` optional, `design` a keyed list with at least one entry once accepted, `sections` the extras capped at eight — with keys unique document-wide and never a family shape; `description` is dropped and the summary's first sentence routes

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py`
- Consequence: `yq .motivation` answers; a document without a motivation cannot be accepted; the routing line is written once
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.5 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

`phasing.yaml` holds `phasing` and `contract_example`, author-written and planner-read; a scope amendment touches it and the amendments file alone

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: Six files per document; the planner's list diffs apart from the prose
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.6 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

Execution is a directory, `execution/<task>-<attempt>-<instant>.yaml`, one landing per file, written once and never deleted; the loader reads it sorted by instant into `landings`; an identical replay is a no-op and a restarted attempt lands under a new instant; the scope gate exempts the directory

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: Two candidates of one document never conflict at merge; no landing is refused for a number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.7 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

The engine writes one instant, `YYYY-MM-DDTHH:MM:SSZ` in UTC, from `torve.base.clock.stamp()`, for amendments, landings, entries, telemetry, run state and their display; the dates that exist convert once to midnight UTC with the loss stated

- Paths: `src/torve/base/clock.py` `src/torve/domain/spec.py` `src/torve/application/telemetry.py` `src/torve/application/runstate.py` `src/torve/application/decisions.py` `src/torve/application/projections.py` `src/torve/cli/spec.py` `src/torve/cli/decisions.py`
- Consequence: A timeline over amendments, landings and entries sorts on one string
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
