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

The next dispatch of a task whose last attempt escalated `blocker_finding` or `locked_conflict`, and which has not landed, continues from the tree that attempt checkpointed, whether it was requeued on the board or its run state was reaped; a gate conviction still restarts from the base

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

A halted divergence entry citing a LOCKED row escalates `locked_conflict`; one of class `spec-gap` escalates `underspecified`; any other halt keeps `locked_conflict`

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

### S-0093/D-1 — `ASSUMED` (A document runs the whole suite before it is ready) — implementation: none

A landing that leaves a document complete runs the fallback battery over the document branch tip before the pull request is published; the pull request leaves draft only on a green battery

- Paths: `src/torve/application/lane.py` `tests/test_lane.py`
- Consequence: no document pull request is ready on a tree the whole suite has not passed

### S-0093/D-2 — `ASSUMED` (A document runs the whole suite before it is ready) — implementation: none

A red battery at completion writes `lane_document_gates_red` with its summary and publishes the pull request as a draft

- Paths: `src/torve/application/lane.py` `tests/test_lane.py`
- Consequence: a person reading the pull request sees why it is still a draft

### S-0093/D-3 — `ASSUMED` (A document runs the whole suite before it is ready) — implementation: none

A red battery at completion is recorded as a review finding on the last landed task — severity major, the failing gates and tests as the claim, the battery's command as the evidence — so the review leg mints a round for it

- Paths: `src/torve/application/lane.py` `src/torve/application/reviewleg.py` `tests/test_reviewleg.py`
- Consequence: the engine fixes what its phases broke outside their scope, and the round's landing reruns the battery

### S-0093/D-4 — `ASSUMED` (A document runs the whole suite before it is ready) — implementation: none

A completion earns one round: the battery reruns when that round lands a change or answers its finding without one, and a second red battery escalates on the last landed task for a person

- Paths: `src/torve/application/lane.py` `src/torve/application/reviewleg.py` `tests/test_lane.py`
- Consequence: a flaky test clears itself on the rerun, a break the round can fix is fixed, and one it cannot reaches a person instead of looping

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
