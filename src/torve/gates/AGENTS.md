<!-- torve:managed src/torve/gates — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/gates/`

### S-0054/D-2 — `LOCKED` (Decisions as gates and the projections beside the code)

A contract's row with a `check` runs as a gate appended to the battery by the runner, named `decision:<id>`, axis compliance, `origin: rfc/NNNN#<id>`, never written into `gates.yaml`

- Paths: `src/torve/gates/runner.py`
- Consequence: Decision gates exist exactly as long as the contract; the manifest stays the operator's (S-0002/D-5); the retry ladder reads them as any compliance conviction (S-0034/D-4)
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-3 — `LOCKED` (Decisions as gates and the projections beside the code)

A row whose check ran in the same pass, green or red, is not owed a divergence entry; a row with no check keeps the silence check

- Paths: `src/torve/gates/decisions_reported.py`
- Consequence: The battery proves what a command can prove; the log is for judgement; 670 of 705 entries stop costing tokens as rows gain checks
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-4 — `LOCKED` (Decisions as gates and the projections beside the code)

A decision gate enters at `shadow`; promotion to blocking is `check_state` on the row, written by `torve rfc amend` citing soak evidence, and a blocking row must name a `check_twin` that `torve gates check` runs

- Paths: `src/torve/gates/runner.py` `src/torve/gates/sabotage.py`
- Consequence: S-0002/D-18 and S-0036/D-3 apply to rows exactly as to manifest entries; no row convicts before someone has seen it fail
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-2 — `LOCKED` (Standing decisions)

Gates execute outside the agent session against the working tree it leaves behind; an agent cannot report a gate outcome

- Paths: `src/torve/gates/**` `src/torve/application/runner.py`
- Consequence: The battery is the acceptance; a green the agent claims is not one
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-10 — `LOCKED` (Standing decisions)

Silence is a finding: a `LOCKED` row whose declared paths a diff touches without a log entry fails `decisions-reported`

- Paths: `src/torve/gates/decisions_reported.py` `.torve/tasks/**`
- Consequence: Sixty-one convictions and their corrections are the record of this boundary being held
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-12 — `ASSUMED` (Standing decisions)

An empty decision list on a contract is legal and explicit; "none apply" is distinct from "field forgotten"

- Paths: `src/torve/gates/decisions_reported.py` `src/torve/domain/task.py`
- Consequence: The document-less lane is governed by standing inheritance, not by silence

### S-0055/D-13 — `ASSUMED` (Standing decisions)

Evidence must locate — a `path:line` inside the repository or a command with its output — and a finding or entry whose evidence does not locate is discarded unread

- Paths: `src/torve/gates/evidence.py`
- Consequence: Locating eliminates fabricated coordinates, not fabricated claims; the reviewer and the log share the rule

### S-0055/D-15 — `LOCKED` (Standing decisions)

Every gate names a sabotage twin that proves it can fail; a manifest that names any twin refuses a twinless entry; `torve gates check` runs the battery in CI

- Paths: `src/torve/gates/sabotage.py` `.torve/gates.yaml` `.github/workflows/ci.yml`
- Consequence: A gate nobody has seen fail is decoration; the battery carries zero grandfather exceptions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-16 — `ASSUMED` (Standing decisions)

Every gate runs on every pass, cheapest first, never short-circuiting; the retry rung reads outcome and axis only, never a trace or a gate's output

- Paths: `src/torve/gates/runner.py` `src/torve/application/session.py`
- Consequence: A replay of the rows reproduces the tier choice exactly; the severity ladder stays meaningful

### S-0055/D-17 — `LOCKED` (Standing decisions)

Scope is a contract: `allow` and `deny` globs make a touch outside them a red gate, and tasks whose allow sets intersect are never dispatched in parallel

- Paths: `src/torve/gates/scope.py` `src/torve/application/manager.py`
- Consequence: Thirty-two convictions and their corrections; a collision is refused at dispatch, never discovered at merge
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-18 — `LOCKED` (Standing decisions)

Secrets never enter the tree; the `secrets` gate reads added lines and is the one gate no bypass trailer can lift

- Paths: `src/torve/gates/secrets.py` `.gitignore`
- Consequence: The boundary has never been crossed in 745 attempts and the tree holds it as structure
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-19 — `ASSUMED` (Standing decisions)

An existing test is edited only under the contract's licence; adding a test is an addition, editing one needs scope

- Paths: `src/torve/gates/no_test_tampering.py` `tests/**`
- Consequence: A green earned by weakening the suite is a red

### S-0055/D-20 — `ASSUMED` (Standing decisions)

Acceptance is a list of shell commands, exit 0 meaning satisfied, run in the battery sandbox over a worktree without a repository; a command that needs git cannot be an acceptance command

- Paths: `src/torve/gates/acceptance.py` `src/torve/application/intake.py`
- Consequence: The lint refuses such a command at mint rather than after three attempts

### S-0055/D-21 — `ASSUMED` (Standing decisions)

A bypass is a `Torve-Bypass: <gate>: <reason>` commit trailer, counted per gate in telemetry

- Paths: `src/torve/gates/runner.py` `src/torve/application/telemetry.py`
- Consequence: Every waiver is a recorded fact with a name on it

### S-0055/D-22 — `LOCKED` (Standing decisions)

User-facing strings carry no corpus coordinates: whoever runs the command has no corpus to resolve them

- Paths: `src/torve/gates/user_facing_text.py` `src/torve/cli/**`
- Consequence: Twenty-eight convictions and their corrections; the audience rule is a gate, not a style
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-24 — `LOCKED` (Standing decisions)

`gates` imports only `domain`, `base` and `config`; the gates-only install stands alone

- Paths: `src/torve/gates/**` `pyproject.toml`
- Consequence: The first shippable increment keeps shipping alone
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-26 — `LOCKED` (Standing decisions)

The specification format terminates at the planner: gates, runtime adapters and agent adapters never import its owner

- Paths: `pyproject.toml` `src/torve/gates/**` `src/torve/adapters/**`
- Consequence: Format containment cannot break quietly; the contract is the only thing a gate reads about a task
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-6 — `LOCKED` (One grammar and the anatomy)

Execution is a directory, `execution/<task>-<attempt>-<instant>.yaml`, one landing per file, written once and never deleted; the loader reads it sorted by instant into `landings`; an identical replay is a no-op and a restarted attempt lands under a new instant; the scope gate exempts the directory

- Paths: `src/torve/domain/spec.py` `src/torve/config/spec.py` `src/torve/config/spec_emit.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: Two candidates of one document never conflict at merge; no landing is refused for a number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0058/D-9 — `ASSUMED` (One grammar and the anatomy)

The `decision:<id>` gate names, the pack's decisions file, the log entry's `decision` and the record's subjects carry the global form; the record re-imports once after the conversion, retiring every old subject with the reason naming its replacement

- Paths: `src/torve/gates/runner.py` `src/torve/gates/decisions_reported.py` `src/torve/application/contextpack.py` `src/torve/application/decisions.py`
- Consequence: The record's history keeps the old ids as retired rows and the new ones as what stands

### S-0059/D-2 — `LOCKED` (One word for the document, and the tree as the record)

Every reader resolves a contract's document by `document_dir(spec_dir, identifier)`; the scope gate's exemption and the runner's decision gates come from the identifier, and the gate context carries the corpus path

- Paths: `src/torve/gates/context.py` `src/torve/gates/scope.py` `src/torve/gates/runner.py` `src/torve/application/decisions.py` `src/torve/adapters/agent/harness.py`
- Consequence: The regexes over the path go; a document that moved is still found
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-4 — `LOCKED` (One word for the document, and the tree as the record)

A gate's `origin` is `structural`, `leak/<task>` or a citation the grammar accepts; the runner's decision gates carry the row's own id as origin; the manifest's `rfc/NNNN` become `S-NNNN`

- Paths: `src/torve/config/manifest.py` `src/torve/gates/runner.py` `src/torve/gates/sabotage.py` `.torve/gates.yaml`
- Consequence: `spec cites S-0054/D-2` finds the gate the row minted
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0059/D-8 — `ASSUMED` (One word for the document, and the tree as the record)

The log's `base_sha` is `base`, the landing's word; the log's schema version is 2 and a log saying `base_sha` reads through a shim; the landing's `commit` stays; `spec new`'s hint and `spec show`'s label name the summary, not a description

- Paths: `src/torve/domain/spec.py` `src/torve/application/**` `src/torve/gates/decisions_reported.py` `src/torve/adapters/agent/harness.py` `src/torve/cli/**`
- Consequence: One word for the commit an attempt built on, in the log and the landing

### S-0059/D-11 — `LOCKED` (One word for the document, and the tree as the record)

A task naming no document lands under `.torve/execution/` in the same file shape; the loader reads it beside the corpus and the archive, and the scope gate exempts it

- Paths: `src/torve/config/layout.py` `src/torve/config/spec.py` `src/torve/application/decisions.py` `src/torve/gates/scope.py`
- Consequence: An operator's ask and a standing job land with a record; the revert leg finds them
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-3 — `LOCKED` (A source is a file, and the contract names it)

`Task.source: str | None` is the provenance — a source identifier, `S-NNNN` or `<kind>/<slug>`, validated and never parsed for inheritance — beside `spec`, which stays whose rows the contract inherits; a contract may carry both, either or neither

- Paths: `src/torve/domain/task.py` `src/torve/gates/context.py`
- Consequence: A task can say what asked for it without pretending a document did
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `src/torve/gates/`

- **S-0055/I-3**: Every gate in the manifest can be made to fail
  - Paths: `.torve/gates.yaml` `src/torve/gates/**`
  - Check: `uv run torve gates check`

<!-- /torve:managed -->
