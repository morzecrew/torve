<!-- torve:managed src/torve/config — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/config/`

### S-0053/D-1 — `LOCKED` (The item model and the rebuilt corpus)

The specification the engine reads is one pydantic model in `domain/spec.py`, `extra="forbid"`; every reader — planner, importer, check, health, show, intake lint, standing inheritance — consumes the model and never a parser's rows

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Six consumers stop re-shaping rows; a field the author wrote cannot be dropped on the way to the record; S-0007/D-17 stands because the *format* still terminates at `config/` while the model may cross
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-2 — `LOCKED` (The item model and the rebuilt corpus)

Markdown stays the authoring surface and the decision table stays a table (S-0016/D-14); typed additions are fenced YAML blocks the model validates, unknown keys refused

- Paths: `.torve/specs/**` `src/torve/config/spec.py`
- Consequence: No migration of any existing document to load; the probe's YAML-per-document root is refused with its reason in §5.2
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-3 — `ASSUMED` (The item model and the rebuilt corpus)

Five fenced kinds: `decision-details`, `invariants`, `alternatives`, `questions`, `changes`; prose sections become `DesignSection`s keyed by heading slug and are typed no further

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: Typing an argument fragments it; what is typed is what was already a list

### S-0053/D-4 — `LOCKED` (The item model and the rebuilt corpus)

A row's grade or paths change only through `torve rfc amend`, which records the typed diff with the prior value in a `changes` fence under the amendment heading; a grade or paths mismatch against the last recorded change fails `rfc check`. A text-only mismatch is editorial drift: a warning, re-stamped by `torve rfc fix` with the before and after recorded, never an `A-n`

- Paths: `src/torve/config/spec_emit.py` `src/torve/config/spec.py`
- Consequence: The prior value exists at exactly one moment and is kept on both lanes; a typo costs one command, a regrade costs an amendment, and 12 of 14 already lost by hand-editing are the last
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-7 — `ASSUMED` (The item model and the rebuilt corpus)

A row whose every glob matches nothing on an accepted, implemented document is path rot: reported by `rfc check`, retired by `amend --retire --reason path-rot` (or `check --fix-rot`), recorded as `decision.retired` on import; never automatic on load, never a red on the document

- Paths: `src/torve/application/decisions.py` `src/torve/config/spec_emit.py`
- Consequence: 27 rows today, 8 `LOCKED`, stop rendering as governance while governing nothing

### S-0053/D-10 — `LOCKED` (The item model and the rebuilt corpus)

The next document number derives as the maximum plus one over the corpus path **and** the archive (S-0016/D-24 extended); identifiers are never reused (S-0016/D-26 unchanged)

- Paths: `src/torve/config/spec.py`
- Consequence: A gap after the archive is a gap, not a free number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-14 — `ASSUMED` (Decisions as gates and the projections beside the code)

The default review skill set is `[reading-isnt-proof, ratchet-what-you-build]`

- Paths: `src/torve/config/runconfig.py`
- Consequence: Two skills that declare the role reach it

### S-0055/D-4 — `LOCKED` (Standing decisions)

Agents hold no provider credentials; the broker injects them at its own boundary, routes providers at the wire and meters spend; every configuration file carries variable names, never values

- Paths: `src/torve/adapters/broker/**` `src/torve/config/runconfig.py` `.torve/config.yaml`
- Consequence: A sandbox that is compromised leaks nothing it was not given; a committed file never holds a secret
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-5 — `LOCKED` (Standing decisions)

Agents do not communicate; executor memory is off by default, per slot when on, and never mounted in a shadow run

- Paths: `src/torve/application/shadow.py` `src/torve/config/runconfig.py`
- Consequence: Shared memory is a communication channel with a euphemism; a remembered replay is an incomparable number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-14 — `ASSUMED` (Standing decisions)

The gate manifest lives in the consuming repository at `.torve/gates.yaml`; builtins are named `@name`; every entry carries `state` and `origin`; a new gate enters at `shadow` and is promoted on soak evidence

- Paths: `.torve/gates.yaml` `src/torve/config/manifest.py`
- Consequence: Promotion is a separate act with a number behind it, never a default

### S-0055/D-30 — `ASSUMED` (Standing decisions)

Everything Torve owns in a consuming repository lives under `.torve/`; the gate manifest and the run configuration are separate files; run configuration is read from where the runner was launched and never from the repository under work

- Paths: `.torve/config.yaml` `.torve/gates.yaml` `src/torve/config/runconfig.py` `src/torve/config/layout.py`
- Consequence: A worked-on repository cannot configure the engine working on it

### S-0055/D-31 — `ASSUMED` (Standing decisions)

Providers a repository's contents may reach are enforced at dispatch, before a sandbox exists; an empty default denies every real provider

- Paths: `src/torve/config/runconfig.py` `src/torve/application/dispatch.py`
- Consequence: Silence is not a policy; it is a closed door

### S-0055/D-32 — `ASSUMED` (Standing decisions)

A tier resolves through profiles in the operator's own configuration directory, never the repository under work; a missing profile or an unknown skill refuses rather than falls back

- Paths: `src/torve/config/runconfig.py` `src/torve/application/skills.py`
- Consequence: Resolution is fail-closed throughout

### S-0055/D-33 — `ASSUMED` (Standing decisions)

Configuration routes by nature — identity in the image, task context in the workspace, secrets as environment names, knobs in the command, state on the slot volume — one item, one channel

- Paths: `src/torve/config/runconfig.py` `src/torve/adapters/agent/harness.py`
- Consequence: A second channel for a secret is a leak; a repository-carried harness config is an injection surface

### S-0055/D-34 — `ASSUMED` (Standing decisions)

A stdio MCP server is image content; a remote MCP endpoint is an egress destination under provider routing; no execution sandbox is given a read surface onto the record

- Paths: `src/torve/config/runconfig.py` `.torve/sandbox/**` `src/torve/cli/mcp.py`
- Consequence: Files in the worktree carry task context; reach into the record is the planner's alone

### S-0056/D-1 — `LOCKED` (Structure for everything) — implementation: none

A document is one YAML file, `rfcs/NNNN-slug.yaml`, in the `Document` model's own shape and key order; loading is the model's validator plus the corpus checks; `schema_version` 2, and 1 is refused

- Paths: `src/torve/config/spec.py` `src/torve/domain/spec.py`
- Consequence: The markdown parser, `load_fences`, the heading and table regexes are deleted; every reader of the corpus is unchanged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-4 — `LOCKED` (Structure for everything) — implementation: none

Every writer — `amend`, `fix`, `retire`, `archive`, `new` — mutates the model and writes it through one serializer; comments are not preserved and `check` refuses one outside the schema header line; `fmt` survives as `--check` only

- Paths: `src/torve/config/spec_emit.py` `src/torve/cli/spec.py`
- Consequence: There is no second renderer to drop a field; the `character:` defect closes by construction
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-9 — `LOCKED` (Structure for everything) — implementation: none

With a store configured, `plan` mints into the record and writes no file; dispatch projects `.torve/tasks/<id>/contract.yaml` into the worktree, gitignored; the log written there is imported after the attempt; without a store the files are the record as today

- Paths: `src/torve/application/planner.py` `src/torve/application/session.py` `src/torve/config/layout.py`
- Consequence: The board is the only place a task is; the file exists for the attempt that reads it
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

### S-0057/D-3 — `LOCKED` (The specification is a directory)

The corpus is `.torve/specs/` and the archive `.torve/archive/`; the configuration key is `specs.path` and `rfcs.path` is refused naming it

- Paths: `src/torve/config/runconfig.py` `src/torve/config/layout.py` `.torve/specs/**` `.torve/archive/**`
- Consequence: Every input torve reads is under `.torve/`; the root carries no torve directory
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-4 — `LOCKED` (The specification is a directory)

`torve spec` absorbs every `torve rfc` verb and `cli/rfc.py` is deleted; the gate is `spec-valid`; the skill is `spec-writer`; `rfc_emit.py` becomes `spec_emit.py`; new prose says specification or document

- Paths: `src/torve/cli/spec.py` `src/torve/cli/spec.py` `src/torve/config/spec_emit.py` `skills/**` `.torve/gates.yaml`
- Consequence: One namespace for the corpus; old prose and test file names keep the old word
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-9 — `LOCKED` (The specification is a directory)

`spec check` resolves every citation-shaped identifier in tracked files under `src/**`, `pages/**` and every `AGENTS.md`, `CLAUDE.md` and `README.md` over the corpus and the archive: unknown is a problem naming `file:line`, retired a warning, archived clean; tests and skills are not scanned

- Paths: `src/torve/config/spec.py`
- Consequence: A comment that cites a row is checked like a `cites` list; an identifier can never be invented in the code
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-11 — `ASSUMED` (The specification is a directory)

`spec check` warns when `implementation: complete` names a phase no landing covers, and when every phase has a landing and `implementation` is not complete

- Paths: `src/torve/config/spec.py`
- Consequence: The status field and the execution file cannot drift apart silently

### S-0058/D-1 — `LOCKED` (One grammar and the anatomy)

Every item the corpus defines has one global identifier, `S-NNNN/<local>` — `D-n`, `I-n`, `Q-n`, `A-n`, `P-n` or a prose key — and is written inside its own document by the local half alone; the document is the namespace, amendments included, and tasks stay `T-NNNN`

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py`
- Consequence: One regex reads every citation; nothing is spelled twice; sections and phases become citable
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-3 — `LOCKED` (One grammar and the anatomy)

`check_cites`, `check_tree`, `spec cites` and `spec show` read the one grammar; a legacy identifier in the corpus or the tree is a problem naming its replacement, `spec show` answers a legacy identifier from the mapping and says which it was, and `cites` reads commit trailers and the record's history through it

- Paths: `src/torve/config/spec.py` `src/torve/cli/spec.py`
- Consequence: A citation written the old way after the conversion cannot survive a check
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-4 — `LOCKED` (One grammar and the anatomy)

`document.yaml` carries the prose as typed keys — `summary`, `motivation`, `current_state`, `goals`, `non_goals`, `tests`, `risks` required of an accepted design, `docs` and `out_of_scope` optional, `design` a keyed list with at least one entry once a design is accepted, `sections` the extras capped at eight — with keys unique document-wide and never a family shape; an accepted convention owes its summary alone; `description` is dropped and the summary's first sentence routes

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py`
- Consequence: `yq .motivation` answers; a document without a motivation cannot be accepted; the routing line is written once
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-6 — `LOCKED` (One grammar and the anatomy)

Execution is a directory, `execution/<task>-<attempt>-<instant>.yaml`, one landing per file, written once and never deleted; the loader reads it sorted by instant into `landings`; an identical replay is a no-op and a restarted attempt lands under a new instant; the scope gate exempts the directory

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: Two candidates of one document never conflict at merge; no landing is refused for a number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-8 — `ASSUMED` (One grammar and the anatomy)

`check` warns for a LOCKED row whose declared paths match files none of which cites it

- Paths: `src/torve/config/spec.py`
- Consequence: A comment an agent deletes is heard; a row over generated files reads a warning and decides

### S-0059/D-4 — `LOCKED` (One word for the document, and the tree as the record)

A gate's `origin` is `structural`, `leak/<task>` or a citation the grammar accepts; the runner's decision gates carry the row's own id as origin; the manifest's `rfc/NNNN` become `S-NNNN`

- Paths: `src/torve/config/manifest.py` `src/torve/gates/runner.py` `src/torve/gates/sabotage.py` `.torve/gates.yaml`
- Consequence: `spec cites S-0054/D-2` finds the gate the row minted
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

### S-0059/D-10 — `LOCKED` (One word for the document, and the tree as the record)

The runner writes no `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` or `Torve-Decisions` trailer, retiring S-0010/D-4; the landing carries `decisions: [{id, grade}]`, the rows the contract carried; `Torve-Bypass`, `Torve-Fixes` and `Torve-Checkpoint` stay

- Paths: `src/torve/application/runner.py` `src/torve/domain/spec.py` `src/torve/config/spec_emit.py`
- Consequence: One record of a landing; the commit author stays the agent's identity (S-0010/D-2)
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-11 — `LOCKED` (One word for the document, and the tree as the record)

A task naming no document lands under `.torve/execution/` in the same file shape; the loader reads it beside the corpus and the archive, and the scope gate exempts it

- Paths: `src/torve/config/layout.py` `src/torve/config/spec.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: An operator's ask and a standing job land with a record; the revert leg finds them
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

### S-0060/D-8 — `LOCKED` (A source is a file, and the contract names it)

A document's `kind` stays `design` or `convention`: it answers what prose an accepted document owes and nothing else, so a bug worth a document is a design whose motivation is the defect, an audit's standing rules are a convention, and where the work came from is the source

- Paths: `src/torve/domain/vocabulary.py` `src/torve/config/spec.py` `skills/spec-writer/**`
- Consequence: One axis per field; the corpus never grows a second way to say provenance
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-1 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

An agent profile carries what the agent is — skills, plugins, prompt_extras — and nothing about how it runs.

- Paths: `src/torve/config/agents.py`
- Consequence: a persona file can no longer decide where a conviction routes, because routing is not a key it has
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-2 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

A harness manifest carries how a model is reached — adapter, command, image and how auth arrives — and names no model.

- Paths: `src/torve/config/agents.py`
- Consequence: one manifest serves every model that image can run, and a model change is a one-line seat edit
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-3 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

The seat names one harness and at most one profile, and holds what varies per run — model, provider, routing, clocks and caches.

- Paths: `src/torve/config/runconfig.py`
- Consequence: `tiers:` reads as an assignment rather than as a full agent definition
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-4 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

The charter's base working rules stay unaddressable from configuration; a profile appends with prompt_extras and a `prompt` key is refused by name.

- Paths: `src/torve/config/agents.py`
- Consequence: no file outside the repository under work can disarm the rules two blocking gates convict on
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-8 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

One merge level survives the split — a seat merges its harness and its profile, and neither references another of its kind.

- Paths: `src/torve/config/agents.py`
- Consequence: the file a refusal names is the file that carries the bad key

### S-0061/D-9 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

A seat carrying a moved key is refused by name, with the file the key belongs in named in the message.

- Paths: `src/torve/config/runconfig.py`
- Consequence: the migration guide is the error, so no repository needs one written

### S-0061/D-10 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

The profile and the harness manifest are committed files of the operating repository under `.torve/`, not files on the operator's machine.

- Paths: `src/torve/config/agents.py` `src/torve/cli/init.py`
- Consequence: a regime is reconstructable from a checkout, and two operators running one repository cannot silently run different equipment
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-11 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

A seat naming no profile resolves the profile that declares the task's role, and SkillsConfig.sets retires.

- Paths: `src/torve/config/runconfig.py` `src/torve/config/agents.py`
- Consequence: the default equipment for a role and a named set stop being two mechanisms answering one question in two files

### S-0061/D-12 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

A profile or a manifest may carry `name`, and that is the identity a seat resolves; a file that carries none is known by its filename stem. Two files claiming one name are refused at load, naming both.

- Paths: `src/torve/config/agents.py` `.torve/agents/**` `.torve/harnesses/**`
- Consequence: a file can be renamed without breaking the seat that names it, and two files claiming one identity are refused naming both rather than resolved by whichever the directory listed first

### S-0061/D-13 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

The profile that supplies a role's default equipment declares `role`; a filename is never read as a role. Two profiles declaring one role are refused, naming both.

- Paths: `src/torve/config/agents.py` `.torve/agents/**`
- Consequence: a seat profile may be called anything, and a role default that is never applied is a visible absence rather than a filename nobody noticed was wrong

### S-0062/D-1 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A profile declares equipment as typed items — a kind, a source, and a ref where the source is fetched — and `skills` and `plugins` fold into it.

- Paths: `src/torve/config/equipment.py` `src/torve/config/agents.py` `.torve/agents/**`
- Consequence: what an agent has is one list with a version per item, instead of two fields with a version for one of them and no external source for either
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-2 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A harness manifest declares which equipment kinds it accepts, and a kind a profile declares and the manifest does not is refused at load, unless the engine can deliver it itself — package-data skills alone, which `materialize` writes into the worktree. Which flag carries each kind is the image's own `/opt/torve/equip` (S-0063/D-3), not a template here.

- Paths: `src/torve/config/agents.py` `.torve/harnesses/**`
- Consequence: a harness that cannot be given something says so once, in its own file, rather than in an attempt that ran without it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-3 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A source is `torve:<name>`, `local:<path>` or `github:<owner>/<repo>`; a fetched source requires a ref and the other two refuse one.

- Paths: `src/torve/config/equipment.py`
- Consequence: no equipment can enter a run at a version nobody wrote down
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-7 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A profile may declare a `prepare` command; torve runs it in the sandbox before the agent, with its own clock, and a non-zero exit is an infrastructure failure that convicts nothing.

- Paths: `src/torve/application/session.py` `src/torve/config/equipment.py`
- Consequence: an index that fails to build ends the attempt as what it is, rather than as a model that could not make the battery pass
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-12 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

Equipment resolves in two layers — the profile named for the task's role, then the seat's profile — appended and deduplicated by kind and source, the seat's ref winning; a profile never names the role it serves.

- Paths: `src/torve/config/equipment.py` `src/torve/config/agents.py`
- Consequence: a seat profile that declares one plugin adds it to the role's equipment instead of replacing it, and a reviewer is equipped by its own seat rather than by the seat it reviews
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-13 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

A repository names an equipment ref once, in `.torve/pins.yaml`, keyed by source; a profile that omits `ref` takes the pin, and one that writes a ref keeps it. A fetched source with neither is refused, naming both.

- Paths: `src/torve/config/equipment.py` `src/torve/config/agents.py` `.torve/pins.yaml` `.torve/agents/**`
- Consequence: a ref moves in one place instead of once per profile that named the source, and a profile that forgot to move with it cannot exist

### S-0063/D-1 — `LOCKED` (The image knows how to equip itself)

A sandbox image carries `/opt/torve/run`, which invokes its harness; a manifest carries no command template and `command` is refused by name.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py`
- Consequence: the shell that knows how to start a harness lives beside the harness, and a seat cannot be misconfigured into a model that silently would not work
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-3 — `LOCKED` (The image knows how to equip itself)

A sandbox image carries `/opt/torve/equip`, which translates the equipment manifest into whatever its harness needs; S-0062/D-2's flag templates retire and `kinds` is what remains of the capability map.

- Paths: `src/torve/config/agents.py` `sandboxes/**`
- Consequence: equipment reaches a harness the way that harness takes it, decided beside the harness rather than by a renderer keeping up with three of them across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-4 — `LOCKED` (The image knows how to equip itself)

`kinds` on a manifest is the enumeration this harness accepts; a profile declaring a kind outside it is refused at load, naming both files, with the package-data skill exception S-0062/A-3 already carries.

- Paths: `src/torve/config/agents.py`
- Consequence: the refusal survives the templates, in reviewed configuration, without pulling an image to learn what a harness can take
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-10 — `ASSUMED` (The image knows how to equip itself)

A manifest's `env` mapping reaches the image's environment and nothing else reads it; a knob that is not there is a rebuild.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py`
- Consequence: an operator changes a permission mode in configuration, and a change to how the harness is invoked moves the image digest the telemetry already records

### S-0063/D-19 — `LOCKED` (The image knows how to equip itself)

Equipment never writes into a path the repository owns. A harness that reads equipment from the workspace declares its own root on the manifest as `equip_root`; the engine excludes that root in the worktree and names it to the image as `TORVE_EQUIP_ROOT`.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py` `sandboxes/**` `.torve/harnesses/**`
- Consequence: an attempt commits its own work and nothing else, and a repository's reviewed skills are never overwritten by a packaged copy of the same name
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-1 — `LOCKED` (A provider is a record, and the seam carries scalars)

A provider is a file, `.torve/providers/<name>.yaml`, holding the credential's variable name, the clocks, the routes and the model roster; `broker.providers` folds into it and `upstream` is named `base_url`.

- Paths: `src/torve/config/providers.py` `src/torve/config/runconfig.py` `.torve/providers/**`
- Consequence: a provider's facts are validated, hashed and reviewed like every other file under `.torve/`, instead of being a string the engine sets and never reads
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-2 — `LOCKED` (A provider is a record, and the seam carries scalars)

A route owns a dialect. `routes` is keyed by api name — `openai`, `anthropic` — and each carries its own `base_url` and its own compat facts; a provider serving two dialects is two routes on one credential.

- Paths: `src/torve/config/providers.py` `.torve/providers/**`
- Consequence: the quirks that differ between two URLs of one provider stop being attributed to the provider, so a seat on either route gets the facts that are true of it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-3 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A model entry's key is what a seat writes and its `id` is what reaches the provider and what the regime hash records; a key with no `id` is its own id.

- Paths: `src/torve/config/providers.py` `src/torve/application/telemetry.py`
- Consequence: a slug that is awkward to type or to use as a path segment gets a local shorthand, and renaming that shorthand cannot move a regime digest

### S-0064/D-4 — `LOCKED` (A provider is a record, and the seam carries scalars)

A harness manifest declares the dialects it speaks as `api`, and a seat whose harness and provider share no dialect is refused at load, naming both files.

- Paths: `src/torve/config/agents.py` `src/torve/config/runconfig.py` `.torve/harnesses/**`
- Consequence: an unreachable pairing is a configuration error with two filenames in it rather than a 404 from an attempt that had already started
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-5 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A model declares the reasoning levels it has and a seat names one of them; a level the model does not declare is refused at load, and the level crosses the seam as a word.

- Paths: `src/torve/config/providers.py` `src/torve/config/runconfig.py`
- Consequence: the engine refuses an unreachable effort before a sandbox exists, where the endpoint would refuse it per request after one does

### S-0064/D-6 — `LOCKED` (A provider is a record, and the seam carries scalars)

Reasoning effort is the seat's and never the agent profile's.

- Paths: `src/torve/config/runconfig.py` `src/torve/config/agents.py`
- Consequence: a profile stays portable across harnesses and models, and an effort that cannot be honoured is refused rather than dropped
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-9 — `ASSUMED` (A provider is a record, and the seam carries scalars)

`api_key_env` retires from the harness manifest: a credential is a property of the provider, which names its own `key_env`. `auth_volume` and `auth_mount` stay.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py` `.torve/harnesses/**`
- Consequence: one file decides which credential reaches a sandbox, and the refusal that keeps a brokered seat honest has one field to watch instead of two

### S-0064/D-10 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A value torve has no name for rides through as a route's `extra`, handed to the image verbatim, unvalidated, and visibly so.

- Paths: `src/torve/config/providers.py`
- Consequence: a provider quirk nobody has modelled yet does not block a seat, and it is obvious in review which values the engine is standing behind and which it is only carrying

### S-0064/D-12 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A model entry may carry `price`, and where it does the attempt's cost is computed from the record and the token counts; a harness's self-reported cost is kept beside it as the adapter's claim and is never the number. Where it does not, cost stays unreported rather than invented.

- Paths: `src/torve/config/providers.py` `src/torve/application/dispatch.py`
- Consequence: the ledger stops depending on whether a harness recognises the model it was pointed at, and the divergence check compares two numbers that are both about this call — torve's arithmetic against the broker's metering — instead of comparing a rate card to reality

### S-0066/D-7 — `ASSUMED` (An attempt's inputs are declared, and the image says what it loaded)

An equipment item's `kind` names the channel a harness carries it on and not the shape the item must be, so a profile declaring a kind two harnesses both accept can still be unreadable to one of them; a harness declares the shapes it takes, and a profile whose item does not match is refused at load with both files named

- Paths: `.torve/harnesses` `src/torve/config/agents.py`
- Consequence: the refusal S-0063/D-4 promises — before an image is pulled — covers the case that actually happened, instead of an unhandled exception at `wall 0s`

### S-0068/D-1 — `ASSUMED` (The refusal that is missing, and the one nobody answered)

`auto_merge: true` with no promotion criterion armed is refused at load, naming the field and what to set; any one of `require_ci`, `require_review`, `approvals` or `quiet_window` counts as armed

- Paths: `src/torve/config/runconfig.py`
- Consequence: the one boolean that converts five unused criteria into five unset ones can no longer be flipped alone, and a deliberate choice stays one line away

### S-0070/D-5 — `ASSUMED` (What is committed may not depend on what is not)

Path rot is a glob that governs nothing, not a glob whose files the repository deliberately does not commit; the check reads `.torve/.gitignore` and says which it found

- Paths: `src/torve/application/decisions.py` `src/torve/config/spec.py`
- Consequence: `torve spec check` passes in a clean clone, so `spec-valid` and the acceptance command stop depending on a task directory that dispatch happens to have written

### S-0071/D-6 — `ASSUMED` (The battery costs what it costs for reasons unrelated to what it judges)

A gate declares what it judges, and an attempt that changed none of it is reported skipped rather than run

- Paths: `src/torve/gates/runner.py` `src/torve/config/manifest.py` `.torve/gates.yaml`
- Consequence: a gate stops spending the battery's wall clock on attempts it has no opinion about, and the record says it had none rather than showing a pass it did not earn

### S-0072/D-1 — `LOCKED` (A hook is one intent, declared once per harness)

An equipment item carries its payload at the root and one directory per harness beside it; a harness reads only its own, and an item with no directory for a harness a seat could use is refused at load, naming the profile and the manifest

- Paths: `.torve/agents/hooks` `src/torve/config/agents.py`
- Consequence: the disagreement between two harnesses' idea of one kind becomes a message before an image is pulled, instead of an exception inside a container
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0079/D-5 — `ASSUMED` (The night as a typed record)

The night's terms are a `night:` section of the runner configuration, refused at load when the budget is zero on both axes or a named stop class is not in `EscalationReason`

- Paths: `src/torve/config/runconfig.py`
- Consequence: a misspelt escalation class is refused while a person is standing there rather than at 04:00, when the stop condition it names would silently never fire

### S-0080/D-1 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The landing mode is a term of the runner configuration — `promotion.landing`, `local` or `pull_request` — and is never inferred from whether a remote exists or from whether `scm.open_pr` is set

- Paths: `src/torve/config/runconfig.py`
- Consequence: a repository's landing act changes only when somebody writes that it should, and the record of what a run was includes which act it was

### S-0080/D-2 — `ASSUMED` (The lane opens a pull request, and a person lands it)

`landing: pull_request` is refused at load when `scm.repo` is unset or `scm.open_pr` is false, with the field named and what to set

- Paths: `src/torve/config/runconfig.py`
- Consequence: a mode that could only ever fail at the first landing fails instead while a person is standing at the terminal

### S-0083/D-1 — `ASSUMED` (The pull request is one per document, not one per task)

The landing unit is a second term of the runner configuration — `promotion.unit`, `task` or `document`, defaulting to `task` — beside `promotion.landing`, and is never inferred from whether the ready candidates happen to share a document

- Paths: `src/torve/config/runconfig.py`
- Consequence: every repository configured today keeps landing one pull request per task, and a repository that changes the unit has said so in a file somebody reviewed

### S-0083/D-2 — `ASSUMED` (The pull request is one per document, not one per task)

Under `landing: local` the unit is ignored rather than refused — a local landing has no pull request to be one per anything

- Paths: `src/torve/config/runconfig.py`
- Consequence: a repository that moves between the two modes edits one key, and a `unit` that survives the switch back is inert rather than wrong

### S-0084/D-5 — `ASSUMED` (The review leg: a pull request's threads become work on its branch)

The leg's terms are a section of the runner configuration — which logins are bots, how many rounds a pass may mint, and whether the leg runs at all — defaulting off, and refused at load when it is on under any landing but `pull_request` with `unit: document`

- Paths: `src/torve/config/runconfig.py`
- Consequence: a repository configured today gains nothing until somebody writes that it should, and a configuration that could only ever fail at the first thread fails while a person is standing at the terminal

### S-0085/D-1 — `ASSUMED` (A document builds on another document's tree)

A phasing file names the documents whose landed tree its work builds on under a top-level `after`, as document ids; the header's `depends_on` keeps its one meaning, decision inheritance, and says nothing about trees

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `.torve/schemas/phasing.json`
- Consequence: S-0009 declares `after: [S-0008]` and `depends_on: []` and both are true; a document may name one document in both lists or in either

### S-0086/D-6 — `ASSUMED` (The review tier's word reaches the work) — implementation: none

The leg's section gains `sources`, a list of `forge` and `record` defaulting to `[forge]`; `record` is refused at load under any landing but `pull_request` with `unit: document`; the second ask has no term

- Paths: `src/torve/config/runconfig.py` `tests/test_runconfig.py`
- Consequence: a configuration that turned the leg on before this document changes nothing; the second ask is what an unreadable verdict costs wherever the tier runs

## Invariants holding over `src/torve/config/`

- **S-0059/I-3**: Every property of every schema `torve init` writes carries a description
  - Paths: `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py`
  - Check: `uv run pytest tests/test_spec.py -k schema_descriptions`
- **S-0061/I-1**: No configuration key reaches the prompt before the charter's base working rules — prompt_extras appends, and nothing replaces.
  - Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py`
  - Check: `uv run pytest tests/test_agents.py -k base_rules`
- **S-0061/I-2**: Every key of the old flat tier body lives in exactly one of the three files, and the other two refuse it by name.
  - Paths: `src/torve/config/agents.py` `src/torve/config/runconfig.py`
  - Check: `uv run pytest tests/test_agents.py -k one_home`
- **S-0062/I-2**: Every equipment item a run used is named by a cache key that resolves to a source and a ref an operator wrote.
  - Paths: `src/torve/config/equipment.py` `src/torve/application/telemetry.py`
  - Check: `uv run pytest tests/test_equipment.py -k reconstructable`
- **S-0063/I-1**: No harness manifest carries a shell line, and no engine code substitutes into one.
  - Paths: `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py`
  - Check: `uv run pytest tests/test_agents.py -k no_shell`
- **S-0064/I-1**: Every dispatchable seat names a model its provider's roster lists and a dialect its harness speaks; neither is discovered by an attempt.
  - Paths: `src/torve/config/runconfig.py` `src/torve/config/providers.py`
  - Check: `uv run pytest tests/test_providers.py -k pairing`

<!-- /torve:managed -->
