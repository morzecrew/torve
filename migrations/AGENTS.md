<!-- torve:managed migrations — rendered from the corpus; do not edit by hand -->

## Decisions governing `migrations/`

### S-0121/D-20 — `LOCKED` (Charter) — implementation: partial

Torve owns the DDL for every relation it uses as owner-grouped, forward-only, checksummed SQL under `migrations/`, and the substrate group pins an exact forze version that `pyproject.toml` matches and `doctor` enforces

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/migrate.py` `pyproject.toml`
- Check: `uv run pytest tests/test_migrate.py` (shadow; runs as `decision:S-0121/D-20`, no log entry owed)

<!-- /torve:managed -->
