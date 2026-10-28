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

Gates execute outside the agent session, inside a sandbox, against a standalone checkout of what the attempt leaves committed; an agent cannot report a gate outcome

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

Acceptance is a list of shell commands, exit 0 meaning satisfied, run in the battery sandbox over a standalone checkout of the attempt's commit; a command that needs git cannot be an acceptance command, because the attempt first runs it in its own sandbox, where git is at most a read-only copy of the attempt's start under Docker and absent under OpenSandbox

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

### S-0070/D-6 — `ASSUMED` (What is committed may not depend on what is not)

An acceptance verdict names the suite it judged: a battery that ran fewer tests than the tree contains reports how many it skipped and why, and `pass` never stands for two different suites

- Paths: `src/torve/gates/acceptance.py` `tests/test_gates.py`
- Consequence: a green battery in a sandbox and a green battery on a laptop stop being the same sentence for two different amounts of evidence

### S-0071/D-6 — `ASSUMED` (The battery costs what it costs for reasons unrelated to what it judges)

A gate declares what it judges, and an attempt that changed none of it is reported skipped rather than run

- Paths: `src/torve/gates/runner.py` `src/torve/config/manifest.py` `.torve/gates.yaml`
- Consequence: a gate stops spending the battery's wall clock on attempts it has no opinion about, and the record says it had none rather than showing a pass it did not earn

### S-0080/D-10 — `ASSUMED` (The lane opens a pull request, and a person lands it)

In `pull_request` mode a worktree is cut from the remote's `main` after a fetch, never from the local one

- Paths: `src/torve/adapters/workspace/git.py` `src/torve/gates/context.py`
- Consequence: an attempt builds on the base its pull request will be merged against, rather than on a copy that is stale from the first merge onward

### S-0081/D-1 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

`red-on-base` is a builtin gate declared beside `no-test-tampering`, entering at `shadow` with a sabotage pair that proves it can convict and can pass

- Paths: `src/torve/gates/red_on_base.py` `src/torve/gates/__init__.py` `.torve/gates.yaml`
- Consequence: the gate produces its own false-positive rate before it can block anything, and promotion is an amendment with that rate attached rather than a line in this document

### S-0081/D-2 — `LOCKED` (The test that proves nothing, and the conviction the record can name)

The gate's input is the test files whose test functions differ from their base version as parsed syntax, never the test files present in the diff

- Paths: `src/torve/gates/red_on_base.py` `src/torve/gates/__init__.py`
- Consequence: a move, a rename or a reformat qualifies nothing, so the gate cannot be routed around by leaving tests untouched, and a refactor that reorganises the suite pays nothing
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0081/D-3 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

The verdict is a conviction only when every qualifying test passes on base; a candidate-side failure is reported as skipped and left to `acceptance`

- Paths: `src/torve/gates/red_on_base.py` `src/torve/gates/__init__.py`
- Consequence: one failing test is never filed under two gate names, so the retry rung and the repair routing are pointed at the failure's actual class

### S-0081/D-4 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

A diff that changes no file outside the manifest's test patterns is not judged by this gate

- Paths: `src/torve/gates/red_on_base.py` `src/torve/gates/__init__.py`
- Consequence: a contract whose whole job is adding missing tests is not convicted for it, and an attempt that splits its source change away from its tests is unjudged in both halves

### S-0081/D-5 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

The base pass extracts `git archive <merge-base>` into a `mktemp -d` directory outside the repository, copies the qualifying test files over it and runs `TEST_COMMAND` on only those files through the pass's executor. A trap removes the directory on every exit, and no worktree is registered.

- Paths: `src/torve/gates/red_on_base.py` `src/torve/gates/sabotage.py`
- Consequence: the declared timeout is what the choice is judged by, and a materialisation that puts this gate beside `coverage-delta` in cost has failed whatever else it does

### S-0083/D-9 — `ASSUMED` (The pull request is one per document, not one per task)

In `unit: document` a task's worktree is cut from the document branch's tip when the branch exists and from the remote's `main` after a fetch when it does not, which means wiring the `fetch` that `resolve_base` and `WorkspaceGit.create` already take through `WorkspacePort` and the runner

- Paths: `src/torve/gates/context.py` `src/torve/adapters/workspace/git.py` `src/torve/application/ports.py` `src/torve/application/runner.py`
- Consequence: the phase after a landed one starts on the tree that landing produced the moment it landed, so a night runs a whole document without a person in the middle

### S-0089/D-4 — `ASSUMED` (A night serves what a worker can finish)

An acceptance command that exits zero with a test summary in which no test ran — every test skipped or deselected — fails, with "suite: no test ran" at the top of its verdict; a command that prints no test summary, or ran at least one test, is judged as before

- Paths: `src/torve/gates/acceptance.py` `tests/test_gates.py`
- Consequence: a phase cannot land green on a lane that never executed; one whose tests need what the sandbox lacks has to say how they run there

### S-0104/D-5 — `ASSUMED` (A phase's scope bends to the files its change reaches)

The gate manifest's `scope.derived` map names files that ride with a source, in both directions (a lock file with its manifest, a golden with its producer, a generated schema with its model); a derived path is contingent wherever its source is in scope. The map replaces the hard-coded lock-file table, and its default is that table.

- Paths: `src/torve/config/manifest.py` `src/torve/gates/scope.py` `src/torve/application/neighbours.py`
- Consequence: a repository names its own generated files once, and the import graph's blind spot is covered where it is named

### S-0104/D-6 — `ASSUMED` (A phase's scope bends to the files its change reaches)

A managed `AGENTS.md` beside a path in scope is writable as a lock file is: `torve spec project` writes it, and the scope gate allows it.

- Paths: `src/torve/gates/scope.py` `skills/hooks/implement/scope_guard.py`
- Consequence: a phase that changes governed code can regenerate its projection without a halt

### S-0104/D-7 — `ASSUMED` (A phase's scope bends to the files its change reaches)

The attempt's prompt lists its contingent paths beside its scope, the scope gate reports every promoted path, and the reviewer is shown the promotions.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/gates/scope.py` `src/torve/application/review.py`
- Consequence: a widening is read by the agent, the gate and the reviewer, never made silently

### S-0105/D-4 — `ASSUMED` (One reading of a scope glob)

The other readers of a path glob read through `matches`: `intake._matched` and `neighbours._matched`, telemetry's `in_scope_read` class, a gate's `paths` filter (`gates/runner._judges_anything`) and `config/spec.check_backlinks`' LOCKED-row paths. Their prefix fallbacks go, since gitignore already matches what is under a matched directory.

- Paths: `src/torve/application/intake.py` `src/torve/application/neighbours.py` `src/torve/application/telemetry.py` `src/torve/gates/runner.py` `src/torve/config/spec.py`
- Consequence: a glob written once means one set of paths wherever torve reads it

### S-0106/D-1 — `ASSUMED` (A decision is judged before the pull request)

A decision gate whose command selects no test fails as vacuous: pytest's exit 5, or the "no tests ran" reading `acceptance` already makes, is `fail` with "the check selected nothing", under the row's state. `decisions-reported` owes an entry for a LOCKED row whose check did not pass, instead of skipping every row that has a check.

- Paths: `src/torve/gates/runner.py` `src/torve/gates/acceptance.py` `src/torve/gates/decisions_reported.py`
- Consequence: a `-k` filter that matches nothing is seen the first time it runs, and a LOCKED row whose check fails gets a word in the log
- Check: `uv run pytest tests/test_gates.py -q -k vacuous` (shadow; runs as `decision:S-0106/D-1`, no log entry owed)

### S-0106/D-2 — `ASSUMED` (A decision is judged before the pull request)

The completion battery and the document-branch regate run the `decision:<id>` gates of every row the document's phases inherited, and an `invariant:<id>` gate for every standing invariant whose paths the document's diff reaches. A decision gate keeps its row's state; an invariant gate is shadow.

- Paths: `src/torve/application/lane.py` `src/torve/gates/runner.py` `src/torve/gates/context.py`
- Consequence: a check runs on the whole document before its pull request turns ready, not only on the attempt that inherited it
- Check: `uv run pytest tests/test_lane.py -q -k completion_runs_checks` (shadow; runs as `decision:S-0106/D-2`, no log entry owed)

### S-0106/D-6 — `ASSUMED` (A decision is judged before the pull request)

Under a document whose `change.type` is `refactor`, `red-on-base` runs the changed test files and the inherited decision checks on the base tree and on the candidate. Green on both passes; green on one and red on the other fails as "behaviour changed under a refactor". Outside a refactor the gate is unchanged.

- Paths: `src/torve/gates/red_on_base.py`
- Consequence: a refactor is judged by whether behaviour held, and S-0081's named risk, a refactor convicted for changing nothing, is closed
- Check: `uv run pytest tests/test_gates.py -q -k refactor` (shadow; runs as `decision:S-0106/D-6`, no log entry owed)

### S-0106/D-7 — `ASSUMED` (A decision is judged before the pull request)

The manifest's `tests:` block gains `run`, the command a list of test files is appended to (default `python3 -m pytest -q -p no:cacheprovider`), and `pair`, templates from a module to its tests over `{dir}` and `{stem}` (default the Python pairing at any depth under `tests/`). `red-on-base`, the intake's pairing, the neighbour derivation, the context pack's named tests and the review leg's fallback scope read them. A changed test file the gate cannot parse into functions qualifies whole.

- Paths: `src/torve/config/manifest.py` `src/torve/gates/red_on_base.py` `src/torve/application/intake.py` `src/torve/application/neighbours.py` `src/torve/application/contextpack.py` `src/torve/application/reviewleg.py`
- Consequence: a repository whose tests are `*_test.go` or `*.spec.ts` gets red-on-base and pairing by naming them, not by a change to torve
- Check: `uv run pytest tests/test_manifest.py tests/test_gates.py tests/test_intake.py -q -k adapter` (shadow; runs as `decision:S-0106/D-7`, no log entry owed)

### S-0107/D-1 — `ASSUMED` (A change's tests are scored by the mutants they kill)

`src/torve/gates/mutation.py` enumerates mutants over the candidate's changed Python source lines only, one edit to one AST node per mutant, from six operators: comparison, boolean, arithmetic, constant, condition and return. A line the diff did not add or change is never mutated, and a test file is never mutated.

- Paths: `src/torve/gates/mutation.py`
- Consequence: every mutant is a recognisable edit to code on a changed line
- Check: `uv run pytest tests/test_mutation.py -q -k operator` (shadow; runs as `decision:S-0107/D-1`, no log entry owed)

### S-0107/D-2 — `ASSUMED` (A change's tests are scored by the mutants they kill)

The `mutation` builtin gate (shadow, attempt battery) samples at most `budget` mutants (default 30) with a seed derived from the diff, applies each in a scratch copy of the worktree, and runs the task's paired tests through `tests.run` with a timeout of twice their baseline run. It reports killed, survived and timed-out counts, the score, and every survivor as `path:line — edit`. A change with no Python source lines, or no paired tests, is skipped with the reason.

- Paths: `src/torve/gates/mutation.py` `src/torve/gates/__init__.py` `src/torve/config/manifest.py` `.torve/gates.yaml`
- Consequence: the score costs a bounded, repeatable amount and never touches the worktree under judgment
- Check: `uv run pytest tests/test_gates.py -q -k mutation_gate` (shadow; runs as `decision:S-0107/D-2`, no log entry owed)

### S-0107/D-5 — `ASSUMED` (A change's tests are scored by the mutants they kill)

The sabotage suite carries a mutation case: a change whose tests assert nothing scores 0 with every mutant a survivor, and a change whose tests pin each line scores 1.

- Paths: `src/torve/gates/sabotage.py`
- Consequence: the gate is shown to tell a weak test from a strong one, as every gate must
- Check: `uv run pytest tests/test_sabotage.py -q -k mutation_case` (shadow; runs as `decision:S-0107/D-5`, no log entry owed)

### S-0110/D-8 — `ASSUMED` (A second adopter runs torve from a tool install)

`gates.yaml` may name the Python project's directory, `layout.root` (default the repository root). The source root is `<root>/src`: the source-layout gate, the citation scan, the spec-quality frontier and the coverage fallback prefix read it, and the `tests` defaults are re-rooted under `<root>` when the `tests` block is absent. Nothing searches the tree for it.

- Paths: `src/torve/config/manifest.py` `src/torve/gates/source_layout.py` `src/torve/config/spec.py` `src/torve/application/specquality.py` `.torve/schemas/**`
- Consequence: a project under `app/` gets every check a root project gets, and a root project changes nothing

### S-0113/D-6 — `ASSUMED` (A gate in the sandbox sees what the host sees)

An invariant gate whose check selects no test fails as "the check selected nothing", as a decision gate does, and stays shadow.

- Paths: `src/torve/gates/runner.py`
- Consequence: a stale `-k` selection reads as stale, not as a broken rule

### S-0115/D-1 — `ASSUMED` (Every decision shows its evidence)

A test function proves a row when its own source, docstring, comments and body, carries the row's full identifier as a whole token. A module or class docstring proves nothing on its functions' behalf, and only test functions that differ from the base tree as parsed syntax qualify.

- Paths: `src/torve/gates/row_evidence.py`
- Consequence: the tests already citing rows become evidence without a new convention, and one citation at the top of a file cannot vouch for every test in it

### S-0115/D-2 — `ASSUMED` (Every decision shows its evidence)

A builtin gate `evidence` runs each row's citing functions against the base tree, built as `red-on-base` builds it, and against the candidate. A row is proved when one of its citing functions is red on base and green on the candidate. A row that carries a `check` is judged by its own decision gate, and `evidence` does not run it.

- Paths: `src/torve/gates/row_evidence.py` `src/torve/gates/__init__.py` `src/torve/config/manifest.py`
- Consequence: a row counts as proved only by a test the change had to turn green

### S-0115/D-3 — `ASSUMED` (Every decision shows its evidence)

A row is behavioural unless every one of its paths is documentation, a `*.md` file or a path under `pages/`. `evidence` fails when a behavioural row is unproved and carries no `check`, naming each such row, and skips when the battery carries no behavioural row. It enters at shadow.

- Paths: `src/torve/gates/row_evidence.py`
- Consequence: an unproved requirement is named in every battery that carries it, without blocking a landing until the gate's record earns it

### S-0115/D-4 — `ASSUMED` (Every decision shows its evidence)

`evidence` names a sabotage case and its twin: a behavioural row whose only citing test passes on base fails, and the same row with a citing test red on base passes.

- Paths: `src/torve/gates/sabotage.py`
- Consequence: `torve gates check` proves the gate can fail before any manifest names it

### S-0115/D-9 — `ASSUMED` (Every decision shows its evidence)

`evidence` runs in every battery that carries a behavioural row, an attempt's as well as the whole document's, and skips in a verify attempt's battery, where `verify-tests` judges. In an implement attempt the test functions of the verifier's file the contract denies it count as changed, and run on the attempt's base tree and on the candidate.

- Paths: `src/torve/gates/row_evidence.py`
- Consequence: a row the verifier's tests prove reads as proved on the attempt that turns them green, and a verify attempt, whose tests are red by design, names no row

### S-0116/D-10 — `ASSUMED` (An independent verifier)

The runner hands a battery's gate context the files of the latest `verify.holdout` event on the task's implement task. A builtin `held-out` writes them into the gate checkout under `<tests_dir>/holdout/`, runs only them and removes them when it ends, so no other gate collects or reports them; its output keeps only each failing test's identifier, message and cited rows, and with no files in that event it is skipped.

- Paths: `src/torve/gates/held_out.py` `src/torve/gates/__init__.py` `src/torve/gates/context.py`
- Consequence: the next attempt's pack learns which row failed and why, and never the test's source

### S-0116/D-11 — `ASSUMED` (An independent verifier)

A builtin `verify-tests` convicts a verify candidate whose file does not collect, whose entry settles a row no test cites, or one of whose visible or held-out tests passes on the candidate; it writes and removes the held-out files for its own run as `held-out` does, and its output names each such row and test.

- Paths: `src/torve/gates/verify_tests.py`
- Consequence: a verifier cannot land a test that proves nothing before the implementation exists

## Invariants holding over `src/torve/gates/`

- **S-0055/I-3**: Every gate in the manifest can be made to fail
  - Paths: `.torve/gates.yaml` `src/torve/gates/**`
  - Check: `uv run torve gates check`
- **S-0081/I-1**: Every gate in the manifest, this one included, can be made to fail and can be made to pass
  - Paths: `.torve/gates.yaml` `src/torve/gates/sabotage.py`
  - Check: `uv run torve gates check`

<!-- /torve:managed -->
