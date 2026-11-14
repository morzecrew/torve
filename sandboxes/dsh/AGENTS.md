<!-- torve:managed sandboxes/dsh — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/dsh/`

### S-0121/D-9 — `LOCKED` (Charter) — implementation: partial

Every sandbox image answers `/opt/torve/equip` and `/opt/torve/run`, and the engine talks to it only through flat `TORVE_*` scalars in its own vocabulary; brokered and direct runs differ only in values, no manifest carries a shell line, and a seat's `env` reaches the image as written with nothing under `src/` reading it

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py` `sandboxes/base/**` `sandboxes/claude/**` `sandboxes/dsh/**`
- Check: `uv run pytest tests/test_sandbox_defs.py -k "seam or broker"` (shadow; runs as `decision:S-0121/D-9`)

### S-0122/D-10 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The dsh seat pins @deepseek-ai/dsh@0.2.1-rc.5 and deepseek-v4.1-flash-1017 and runs the headless profile narrowed to nine tools, with DSH_PERMISSION_MODE=danger-full-access inside the container, telemetry and the session-log upload off, per-attempt homes and a 900 s bash timeout; the image's smoke check reads every patch back out of --dump-config and fails the build on a dropped key or a disabled plugin

- Paths: `sandboxes/dsh/**` `.torve/harnesses/dsh.yaml`

### S-0127/D-19 — `ASSUMED` (A verdict comes from what ran)

The dsh image deletes `[::1]` from the loopback entries `@deepseek-ai/dsh-http-proxy` adds to the `no_proxy` it publishes to every child, keeping the bare `::1`, and the build's smoke check reads a child's `no_proxy` back under a proxy and fails on a bracketed entry or a lost one of the sandbox's own

- Paths: `sandboxes/dsh/Dockerfile` `sandboxes/dsh/toolkit/smoke`
- Consequence: a project test that builds an httpx client runs under the dsh seat; a Node child that requests `http://[::1]:port` itself goes to the proxy, because undici reads the bare `::1` as host `:` port `1`, while dsh's own requests still bypass loopback by address
- Check: `uv run pytest -q tests/test_sandbox_defs.py -k bracketed_loopback` (shadow; runs as `decision:S-0127/D-19`)

<!-- /torve:managed -->
