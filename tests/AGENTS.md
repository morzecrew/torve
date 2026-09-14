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

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
