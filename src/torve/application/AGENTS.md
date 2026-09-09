<!-- torve:managed src/torve/application — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/application/`

### D-53.5 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Every row carries a content fingerprint; a mismatch between a contract's copied row and the row as it stands is reported as *suspect* by `decisions show` and `rfc health`, never as a conviction

- Paths: `src/torve/domain/spec.py` `src/torve/application/decisions.py`
- Consequence: Copy-at-mint becomes checkable; D-31.3's no-retroactive rule is preserved by making the mismatch a reading, not a red

### D-53.6 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

Coverage is a per-path fact with three values — governed, ungoverned, retired — computed over decision paths and accepted documents' phase scopes together; ungoverned is never a check failure

- Paths: `src/torve/application/decisions.py`
- Consequence: The ratchet's frontier is visible and moves; a directory a phase is changing never reads as ungoverned; blind spots are the default state of a repository, not an error
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.7 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

A row whose every glob matches nothing on an accepted, implemented document is path rot: reported by `rfc check`, retired by `amend --retire --reason path-rot` (or `check --fix-rot`), recorded as `decision.retired` on import; never automatic on load, never a red on the document

- Paths: `src/torve/application/decisions.py` `src/torve/config/spec_emit.py`
- Consequence: 27 rows today, 8 `LOCKED`, stop rendering as governance while governing nothing

### D-53.9 — `LOCKED` (RFC 0053 — The item model and the rebuilt corpus)

The importer records every archived document as a source and every archived row as retired; `torve why` and `show` resolve archived identifiers and say they are archived

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: 705 log entries and 139 amendments keep their targets
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-53.13 — `ASSUMED` (RFC 0053 — The item model and the rebuilt corpus)

Each reader switches from `rfc_parse` to the model beside its old path with a parity assertion, one at a time, in phase 2; `rfc_parse.py` is deleted in phase 5 only after the archive lands

- Paths: `src/torve/application/decisions.py` `src/torve/cli/spec.py`
- Consequence: Two owners of the format exist for one bounded window, and the parity test is what bounds it

### D-54.1 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

`InheritedDecision` gains `consequence` and `check`, copied at mint from the model, carried by `decision.recorded`, rendered after each row in the prompt; the fingerprinted set stays text, grade and paths

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: A gate reads the contract and never the corpus (D-7.18 stands); the reason a row exists reaches the executor for the first time
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.5 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

A `LOCKED` row with no check is reported *soft* by `rfc health`, counted beside the hard ones, never regraded by the engine

- Paths: `src/torve/application/specquality.py`
- Consequence: The author sees which locks are prose; RFC 0022's non-goal on automatic edits stands

### D-54.6 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

`torve spec project` renders a managed section into the `AGENTS.md` of every directory a standing row's paths or an accepted phase's scope names — rows with grade, text, consequence, check and state; invariants; contended paths — and a root index of governed directories; text outside the markers is never touched

- Paths: `src/torve/application/colocation.py` `src/torve/cli/spec.py`
- Consequence: Every harness and every person reads the rules where the code is, with no prompt change; a directory that stops being governed loses its section
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.8 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

`AGENTS.md` sections carry rows, invariants, checks, tests and warnings only; no generated overview, no prose beyond the row's own text and consequence

- Paths: `src/torve/application/colocation.py`
- Consequence: The one controlled study says overviews cost and do not help; rules and facts do

### D-54.10 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

The context pack under `.torve/context/` is computed host-side from the record and the tree, with no model, before the prompt is written; deterministic for a base sha and record state; gitignored; never in an image; byte-identical for a shadow run; own task only

- Paths: `src/torve/application/contextpack.py` `src/torve/application/session.py`
- Consequence: RFC 0035's rule for derived state applied to knowledge; D-17.4, D-17.7 and D-31 hold; a replay stays comparable
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.11 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

The pack's files are `index.md`, `decisions.json`, `gates.json`, `tests.json` (from `coverage.xml`), `attempts.json`, `contended.json`, `schema/`; each lands one at a time behind a config-eval on the compliance-red population and is deleted if attempts to green and dollars per landing do not fall by more than its tokens cost

- Paths: `src/torve/application/contextpack.py`
- Consequence: Nothing that adds tokens to every attempt ships without a number; the study's +20 % is the prior to beat

### D-54.12 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

A red pass writes its gate report into the next attempt's `attempts.json` — output tails with byte-loss markers, governing decision ids for `decisions-reported` and `scope`, failed test ids from JUnit — and the retry ladder still selects the tier from outcome and axis alone (D-34.5)

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py`
- Consequence: What tier runs next is unchanged; what it is told is the change; RFC 0043's loop becomes the general case

### D-54.13 — `LOCKED` (RFC 0054 — Decisions as gates and the projections beside the code)

The reviewer reads the pack minus any model-authored entry, plus `touched.json` (rows the diff intersects, coverage of changed lines, prior findings on the task); nothing the author wrote enters (D-5.3 unchanged)

- Paths: `src/torve/application/review.py`
- Consequence: Engine facts about the diff are not the author's voice; the reviewer stops being the role with the least context
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-54.15 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

Findings and drafts are validated against the pydantic models' JSON Schema shipped in `schema/`; a validation failure is a lint refusal naming the field, never an `unparseable` row

- Paths: `src/torve/application/review.py` `src/torve/application/intake.py`
- Consequence: The output contract is a contract the agent can read; two of three unparseable rows on record were shape failures

### D-54.16 — `ASSUMED` (RFC 0054 — Decisions as gates and the projections beside the code)

The drafter's 400-path listing is replaced by the pack's `index.md` and `torve spec paths` over the candidate scope

- Paths: `src/torve/application/intake.py`
- Consequence: A split is proposed against the rows it would cross, not a list of filenames

### D-55.1 — `LOCKED` (RFC 0055 — Standing decisions)

Models never decide what work exists or whether it is finished; the runner executes state transitions from facts — exit codes, gate outcomes, approvals — and an agent reports observations that never cause a transition

- Paths: `src/torve/application/runner.py` `src/torve/application/manager.py` `src/torve/domain/states.py`
- Consequence: A reviewer's severity is data whose consequence configuration sets; the planner invokes no model at all
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.2 — `LOCKED` (RFC 0055 — Standing decisions)

Gates execute outside the agent session against the working tree it leaves behind; an agent cannot report a gate outcome

- Paths: `src/torve/gates/**` `src/torve/application/runner.py`
- Consequence: The battery is the acceptance; a green the agent claims is not one
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.3 — `LOCKED` (RFC 0055 — Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.5 — `LOCKED` (RFC 0055 — Standing decisions)

Agents do not communicate; executor memory is off by default, per slot when on, and never mounted in a shadow run

- Paths: `src/torve/application/shadow.py` `src/torve/config/runconfig.py`
- Consequence: Shared memory is a communication channel with a euphemism; a remembered replay is an incomparable number
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.7 — `LOCKED` (RFC 0055 — Standing decisions)

A task contract is immutable once minted: a changed contract is a new task, a re-mint is a version and never a transition, and a task in flight is never re-minted

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py`
- Consequence: An executor's contract cannot drift out from under it; `torve plan` refuses to re-mint over minted phases and leaves the decision to a person
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.8 — `ASSUMED` (RFC 0055 — Standing decisions)

Contracts are derived artefacts committed under `.torve/tasks/`; the task directory is the importer and the record is what dispatch reads

- Paths: `.torve/tasks/**` `src/torve/application/taskstore.py`
- Consequence: Reproducible from a sha, refusable in a diff; the file is authored, the record is read

### D-55.9 — `LOCKED` (RFC 0055 — Standing decisions)

A contract copies its rows' grade, text and paths at mint time; a grade is never resolved at read time

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py`
- Consequence: A regrade never rewrites the judgement of a task that ran under the old grade
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.11 — `ASSUMED` (RFC 0055 — Standing decisions)

A divergence entry is written only through `torve log divergence`, validated by the gate's own checks at intake, with located evidence; `drift_count` is derived, never declared

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `.torve/tasks/**`
- Consequence: A hand-edited log is refused by the same code that gates it

### D-55.16 — `ASSUMED` (RFC 0055 — Standing decisions)

Every gate runs on every pass, cheapest first, never short-circuiting; the retry rung reads outcome and axis only, never a trace or a gate's output

- Paths: `src/torve/gates/runner.py` `src/torve/application/session.py`
- Consequence: A replay of the rows reproduces the tier choice exactly; the severity ladder stays meaningful

### D-55.17 — `LOCKED` (RFC 0055 — Standing decisions)

Scope is a contract: `allow` and `deny` globs make a touch outside them a red gate, and tasks whose allow sets intersect are never dispatched in parallel

- Paths: `src/torve/gates/scope.py` `src/torve/application/manager.py`
- Consequence: Thirty-two convictions and their corrections; a collision is refused at dispatch, never discovered at merge
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.20 — `ASSUMED` (RFC 0055 — Standing decisions)

Acceptance is a list of shell commands, exit 0 meaning satisfied, run in the battery sandbox over a worktree without a repository; a command that needs git cannot be an acceptance command

- Paths: `src/torve/gates/acceptance.py` `src/torve/application/intake.py`
- Consequence: The lint refuses such a command at mint rather than after three attempts

### D-55.21 — `ASSUMED` (RFC 0055 — Standing decisions)

A bypass is a `Torve-Bypass: <gate>: <reason>` commit trailer, counted per gate in telemetry

- Paths: `src/torve/gates/runner.py` `src/torve/application/telemetry.py`
- Consequence: Every waiver is a recorded fact with a name on it

### D-55.29 — `ASSUMED` (RFC 0055 — Standing decisions)

Application code reaches stores only through injected factories; substrate wiring lives in the facade and `config_hash` beside the telemetry it stamps

- Paths: `src/torve/application/**`
- Consequence: Anything derived below a port is invisible to simulation

### D-55.31 — `ASSUMED` (RFC 0055 — Standing decisions)

Providers a repository's contents may reach are enforced at dispatch, before a sandbox exists; an empty default denies every real provider

- Paths: `src/torve/config/runconfig.py` `src/torve/application/dispatch.py`
- Consequence: Silence is not a policy; it is a closed door

### D-55.32 — `ASSUMED` (RFC 0055 — Standing decisions)

A tier resolves through profiles in the operator's own configuration directory, never the repository under work; a missing profile or an unknown skill refuses rather than falls back

- Paths: `src/torve/config/runconfig.py` `src/torve/application/skills.py`
- Consequence: Resolution is fail-closed throughout

### D-55.35 — `ASSUMED` (RFC 0055 — Standing decisions)

Sandbox images are digest-pinned inputs to a run; an image the eval ledger has not measured refuses dispatch unless the rebuild hatch is set, and the dispatch records the unmeasured digest

- Paths: `.torve/sandbox/**` `src/torve/application/dispatch.py` `.torve/evals.jsonl`
- Consequence: Numbers from different images are never silently compared

### D-55.36 — `ASSUMED` (RFC 0055 — Standing decisions)

Skills ship as package data under `skills/` and are materialised role-scoped into the sandbox; a vendored skill lives under `.torve/skills-vendor/`; a name in both refuses, a name in neither is a configuration error

- Paths: `skills/**` `.torve/skills-vendor/**` `src/torve/application/skills.py`
- Consequence: What an agent knows is versioned and named, never ambient

### D-55.37 — `LOCKED` (RFC 0055 — Standing decisions)

A fact is an event of a closed kind vocabulary with an authority table; an agent may write only `divergence.recorded` and `message.sent`; no update command exists on the record

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py`
- Consequence: An audit trail that can be edited cannot be trusted; the authority table refuses before any store sees the write
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.38 — `ASSUMED` (RFC 0055 — Standing decisions)

The manager holds nothing between passes and rebuilds its view from the record; a worker holds a lease and nothing else

- Paths: `src/torve/application/manager.py` `src/torve/application/worker.py` `src/torve/application/residency.py`
- Consequence: A restart is a re-read, never a recovery procedure

### D-55.39 — `ASSUMED` (RFC 0055 — Standing decisions)

Retirement of a decision is recorded, never inferred from a row that stopped appearing; a second `decision.recorded` on a subject is a version, `supersedes` names a different decision

- Paths: `src/torve/application/decisions.py`
- Consequence: Absence cannot tell a retirement from a broken table

### D-55.40 — `ASSUMED` (RFC 0055 — Standing decisions)

A landing is recorded from the `Torve-Task` trailer on the commit; git log is the surviving record and the importer mints landed history from it

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/runner.py`
- Consequence: A session-authored landing enters the record the same way an agent's does

### D-55.41 — `ASSUMED` (RFC 0055 — Standing decisions)

Every attempt appends one telemetry row stamped with the `config_hash` of the regime it ran under; absent counts stay absent, never zero; the verdict is engine-derived and never model output

- Paths: `src/torve/application/telemetry.py` `.torve/telemetry.jsonl` `.torve/regimes/**`
- Consequence: Numbers from different regimes are never silently compared; a mute row is a mute row

### D-55.42 — `ASSUMED` (RFC 0055 — Standing decisions)

Every read of the log pages; no reader folds a truncated prefix as if it were the whole

- Paths: `src/torve/application/eventlog.py`
- Consequence: The board once decided dispatch over a third of its tasks missing

### D-55.43 — `ASSUMED` (RFC 0055 — Standing decisions)

Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py`
- Consequence: A substrate surface change fails at the pin bump, not in production

### D-55.44 — `ASSUMED` (RFC 0055 — Standing decisions)

`torve plan` is deterministic and invokes no model; it mints from exactly one accepted, committed document, copies that document's whole table, and refuses to re-mint over minted phases

- Paths: `src/torve/application/planner.py` `src/torve/cli/plan.py`
- Consequence: What to do with existing tasks is a human decision

### D-55.45 — `ASSUMED` (RFC 0055 — Standing decisions)

Nothing inherits from a document that is not `accepted`; a contract without a document inherits every accepted row whose declared paths intersect its `scope.allow`

- Paths: `src/torve/application/planner.py` `src/torve/application/intake.py`
- Consequence: The document-less lane is governed; a draft's rows never stand

### D-55.46 — `ASSUMED` (RFC 0055 — Standing decisions)

A draft passes the deterministic lint before adoption — non-empty intent and acceptance, in-tree globs, a module's test file allowed beside it, no git in acceptance, pairwise-disjoint scopes — and adoption mints identifiers under the engine lock

- Paths: `src/torve/application/intake.py` `src/torve/application/enginelock.py`
- Consequence: A defect the lint names at drafting costs nothing; the same defect at attempt three cost 2410 seconds once

### D-55.47 — `ASSUMED` (RFC 0055 — Standing decisions)

A contract the size estimate calls `too_large` routes to a decomposition run rather than an attempt; children stay inside the parent's scope, decomposition depth is at most two, and the parent survives as the integration task

- Paths: `src/torve/application/sizing.py` `src/torve/application/intake.py`
- Consequence: Oversized work is split at drafting time, where the tree is in view

### D-55.48 — `ASSUMED` (RFC 0055 — Standing decisions)

The poison ceiling is checked before dispatch and reaching it escalates, never retries; a red gate pass retries under the ladder's next rung

- Paths: `src/torve/application/dispatch.py` `src/torve/application/session.py` `.torve/config.yaml`
- Consequence: An attempt is spent on a chance, never on a certainty

### D-55.49 — `LOCKED` (RFC 0055 — Standing decisions)

Review is a second run role in its own sandbox; the reviewer never receives the author's trace; findings are one JSON document with located evidence, a blocker escalates the target and the rest go to the operator's ledger

- Paths: `src/torve/application/review.py`
- Consequence: Reviewer independence is from the author's reasoning; engine facts about the diff are not the author's
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.50 — `LOCKED` (RFC 0055 — Standing decisions)

The engine never resolves a merge conflict and never lands without the configured approval; landings serialize through one lane per base

- Paths: `src/torve/application/lane.py` `src/torve/application/residency.py`
- Consequence: A conflict against an unmoved base is the same conflict, and re-queueing it is a loop
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.51 — `ASSUMED` (RFC 0055 — Standing decisions)

A landing commit carries `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` and `Torve-Decisions` trailers; provenance is written by the runner, never by the agent

- Paths: `src/torve/application/runner.py`
- Consequence: What landed, under which regime, inheriting which rows, is readable from git alone

### D-55.56 — `ASSUMED` (RFC 0055 — Standing decisions)

Cadence belongs to the manager's pass; there is no resident scanning loop, and the standing legs — maintenance, relay, lane — run inside the pass under their switches

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Consequence: One process to run and supervise; a pause stops what advances the repository

### D-55.57 — `ASSUMED` (RFC 0055 — Standing decisions)

Standing maintenance is a committed contract under `.torve/standing/` with a trigger kind, cooldown, `max_open` and `strike_limit`, evaluated deterministically

- Paths: `.torve/standing/**` `src/torve/application/standing.py`
- Consequence: A job that fires is a fact with a bound, never a daemon's habit

### D-55.58 — `ASSUMED` (RFC 0055 — Standing decisions)

The review corpus under `.torve/review-corpus/` grows from post-merge escapes, and reviewer regressions are measured by replaying it

- Paths: `.torve/review-corpus/**` `src/torve/application/review.py`
- Consequence: A reviewer's calibration is a measured quantity

### D-55.60 — `ASSUMED` (RFC 0055 — Standing decisions)

Every attempt runs on a worktree seeded with the base sha pinned host-side, the role's skills materialised, and the divergence log seeded; the session trace is captured to `.torve/traces/` and its content enters no prompt and drives no control flow

- Paths: `src/torve/application/session.py` `src/torve/adapters/agent/harness.py` `.torve/traces/**`
- Consequence: What an agent saw is reconstructible; what it reasoned is never an input to another agent

### D-56.2 — `LOCKED` (RFC 0056 — Structure for everything)

`DecisionDetail` folds into `Decision`; the fenced kinds are gone; a row carries `rationale`, `cites`, `check`, `check_state`, `check_twin`, `superseded_by` and its `fingerprint` on itself

- Paths: `src/torve/domain/spec.py` `src/torve/application/planner.py`
- Consequence: `inherit_decisions` reads the row; the frontmatter fingerprint map is gone
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.8 — `ASSUMED` (RFC 0056 — Structure for everything)

`SKILL.md`, `AGENTS.md`, the colocated sections and the pack are unchanged: projections rendered from the model, read by harnesses and people

- Paths: `skills/**` `src/torve/application/colocation.py`
- Consequence: Nothing a harness reads changes shape

### D-56.9 — `LOCKED` (RFC 0056 — Structure for everything)

With a store configured, `plan` mints into the record and writes no file; dispatch projects `.torve/tasks/<id>/contract.yaml` into the worktree, gitignored; the log written there is imported after the attempt; without a store the files are the record as today

- Paths: `src/torve/application/planner.py` `src/torve/application/session.py` `src/torve/config/layout.py`
- Consequence: The board is the only place a task is; the file exists for the attempt that reads it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-56.11 — `ASSUMED` (RFC 0056 — Structure for everything)

`torve migrate telemetry` reads the file once into the record; every reader that opens `.torve/telemetry.jsonl` goes through one function that reads the record when a store is configured and the file when not

- Paths: `src/torve/application/telemetry.py` `src/torve/application/contextpack.py` `src/torve/application/intake.py` `src/torve/application/specquality.py`
- Consequence: The file is a carrier and an append target, never what a reader with a record opens

### D-56.13 — `OPEN` (RFC 0056 — Structure for everything)

Whether phase 4 gives `traces/` and `regimes/` a retention window or leaves them to RFC 0039

- Paths: `src/torve/application/reaper.py`
- Consequence: Decided by whoever executes phase 4, logged

### D-57.7 — `LOCKED` (RFC 0057 — The specification is a directory)

`execution.yaml` holds landings — task, phase, attempt, time, agent, the commit when the lander knows it, and the log's entries typed as `LogEntry` — appended by one function the runner calls before the candidate commit, whose trailers name the task so the field stays empty there, and `torve log land --commit SHA` exposes for a landing made by hand; a contract naming no document lands nowhere and says so

- Paths: `src/torve/application/divergence.py` `src/torve/application/runner.py` `src/torve/cli/log.py`
- Consequence: Every clone carries what execution found, beside the rows it informs; the task directory carries nothing git keeps
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-57.8 — `ASSUMED` (RFC 0057 — The specification is a directory)

`torve decisions import` reads every execution file, live and archived, and records the divergence and landing events the record lacks, idempotent by task, attempt, decision and time; the attempt-time `ingest` is unchanged

- Paths: `src/torve/application/decisions.py` `src/torve/cli/decisions.py`
- Consequence: A clone without a store rebuilds the same record from the tree

### D-57.12 — `ASSUMED` (RFC 0057 — The specification is a directory)

The colocated sections, the pack and the skills change in no shape but the word and the gate name; they render the same model

- Paths: `src/torve/application/colocation.py` `src/torve/application/contextpack.py`
- Consequence: Nothing a harness reads changes shape

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

### D-58.9 — `ASSUMED` (RFC 0058 — One grammar and the anatomy)

The `decision:<id>` gate names, the pack's decisions file, the log entry's `decision` and the record's subjects carry the global form; the record re-imports once after the conversion, retiring every old subject with the reason naming its replacement

- Paths: `src/torve/gates/runner.py` `src/torve/gates/decisions_reported.py` `src/torve/application/contextpack.py` `src/torve/application/decisions.py`
- Consequence: The record's history keeps the old ids as retired rows and the new ones as what stands

### D-58.10 — `ASSUMED` (RFC 0058 — One grammar and the anatomy)

The skill and its template, the schemas `torve init` writes, the projections beside the code and the operating page follow the grammar and the anatomy in the same phase that changes them

- Paths: `skills/**` `src/torve/application/colocation.py` `src/torve/cli/init.py` `pages/docs/operating.md`
- Consequence: Nothing a harness or a person reads names an identifier the check refuses

<!-- /torve:managed -->
