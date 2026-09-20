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

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
