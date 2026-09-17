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

### S-0061/D-10 — `LOCKED` (The agent profile, the harness manifest, and the seat that names them)

The profile and the harness manifest are committed files of the operating repository under `.torve/`, not files on the operator's machine.

- Paths: `src/torve/config/agents.py` `src/torve/cli/init.py`
- Consequence: a regime is reconstructable from a checkout, and two operators running one repository cannot silently run different equipment
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-4 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

Equipment is fetched host-side into a cache keyed by source and ref, never inside an attempt.

- Paths: `src/torve/application/equipment.py` `src/torve/cli/equip.py`
- Consequence: an attempt's failures do not include the internet's, and a warmed cache makes dispatch touch no network at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-9 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

S-0061/D-5 is superseded by D-1 and S-0061/D-6's renderer retires; the harness is told about a plugin by its own flag, not by files torve writes into its state.

- Paths: `sandboxes/**` `src/torve/cli/sandbox.py` `src/torve/application/ports.py`
- Consequence: torve stops keeping a second copy of a harness's internal bookkeeping in step with it across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-11 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

`torve equip --check` audits the cache against what each source recorded — a skill's `github-pinned` frontmatter, a clone's HEAD — and reports; it never refetches and never resolves.

- Paths: `src/torve/cli/equip.py` `src/torve/application/equipment.py`
- Consequence: a cache directory that does not hold what its key claims is a finding an operator can read, rather than a regime hash that agrees with itself and with nothing else

### S-0063/D-6 — `LOCKED` (The image knows how to equip itself)

Image definitions live at `sandboxes/<name>/` in the repository root and build to `<name>-sandbox`; `.torve/sandbox/` stays the hook for a consuming repository's own.

- Paths: `sandboxes/**` `src/torve/cli/sandbox.py`
- Consequence: torve's own source stops living in the directory torve creates inside repositories it works on, and an image gets a name worth publishing
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-8 — `ASSUMED` (The image knows how to equip itself)

`bake.hcl` declares a target per definition with the base as a named context, and a justfile recipe is the build; `torve sandbox build` shells `docker buildx bake`.

- Paths: `bake.hcl` `justfile` `src/torve/cli/sandbox.py`
- Consequence: the build expresses its own dependency graph, and the context staging the verb does by hand becomes the base image's inheritance

### S-0063/D-11 — `LOCKED` (The image knows how to equip itself)

`torve sandbox build` and `stage` retire, and `RuntimePort.build_image` with them; the verb keeps `list` and `digest`, and `just images` is the build.

- Paths: `src/torve/cli/sandbox.py` `src/torve/application/ports.py` `src/torve/adapters/runtime/**`
- Consequence: the engine loses its last way to build an image, which is the rule S-0017/D-3 already stated and could not enforce while a verb of its own did it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0065/D-1 — `ASSUMED` (The record is read, and what looks lost is unjoined)

`torve ledger` folds the record into rates — cost per landed task, attempts per landing, convictions before landing, duty cycle — printed per seat and per gate, and never lists rows a renderer already prints

- Paths: `src/torve/application/ledger.py` `src/torve/cli/ledger.py`
- Consequence: the four numbers the engine's case rests on become a command rather than a hand computation in a document, and every later comparison has one arithmetic to cite

### S-0067/D-1 — `ASSUMED` (A session is briefed, and the working rules have one source)

`torve brief <contract>` runs what dispatch runs before an agent starts — the lint, the size estimate, the uninherited rows, the pack, the battery — prints it, and refuses nothing

- Paths: `src/torve/cli/brief.py`
- Consequence: the hand-minted path gets the protection the drafted path already has, printed before the work rather than convicted after it

### S-0067/D-2 — `ASSUMED` (A session is briefed, and the working rules have one source)

The brief prints the rows whose declared paths intersect the scope and which the contract has not inherited

- Paths: `src/torve/cli/brief.py`
- Consequence: a session stops learning from a conviction that it was governed by a row it never carried

### S-0067/D-5 — `LOCKED` (A session is briefed, and the working rules have one source)

The MCP surface serves the per-task pack as one more read-only tool and registers no tool that writes

- Paths: `src/torve/cli/mcp.py`
- Consequence: every session gets the facts a sandbox is handed on disk, whichever harness it drove up in, and no verb's refusals are reimplemented behind a second interface
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0067/D-9 — `ASSUMED` (A session is briefed, and the working rules have one source)

Dispatch refuses a contract the lint refuses, at the rung the size check already stands on: named reasons, and a recorded override for the operator who means it

- Paths: `src/torve/cli/run.py`
- Consequence: a contract that can never go green is refused before an attempt is paid for, rather than after three of them

### S-0067/D-10 — `ASSUMED` (A session is briefed, and the working rules have one source)

A verb that takes a contract takes its task id, resolving through `layout.task_file`; a path stays accepted for a draft that has no id yet

- Paths: `src/torve/cli/brief.py` `src/torve/cli/gates.py` `src/torve/cli/intake.py`
- Consequence: the three reading verbs address work the way `torve run` does, and where a contract file lives stops being the caller's problem

### S-0068/D-2 — `ASSUMED` (The refusal that is missing, and the one nobody answered)

`doctor` names which promotion criteria a served manager would land without, and says when the landing leg is off entirely

- Paths: `src/torve/cli/doctor.py`
- Consequence: the configuration nobody wrote becomes as visible as the one someone did, before a pass lands anything

### S-0068/D-3 — `ASSUMED` (The refusal that is missing, and the one nobody answered)

`doctor` reports a standing job that has been refused instantiation, how many times, and on what

- Paths: `src/torve/cli/doctor.py`
- Consequence: a mechanism blocked for a reason stops being indistinguishable from one that does not exist

### S-0073/D-7 — `ASSUMED` (The working rules live once, and say what an attempt costs)

`torve log divergence` records several rows in one invocation, and the working rules say the finishing check answers `log owed` before a stop

- Paths: `src/torve/cli/log.py` `skills/working-rules/**`
- Consequence: the bookkeeping tail becomes one round trip instead of one per governed row

### S-0075/D-3 — `LOCKED` (What an attempt costs, measured per changed line)

The ledger reports cache-read tokens, wall seconds, tool calls and dollars per changed line and per file in scope, from the diff the landing already commits, beside the per-task rates it reports now

- Paths: `src/torve/application/ledger.py` `src/torve/cli/ledger.py`
- Consequence: a fixed overhead that only hurts small tasks becomes visible, and a mitigation aimed at it can be judged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0075/D-6 — `LOCKED` (What an attempt costs, measured per changed line)

The burn profile is derived from the attempt's retained trace when it is read, not only recorded when the attempt ends; the recorded block is a cache of that derivation and the trace is what settles a disagreement

- Paths: `src/torve/application/telemetry.py` `src/torve/cli/ledger.py`
- Consequence: every attempt whose trace is still on disk has a profile, so a change is judged against a population rather than against the attempts that happened after the classifier shipped, and a correction to a class reclassifies the history instead of leaving it wrong
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0079/D-10 — `ASSUMED` (The night as a typed record)

The lane leg a served night runs is handed `conflict_disposal`, so a candidate whose rebase conflicts against a moved base is re-queued by the engine instead of escalating `merge_conflict`

- Paths: `src/torve/application/lane.py` `src/torve/cli/manager.py`
- Consequence: one class of overnight escalation stops waiting for a person, and a candidate disposed of this way cannot be re-queued twice against the same tip because `conflict_base` bounds it

### S-0082/D-8 — `ASSUMED` (The arms can be launched)

Arm mode is the third mode of `torve eval`, told apart by `--arm` — repeatable, defaulting to all three — and refused at parse together with a skill argument or with `--image` or `--variant`; `--report` is not touched

- Paths: `src/torve/cli/evals.py`
- Consequence: the arms land in the ledger the reading already reads, and no invocation can be both a comparison inside the apparatus and a comparison that removes it

### S-0082/D-9 — `ASSUMED` (The arms can be launched)

`--arm` without `--task` is refused before any spend, and the refusal names which tasks are eligible rather than only which option is missing

- Paths: `src/torve/cli/evals.py`
- Consequence: the first invocation anybody types is answered with the set it could have named, so the launcher is usable without reading this document

### S-0082/D-10 — `ASSUMED` (The arms can be launched)

The seat is the task's own tier unless `--tier` names another, every arm of one invocation runs on the same seat, and `--tier` given with an arm names the seat the arms run on rather than a candidate under measurement

- Paths: `src/torve/cli/evals.py`
- Consequence: the same three arms can later be run on a variant seat without a second axis, and a difference between arms is never a difference between seats

### S-0082/D-11 — `ASSUMED` (The arms can be launched)

Before the first replay the verb prints which arms it will run over which tasks and what each of those tasks' recorded attempts cost, and adds no ceiling of its own — an arm run is bounded by the contract's budget and the broker's mid-run refusal

- Paths: `src/torve/cli/evals.py`
- Consequence: the largest deliberate spend behind one command in this engine says so before it starts, and there is no second, weaker budget mechanism beside the working one

<!-- /torve:managed -->

## The night verb, beside the manager's

`torve manager serve --night` opens a night and `torve night show` reads one
back. They are the two halves of one mechanism, and neither stores anything:
the terms are recorded on `night.opened` at the open (S-0079/D-2) and the
report is a projection of the window, computed on every call and stored
nowhere (S-0079/D-4).

- `serve --night` reads the configuration's `night:` section once, at the open,
  refuses a board with nothing startable (S-0079/D-6), and stops on a budget,
  on the wall-clock end or on the first escalation of a class the terms name
  (S-0079/D-7). Every bound is soft: an attempt in flight finishes, and the
  close is written even when the end fell inside a pass (S-0079/D-12).
- `night show <partition>` folds the window into landed, convicted, ended and
  waiting-on-a-person. `--night` picks one by id; omitted takes the most
  recent open. A night with an open and no close reads as unfinished rather
  than as lost, which is what a manager killed at 04:00 leaves behind.

The report holds no field prose can occupy, so no line of it is a model's
account of its own night. Adding one is the way that rule is broken.

What the knobs cost is written where they are set, `.torve/config.yaml`; what
a night seat runs under is declared in `.torve/harnesses/` as seat `env`
(S-0079/D-8), and torve interprets none of those three variables (S-0079/D-9).
