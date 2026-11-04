<!-- torve:managed skills/hooks/implement — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/hooks/implement/`

### S-0121/D-17 — `ASSUMED` (Charter) — implementation: partial

`base/globs.py` is the one reading of a scope glob, with gitignore semantics, and the pre-write hook shipped in `skills/hooks/implement/` imports a byte-identical copy

- Paths: `src/torve/base/globs.py` `skills/hooks/implement/globs.py`
- Check: `uv run pytest tests/test_globs.py && uv run pytest tests/test_equipment.py -k reads_its_globs_through_the_engine` (shadow; runs as `decision:S-0121/D-17`, no log entry owed)

<!-- /torve:managed -->
