<!-- torve:managed src/torve/cli — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/cli/`

### S-0053/D-9 — `LOCKED` (The item model and the rebuilt corpus)

The importer records every archived document as a source and every archived row as retired; `torve why` and `show` resolve archived identifiers and say they are archived

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: 705 log entries and 139 amendments keep their targets
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-13 — `ASSUMED` (The item model and the rebuilt corpus)

Each reader switches from `rfc_parse` to the model beside its old path with a parity assertion, one at a time, in phase 2; `rfc_parse.py` is deleted in phase 5 only after the archive lands

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: Two owners of the format exist for one bounded window, and the parity test is what bounds it

### S-0054/D-6 — `LOCKED` (Decisions as gates and the projections beside the code)

`torve spec project` renders a managed section into the `AGENTS.md` of every directory a standing row's paths or an accepted phase's scope names — rows with grade, text, consequence, check and state; invariants; contended paths — and a root index of governed directories; text outside the markers is never touched

- Paths: `src/torve/application/colocation.py` `src/torve/cli/spec.py`
- Consequence: Every harness and every person reads the rules where the code is, with no prompt change; a directory that stops being governed loses its section
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-7 — `LOCKED` (Decisions as gates and the projections beside the code)

The rendered sections are committed and drift-checked by a manifest gate running `torve spec project --check`; a hand edit inside the markers is drift naming the file

- Paths: `.torve/gates.yaml` `src/torve/cli/spec.py`
- Consequence: The INDEX.md rule (S-0016/D-17) applied to the tree; the projection cannot lie about the corpus
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-9 — `LOCKED` (Decisions as gates and the projections beside the code)

`torve spec show`, `paths`, `tests` and `why-not` form a read-only verb over the worktree's corpus and the archive beside it; a form that would need the record, another task or an escalation is refused by design

- Paths: `src/torve/cli/spec.py`
- Consequence: S-0007/mcp-as-the-read-surface stands: the sandbox gains disclosure over files it could open, never reach
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-6 — `ASSUMED` (Standing decisions)

Escalation reasons are a closed enum mapped to exit codes; `locked_conflict` and `underspecified` are halts on working judgement, one indicting the code and the other the contract

- Paths: `src/torve/domain/states.py` `src/torve/cli/options.py`
- Consequence: A new reason is an amendment and a code path, never a free string

### S-0055/D-11 — `ASSUMED` (Standing decisions)

A divergence entry is written only through `torve log divergence`, validated by the gate's own checks at intake, with located evidence; `drift_count` is derived, never declared

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `.torve/tasks/**`
- Consequence: A hand-edited log is refused by the same code that gates it

### S-0055/D-22 — `LOCKED` (Standing decisions)

User-facing strings carry no corpus coordinates: whoever runs the command has no corpus to resolve them

- Paths: `src/torve/gates/user_facing_text.py` `src/torve/cli/**`
- Consequence: Twenty-eight convictions and their corrections; the audience rule is a gate, not a style
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-28 — `ASSUMED` (Standing decisions)

A module named after a CLI verb lives under `cli/` and holds parsing and rendering only; presentation never crosses inward

- Paths: `src/torve/cli/**`
- Consequence: Logic and presentation stay separable where they are hardest to separate

### S-0055/D-34 — `ASSUMED` (Standing decisions)

A stdio MCP server is image content; a remote MCP endpoint is an egress destination under provider routing; no execution sandbox is given a read surface onto the record

- Paths: `src/torve/config/runconfig.py` `.torve/sandbox/**` `src/torve/cli/mcp.py`
- Consequence: Files in the worktree carry task context; reach into the record is the planner's alone

### S-0055/D-43 — `ASSUMED` (Standing decisions)

Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py`
- Consequence: A substrate surface change fails at the pin bump, not in production

### S-0055/D-44 — `ASSUMED` (Standing decisions)

`torve plan` is deterministic and invokes no model; it mints from exactly one accepted, committed document, copies that document's whole table, and refuses to re-mint over minted phases

- Paths: `src/torve/application/planner.py` `src/torve/cli/plan.py`
- Consequence: What to do with existing tasks is a human decision

### S-0055/D-56 — `ASSUMED` (Standing decisions)

Cadence belongs to the manager's pass; there is no resident scanning loop, and the standing legs — maintenance, relay, lane — run inside the pass under their switches

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Consequence: One process to run and supervise; a pause stops what advances the repository

### S-0056/D-4 — `LOCKED` (Structure for everything)

Every writer — `amend`, `fix`, `retire`, `archive`, `new` — mutates the model and writes it through one serializer; comments are not preserved and `check` refuses one outside the schema header line; `fmt` survives as `--check` only

- Paths: `src/torve/config/spec_emit.py` `src/torve/cli/spec.py`
- Consequence: There is no second renderer to drop a field; the `character:` defect closes by construction
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-6 — `LOCKED` (Structure for everything)

`torve rfc schema` writes `rfcs/schema/document.json` from the model, drift-checked by `rfc check`; every document's first line names it; `rfc new` emits 0055's shape with the header line

- Paths: `.torve/schemas/**` `src/torve/cli/spec.py`
- Consequence: An editor validates a row as it is typed; a new document starts small
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-7 — `ASSUMED` (Structure for everything)

`torve rfc list` replaces `INDEX.md`; `torve rfc render NNNN` is the only markdown writer and never the source of anything

- Paths: `src/torve/cli/spec.py`
- Consequence: The index is a query, not a file

### S-0057/D-4 — `LOCKED` (The specification is a directory)

`torve spec` absorbs every `torve rfc` verb and `cli/rfc.py` is deleted; the gate is `spec-valid`; the skill is `spec-writer`; `rfc_emit.py` becomes `spec_emit.py`; new prose says specification or document

- Paths: `src/torve/cli/spec.py` `src/torve/cli/spec.py` `src/torve/config/spec_emit.py` `skills/**` `.torve/gates.yaml`
- Consequence: One namespace for the corpus; old prose and test file names keep the old word
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-5 — `LOCKED` (The specification is a directory)

`torve init` writes `.torve/schemas/*.json` from every model torve reads from YAML — the four files, the contract, the log, the configuration, the manifest — and `.torve/.gitignore` with the patterns for what torve alone writes, idempotent, never a configuration or a manifest; every YAML torve writes names its schema on its first line; `doctor` and `spec check` redden when a schema lags its model or the ignore file lacks a minted pattern

- Paths: `.torve/schemas/**` `.torve/.gitignore` `.gitignore` `src/torve/cli/init.py` `src/torve/cli/doctor.py`
- Consequence: An editor validates any torve YAML as it is typed; an adopting repository ignores the right files without copying a block; `init` is the initialisation there is
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-7 — `LOCKED` (The specification is a directory)

`execution.yaml` holds landings — task, phase, attempt, time, agent, the commit when the lander knows it, and the log's entries typed as `LogEntry` — appended by one function the runner calls before the candidate commit, whose trailers name the task so the field stays empty there, and `torve log land --commit SHA` exposes for a landing made by hand; a contract naming no document lands nowhere and says so

- Paths: `src/torve/application/divergence.py` `src/torve/application/runner.py` `src/torve/cli/log.py`
- Consequence: Every clone carries what execution found, beside the rows it informs; the task directory carries nothing git keeps
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-8 — `ASSUMED` (The specification is a directory)

`torve decisions import` reads every execution file, live and archived, and records the divergence and landing events the record lacks, idempotent by task, attempt, decision and time; the attempt-time `ingest` is unchanged

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: A clone without a store rebuilds the same record from the tree

### S-0057/D-10 — `ASSUMED` (The specification is a directory)

`torve spec cites IDENT` lists the code lines, landings, amendments and documents that cite an identifier

- Paths: `src/torve/cli/spec.py`
- Consequence: The row's side of the link is a query, not a grep

### S-0057/D-13 — `OPEN` (The specification is a directory)

Whether `spec cites` also reads the `Torve-Decisions` trailers of the commit history

- Paths: `src/torve/cli/spec.py`
- Consequence: Decided by whoever executes phase 4, logged

### S-0058/D-3 — `LOCKED` (One grammar and the anatomy)

`check_cites`, `check_tree`, `spec cites` and `spec show` read the one grammar; a legacy identifier in the corpus or the tree is a problem naming its replacement, `spec show` answers a legacy identifier from the mapping and says which it was, and `cites` reads commit trailers and the record's history through it

- Paths: `src/torve/config/spec.py` `src/torve/cli/spec.py`
- Consequence: A citation written the old way after the conversion cannot survive a check
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-7 — `LOCKED` (One grammar and the anatomy)

The engine writes one instant, `YYYY-MM-DDTHH:MM:SSZ` in UTC, from `torve.base.clock.stamp()`, for amendments, landings, entries, telemetry, run state and their display; the dates that exist convert once to midnight UTC with the loss stated

- Paths: `src/torve/base/clock.py` `src/torve/domain/spec.py` `src/torve/application/telemetry.py` `src/torve/application/runstate.py` `src/torve/application/decisions.py` `src/torve/application/projections.py` `src/torve/cli/spec.py` `src/torve/cli/decisions.py`
- Consequence: A timeline over amendments, landings and entries sorts on one string
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-10 — `ASSUMED` (One grammar and the anatomy)

The skill and its template, the schemas `torve init` writes, the projections beside the code and the operating page follow the grammar and the anatomy in the same phase that changes them

- Paths: `skills/**` `src/torve/application/colocation.py` `src/torve/cli/init.py` `pages/docs/operating.md`
- Consequence: Nothing a harness or a person reads names an identifier the check refuses

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

### S-0059/D-6 — `LOCKED` (One word for the document, and the tree as the record)

One shared `ConfigDict` in `torve/base/model.py` — `extra="forbid"`, `use_attribute_docstrings=True` — configures every model that forbids extras; a field's words are its attribute docstring, never a comment beside it and never `Field(description=...)`; a test asserts every property of every schema `init` writes carries a description

- Paths: `src/torve/base/model.py` `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py` `src/torve/cli/init.py` `.torve/schemas/**`
- Consequence: The schema an editor shows carries the field's meaning; a field added without its words fails the suite
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-7 — `ASSUMED` (One word for the document, and the tree as the record)

`init` mints `standing.json` from `StandingContract` and adds the schema line to every file under `.torve/standing/`; `doctor` reddens when either lags

- Paths: `src/torve/cli/init.py` `src/torve/cli/doctor.py` `.torve/standing/**`
- Consequence: Every custom YAML document torve reads from a repository names its schema

### S-0059/D-8 — `ASSUMED` (One word for the document, and the tree as the record)

The log's `base_sha` is `base`, the landing's word; the log's schema version is 2 and a log saying `base_sha` reads through a shim; the landing's `commit` stays; `spec new`'s hint and `spec show`'s label name the summary, not a description

- Paths: `src/torve/domain/spec.py` `src/torve/application/**` `src/torve/gates/decisions_reported.py` `src/torve/adapters/agent/harness.py` `src/torve/cli/**`
- Consequence: One word for the commit an attempt built on, in the log and the landing

### S-0059/D-12 — `LOCKED` (One word for the document, and the tree as the record)

Every reader of a landing reads the tree through `landings` and `landed_commits` — `shipped_landings`, `shipped_ids`, `shipped_commit`, the revert leg, `status`, `shadow`, `evals`, the review's defect lookup, the PR review's `landed_tasks` and `spec cites`; no git subprocess reads a trailer or a subject, closing S-0022/A-1's exception and retiring S-0007/D-26's subject spellings; the five local tasks without a landing get one written once from their trailers, by a script not committed

- Paths: `src/torve/application/projections.py` `src/torve/application/specquality.py` `src/torve/application/review.py` `src/torve/application/session.py` `src/torve/application/ports.py` `src/torve/application/residency.py` `src/torve/application/shadow.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/workspace/git.py` `src/torve/cli/**`
- Consequence: A tree without git answers what landed; S-0022/D-5 holds again without its exception
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-1 — `LOCKED` (A source is a file, and the contract names it)

A source is a file, `.torve/sources/<kind>/<slug>.yaml`, carrying `id` (which must equal `<kind>/<slug>` from its own path), `title`, `ref`, `at` and `summary`; its identifier is `<kind>/<slug>`, a document's stays `S-NNNN`, and `specification` is not a directory because a document is already a source; `torve init` writes `sources.json` and each file opens with its schema line

- Paths: `src/torve/domain/source.py` `src/torve/config/sources.py` `src/torve/cli/init.py` `.torve/sources/**` `.torve/schemas/**`
- Consequence: A source identifier resolves to something a person can open, which is what makes it worth putting on a contract
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-5 — `LOCKED` (A source is a file, and the contract names it)

`torve intake --source <id>` records the source on the drafting run, carries it in the drafts file and copies it onto every contract adoption mints, refusing an unknown source before a model is called; a standing job names `source` beside `decisions_from`; `torve plan` sets none, because a phase's task is sourced by its document

- Paths: `src/torve/cli/intake.py` `src/torve/application/intake.py` `src/torve/application/standing.py`
- Consequence: The front door records what walked in, and a recurring job's contracts say which job minted them
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-6 — `ASSUMED` (A source is a file, and the contract names it)

`torve source new <kind> <slug>`, `list` and `show <id>` write and read the files, `show` naming the tasks that cite the source

- Paths: `src/torve/cli/sources.py`
- Consequence: A source is minted and read by the tool that owns it, as a document is

### S-0060/D-7 — `LOCKED` (A source is a file, and the contract names it)

`import_corpus` becomes `import_sources`, recording every file under `.torve/sources/` as a `SourceImported` with its own kind beside every document as today, idempotent as before; a source whose file is deleted keeps what was recorded and is not retired

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: Every kind the vocabulary admits has a producer
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
