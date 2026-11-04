<!-- torve:managed src/torve — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/`

### S-0121/D-12 — `LOCKED` (Charter) — implementation: partial

Imports run downward only (cli > adapters > application > domain > base); gates and config import neither application nor adapters, config never imports cli, adapters never import each other, gates and the runtime and agent adapters never import `torve.config.spec`, and import-linter enforces it

- Paths: `src/torve/**` `pyproject.toml`
- Check: `uv run lint-imports && uv run pytest tests/test_layering.py` (shadow; runs as `decision:S-0121/D-12`, no log entry owed)

<!-- /torve:managed -->
