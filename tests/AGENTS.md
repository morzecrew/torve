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

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

## Contended now

- `tests/**` — 1 blocked dispatch(es) in the last 500 attempts

<!-- /torve:managed -->
