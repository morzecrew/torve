The suite runs in parallel (`-n auto`, S-0071/D-1): a test that reaches a shared daemon must name the sandboxes it asserts on rather than filter a listing on a label literal another test also uses — on a parallel run that filter matches every concurrent test's containers too.

<!-- torve:managed tests — rendered from the corpus; do not edit by hand -->

## Decisions governing `tests/`

### S-0055/D-19 — `ASSUMED` (Standing decisions)

An existing test is edited only under the contract's licence; adding a test is an addition, editing one needs scope

- Paths: `src/torve/gates/no_test_tampering.py` `tests/**`
- Consequence: A green earned by weakening the suite is a red

### S-0063/D-7 — `LOCKED` (The image knows how to equip itself)

One base image carries `git`, `uv` and the engine's CLI; every definition inherits it, and `_torve-cli.dockerfile` with the test pinning five copies against it retires.

- Paths: `sandboxes/**` `tests/test_sandbox_defs.py`
- Consequence: the layer five images duplicate is built once and inherited, and the test that stood in for inheritance goes with it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-13 — `LOCKED` (The image knows how to equip itself)

Building a sandbox image is an operator's act and never an attempt's: the battery checks a definition's shape and never its build, and `TORVE_IMAGE_TESTS` runs the probes that build one.

- Paths: `tests/test_sandbox_images.py` `tests/test_sandbox_defs.py`
- Consequence: the acceptance battery stays under its clock, and a definition that would not build is an operator's finding rather than a timed-out gate
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0070/D-3 — `ASSUMED` (What is committed may not depend on what is not)

A check that walks artefacts the repository may not be carrying judges what is there or skips naming why, and never asserts that they exist

- Paths: `tests/test_gates.py`
- Consequence: a verdict stops depending on which machine ran it, and a suite that has nothing to judge says so instead of passing

### S-0070/D-4 — `ASSUMED` (What is committed may not depend on what is not)

The rule is proved by rendering twice — once with the record present, once without — and comparing the bytes

- Paths: `tests/test_colocation.py`
- Consequence: the violation becomes visible on the machine of the person committing it, which is the only machine where it is currently invisible

### S-0070/D-6 — `ASSUMED` (What is committed may not depend on what is not)

An acceptance verdict names the suite it judged: a battery that ran fewer tests than the tree contains reports how many it skipped and why, and `pass` never stands for two different suites

- Paths: `src/torve/gates/acceptance.py` `tests/test_gates.py`
- Consequence: a green battery in a sandbox and a green battery on a laptop stop being the same sentence for two different amounts of evidence

### S-0073/D-1 — `LOCKED` (The working rules live once, and say what an attempt costs)

`build_prompt` names the working-rules skill and does not restate it; the test over it asserts that property rather than the words, so a bullet added back fails instead of passing quietly beside the skill

- Paths: `src/torve/adapters/agent/harness.py` `tests/test_tiering.py`
- Consequence: one copy of the text that governs behaviour, and a test that cannot be satisfied by two
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0074/D-2 — `ASSUMED` (What the engine is worth against a bare harness)

The bare arm's prompt carries the task's intent and nothing else — no inherited rows, no context pack, no working rules — as a fourth `build_prompt` mode beside revision, continuation and repair

- Paths: `src/torve/adapters/agent/harness.py` `tests/test_tiering.py`
- Consequence: what an arm removed is a property of the prompt a test can assert, rather than something read back out of a transcript

### S-0075/D-2 — `ASSUMED` (What an attempt costs, measured per changed line)

The burn profile classifies an attempt's tool calls — pack reads, orientation, in-scope reads, edits, test runs, lint runs, bookkeeping, other — and records calls before the first edit, reruns, calls per message, result bytes by class, compaction events and the latency medians

- Paths: `src/torve/application/telemetry.py` `tests/test_attempt_record.py`
- Consequence: every mitigation has a class that judges it, so a change can be shown to have moved what it claimed rather than argued to have

### S-0085/D-2 — `ASSUMED` (A document builds on another document's tree)

`torve plan` turns `after` into contract `depends_on`: every task of the document's phases with no in-document predecessor depends on every task minted from each named document; a named document with no minted tasks refuses the plan by name, one that is not accepted refuses as `depends_on` does, and one whose implementation is complete adds no edge

- Paths: `src/torve/application/planner.py` `tests/test_plan.py`
- Consequence: nothing downstream learns a new word — the board, the worker, the lane and every projection read the contract they already read, and a contract edited by hand to the same edge behaves the same

### S-0085/D-3 — `ASSUMED` (A document builds on another document's tree)

A document's landing on the base is its tasks' landing: `lane_landings` reads `lane_document_landed` beside `lane_landed` and stamps each task the branch carried with the merge commit, newest winning

- Paths: `src/torve/application/projections.py` `tests/test_projections.py`
- Consequence: a squash-merged document's tasks are on the base by the commit the base holds, so ancestry answers the same question for a squash as for a fast-forward; `shipped_landings` and the tree's landing files are unchanged

### S-0086/D-1 — `ASSUMED` (The review tier's word reaches the work)

An unreadable review verdict — unparseable or refused — is asked once more of the same reviewer in the same staged copy before it is escalated; a second unreadable answer escalates as S-0043/D-4 says, with both trace refs on the record, and the record names a review that needed the second ask

- Paths: `src/torve/application/review.py` `tests/test_review_run.py`
- Consequence: a reviewer's stray subprocess costs one more review and not a phase; the ledger can count how often a harness fails to produce a findings document

### S-0086/D-2 — `ASSUMED` (The review tier's word reaches the work)

Before any escalation from the review stage, and before the escalation a halted divergence entry raises, the attempt's tree is committed on the task's branch under the checkpoint trailer, kin to the budget checkpoint and the convicted-tree commit — no landing is written, and a commit that fails leaves the escalation as it was

- Paths: `src/torve/application/runner.py` `tests/test_runner.py`
- Consequence: nothing that passed the gates is lost to an escalation the attempt did not cause; the operator's hand checkpoint of 2026-09-19 is the engine's own act

### S-0086/D-3 — `ASSUMED` (The review tier's word reaches the work)

The review-thread leg reads a second source: the stream's `task_gated` review records for tasks an open document branch carries, whose findings are not yet answered; each finding becomes the leg's shape — anchor from its evidence's leading citation, its claim and evidence as the one thread under it, the review task as author — and is grouped with the forge's threads by the same anchor rule

- Paths: `src/torve/application/reviewleg.py` `src/torve/application/threads.py` `tests/test_reviewleg.py`
- Consequence: a bot and the tier flagging one line are one finding and one round; a non-blocking finding is worked on the branch before the pull request is ready instead of read off the record by a person

### S-0086/D-6 — `ASSUMED` (The review tier's word reaches the work)

The leg's section gains `sources`, a list of `forge` and `record` defaulting to `[forge]`; `record` is refused at load under any landing but `pull_request` with `unit: document`; the second ask has no term

- Paths: `src/torve/config/runconfig.py` `tests/test_runconfig.py`
- Consequence: a configuration that turned the leg on before this document changes nothing; the second ask is what an unreadable verdict costs wherever the tier runs

### S-0087/D-2 — `ASSUMED` (A document names its change, and its pull request wears the name)

With `change` present the composer titles the document's pull request `<gitmoji> <type>(<scope>)[!]: <title lowered>`, appends ` · n/m phases` only while phases are still to come, and cuts an overflowing title at the description; without the field the title is today's, byte for byte

- Paths: `src/torve/application/forge.py` `tests/test_forge.py`
- Consequence: the subject the squash merge takes at the last landing is one line in the repository's own format with nothing to edit, and no document accepted before this changes title

### S-0087/D-3 — `ASSUMED` (A document names its change, and its pull request wears the name)

Under the task unit `compose_pr` wears the task's document's `change` with the phase's title as the description; a task naming no document, or a document without the field, is titled as today

- Paths: `src/torve/application/forge.py` `tests/test_forge.py`
- Consequence: one rule for both units; a repository landing by task gets typed history too

### S-0088/D-1 — `ASSUMED` (A minted contract follows its amended document)

`torve plan <document> --refresh` admits and derives the document as `plan` does and rewrites each already-minted phase whose contract differs from the derivation in intent, scope, acceptance, decisions, character or tier variant — keeping its id, edges and minted-by — and mints no phase

- Paths: `src/torve/application/planner.py` `src/torve/cli/plan.py` `tests/test_plan.py`
- Consequence: an amendment reaches its phases through the one code path that knows how a document becomes a contract, and the hand edit of a contract has no reason left

### S-0088/D-2 — `ASSUMED` (A minted contract follows its amended document)

A task that is running, landed, or carried by a document branch is left alone and named with the reason; an escalated or reaped task with no landing is refreshed; no worktree is touched

- Paths: `src/torve/application/planner.py` `tests/test_plan.py`
- Consequence: an attempt in flight reads the contract it was dispatched under, and a landed phase's terms are the ones its landing was judged by

### S-0089/D-1 — `ASSUMED` (A night serves what a worker can finish)

Dispatch does not consult the size estimate: a queued implement or revert contract whose dependencies have landed and whose scope overlaps nothing in flight is dispatchable whatever its size, and `torve run` dispatches it with the verdict's reasons printed as a note; `--oversize` is accepted and ignored until the next minor release removes it

- Paths: `src/torve/application/manager.py` `src/torve/cli/run.py` `tests/test_manager.py` `tests/test_cli.py`
- Consequence: a served night takes the phases it used to leave for hand runs, so their landings reach the record; decomposition happens when an operator runs `torve decompose`

### S-0089/D-2 — `ASSUMED` (A night serves what a worker can finish)

A seat whose failure no retry can change escalates at once as `seat_refused` with the seat's own words — the harness could not be executed (exit 126 or 127), or its envelope reports an API error with a 4xx status other than 429 and zero tokens in, cached and out; no attempt is counted, nothing is convicted or checkpointed, and the reason maps to exit code 4

- Paths: `src/torve/application/ports.py` `src/torve/adapters/agent/harness.py` `src/torve/application/runner.py` `src/torve/domain/states.py` `tests/test_run_loop.py` `tests/test_agents.py` `tests/test_domain.py`
- Consequence: a broken image, an over-long prompt or an unsupported model is named as what it is in one dispatch, and the poison ceiling counts only attempts a model made

### S-0089/D-3 — `ASSUMED` (A night serves what a worker can finish)

A night opened under a worker's name first releases every in-flight claim held under that same name, recording why; a night that still finds nothing dispatchable names each held claim with its holder, age and lease expiry, and counts the queued tasks waiting on dependencies and on overlap

- Paths: `src/torve/application/residency.py` `tests/test_residency.py`
- Consequence: a worker restarted after a crash opens its night at once, and a refusal says what the operator is waiting on and until when

### S-0089/D-4 — `ASSUMED` (A night serves what a worker can finish)

An acceptance command that exits zero with a test summary in which no test ran — every test skipped or deselected — fails, with "suite: no test ran" at the top of its verdict; a command that prints no test summary, or ran at least one test, is judged as before

- Paths: `src/torve/gates/acceptance.py` `tests/test_gates.py`
- Consequence: a phase cannot land green on a lane that never executed; one whose tests need what the sandbox lacks has to say how they run there

### S-0090/D-1 — `ASSUMED` (Review rounds converge, and judged work continues from its tree)

The review-thread leg's record source reads no finding from a review whose target is a round the leg minted; such a review still blocks its round's landing on a blocker, and what it finds below that grade stays on the stream

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a document's rounds are bounded by the findings on its phases, and a round's check opens no further round

### S-0090/D-2 — `ASSUMED` (Review rounds converge, and judged work continues from its tree)

The next dispatch of a task whose last attempt escalated `blocker_finding`, `locked_conflict` or `underspecified` from a halt, and which has not landed, continues from the tree that attempt checkpointed, whether it was requeued on the board or by a person

- Paths: `src/torve/application/runner.py` `tests/test_runner.py` `tests/test_run_loop.py`
- Consequence: a review that asks for one file's change costs that change, and a halt answered by an amendment resumes where it stopped

### S-0090/D-3 — `ASSUMED` (Review rounds converge, and judged work continues from its tree)

A continued attempt reads the task's contract as it stands at that dispatch, as every attempt does: after `plan --refresh` it is the refreshed contract. The tree carries over from the checkpoint; the terms do not

- Paths: `src/torve/application/runner.py` `tests/test_runner.py`
- Consequence: an amendment that answered a halt reaches the attempt that resumes from it, and the resumed tree is judged by the terms that now stand

### S-0091/D-1 — `ASSUMED` (A document branch is the remote's, and a hand landing counts)

The lane fetches with prune before it lands onto a document branch and works from the remote's copy: a branch the remote no longer has, or whose pull request merged, is moved aside under `refs/torve/documents/` and cut again from the remote's `main`; a branch the remote has sets the local ref to the remote tip; the task cut and the dependency check read the same remote ref

- Paths: `src/torve/application/ports.py` `src/torve/adapters/vcs/git.py` `src/torve/application/lane.py` `src/torve/application/runner.py` `src/torve/cli/manager.py` `tests/test_lane.py` `tests/test_runner.py` `tests/test_manager.py`
- Consequence: a merged or deleted document branch is never landed onto again, a merged document's next pull request carries only what `main` lacks, and the old commits stay reachable

### S-0091/D-2 — `ASSUMED` (A document branch is the remote's, and a hand landing counts)

Commits on the remote document branch that the lane did not make are the branch having moved: the candidate is rebased onto them and the battery re-run, a conflict escalates for a person, and the branch is published with a lease on the exact commit the lane fetched

- Paths: `src/torve/application/lane.py` `src/torve/adapters/vcs/git.py` `tests/test_lane.py`
- Consequence: a hand commit on an open document branch survives the next landing or stops it, and a push never drops a commit it did not see

### S-0091/D-3 — `ASSUMED` (A document branch is the remote's, and a hand landing counts)

A document's pull request counts a phase as landed when the branch tip's tree holds its landing file, as well as when the lane recorded landing it; `manager resolve --resolution landed` refuses a sha whose tree holds no landing file for the task and names `torve log land`

- Paths: `src/torve/cli/merge.py` `src/torve/cli/manager.py` `tests/test_manager.py` `tests/test_forge.py`
- Consequence: a phase finished by hand reads as landed in the pull request's title and body, and a hand resolution cannot claim a landing the tree does not carry

### S-0092/D-1 — `ASSUMED` (A review round is scoped by its document)

A round's scope is the phasing scope of the phases its target task landed — for a thread on the pull request, which has no target task, the phases whose scope covers the file it anchors — read from the document branch tip, plus the round's own log directory. A round that halts on a `spec-gap` is re-dispatched once with the union of the whole document's phasing; a second such halt escalates `underspecified`. This amends S-0084/D-7's "the files the threads anchor"

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a round stays small enough to run beside the phases still in flight, and a fix that needs a file another phase owns is made on the retry, not by a person

### S-0092/D-2 — `ASSUMED` (A review round is scoped by its document)

Whether a finding lies inside the document's phasing is judged against the phasing on the document branch tip, not the checkout's

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a phase widened on the branch by amendment reaches the leg on its next pass

### S-0092/D-3 — `ASSUMED` (A review round is scoped by its document)

A halted divergence entry citing a LOCKED row escalates `locked_conflict`; every other halt, whatever its class, escalates `underspecified`

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py` `tests/test_runner.py`
- Consequence: the escalation tells the operator whether to amend a phase or ask the owner about a locked row

### S-0092/D-4 — `ASSUMED` (A review round is scoped by its document)

A round requeued with `manager resolve --resolution requeued` is re-scoped from its document's phasing on the branch at the requeue

- Paths: `src/torve/cli/manager.py` `src/torve/application/reviewleg.py` `tests/test_manager.py`
- Consequence: an operator's widening of the phase reaches the round's next attempt

### S-0092/D-5 — `ASSUMED` (A review round is scoped by its document)

The collapsed `<details>` blocks of a forge thread are removed before the injection check and before the fence; they never reach the attempt

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: CodeRabbit's findings become rounds, and its analysis scripts are neither judged as requests nor shown to the attempt

### S-0093/D-1 — `ASSUMED` (A document runs the whole suite before it is ready)

A landing that leaves a document complete runs the fallback battery over the document branch tip before the pull request is published; the pull request leaves draft only on a green battery

- Paths: `src/torve/application/lane.py` `tests/test_lane.py`
- Consequence: no document pull request is ready on a tree the whole suite has not passed

### S-0093/D-2 — `ASSUMED` (A document runs the whole suite before it is ready)

A red battery at completion writes `lane_document_gates_red` with its summary and publishes the pull request as a draft

- Paths: `src/torve/application/lane.py` `tests/test_lane.py`
- Consequence: a person reading the pull request sees why it is still a draft

### S-0093/D-3 — `ASSUMED` (A document runs the whole suite before it is ready)

Where a review leg reads recorded findings, a red battery at completion is recorded as a review finding on the last landed task — severity major, the failing gates and tests as the claim, the battery's command as the evidence — so the review leg mints a round for it; elsewhere a red battery at completion escalates the completing task `blocker_finding` at once

- Paths: `src/torve/application/lane.py` `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: the engine fixes what its phases broke outside their scope, and the round's landing reruns the battery

### S-0093/D-4 — `ASSUMED` (A document runs the whole suite before it is ready)

Where a review leg runs, a completion earns one round: the battery reruns when that round lands a change or answers its finding without one, and a second red battery escalates on the last landed task for a person

- Paths: `src/torve/application/lane.py` `src/torve/application/reviewleg.py` `tests/test_lane.py`
- Consequence: a flaky test clears itself on the rerun, a break the round can fix is fixed, and one it cannot reaches a person instead of looping

### S-0094/D-1 — `ASSUMED` (The escalation loop runs without scripts)

`manager resolve --resolution requeued` refreshes a phase task's contract through `refresh_document` and writes it, reading the task's document as the remote's document branch holds it after a fetch, and as the checkout holds it when the remote has no such branch

- Paths: `src/torve/cli/manager.py` `src/torve/application/planner.py` `tests/test_manager.py` `tests/test_plan.py`
- Consequence: a phase widened on the document branch reaches the next attempt without a hand edit to the contract

### S-0094/D-2 — `ASSUMED` (The escalation loop runs without scripts)

The document branch the review leg's phasing reads (S-0092/D-2) is the remote's copy after a fetch, not the checkout's ref

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a round re-scoped at a requeue sees a phase pushed from another checkout

### S-0094/D-3 — `ASSUMED` (The escalation loop runs without scripts)

`manager resolve --resolution requeued` clears the task's escalated host state (its run-state file, worktree and sandbox) before it writes the requeue, and keeps the checkpoint a continued attempt resumes from

- Paths: `src/torve/cli/manager.py` `src/torve/application/reaper.py` `tests/test_manager.py` `tests/test_reaper.py`
- Consequence: a requeue never fails as `gate_infrastructure_failure` on what its own escalation left behind

### S-0094/D-4 — `ASSUMED` (The escalation loop runs without scripts)

While a pass is paused, the latest undelivered escalation of each task holding the pause is relayed whatever its reason, and the notification says the night is paused; every other escalation is judged by S-0051/D-8's interrupt classes

- Paths: `src/torve/application/notify.py` `src/torve/cli/manager.py` `tests/test_notify.py`
- Consequence: a night that stops serving work pages the operator once, if a destination is configured

### S-0094/D-5 — `ASSUMED` (The escalation loop runs without scripts)

`manager resolve --resolution abandoned` leaves the run's host state where it is; `torve reap --escalated` still clears it

- Paths: `src/torve/cli/manager.py` `tests/test_manager.py`
- Consequence: a person can still read an abandoned attempt's worktree and diff before sweeping it

### S-0095/D-1 — `ASSUMED` (A second repository can adopt torve)

`torve init --starter` writes `.torve/gates.yaml` and `.torve/config.yaml`, each with its schema line, unless the file exists, which it names and leaves alone; plain `torve init` writes neither

- Paths: `src/torve/cli/init.py` `tests/test_cli.py`
- Consequence: a fresh repository is one command from a gate run, and no adopter's file is ever overwritten

### S-0095/D-2 — `ASSUMED` (A second repository can adopt torve)

The starter manifest is the four structural builtins (`scope`, `secrets`, `no-test-tampering`, `decisions-reported`) blocking, with the repository's test gate a commented entry saying gates run inside the sandbox image; the starter configuration is `runtime.adapter: docker`, `image: python:3.13-slim`, `store.adapter: mock`

- Paths: `src/torve/cli/init.py` `tests/test_cli.py` `pages/docs/get-started.md`
- Consequence: `torve gates run --base main` exits 0 in a repository the starter just set up

### S-0095/D-3 — `ASSUMED` (A second repository can adopt torve)

`torve doctor` runs dispatch's `route_provider` for every configured tier and shows a refused one as a red line naming the seat, its provider and the providers allowed

- Paths: `src/torve/cli/doctor.py` `tests/test_doctor.py`
- Consequence: a seat outside `providers.default` is found by `doctor`, not by a dispatch that fails with `ProviderDenied`

### S-0095/D-4 — `ASSUMED` (A second repository can adopt torve)

The retired citation grammar (`LEGACY_CITE`, `TREE_LEGACY_CITE`) is checked, in the tree scan and in a document's prose, only when the archive holds `identifiers.yaml`; without it such text is prose

- Paths: `src/torve/config/spec.py` `tests/test_spec.py` `tests/test_cli_spec.py`
- Consequence: a repository that never wrote torve's retired grammar can run `spec-valid`, and torve and bloomery are checked exactly as today

### S-0095/D-5 — `ASSUMED` (A second repository can adopt torve)

Every model torve reads from YAML checks `schema_version` through one shared field type: absent reads as current; another value is refused naming the file, the version found and the version read, with "a newer torve" for a newer file and the release notes' conversion for an older one

- Paths: `src/torve/base/model.py` `src/torve/config/runconfig.py` `src/torve/config/manifest.py` `src/torve/config/agents.py` `src/torve/config/providers.py` `src/torve/config/fleet.py` `src/torve/domain/task.py` `src/torve/domain/spec.py` `tests/test_versions.py`
- Consequence: a file written for another torve stops the command that reads it, instead of being read as current

### S-0096/D-1 — `ASSUMED` (The loose ends the 0.1 nights left) — implementation: none

A halted divergence entry escalates `locked_conflict` only when it cites a LOCKED row; every other halt escalates `underspecified`, whatever its class. such a halt continues from its checkpoint as a locked one did. This amends S-0092/D-3 and S-0090/D-2

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py` `tests/test_session.py`
- Consequence: an escalation names a LOCKED row only when one was cited, and a round halted on its scope gets the one whole-phasing retry

### S-0096/D-2 — `ASSUMED` (The loose ends the 0.1 nights left) — implementation: none

The lane mints a red completion battery's round only where a review leg reads recorded findings (`threads.enabled` with `record` among `threads.sources`); elsewhere a red battery at completion escalates the completing task `blocker_finding` at once and records no finding. This narrows S-0093/D-3 and S-0093/D-4

- Paths: `src/torve/application/lane.py` `src/torve/cli/manager.py` `src/torve/cli/merge.py` `tests/test_lane.py` `tests/test_manager.py`
- Consequence: a red battery reaches a person on every repository, never a wait for a round nobody mints

### S-0096/D-3 — `ASSUMED` (The loose ends the 0.1 nights left) — implementation: none

`manager serve --night` runs one import pass, the scan's `mint` over the repository's contracts, before it opens the night, so the queue the open reads includes freshly minted contracts; a queue still empty after the import is refused as before

- Paths: `src/torve/cli/manager.py` `tests/test_manager.py`
- Consequence: a night needs one command, and the queue `night.opened` records is the queue it will work

### S-0096/D-4 — `ASSUMED` (The loose ends the 0.1 nights left) — implementation: none

The T-0113 rule pairs an existing module `<stem>.py` with `tests/test_<stem>.py` and every existing `tests/test_<stem>_*.py`, in the contract lint and in the tests the context pack names

- Paths: `src/torve/application/intake.py` `src/torve/application/contextpack.py` `tests/test_intake.py` `tests/test_contextpack.py`
- Consequence: a phase owns the tests named for the modules it changes, and its attempt is told which they are

### S-0097/D-1 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

A forge thread's HTML comments are set aside with its collapsed `<details>` blocks, before the injection check and before the fence; neither is judged and neither reaches the attempt

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a bot's hidden bookkeeping no longer turns its thread away as injection

### S-0097/D-2 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

A thread anchored to a landing record under a document's `execution/` or to an `AGENTS.md` projection mints no round and is not escalated; the leg replies once with a fixed text naming what writes the file and where a fix belongs, and resolves the thread when its author is in `threads.bots`; every other `.torve/` and `.github/` anchor stays refused as injection

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a thread on what the engine writes costs the operator nothing, and nothing the engine writes is edited on a comment's say-so

### S-0097/D-3 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

`lane_thread_refused` names the thread ids it refused, and a later pass neither refuses nor escalates a thread already refused unless the thread gained a comment since

- Paths: `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: a refusal reaches the operator once

### S-0097/D-4 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

A night whose lane published a document pull request out of draft is not drained while that head's review wait runs, until every login in `threads.bots` has reviewed the head or `threads.review_wait` minutes (default 45) have passed since it was pushed; the leg mints that pull request's rounds only after the wait, and `PrInfo` carries the logins that reviewed the head and the head's check state from the same call as its threads

- Paths: `src/torve/application/reviewleg.py` `src/torve/application/ports.py` `src/torve/adapters/vcs/git.py` `src/torve/cli/manager.py` `src/torve/config/runconfig.py` `tests/test_reviewleg.py` `tests/test_manager.py` `tests/test_runconfig.py` `tests/test_forge.py`
- Consequence: the leg sees the review wave the night produced instead of a drained night leaving it to a person

### S-0097/D-5 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

A head's findings are minted together as rounds of up to `threads.findings_per_round` findings (default 15), grouped by file so no two rounds of the wave share a file; `threads.rounds_per_pass` still bounds what one pass mints

- Paths: `src/torve/application/reviewleg.py` `src/torve/config/runconfig.py` `tests/test_reviewleg.py` `tests/test_runconfig.py`
- Consequence: a wave of 83 threads is a handful of rounds, not one round per finding

### S-0097/D-6 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

While rounds minted from one head's wave are queued or running, the lane lands each onto the document branch without publishing; the landing that leaves none outstanding publishes, and a round that escalates releases the hold

- Paths: `src/torve/application/lane.py` `tests/test_lane.py`
- Consequence: a wave costs one push, one re-review by the bots and at most one dismissed approval

### S-0097/D-7 — `ASSUMED` (The review leg keeps pace with the review bots) — implementation: none

When every thread a login in `threads.bots` opened on a head is resolved and the head's checks are green, the leg posts `threads.approve_comment` on the pull request once per head, keyed by the head sha; unset by default

- Paths: `src/torve/application/reviewleg.py` `src/torve/config/runconfig.py` `tests/test_reviewleg.py` `tests/test_runconfig.py`
- Consequence: the operator stops asking a bot for approval by hand

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
