<!-- torve:managed src/torve/adapters/store — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/store/`

### S-0121/D-19 — `LOCKED` (Charter) — implementation: partial

Git holds what should be and the store holds what happened: `TaskStore` is a thin facade over forze's leased queue, the manager is one resident process that rebuilds its board from the record each pass, a lease expires when no activity is recorded, and the repository is the partition key

- Paths: `src/torve/application/taskstore.py` `src/torve/application/manager.py` `src/torve/application/worker.py` `src/torve/adapters/store/**`
- Check: `uv run pytest tests/test_taskstore.py tests/test_manager.py::test_a_claim_that_has_gone_silent_past_its_lease_is_expired` (shadow; runs as `decision:S-0121/D-19`, no log entry owed)

<!-- /torve:managed -->
