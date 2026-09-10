<!-- torve:managed src/torve/application — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/application/`

### S-0053/D-5 — `ASSUMED` (The item model and the rebuilt corpus)

Every row carries a content fingerprint; a mismatch between a contract's copied row and the row as it stands is reported as *suspect* by `decisions show` and `rfc health`, never as a conviction

- Paths: `src/torve/domain/spec.py` `src/torve/application/decisions.py`
- Consequence: Copy-at-mint becomes checkable; S-0031/D-3's no-retroactive rule is preserved by making the mismatch a reading, not a red

### S-0053/D-6 — `LOCKED` (The item model and the rebuilt corpus)

Coverage is a per-path fact with three values — governed, ungoverned, retired — computed over decision paths and accepted documents' phase scopes together; ungoverned is never a check failure

- Paths: `src/torve/application/decisions.py`
- Consequence: The ratchet's frontier is visible and moves; a directory a phase is changing never reads as ungoverned; blind spots are the default state of a repository, not an error
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-7 — `ASSUMED` (The item model and the rebuilt corpus)

A row whose every glob matches nothing on an accepted, implemented document is path rot: reported by `rfc check`, retired by `amend --retire --reason path-rot` (or `check --fix-rot`), recorded as `decision.retired` on import; never automatic on load, never a red on the document

- Paths: `src/torve/application/decisions.py` `src/torve/config/spec_emit.py`
- Consequence: 27 rows today, 8 `LOCKED`, stop rendering as governance while governing nothing

### S-0053/D-9 — `LOCKED` (The item model and the rebuilt corpus)

The importer records every archived document as a source and every archived row as retired; `torve why` and `show` resolve archived identifiers and say they are archived

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: 705 log entries and 139 amendments keep their targets
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0053/D-13 — `ASSUMED` (The item model and the rebuilt corpus)

Each reader switches from `rfc_parse` to the model beside its old path with a parity assertion, one at a time, in phase 2; `rfc_parse.py` is deleted in phase 5 only after the archive lands

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: Two owners of the format exist for one bounded window, and the parity test is what bounds it

### S-0054/D-1 — `LOCKED` (Decisions as gates and the projections beside the code)

`InheritedDecision` gains `consequence` and `check`, copied at mint from the model, carried by `decision.recorded`, rendered after each row in the prompt; the fingerprinted set stays text, grade and paths

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: A gate reads the contract and never the corpus (S-0007/D-18 stands); the reason a row exists reaches the executor for the first time
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-5 — `ASSUMED` (Decisions as gates and the projections beside the code)

A `LOCKED` row with no check is reported *soft* by `rfc health`, counted beside the hard ones, never regraded by the engine

- Paths: `src/torve/application/specquality.py`
- Consequence: The author sees which locks are prose; S-0022's non-goal on automatic edits stands

### S-0054/D-6 — `LOCKED` (Decisions as gates and the projections beside the code)

`torve spec project` renders a managed section into the `AGENTS.md` of every directory a standing row's paths or an accepted phase's scope names — rows with grade, text, consequence, check and state; invariants; contended paths — and a root index of governed directories; text outside the markers is never touched

- Paths: `src/torve/application/colocation.py` `src/torve/cli/spec.py`
- Consequence: Every harness and every person reads the rules where the code is, with no prompt change; a directory that stops being governed loses its section
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-8 — `ASSUMED` (Decisions as gates and the projections beside the code)

`AGENTS.md` sections carry rows, invariants, checks, tests and warnings only; no generated overview, no prose beyond the row's own text and consequence

- Paths: `src/torve/application/colocation.py`
- Consequence: The one controlled study says overviews cost and do not help; rules and facts do

### S-0054/D-10 — `LOCKED` (Decisions as gates and the projections beside the code)

The context pack under `.torve/context/` is computed host-side from the record and the tree, with no model, before the prompt is written; deterministic for a base sha and record state; gitignored; never in an image; byte-identical for a shadow run; own task only

- Paths: `src/torve/application/contextpack.py` `src/torve/application/session.py`
- Consequence: S-0035's rule for derived state applied to knowledge; S-0017/D-4, S-0017/D-7 and S-0001/D-29 hold; a replay stays comparable
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-11 — `ASSUMED` (Decisions as gates and the projections beside the code)

The pack's files are `index.md`, `decisions.json`, `gates.json`, `tests.json` (from `coverage.xml`), `attempts.json`, `contended.json`, `schema/`; each lands one at a time behind a config-eval on the compliance-red population and is deleted if attempts to green and dollars per landing do not fall by more than its tokens cost

- Paths: `src/torve/application/contextpack.py`
- Consequence: Nothing that adds tokens to every attempt ships without a number; the study's +20 % is the prior to beat

### S-0054/D-12 — `ASSUMED` (Decisions as gates and the projections beside the code)

A red pass writes its gate report into the next attempt's `attempts.json` — output tails with byte-loss markers, governing decision ids for `decisions-reported` and `scope`, failed test ids from JUnit — and the retry ladder still selects the tier from outcome and axis alone (S-0034/D-5)

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py`
- Consequence: What tier runs next is unchanged; what it is told is the change; S-0043's loop becomes the general case

### S-0054/D-13 — `LOCKED` (Decisions as gates and the projections beside the code)

The reviewer reads the pack minus any model-authored entry, plus `touched.json` (rows the diff intersects, coverage of changed lines, prior findings on the task); nothing the author wrote enters (S-0005/D-3 unchanged)

- Paths: `src/torve/application/review.py`
- Consequence: Engine facts about the diff are not the author's voice; the reviewer stops being the role with the least context
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0054/D-15 — `ASSUMED` (Decisions as gates and the projections beside the code)

Findings and drafts are validated against the pydantic models' JSON Schema shipped in `schema/`; a validation failure is a lint refusal naming the field, never an `unparseable` row

- Paths: `src/torve/application/review.py` `src/torve/application/intake.py`
- Consequence: The output contract is a contract the agent can read; two of three unparseable rows on record were shape failures

### S-0054/D-16 — `ASSUMED` (Decisions as gates and the projections beside the code)

The drafter's 400-path listing is replaced by the pack's `index.md` and `torve spec paths` over the candidate scope

- Paths: `src/torve/application/intake.py`
- Consequence: A split is proposed against the rows it would cross, not a list of filenames

### S-0055/D-1 — `LOCKED` (Standing decisions)

Models never decide what work exists or whether it is finished; the runner executes state transitions from facts — exit codes, gate outcomes, approvals — and an agent reports observations that never cause a transition

- Paths: `src/torve/application/runner.py` `src/torve/application/manager.py` `src/torve/domain/states.py`
- Consequence: A reviewer's severity is data whose consequence configuration sets; the planner invokes no model at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-2 — `LOCKED` (Standing decisions)

Gates execute outside the agent session against the working tree it leaves behind; an agent cannot report a gate outcome

- Paths: `src/torve/gates/**` `src/torve/application/runner.py`
- Consequence: The battery is the acceptance; a green the agent claims is not one
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-3 — `LOCKED` (Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-5 — `LOCKED` (Standing decisions)

Agents do not communicate; executor memory is off by default, per slot when on, and never mounted in a shadow run

- Paths: `src/torve/application/shadow.py` `src/torve/config/runconfig.py`
- Consequence: Shared memory is a communication channel with a euphemism; a remembered replay is an incomparable number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-7 — `LOCKED` (Standing decisions)

A task contract is immutable once minted: a changed contract is a new task, a re-mint is a version and never a transition, and a task in flight is never re-minted

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: An executor's contract cannot drift out from under it; `torve plan` refuses to re-mint over minted phases and leaves the decision to a person
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-8 — `ASSUMED` (Standing decisions)

Contracts are derived artefacts committed under `.torve/tasks/`; the task directory is the importer and the record is what dispatch reads

- Paths: `.torve/tasks/**` `src/torve/application/taskstore.py`
- Consequence: Reproducible from a sha, refusable in a diff; the file is authored, the record is read

### S-0055/D-9 — `LOCKED` (Standing decisions)

A contract copies its rows' grade, text and paths at mint time; a grade is never resolved at read time

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py`
- Consequence: A regrade never rewrites the judgement of a task that ran under the old grade
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-11 — `ASSUMED` (Standing decisions)

A divergence entry is written only through `torve log divergence`, validated by the gate's own checks at intake, with located evidence; `drift_count` is derived, never declared

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `.torve/tasks/**`
- Consequence: A hand-edited log is refused by the same code that gates it

### S-0055/D-16 — `ASSUMED` (Standing decisions)

Every gate runs on every pass, cheapest first, never short-circuiting; the retry rung reads outcome and axis only, never a trace or a gate's output

- Paths: `src/torve/gates/runner.py` `src/torve/application/session.py`
- Consequence: A replay of the rows reproduces the tier choice exactly; the severity ladder stays meaningful

### S-0055/D-17 — `LOCKED` (Standing decisions)

Scope is a contract: `allow` and `deny` globs make a touch outside them a red gate, and tasks whose allow sets intersect are never dispatched in parallel

- Paths: `src/torve/gates/scope.py` `src/torve/application/manager.py`
- Consequence: Thirty-two convictions and their corrections; a collision is refused at dispatch, never discovered at merge
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-20 — `ASSUMED` (Standing decisions)

Acceptance is a list of shell commands, exit 0 meaning satisfied, run in the battery sandbox over a worktree without a repository; a command that needs git cannot be an acceptance command

- Paths: `src/torve/gates/acceptance.py` `src/torve/application/intake.py`
- Consequence: The lint refuses such a command at mint rather than after three attempts

### S-0055/D-21 — `ASSUMED` (Standing decisions)

A bypass is a `Torve-Bypass: <gate>: <reason>` commit trailer, counted per gate in telemetry

- Paths: `src/torve/gates/runner.py` `src/torve/application/telemetry.py`
- Consequence: Every waiver is a recorded fact with a name on it

### S-0055/D-29 — `ASSUMED` (Standing decisions)

Application code reaches stores only through injected factories; substrate wiring lives in the facade and `config_hash` beside the telemetry it stamps

- Paths: `src/torve/application/**`
- Consequence: Anything derived below a port is invisible to simulation

### S-0055/D-31 — `ASSUMED` (Standing decisions)

Providers a repository's contents may reach are enforced at dispatch, before a sandbox exists; an empty default denies every real provider

- Paths: `src/torve/config/runconfig.py` `src/torve/application/dispatch.py`
- Consequence: Silence is not a policy; it is a closed door

### S-0055/D-32 — `ASSUMED` (Standing decisions)

A tier resolves through profiles in the operator's own configuration directory, never the repository under work; a missing profile or an unknown skill refuses rather than falls back

- Paths: `src/torve/config/runconfig.py` `src/torve/application/skills.py`
- Consequence: Resolution is fail-closed throughout

### S-0055/D-35 — `ASSUMED` (Standing decisions)

Sandbox images are digest-pinned inputs to a run; an image the eval ledger has not measured refuses dispatch unless the rebuild hatch is set, and the dispatch records the unmeasured digest

- Paths: `.torve/sandbox/**` `src/torve/application/dispatch.py` `.torve/evals.jsonl`
- Consequence: Numbers from different images are never silently compared

### S-0055/D-36 — `ASSUMED` (Standing decisions)

Skills ship as package data under `skills/` and are materialised role-scoped into the sandbox; a vendored skill lives under `.torve/skills-vendor/`; a name in both refuses, a name in neither is a configuration error

- Paths: `skills/**` `.torve/skills-vendor/**` `src/torve/application/skills.py`
- Consequence: What an agent knows is versioned and named, never ambient

### S-0055/D-37 — `LOCKED` (Standing decisions)

A fact is an event of a closed kind vocabulary with an authority table; an agent may write only `divergence.recorded` and `message.sent`; no update command exists on the record

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py`
- Consequence: An audit trail that can be edited cannot be trusted; the authority table refuses before any store sees the write
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-38 — `ASSUMED` (Standing decisions)

The manager holds nothing between passes and rebuilds its view from the record; a worker holds a lease and nothing else

- Paths: `src/torve/application/manager.py` `src/torve/application/worker.py` `src/torve/application/residency.py`
- Consequence: A restart is a re-read, never a recovery procedure

### S-0055/D-39 — `ASSUMED` (Standing decisions)

Retirement of a decision is recorded, never inferred from a row that stopped appearing; a second `decision.recorded` on a subject is a version, `supersedes` names a different decision

- Paths: `src/torve/application/decisions.py`
- Consequence: Absence cannot tell a retirement from a broken table

### S-0055/D-40 — `ASSUMED` (Standing decisions)

A landing is recorded from the `Torve-Task` trailer on the commit; git log is the surviving record and the importer mints landed history from it

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/runner.py`
- Consequence: A session-authored landing enters the record the same way an agent's does

### S-0055/D-41 — `ASSUMED` (Standing decisions)

Every attempt appends one telemetry row stamped with the `config_hash` of the regime it ran under; absent counts stay absent, never zero; the verdict is engine-derived and never model output

- Paths: `src/torve/application/telemetry.py` `.torve/telemetry.jsonl` `.torve/regimes/**`
- Consequence: Numbers from different regimes are never silently compared; a mute row is a mute row

### S-0055/D-42 — `ASSUMED` (Standing decisions)

Every read of the log pages; no reader folds a truncated prefix as if it were the whole

- Paths: `src/torve/application/eventlog.py`
- Consequence: The board once decided dispatch over a third of its tasks missing

### S-0055/D-43 — `ASSUMED` (Standing decisions)

Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py`
- Consequence: A substrate surface change fails at the pin bump, not in production

### S-0055/D-44 — `ASSUMED` (Standing decisions)

`torve plan` is deterministic and invokes no model; it mints from exactly one accepted, committed document, copies that document's whole table, and refuses to re-mint over minted phases

- Paths: `src/torve/application/planner.py` `src/torve/cli/plan.py`
- Consequence: What to do with existing tasks is a human decision

### S-0055/D-45 — `ASSUMED` (Standing decisions)

Nothing inherits from a document that is not `accepted`; a contract without a document inherits every accepted row whose declared paths intersect its `scope.allow`

- Paths: `src/torve/application/planner.py` `src/torve/application/intake.py`
- Consequence: The document-less lane is governed; a draft's rows never stand

### S-0055/D-46 — `ASSUMED` (Standing decisions)

A draft passes the deterministic lint before adoption — non-empty intent and acceptance, in-tree globs, a module's test file allowed beside it, no git in acceptance, pairwise-disjoint scopes — and adoption mints identifiers under the engine lock

- Paths: `src/torve/application/intake.py` `src/torve/application/enginelock.py`
- Consequence: A defect the lint names at drafting costs nothing; the same defect at attempt three cost 2410 seconds once

### S-0055/D-47 — `ASSUMED` (Standing decisions)

A contract the size estimate calls `too_large` routes to a decomposition run rather than an attempt; children stay inside the parent's scope, decomposition depth is at most two, and the parent survives as the integration task

- Paths: `src/torve/application/sizing.py` `src/torve/application/intake.py`
- Consequence: Oversized work is split at drafting time, where the tree is in view

### S-0055/D-48 — `ASSUMED` (Standing decisions)

The poison ceiling is checked before dispatch and reaching it escalates, never retries; a red gate pass retries under the ladder's next rung

- Paths: `src/torve/application/dispatch.py` `src/torve/application/session.py` `.torve/config.yaml`
- Consequence: An attempt is spent on a chance, never on a certainty

### S-0055/D-49 — `LOCKED` (Standing decisions)

Review is a second run role in its own sandbox; the reviewer never receives the author's trace; findings are one JSON document with located evidence, a blocker escalates the target and the rest go to the operator's ledger

- Paths: `src/torve/application/review.py`
- Consequence: Reviewer independence is from the author's reasoning; engine facts about the diff are not the author's
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-50 — `LOCKED` (Standing decisions)

The engine never resolves a merge conflict and never lands without the configured approval; landings serialize through one lane per base

- Paths: `src/torve/application/lane.py` `src/torve/application/residency.py`
- Consequence: A conflict against an unmoved base is the same conflict, and re-queueing it is a loop
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-51 — `ASSUMED` (Standing decisions)

A landing commit carries `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` and `Torve-Decisions` trailers; provenance is written by the runner, never by the agent

- Paths: `src/torve/application/runner.py`
- Consequence: What landed, under which regime, inheriting which rows, is readable from git alone

### S-0055/D-56 — `ASSUMED` (Standing decisions)

Cadence belongs to the manager's pass; there is no resident scanning loop, and the standing legs — maintenance, relay, lane — run inside the pass under their switches

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Consequence: One process to run and supervise; a pause stops what advances the repository

### S-0055/D-57 — `ASSUMED` (Standing decisions)

Standing maintenance is a committed contract under `.torve/standing/` with a trigger kind, cooldown, `max_open` and `strike_limit`, evaluated deterministically

- Paths: `.torve/standing/**` `src/torve/application/standing.py`
- Consequence: A job that fires is a fact with a bound, never a daemon's habit

### S-0055/D-58 — `ASSUMED` (Standing decisions)

The review corpus under `.torve/review-corpus/` grows from post-merge escapes, and reviewer regressions are measured by replaying it

- Paths: `.torve/review-corpus/**` `src/torve/application/review.py`
- Consequence: A reviewer's calibration is a measured quantity

### S-0055/D-60 — `ASSUMED` (Standing decisions)

Every attempt runs on a worktree seeded with the base sha pinned host-side, the role's skills materialised, and the divergence log seeded; the session trace is captured to `.torve/traces/` and its content enters no prompt and drives no control flow

- Paths: `src/torve/application/session.py` `src/torve/adapters/agent/harness.py` `.torve/traces/**`
- Consequence: What an agent saw is reconstructible; what it reasoned is never an input to another agent

### S-0056/D-2 — `LOCKED` (Structure for everything)

`DecisionDetail` folds into `Decision`; the fenced kinds are gone; a row carries `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by` and its `fingerprint` on itself

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: `inherit_decisions` reads the row; the frontmatter fingerprint map is gone
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-8 — `ASSUMED` (Structure for everything)

`SKILL.md`, `AGENTS.md`, the colocated sections and the pack are unchanged: projections rendered from the model, read by harnesses and people

- Paths: `skills/**` `src/torve/application/colocation.py`
- Consequence: Nothing a harness reads changes shape

### S-0056/D-9 — `LOCKED` (Structure for everything)

With a store configured, `plan` mints into the record and writes no file; dispatch projects `.torve/tasks/<id>/contract.yaml` into the worktree, gitignored; the log written there is imported after the attempt; without a store the files are the record as today

- Paths: `src/torve/application/planner.py` `src/torve/application/session.py` `src/torve/config/layout.py`
- Consequence: The board is the only place a task is; the file exists for the attempt that reads it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0056/D-11 — `ASSUMED` (Structure for everything)

`torve migrate telemetry` reads the file once into the record; every reader that opens `.torve/telemetry.jsonl` goes through one function that reads the record when a store is configured and the file when not

- Paths: `src/torve/application/telemetry.py` `src/torve/application/contextpack.py` `src/torve/application/intake.py` `src/torve/application/specquality.py`
- Consequence: The file is a carrier and an append target, never what a reader with a record opens

### S-0056/D-13 — `OPEN` (Structure for everything)

Whether phase 4 gives `traces/` and `regimes/` a retention window or leaves them to S-0039

- Paths: `src/torve/application/reaper.py`
- Consequence: Decided by whoever executes phase 4, logged

### S-0057/D-7 — `LOCKED` (The specification is a directory)

`execution.yaml` holds landings — task, phase, attempt, time, agent, the commit when the lander knows it, and the log's entries typed as `LogEntry` — appended by one function the runner calls before the candidate commit, whose trailers name the task so the field stays empty there, and `torve log land --commit SHA` exposes for a landing made by hand; a contract naming no document lands nowhere and says so

- Paths: `src/torve/application/divergence.py` `src/torve/application/runner.py` `src/torve/cli/log.py`
- Consequence: Every clone carries what execution found, beside the rows it informs; the task directory carries nothing git keeps
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0057/D-8 — `ASSUMED` (The specification is a directory)

`torve decisions import` reads every execution file, live and archived, and records the divergence and landing events the record lacks, idempotent by task, attempt, decision and time; the attempt-time `ingest` is unchanged

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: A clone without a store rebuilds the same record from the tree

### S-0057/D-12 — `ASSUMED` (The specification is a directory)

The colocated sections, the pack and the skills change in no shape but the word and the gate name; they render the same model

- Paths: `src/torve/application/colocation.py` `src/torve/application/contextpack.py`
- Consequence: Nothing a harness reads changes shape

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

### S-0058/D-9 — `ASSUMED` (One grammar and the anatomy)

The `decision:<id>` gate names, the pack's decisions file, the log entry's `decision` and the record's subjects carry the global form; the record re-imports once after the conversion, retiring every old subject with the reason naming its replacement

- Paths: `src/torve/gates/runner.py` `src/torve/gates/decisions_reported.py` `src/torve/application/contextpack.py` `src/torve/application/decisions.py`
- Consequence: The record's history keeps the old ids as retired rows and the new ones as what stands

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

### S-0059/D-2 — `LOCKED` (One word for the document, and the tree as the record)

Every reader resolves a contract's document by `document_dir(spec_dir, identifier)`; the scope gate's exemption and the runner's decision gates come from the identifier, and the gate context carries the corpus path

- Paths: `src/torve/gates/context.py` `src/torve/gates/scope.py` `src/torve/gates/runner.py` `src/torve/application/decisions.py` `src/torve/adapters/agent/harness.py`
- Consequence: The regexes over the path go; a document that moved is still found
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

### S-0059/D-9 — `LOCKED` (One word for the document, and the tree as the record)

The runner commits the work, then writes the landing naming `base` and `commit` and commits it alone as `torve(T-NNNN): landing of attempt N` — two commits per attempt, departing S-0010/D-8; an attempt that changed nothing still lands, with an empty `commit`

- Paths: `src/torve/application/runner.py` `src/torve/application/decisions.py`
- Consequence: The runner's landing names its commit the way a by-hand landing does; the landing commit is the branch's tip
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

### S-0059/D-12 — `LOCKED` (One word for the document, and the tree as the record)

Every reader of a landing reads the tree through `landings` and `landed_commits` — `shipped_landings`, `shipped_ids`, `shipped_commit`, the revert leg, `status`, `shadow`, `evals`, the review's defect lookup, the PR review's `landed_tasks` and `spec cites`; no git subprocess reads a trailer or a subject, closing S-0022/A-1's exception and retiring S-0007/D-26's subject spellings; the five local tasks without a landing get one written once from their trailers, by a script not committed

- Paths: `src/torve/application/projections.py` `src/torve/application/specquality.py` `src/torve/application/review.py` `src/torve/application/session.py` `src/torve/application/ports.py` `src/torve/application/residency.py` `src/torve/application/shadow.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/workspace/git.py` `src/torve/cli/**`
- Consequence: A tree without git answers what landed; S-0022/D-5 holds again without its exception
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-4 — `LOCKED` (A source is a file, and the contract names it)

The record's source id of a task is `source` before `spec` before `operator`, so `operator` means nobody said rather than the operator said; the 20 mints already recorded as `operator` are not rewritten

- Paths: `src/torve/application/residency.py`
- Consequence: The record answers what made us do this with the truth or with an honest absence
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-5 — `LOCKED` (A source is a file, and the contract names it)

`torve intake --source <id>` records the source on the drafting run, carries it in the drafts file and copies it onto every contract adoption mints, refusing an unknown source before a model is called; a standing job names `source` beside `decisions_from`; `torve plan` sets none, because a phase's task is sourced by its document

- Paths: `src/torve/cli/intake.py` `src/torve/application/intake.py` `src/torve/application/standing.py`
- Consequence: The front door records what walked in, and a recurring job's contracts say which job minted them
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-7 — `LOCKED` (A source is a file, and the contract names it)

`import_corpus` becomes `import_sources`, recording every file under `.torve/sources/` as a `SourceImported` with its own kind beside every document as today, idempotent as before; a source whose file is deleted keeps what was recorded and is not retired

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: Every kind the vocabulary admits has a producer
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0060/D-9 — `ASSUMED` (A source is a file, and the contract names it)

The harness prompt names the source, its title and its ref in the line above the decisions, and the pack carries the source's own file beside `decisions.json`

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/contextpack.py`
- Consequence: The executor reads what asked, not a slug

### S-0061/D-7 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

The regime hash reads each seat's resolved harness, profile and own keys, and stops reading skills-lock.json.

- Paths: `src/torve/application/telemetry.py`
- Consequence: the hash changes when the equipment changes, and stops changing when a tool torve does not run reformats its own lockfile

## Invariants holding over `src/torve/application/`

- **S-0059/I-3**: Every property of every schema `torve init` writes carries a description
  - Paths: `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py`
  - Check: `uv run pytest tests/test_spec.py -k schema_descriptions`

<!-- /torve:managed -->
