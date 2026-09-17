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

### S-0062/D-4 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

Equipment is fetched host-side into a cache keyed by source and ref, never inside an attempt.

- Paths: `src/torve/application/equipment.py` `src/torve/cli/equip.py`
- Consequence: an attempt's failures do not include the internet's, and a warmed cache makes dispatch touch no network at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-5 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

The equipment cache mounts read-only into the sandbox, one directory per item, and the image's own `/opt/torve/equip` composes whatever its harness needs from the manifest at the mount root (S-0063/D-3, S-0063/D-12).

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/ports.py`
- Consequence: nothing inside an attempt can edit what it was equipped with, and the cache stays derived state that deleting costs only wall clock

### S-0062/D-6 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A skill a gate reads stays package data, versioned with the engine; `torve:` is the source that names one.

- Paths: `src/torve/application/skills.py` `skills/**`
- Consequence: the two skills this engine ships cannot drift against the gate that parses what they teach
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-7 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

A profile may declare a `prepare` command; torve runs it in the sandbox before the agent, with its own clock, and a non-zero exit is an infrastructure failure that convicts nothing.

- Paths: `src/torve/application/session.py` `src/torve/config/equipment.py`
- Consequence: an index that fails to build ends the attempt as what it is, rather than as a model that could not make the battery pass
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-8 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

The regime hash reads the equipment cache keys, not the fetched contents.

- Paths: `src/torve/application/telemetry.py`
- Consequence: two checkouts of one tree hash one regime without either having fetched anything yet

### S-0062/D-9 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

S-0061/D-5 is superseded by D-1 and S-0061/D-6's renderer retires; the harness is told about a plugin by its own flag, not by files torve writes into its state.

- Paths: `sandboxes/**` `src/torve/cli/sandbox.py` `src/torve/application/ports.py`
- Consequence: torve stops keeping a second copy of a harness's internal bookkeeping in step with it across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-10 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

For a harness that takes the `skill` kind the prompt stops naming `.torve/skills/`; for one that does not, `materialize` and the prompt's paragraph stand unchanged. All three harnesses this repository builds take it — claude by flag, dsh at `.agents/skills`, mimo at `.mimocode/skill/` (S-0063/D-16) — so the paragraph stands for none of them.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/skills.py`
- Consequence: a loaded skill is loaded, not described — and a harness with no skill channel keeps the only mechanism it has

### S-0062/D-11 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

`torve equip --check` audits the cache against what each source recorded — a skill's `github-pinned` frontmatter, a clone's HEAD — and reports; it never refetches and never resolves.

- Paths: `src/torve/cli/equip.py` `src/torve/application/equipment.py`
- Consequence: a cache directory that does not hold what its key claims is a finding an operator can read, rather than a regime hash that agrees with itself and with nothing else

### S-0063/D-2 — `LOCKED` (The image knows how to equip itself)

The engine speaks to every image through one set of environment variables — `TORVE_PROMPT`, `TORVE_MODEL`, `TORVE_EQUIPMENT`, `TORVE_EQUIP_ROOT`, `TORVE_OUTPUT`, and `TORVE_BROKER_URL`/`TORVE_BROKER_TOKEN` where a broker is in force.

- Paths: `src/torve/application/ports.py` `src/torve/adapters/agent/harness.py`
- Consequence: a new harness is a new image and never a new template language, and every adapter fills one shape
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-10 — `ASSUMED` (The image knows how to equip itself)

A manifest's `env` mapping reaches the image's environment and nothing else reads it; a knob that is not there is a rebuild.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py`
- Consequence: an operator changes a permission mode in configuration, and a change to how the harness is invoked moves the image digest the telemetry already records

### S-0063/D-11 — `LOCKED` (The image knows how to equip itself)

`torve sandbox build` and `stage` retire, and `RuntimePort.build_image` with them; the verb keeps `list` and `digest`, and `just images` is the build.

- Paths: `src/torve/cli/sandbox.py` `src/torve/application/ports.py` `src/torve/adapters/runtime/**`
- Consequence: the engine loses its last way to build an image, which is the rule S-0017/D-3 already stated and could not enforce while a verb of its own did it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-12 — `LOCKED` (The image knows how to equip itself)

The equipment manifest is `manifest.json` at the root of the read-only equipment mount, named by `TORVE_EQUIPMENT` alone — never a second variable, never a file in the workspace.

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/**`
- Consequence: the attempt cannot rewrite the description of what it was equipped with, and one variable names one root rather than two disagreeing about which is authoritative
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-16 — `LOCKED` (The image knows how to equip itself)

`skill` is a kind every harness this repository builds accepts, delivered the way that harness reads skills; S-0062/D-10's prompt paragraph stands only for a harness that reads none.

- Paths: `sandboxes/**` `.torve/harnesses/**` `src/torve/application/skills.py`
- Consequence: a skill reaches a dsh or mimo seat as a skill its harness loads, rather than as a directory the prompt names and the model may or may not read
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-19 — `LOCKED` (The image knows how to equip itself)

Equipment never writes into a path the repository owns. A harness that reads equipment from the workspace declares its own root on the manifest as `equip_root`; the engine excludes that root in the worktree and names it to the image as `TORVE_EQUIP_ROOT`.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py` `sandboxes/**` `.torve/harnesses/**`
- Consequence: an attempt commits its own work and nothing else, and a repository's reviewed skills are never overwritten by a packaged copy of the same name
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-3 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A model entry's key is what a seat writes and its `id` is what reaches the provider and what the regime hash records; a key with no `id` is its own id.

- Paths: `src/torve/config/providers.py` `src/torve/application/telemetry.py`
- Consequence: a slug that is awkward to type or to use as a path segment gets a local shorthand, and renaming that shorthand cannot move a regime digest

### S-0064/D-8 — `LOCKED` (A provider is a record, and the seam carries scalars)

Brokered and direct differ by the value of `TORVE_BASE_URL` and `TORVE_API_KEY` and never by a variable's presence; `TORVE_BROKER_URL` and `TORVE_BROKER_TOKEN` retire and no image tests whether a broker is in force.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/session.py` `sandboxes/**`
- Consequence: nine broker branches across three definitions go, and mimo becomes brokerable by deletion rather than by implementation
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-9 — `ASSUMED` (A provider is a record, and the seam carries scalars)

`api_key_env` retires from the harness manifest: a credential is a property of the provider, which names its own `key_env`. `auth_volume` and `auth_mount` stay.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py` `.torve/harnesses/**`
- Consequence: one file decides which credential reaches a sandbox, and the refusal that keeps a brokered seat honest has one field to watch instead of two

### S-0064/D-12 — `ASSUMED` (A provider is a record, and the seam carries scalars)

A model entry may carry `price`, and where it does the attempt's cost is computed from the record and the token counts; a harness's self-reported cost is kept beside it as the adapter's claim and is never the number. Where it does not, cost stays unreported rather than invented.

- Paths: `src/torve/config/providers.py` `src/torve/application/dispatch.py`
- Consequence: the ledger stops depending on whether a harness recognises the model it was pointed at, and the divergence check compares two numbers that are both about this call — torve's arithmetic against the broker's metering — instead of comparing a rate card to reality

### S-0065/D-1 — `ASSUMED` (The record is read, and what looks lost is unjoined)

`torve ledger` folds the record into rates — cost per landed task, attempts per landing, convictions before landing, duty cycle — printed per seat and per gate, and never lists rows a renderer already prints

- Paths: `src/torve/application/ledger.py` `src/torve/cli/ledger.py`
- Consequence: the four numbers the engine's case rests on become a command rather than a hand computation in a document, and every later comparison has one arithmetic to cite

### S-0065/D-2 — `ASSUMED` (The record is read, and what looks lost is unjoined)

A seat is a tier and the image it was pointed at together; a rate is never reported across seats

- Paths: `src/torve/application/ledger.py`
- Consequence: one tier name aimed at three images over the record stops averaging into a figure that describes nothing that exists

### S-0065/D-3 — `ASSUMED` (The record is read, and what looks lost is unjoined)

An attempt's record carries what it was fenced by and judged against — the inherited rows, the scope and the acceptance — and where the contract is still in git history it is additionally read at the attempt's own sha

- Paths: `src/torve/application/ledger.py` `src/torve/application/telemetry.py`
- Consequence: the whole record becomes joinable retroactively rather than only what comes after a policy change; a deleted task directory costs a `git cat-file`, not a fact

### S-0065/D-4 — `ASSUMED` (The record is read, and what looks lost is unjoined)

An attempt record without a base sha is refused at the writer, naming the field; the rows already written that carry none are reported as unjoinable and excluded from every rate

- Paths: `src/torve/application/telemetry.py`
- Consequence: a cost and a duration attached to nothing stops entering the record, and the eighteen that exist stop silently moving an average

### S-0065/D-5 — `LOCKED` (The record is read, and what looks lost is unjoined)

A derived rate counts only attempts that ran a model — `fake` adapters and shadow replays are excluded, and the exclusion is reported rather than silent

- Paths: `src/torve/application/ledger.py`
- Consequence: every later comparison rests on the same denominator, and a seat cannot be made to look catastrophic by fixture traffic that never called a provider
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0065/D-6 — `ASSUMED` (The record is read, and what looks lost is unjoined)

`terminal_reason` and `session_id` are recorded on the attempt when the harness returns them, absent when it does not, and never synthesised

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/runner.py`
- Consequence: a three-second boot failure and a twenty-minute clock stop being the same class, and the continuation question becomes answerable without committing to an answer

### S-0065/D-7 — `ASSUMED` (The record is read, and what looks lost is unjoined)

One carrier is the landing, and the other two are reconciled against it or dropped

- Paths: `src/torve/application/lane.py` `src/torve/application/projections.py`
- Consequence: duty cycle and landings-through-the-lane acquire a denominator that does not depend on which reader was asked

### S-0069/D-2 — `ASSUMED` (A conviction is the next contract's input)

The conviction reaches the next attempt in the prompt — the gate, its output tail, the paths the diff touched and the inherited rows governing them — as evidence the contract still outranks

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/contextpack.py`
- Consequence: the most specific thing the engine ever learns about a task stops arriving through a channel an attempt may skip

### S-0069/D-3 — `ASSUMED` (A conviction is the next contract's input)

A repair's acceptance gains the convicting gate's own command, and keeps everything the contract already declared

- Paths: `src/torve/application/runner.py`
- Consequence: a repair that leaves the gate red fails inside the attempt, and one that clears it by breaking another gate still fails

### S-0069/D-4 — `ASSUMED` (A conviction is the next contract's input)

A repair starts from the convicted attempt's tree, not from base

- Paths: `src/torve/application/runner.py`
- Consequence: the work that was right survives the mistake, and the tree a mechanical conviction names is repaired rather than rebuilt

### S-0069/D-5 — `ASSUMED` (A conviction is the next contract's input)

Which convictions qualify for a repair — the set starts at the gates whose checks are pure functions of the tree and grows on the ledger's evidence

- Paths: `src/torve/application/runner.py`
- Consequence: a repair is never offered for a conviction where the approach rather than the tree is what failed, and widening the set is a decision with a number behind it

### S-0069/D-6 — `LOCKED` (A conviction is the next contract's input)

A repair is attempted once per conviction; a second conviction on the same gate routes exactly where it routes today

- Paths: `src/torve/application/runner.py`
- Consequence: the poison ceiling keeps meaning three failures the same way rather than three attempts at one fix, and a repair cannot become a loop reading its own failure as its input
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0070/D-1 — `LOCKED` (What is committed may not depend on what is not)

A committed artefact is a function of committed inputs; anything derived from the record reaches a reader through the pack or a verb, never through a file a gate diffs against a fresh render

- Paths: `src/torve/application/colocation.py` `src/torve/application/projections.py`
- Consequence: a committed file renders the same on a laptop, in a sandbox and in a clean clone, which is what makes a drift check mean anything
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0070/D-5 — `ASSUMED` (What is committed may not depend on what is not)

Path rot is a glob that governs nothing, not a glob whose files the repository deliberately does not commit; the check reads `.torve/.gitignore` and says which it found

- Paths: `src/torve/application/decisions.py` `src/torve/config/spec.py`
- Consequence: `torve spec check` passes in a clean clone, so `spec-valid` and the acceptance command stop depending on a task directory that dispatch happens to have written

### S-0070/D-2 — `ASSUMED` (What is committed may not depend on what is not)

The projection's live sections move to the pack, which already carries the same facts through a channel that is neither committed nor diffed

- Paths: `src/torve/application/colocation.py`
- Consequence: the reader who most needs contention — an attempt about to touch a contended path — keeps it, and the drift check stops judging a number nobody wrote

### S-0071/D-5 — `LOCKED` (The battery costs what it costs for reasons unrelated to what it judges)

A phase's acceptance commands are runnable in the environment that judges them: a sandbox has no Docker daemon, no host credentials and no operational state, so a command that reads any of those can never go green there and the contract is unlandable by construction

- Paths: `.torve/specs` `src/torve/application/intake.py`
- Consequence: an authoring mistake costs one refusal at mint instead of the poison ceiling, three attempts and their money
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0073/D-3 — `ASSUMED` (The working rules live once, and say what an attempt costs)

`num_turns`, `permission_denials` and `subagent_stats` are recorded on the attempt when the receipt carries them, absent when it does not, and never invented

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/telemetry.py`
- Consequence: an attempt at 113 turns and an attempt at 12 stop reading as the same attempt

### S-0074/D-1 — `LOCKED` (What the engine is worth against a bare harness)

An arm is named by the apparatus it removes — bare, gated, configured — and an eval record carries which arm produced each result, so the comparison is rebuildable from the ledger alone

- Paths: `src/torve/application/evals.py` `.torve/evals.jsonl`
- Consequence: the three-arm table can be recomputed a year later without the person who ran it, which is the difference between a measurement and an anecdote
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0074/D-3 — `LOCKED` (What the engine is worth against a bare harness)

An arm that runs without the battery still merges nothing, and the removal travels as a property of the replay rather than as an edit to the gate manifest

- Paths: `src/torve/application/evals.py` `src/torve/application/runner.py`
- Consequence: a bare arm cannot land anything, and the manifest every other attempt is judged by is never the thing that changed
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0074/D-4 — `ASSUMED` (What the engine is worth against a bare harness)

The result is reported per task across three rows, and any aggregate is a distribution rather than a mean

- Paths: `src/torve/application/evals.py`
- Consequence: a task where the bare arm shipped what the battery would have refused stays visible instead of averaging into a percentage

### S-0074/D-5 — `ASSUMED` (What the engine is worth against a bare harness)

`torve decompose` splits an oversized contract, so a document phased carefully enough to mint none is not a candidate for it; the path is exercised where authoring did not happen — a drafted intake, or a contract minted wide on purpose — and never by un-phasing work that was phased correctly

- Paths: `src/torve/application/intake.py`
- Consequence: C1 stops being a thing to attach to whichever document is next, and becomes a thing that needs the condition it handles

### S-0075/D-1 — `LOCKED` (What an attempt costs, measured per changed line)

An attempt records its per-request context curve — first, median, max and sum — on every seat, reconstructed from the message usage where cache fields are absent, with the shape used named on the row and the sum checked against the receipt's own total

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/telemetry.py`
- Consequence: the identity behind every token figure can be verified per attempt instead of trusted, and an attempt that cannot be reconstructed says so
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0075/D-2 — `ASSUMED` (What an attempt costs, measured per changed line)

The burn profile classifies an attempt's tool calls — pack reads, orientation, in-scope reads, edits, test runs, lint runs, bookkeeping, other — and records calls before the first edit, reruns, calls per message, result bytes by class, compaction events and the latency medians

- Paths: `src/torve/application/telemetry.py` `tests/test_attempt_record.py`
- Consequence: every mitigation has a class that judges it, so a change can be shown to have moved what it claimed rather than argued to have

### S-0075/D-3 — `LOCKED` (What an attempt costs, measured per changed line)

The ledger reports cache-read tokens, wall seconds, tool calls and dollars per changed line and per file in scope, from the diff the landing already commits, beside the per-task rates it reports now

- Paths: `src/torve/application/ledger.py` `src/torve/cli/ledger.py`
- Consequence: a fixed overhead that only hurts small tasks becomes visible, and a mitigation aimed at it can be judged
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0075/D-4 — `ASSUMED` (What an attempt costs, measured per changed line)

The attempt records what every request re-read: the tool and skill counts the init line already reports, the contract's row count and size, and the first request's context

- Paths: `src/torve/application/telemetry.py`
- Consequence: a change to the prefix is seen in the number rather than assumed from the diff that made it

### S-0075/D-6 — `LOCKED` (What an attempt costs, measured per changed line)

The burn profile is derived from the attempt's retained trace when it is read, not only recorded when the attempt ends; the recorded block is a cache of that derivation and the trace is what settles a disagreement

- Paths: `src/torve/application/telemetry.py` `src/torve/cli/ledger.py`
- Consequence: every attempt whose trace is still on disk has a profile, so a change is judged against a population rather than against the attempts that happened after the classifier shipped, and a correction to a class reclassifies the history instead of leaving it wrong
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0076/D-1 — `ASSUMED` (The attempt is handed what the engine already knows)

The pack's small deterministic files arrive in the attempt's first message rather than behind a read; `decisions.json` stays behind one

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/contextpack.py`
- Consequence: seven round trips every attempt spends opening what the engine wrote become zero, and the attempt starts with what the engine intended rather than with what it opened first

### S-0076/D-2 — `ASSUMED` (The attempt is handed what the engine already knows)

For a scope under a configured size, the in-scope files' contents and the tests `tests.json` names arrive in the attempt's first message; over it, an outline of the same files arrives instead

- Paths: `src/torve/application/contextpack.py` `.torve/config.yaml`
- Consequence: the sixteen calls a small attempt spends reading files the contract already named become one read of one document

### S-0076/D-3 — `LOCKED` (The attempt is handed what the engine already knows)

The hand-over is judged by the orientation class and the landing rate together, and is reverted if orientation falls while attempts per landing rises

- Paths: `src/torve/application/contextpack.py`
- Consequence: a change that buys turns by making attempts worse is caught by the measurement that motivated it rather than defended by it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0076/D-5 — `ASSUMED` (The attempt is handed what the engine already knows)

The pack writes a file naming every symbol the repository defines and where — the whole tree, not the scope — and the pack's own index names that file; the symbols are a file the attempt may grep, never content the prompt carries

- Paths: `src/torve/application/contextpack.py`
- Consequence: a lookup costs one call against one file instead of two or three against the tree, and an attempt that never looks pays nothing for it

### S-0077/D-4 — `ASSUMED` (The turns the engine could have taken itself)

The pack carries a map of where things are, and it arrives in the first message rather than behind a read

- Paths: `src/torve/application/contextpack.py` `src/torve/adapters/agent/harness.py`
- Consequence: the median eight calls an attempt spends finding its way around this repository become zero, and every attempt starts from the same account of the tree instead of from whichever corner it happened to list first

### S-0079/D-3 — `ASSUMED` (The night as a typed record)

The night's entries are the facts the log already holds inside the window — landings, gate verdicts, engine-ended attempts, escalations — and the night stamps no event of its own

- Paths: `src/torve/application/manager.py`
- Consequence: the report cannot disagree with the board beside it, and the price is that membership is a time comparison: a fact recorded between two nights belongs to whichever window holds its instant

### S-0079/D-4 — `ASSUMED` (The night as a typed record)

The morning report is a projection of the window computed on every call and stored nowhere, and it holds no field that prose can occupy

- Paths: `src/torve/application/manager.py`
- Consequence: no line of the report can be a model's account of its own night, because there is nowhere to put one — the rule is enforced by the shape rather than by a reviewer

### S-0079/D-6 — `ASSUMED` (The night as a typed record)

An empty ready queue refuses the night at the open, before the first pass; a queue that drains later closes the night rather than failing it

- Paths: `src/torve/application/residency.py`
- Consequence: a night that would have slept eight hours costs nothing and says so at the last moment the operator is present, and draining is never mistaken for an error

### S-0079/D-7 — `ASSUMED` (The night as a typed record)

The night stops on the first escalation whose class the operator named and continues on every other; `loop.pause_escalations` is untouched and still counts a queue

- Paths: `src/torve/application/residency.py`
- Consequence: an operator can say "wake me for a locked conflict, keep going on a merge conflict" without changing what escalates, and the count-based pause keeps governing the thing it already governs

### S-0079/D-10 — `ASSUMED` (The night as a typed record)

The lane leg a served night runs is handed `conflict_disposal`, so a candidate whose rebase conflicts against a moved base is re-queued by the engine instead of escalating `merge_conflict`

- Paths: `src/torve/application/lane.py` `src/torve/cli/manager.py`
- Consequence: one class of overnight escalation stops waiting for a person, and a candidate disposed of this way cannot be re-queued twice against the same tip because `conflict_base` bounds it

### S-0079/D-11 — `ASSUMED` (The night as a typed record)

The width is recorded as a term of the night and is one; this document does not claim more than one task per pass

- Paths: `src/torve/application/residency.py`
- Consequence: a later night at width three is comparable against tonight's, because tonight's record says what it ran at rather than leaving it to be inferred from the manager's source as of that date

### S-0079/D-12 — `OPEN` (The night as a typed record)

Where the wall-clock end is measured from and what a pass in flight does when it arrives is implementation's, under two constraints — an attempt in flight is not interrupted by the end, and the close is written even when the end falls inside a pass

- Paths: `src/torve/application/residency.py`
- Consequence: the end is a soft bound and the record has to say so, or a night that closed eleven minutes late reads as a night that ignored its terms

### S-0080/D-3 — `ASSUMED` (The lane opens a pull request, and a person lands it)

In `pull_request` mode the lane runs unchanged up to the landing and then, in place of `merge_ff`, pushes the candidate branch under lease and opens or refreshes the task's pull request, and stops; it never moves the base

- Paths: `src/torve/application/lane.py`
- Consequence: the second mode is a different last step rather than a second lane, so every criterion, probe and rebase before it is exercised by both modes and cannot drift apart

### S-0080/D-4 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The pull request's title and body are composed from the landing record — the task, the document it was minted from, the rows the contract carried with their grades, the gates' verdicts and the divergence entries — and from nothing the agent wrote as prose

- Paths: `src/torve/application/forge.py`
- Consequence: a reviewer on the forge decides from the same facts the execution file holds, and no line of the body is a model's account of its own work

### S-0080/D-5 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The sentence in a composed body saying the pull request lands by fast-forward and the merge button is never used is written in `local` mode alone

- Paths: `src/torve/application/forge.py`
- Consequence: a reader of a pull request in the new mode is not told not to use the one control the mode depends on

### S-0080/D-6 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The forge surface answers about a branch — its pull request's state and, when merged, the merge commit — and `PrInfo` carries that commit

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/ports.py`
- Consequence: the lane asks the one question it has, which is what happened to this task's branch, without first having to remember a pull request number

### S-0080/D-7 — `ASSUMED` (The lane opens a pull request, and a person lands it)

A pull request the forge reports merged is the landing: the engine records it on a later pass with the merge commit as the landed sha and the mode named, in the same record shape the local lane writes

- Paths: `src/torve/application/lane.py`
- Consequence: everything that asks what shipped keeps asking one way, and a landing performed by a person is a landing in the record rather than a gap in it

### S-0080/D-8 — `ASSUMED` (The lane opens a pull request, and a person lands it)

A pull request closed without merging is a terminal verdict the engine records as abandoned by a person, never re-queued and never escalated for triage

- Paths: `src/torve/application/lane.py`
- Consequence: a person who declined the work has declined it, and the engine does not spend a second attempt arguing with the only reviewer it has

### S-0080/D-9 — `ASSUMED` (The lane opens a pull request, and a person lands it)

A pull request that stops merging cleanly because the base moved takes the disposal the lane already has — once per base tip, rebased in a disposable worktree, the battery re-run — and the branch is republished so the pull request shows the tree that was measured

- Paths: `src/torve/application/lane.py`
- Consequence: the person looking at the pull request is looking at the candidate the battery judged, and a candidate cannot be disposed of twice against the same tip

### S-0080/D-11 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The promotion criteria torve holds decide whether a pull request is opened and nothing after it; once it is open the forge's own rules govern the merge and torve reads state

- Paths: `src/torve/application/lane.py`
- Consequence: a repository has one promotion policy rather than two that can disagree, and a candidate that fails a torve criterion costs nobody's attention on the forge

### S-0080/D-12 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The candidate branch is kept after the merge — nobody deletes it — so the attempt's own commits stay reachable and the record's join from task to commits survives the squash

- Paths: `src/torve/application/lane.py`
- Consequence: a landing whose sha no attempt wrote can still be traced back to the attempts that produced it, which is what the projections read

### S-0080/D-14 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The morning report holds pull requests opened, merged, conflicted and closed as four counts folded from the events in the night's window, with no field prose can occupy

- Paths: `src/torve/application/manager.py`
- Consequence: a person reading the report knows what is waiting on them on the forge, which in this mode is the whole of what is waiting on them

### S-0080/D-16 — `OPEN` (The lane opens a pull request, and a person lands it)

When the lane asks the forge, and whether it asks once per pass or once per candidate, is implementation's — under one constraint: a pass holding no open pull request asks nothing

- Paths: `src/torve/application/lane.py`
- Consequence: the credential is used in proportion to what is actually open, and an idle night costs no forge calls at all

### S-0080/D-17 — `OPEN` (The lane opens a pull request, and a person lands it)

What the engine does with a candidate whose branch the forge can no longer resolve — deleted on merge, or renamed — is implementation's, under one constraint: it is recorded and never silently re-opened

- Paths: `src/torve/application/lane.py`
- Consequence: a repository configured to delete head branches on merge produces a stated gap in the record rather than a task that quietly pushes itself back to the forge

### S-0081/D-6 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

The repair fact in the attempt record names the attempt number it repairs beside the convicting gate

- Paths: `src/torve/application/runner.py` `src/torve/application/dispatch.py`
- Consequence: whether taking the conviction as input beat re-dispatching from base becomes a query over the record, which is the question S-0069 exists to answer and currently cannot be asked of anything but a transcript

### S-0081/D-7 — `ASSUMED` (The test that proves nothing, and the conviction the record can name)

`red-on-base` joins the set of convictions that route a repair, and only once it blocks

- Paths: `src/torve/application/runner.py`
- Consequence: the first widening of that set since it was fixed at four, with its reason on the record, so the next one is argued rather than edited in

### S-0082/D-1 — `ASSUMED` (The arms can be launched)

The arm reaches the attempt as a composed prompt — the dispatch carries which arm is running, and an arm whose prompt is bare hands the session `build_prompt(task, bare=True)` through `AgentContext.prompt` instead of letting the adapter compose one — so the prompt's removal and the battery's are two properties of one replay

- Paths: `src/torve/application/dispatch.py` `src/torve/application/runner.py` `src/torve/application/session.py`
- Consequence: the gated arm — the bare prompt with the battery judging what comes back — is a combination of two flags rather than a third mechanism, and the arm that runs today under the name `bare` stops being the configured prompt with the gates off

### S-0082/D-2 — `OPEN` (The arms can be launched)

Whether a bare arm's worktree is also stripped of the projected contract, the materialised skill set and the context pack is decided at implementation, and whichever way it is decided the replay's record names which removals were in force

- Paths: `src/torve/application/session.py`
- Consequence: a reader of a bare arm's numbers can tell what the agent could still open, so the arm's limit is a recorded fact rather than something rediscovered by whoever doubts the result

### S-0082/D-3 — `ASSUMED` (The arms can be launched)

Which tasks an arm run may name is read from the landings the tree holds — completed, with a landing that names a commit — and what those tasks already cost is read from the telemetry ledger, both by one reader that takes no configuration and no agent

- Paths: `src/torve/application/evals.py`
- Consequence: the refusal for an arm with no task and the pre-flight the verb prints come from the same read, so they can never disagree about what is eligible

### S-0082/D-4 — `ASSUMED` (The arms can be launched)

The three arms are one replay function taking the arm by name, and `run_bare_shadow` becomes its bare case rather than a second copy of `run_shadow`

- Paths: `src/torve/application/evals.py`
- Consequence: the isolation every arm depends on — replay from the parent commit in a truncated clone, merging nothing — is written once, so an arm cannot acquire a landing path by being added to a copy nobody re-read

### S-0082/D-5 — `ASSUMED` (The arms can be launched)

One eval record per invocation, `kind: arm-eval`, whose `arms` map is keyed by the three arm names and whose rows are the ones `_arm_row` already writes, so `three_arm_table` reads it with no change to the reading

- Paths: `src/torve/application/evals.py`
- Consequence: the reader S-0074 built is the reader, unversioned and unbranched — a record written today and a record written a year from now are read by the same six lines

### S-0082/D-6 — `ASSUMED` (The arms can be launched)

An arm record carries no verdict field — no `matched` beside the rows — because three arms are not equally exposed to the same failures and a boolean over them is the mean the axis already refused

- Paths: `src/torve/application/evals.py`
- Consequence: nothing downstream can act on an arm run without reading it per task, which is the only way it can honestly be read

### S-0082/D-7 — `OPEN` (The arms can be launched)

Whether an arm run that raises partway through lands the rows it has or lands nothing is decided at implementation, and the record says which it did

- Paths: `src/torve/application/evals.py`
- Consequence: a three-replay invocation that dies on the third does not silently become a two-arm record that reads like a complete one

## Invariants holding over `src/torve/application/`

- **S-0059/I-3**: Every property of every schema `torve init` writes carries a description
  - Paths: `src/torve/domain/**` `src/torve/config/**` `src/torve/application/standing.py`
  - Check: `uv run pytest tests/test_spec.py -k schema_descriptions`
- **S-0062/I-1**: No equipment is fetched while an attempt is running — every fetch is host-side, before the sandbox exists.
  - Paths: `src/torve/application/equipment.py` `src/torve/adapters/runtime/**`
  - Check: `uv run pytest tests/test_equipment.py -k host_side`
- **S-0062/I-2**: Every equipment item a run used is named by a cache key that resolves to a source and a ref an operator wrote.
  - Paths: `src/torve/config/equipment.py` `src/torve/application/telemetry.py`
  - Check: `uv run pytest tests/test_equipment.py -k reconstructable`
- **S-0082/I-1**: An arm replay merges nothing and records no result for a task whose landing names no commit, whichever arm it is and however the arm was launched
  - Paths: `src/torve/application/evals.py`
  - Check: `uv run pytest tests/test_evals.py`

<!-- /torve:managed -->
