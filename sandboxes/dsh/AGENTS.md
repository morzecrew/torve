<!-- torve:managed sandboxes/dsh — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/dsh/`

### S-0121/D-9 — `LOCKED` (Charter) — implementation: partial

Every sandbox image answers `/opt/torve/equip` and `/opt/torve/run`, and the engine talks to it only through flat `TORVE_*` scalars in its own vocabulary; brokered and direct runs differ only in values, no manifest carries a shell line, and a seat's `env` reaches the image as written with nothing under `src/` reading it

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py` `sandboxes/base/**` `sandboxes/claude/**` `sandboxes/dsh/**`
- Check: `uv run pytest tests/test_sandbox_defs.py -k "seam or broker"` (shadow; runs as `decision:S-0121/D-9`)

### S-0122/D-10 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

The dsh seat pins `@deepseek-ai/dsh@0.2.1-rc.5` and `deepseek-v4.1-flash-1017`, runs a `torve-exec` profile built on `sdk-minimal` with `DSH_PERMISSION_MODE=danger-full-access` inside the container, telemetry off, a per-attempt `DSH_HOME`, an empty `DSH_AGENTS_HOME` and a 900 s bash timeout, and every patch carries a `--dump-config` boot assertion

- Paths: `sandboxes/dsh/**` `.torve/harnesses/dsh.yaml`

<!-- /torve:managed -->
