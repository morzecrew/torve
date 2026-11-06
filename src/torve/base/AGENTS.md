<!-- torve:managed src/torve/base — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/base/`

### S-0121/D-15 — `LOCKED` (Charter) — implementation: partial

Every YAML model checks `schema_version` through `base.model.SchemaVersion`, and an `engine:` range in `config.yaml` stops any configuration-loading command under a torve outside it

- Paths: `src/torve/base/model.py` `src/torve/config/runconfig.py` `src/torve/domain/task.py` `src/torve/domain/spec.py`
- Check: `uv run pytest tests/test_versions.py && uv run pytest tests/test_runconfig.py -k "engine_range"` (shadow; runs as `decision:S-0121/D-15`)

### S-0121/D-16 — `ASSUMED` (Charter) — implementation: partial

The engine writes one instant format, `YYYY-MM-DDTHH:MM:SSZ`, from `base.clock.stamp()`, each closed vocabulary is defined once in `domain/vocabulary.py`, and every strict model uses `base.model.STRICT` with described fields, so `torve init` writes schemas an editor checks

- Paths: `src/torve/base/clock.py` `src/torve/base/model.py` `src/torve/domain/vocabulary.py`
- Check: `uv run pytest tests/test_clock.py::test_no_module_but_the_clock_spells_the_instant tests/test_spec.py::test_schema_descriptions_cover_every_property` (shadow; runs as `decision:S-0121/D-16`)

### S-0121/D-17 — `ASSUMED` (Charter) — implementation: partial

`base/globs.py` is the one reading of a scope glob, with gitignore semantics, and the pre-write hook shipped in `skills/hooks/implement/` imports a byte-identical copy

- Paths: `src/torve/base/globs.py` `skills/hooks/implement/globs.py`
- Check: `uv run pytest tests/test_globs.py && uv run pytest tests/test_equipment.py -k reads_its_globs_through_the_engine` (shadow; runs as `decision:S-0121/D-17`)

### S-0123/D-6 — `ASSUMED` (What the first 0.9.0 run left)

`host_executor` starts each command in a session of its own and, on a timeout, kills that session's process group before it reports the timeout

- Paths: `src/torve/base/shell.py`
- Consequence: no process a timed-out host command started outlives it
- Check: `uv run pytest -q tests/test_shell.py -k process_group` (shadow; runs as `decision:S-0123/D-6`)

<!-- /torve:managed -->
