<!-- torve:managed scripts/measure — rendered from the corpus; do not edit by hand -->

## Decisions governing `scripts/measure/`

### S-0124/D-15 — `ASSUMED` (Candidates side by side, and S-0122's revision)

A session revision's turn re-runs the gates inside its attempt (D-8), so the telemetry stream and the record hold a row per gate pass for that attempt, each carrying the attempt's whole agent block so far. Every reader that counts attempts or sums their spend folds an attempt's rows into its newest, keyed by its task, attempt stamp and candidate as `torve why` groups them: the ledger's attempts, seats and burn population, the context report's attempt blocks, `torve why`'s regime comparator, the harness populations and the bed's trial cost. A row with a kind, a shadow replay's, or one with no attempt stamp, is counted as it stands: a replay can share a live attempt's stamp, and the readers drop replays only after the fold

- Paths: `src/torve/application/streams.py` `src/torve/application/ledger.py` `src/torve/application/projections.py` `src/torve/cli/ledger.py` `scripts/measure/__main__.py`
- Consequence: `torve ledger`, `torve context` and the harness facts count a session revision's attempt once, at its final block, and so do the gate tallies they fold: the gate pass before the turn is superseded by the one after it
- Check: `uv run pytest -q tests/test_streams.py tests/test_ledger.py tests/test_projections.py tests/test_intake.py tests/test_measure_harness.py -k "re_gated or session_revision or newest_row or selfcheck"` (shadow; runs as `decision:S-0124/D-15`)

<!-- /torve:managed -->
