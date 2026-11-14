<!-- torve:managed sandboxes/dsh/toolkit — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/dsh/toolkit/`

### S-0121/D-10 — `ASSUMED` (Charter) — implementation: partial

An attempt's inputs are only what its seat declared: equipment is fetched host-side before the sandbox exists and mounted read-only, never written into a path the repository owns, and each image's `run` shuts out worktree files written for other readers (`AGENTS.md`, `CLAUDE.md`, `.claude/skills`, `.agents/skills`, `.mcp.json`) before adding its own

- Paths: `src/torve/config/equipment.py` `src/torve/application/equipment.py` `sandboxes/claude/toolkit/**` `sandboxes/dsh/toolkit/**`
- Check: `uv run pytest tests/test_equipment.py tests/test_session.py -k "host_side or read_only or equipment_root"` (shadow; runs as `decision:S-0121/D-10`)

### S-0122/D-11 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The engine drives a dsh session through headless `--json` and continues it with `--session-id`, reads tool calls, results and per-step usage from the NDJSON events in place of `report-usage`, and interrupts a session by killing the process and resuming it later

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/adapters/agent/dsh_session.py` `sandboxes/dsh/toolkit/**`

### S-0127/D-19 — `ASSUMED` (A verdict comes from what ran)

The dsh image deletes `[::1]` from the loopback entries `@deepseek-ai/dsh-http-proxy` adds to the `no_proxy` it publishes to every child, keeping the bare `::1`, and the build's smoke check reads a child's `no_proxy` back under a proxy and fails on a bracketed entry or a lost one of the sandbox's own

- Paths: `sandboxes/dsh/Dockerfile` `sandboxes/dsh/toolkit/smoke`
- Consequence: a project test that builds an httpx client runs under the dsh seat; a Node child that requests `http://[::1]:port` itself goes to the proxy, because undici reads the bare `::1` as host `:` port `1`, while dsh's own requests still bypass loopback by address
- Check: `uv run pytest -q tests/test_sandbox_defs.py -k bracketed_loopback` (shadow; runs as `decision:S-0127/D-19`)

<!-- /torve:managed -->
