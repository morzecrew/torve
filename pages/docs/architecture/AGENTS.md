<!-- torve:managed pages/docs/architecture — rendered from the corpus; do not edit by hand -->

## Decisions governing `pages/docs/architecture/`

### S-0109/D-2 — `ASSUMED` (A conflict the served lane re-queues reaches the board) — implementation: none

`task.requeued` may be written by the manager only, and `task.returned` stays the operator's (S-0044/A-13).

- Paths: `src/torve/domain/events.py` `pages/docs/architecture/record.md`
- Consequence: the record tells a mechanical disposal apart from a person sending judged work back
- Check: `uv run pytest tests/test_events.py tests/test_docs.py -q` (shadow; runs as `decision:S-0109/D-2`, no log entry owed)

<!-- /torve:managed -->
