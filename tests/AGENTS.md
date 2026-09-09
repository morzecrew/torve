<!-- torve:managed tests — rendered from the corpus; do not edit by hand -->

## Decisions governing `tests/`

### D-55.19 — `ASSUMED` (RFC 0055 — Standing decisions)

An existing test is edited only under the contract's licence; adding a test is an addition, editing one needs scope

- Paths: `src/torve/gates/no_test_tampering.py` `tests/**`
- Consequence: A green earned by weakening the suite is a red

## Invariants holding over `tests/`

- **I-55.5** (RFC 0055): The suite is green
  - Paths: `src/torve/**` `tests/**`
  - Check: `uv run pytest`

<!-- /torve:managed -->
