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

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

## Contended now

- `tests/**` — 1 blocked dispatch(es) in the last 500 attempts

<!-- /torve:managed -->
