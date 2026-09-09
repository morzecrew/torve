<!-- torve:managed src/torve/cli — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/cli/`

### D-53.9 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

The importer records every archived document as a source and every archived row as retired; `torve why` and `show` resolve archived identifiers and say they are archived

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: 705 log entries and 139 amendments keep their targets
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.13 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Each reader switches from `rfc_parse` to the model beside its old path with a parity assertion, one at a time, in phase 2; `rfc_parse.py` is deleted in phase 5 only after the archive lands

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: Two owners of the format exist for one bounded window, and the parity test is what bounds it

### D-54.6 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

`torve spec project` renders a managed section into the `AGENTS.md` of every directory a standing row's paths or an accepted phase's scope names — rows with grade, text, consequence, check and state; invariants; contended paths — and a root index of governed directories; text outside the markers is never touched

- Paths: `src/torve/application/colocation.py` `src/torve/cli/spec.py`
- Consequence: Every harness and every person reads the rules where the code is, with no prompt change; a directory that stops being governed loses its section
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.7 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

The rendered sections are committed and drift-checked by a manifest gate running `torve spec project --check`; a hand edit inside the markers is drift naming the file

- Paths: `.torve/gates.yaml` `src/torve/cli/spec.py`
- Consequence: The INDEX.md rule (D-A.6) applied to the tree; the projection cannot lie about the corpus
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.9 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

`torve spec show`, `paths`, `tests` and `why-not` form a read-only verb over the worktree's corpus and the archive beside it; a form that would need the record, another task or an escalation is refused by design

- Paths: `src/torve/cli/spec.py`
- Consequence: RFC 0007 §5 stands: the sandbox gains disclosure over files it could open, never reach
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.6 — `ASSUMED` (RFC 0055 — Standing decisions)

Escalation reasons are a closed enum mapped to exit codes; `locked_conflict` and `underspecified` are halts on working judgement, one indicting the code and the other the contract

- Paths: `src/torve/domain/states.py` `src/torve/cli/options.py`
- Consequence: A new reason is an amendment and a code path, never a free string

### D-55.11 — `ASSUMED` (RFC 0055 — Standing decisions)

A divergence entry is written only through `torve log divergence`, validated by the gate's own checks at intake, with located evidence; `drift_count` is derived, never declared

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `.torve/tasks/**`
- Consequence: A hand-edited log is refused by the same code that gates it

### D-55.22 — `LOCKED` (RFC 0055 — Standing decisions)

User-facing strings carry no corpus coordinates: whoever runs the command has no corpus to resolve them

- Paths: `src/torve/gates/user_facing_text.py` `src/torve/cli/**`
- Consequence: Twenty-eight convictions and their corrections; the audience rule is a gate, not a style
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.28 — `ASSUMED` (RFC 0055 — Standing decisions)

A module named after a CLI verb lives under `cli/` and holds parsing and rendering only; presentation never crosses inward

- Paths: `src/torve/cli/**`
- Consequence: Logic and presentation stay separable where they are hardest to separate

### D-55.34 — `ASSUMED` (RFC 0055 — Standing decisions)

A stdio MCP server is image content; a remote MCP endpoint is an egress destination under provider routing; no execution sandbox is given a read surface onto the record

- Paths: `src/torve/config/runconfig.py` `.torve/sandbox/**` `src/torve/cli/mcp.py`
- Consequence: Files in the worktree carry task context; reach into the record is the planner's alone

### D-55.43 — `ASSUMED` (RFC 0055 — Standing decisions)

Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py`
- Consequence: A substrate surface change fails at the pin bump, not in production

### D-55.44 — `ASSUMED` (RFC 0055 — Standing decisions)

`torve plan` is deterministic and invokes no model; it mints from exactly one accepted, committed document, copies that document's whole table, and refuses to re-mint over minted phases

- Paths: `src/torve/application/planner.py` `src/torve/cli/plan.py`
- Consequence: What to do with existing tasks is a human decision

### D-55.56 — `ASSUMED` (RFC 0055 — Standing decisions)

Cadence belongs to the manager's pass; there is no resident scanning loop, and the standing legs — maintenance, relay, lane — run inside the pass under their switches

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Consequence: One process to run and supervise; a pause stops what advances the repository

### D-56.4 — `LOCKED` (RFC 0056 — Structure for everything)

Every writer — `amend`, `fix`, `retire`, `archive`, `new` — mutates the model and writes it through one serializer; comments are not preserved and `check` refuses one outside the schema header line; `fmt` survives as `--check` only

- Paths: `src/torve/config/spec_emit.py` `src/torve/cli/spec.py`
- Consequence: There is no second renderer to drop a field; the `character:` defect closes by construction
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.6 — `LOCKED` (RFC 0056 — Structure for everything)

`torve rfc schema` writes `rfcs/schema/document.json` from the model, drift-checked by `rfc check`; every document's first line names it; `rfc new` emits 0055's shape with the header line

- Paths: `.torve/schemas/**` `src/torve/cli/spec.py`
- Consequence: An editor validates a row as it is typed; a new document starts small
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.7 — `ASSUMED` (RFC 0056 — Structure for everything)

`torve rfc list` replaces `INDEX.md`; `torve rfc render NNNN` is the only markdown writer and never the source of anything

- Paths: `src/torve/cli/spec.py`
- Consequence: The index is a query, not a file

### D-57.4 — `LOCKED` (RFC 0057 — The specification is a directory)

`torve spec` absorbs every `torve rfc` verb and `cli/rfc.py` is deleted; the gate is `spec-valid`; the skill is `spec-writer`; `rfc_emit.py` becomes `spec_emit.py`; new prose says specification or document

- Paths: `src/torve/cli/spec.py` `src/torve/cli/spec.py` `src/torve/config/spec_emit.py` `skills/**` `.torve/gates.yaml`
- Consequence: One namespace for the corpus; old prose and test file names keep the old word
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.5 — `LOCKED` (RFC 0057 — The specification is a directory)

`torve init` writes `.torve/schemas/*.json` from every model torve reads from YAML — the four files, the contract, the log, the configuration, the manifest — and `.torve/.gitignore` with the patterns for what torve alone writes, idempotent, never a configuration or a manifest; every YAML torve writes names its schema on its first line; `doctor` and `spec check` redden when a schema lags its model or the ignore file lacks a minted pattern

- Paths: `.torve/schemas/**` `.torve/.gitignore` `.gitignore` `src/torve/cli/init.py` `src/torve/cli/doctor.py`
- Consequence: An editor validates any torve YAML as it is typed; an adopting repository ignores the right files without copying a block; `init` is the initialisation there is
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.7 — `LOCKED` (RFC 0057 — The specification is a directory)

`execution.yaml` holds landings — task, phase, attempt, commit, time, agent, the log's entries typed as `LogEntry` — appended by one function the runner calls before the merge commit and `torve log land` exposes; a contract naming no document lands nowhere and says so

- Paths: `src/torve/application/divergence.py` `src/torve/application/runner.py` `src/torve/cli/log.py`
- Consequence: Every clone carries what execution found, beside the rows it informs; the task directory carries nothing git keeps
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.8 — `ASSUMED` (RFC 0057 — The specification is a directory)

`torve decisions import` reads every execution file, live and archived, and records the divergence and landing events the record lacks, idempotent by task, attempt, decision and time; the attempt-time `ingest` is unchanged

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: A clone without a store rebuilds the same record from the tree

### D-57.10 — `ASSUMED` (RFC 0057 — The specification is a directory)

`torve spec cites IDENT` lists the code lines, landings, amendments and documents that cite an identifier

- Paths: `src/torve/cli/spec.py`
- Consequence: The row's side of the link is a query, not a grep

### D-57.13 — `OPEN` (RFC 0057 — The specification is a directory)

Whether `spec cites` also reads the `Torve-Decisions` trailers of the commit history

- Paths: `src/torve/cli/spec.py`
- Consequence: Decided by whoever executes phase 4, logged

<!-- /torve:managed -->
