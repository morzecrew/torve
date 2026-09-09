<!-- torve:managed tests — rendered from the corpus; do not edit by hand -->

## Decisions governing `tests/`

### S-0055/D-19 — `ASSUMED` (Standing decisions)

An existing test is edited only under the contract's licence; adding a test is an addition, editing one needs scope

- Paths: `src/torve/gates/no_test_tampering.py` `tests/**`
- Consequence: A green earned by weakening the suite is a red

## Invariants holding over `tests/`

- **S-0055/I-5**: The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

## Contended now

- `tests/**` — 1 blocked dispatch(es) in the last 500 attempts

<!-- /torve:managed -->
