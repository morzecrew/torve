<!-- torve:managed sandboxes/dsh/toolkit — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/dsh/toolkit/`

### S-0121/D-10 — `ASSUMED` (Charter) — implementation: partial

An attempt's inputs are only what its seat declared: equipment is fetched host-side before the sandbox exists and mounted read-only, never written into a path the repository owns, and each image's `run` shuts out worktree files written for other readers (`AGENTS.md`, `CLAUDE.md`, `.claude/skills`, `.agents/skills`, `.mcp.json`) before adding its own

- Paths: `src/torve/config/equipment.py` `src/torve/application/equipment.py` `sandboxes/claude/toolkit/**` `sandboxes/dsh/toolkit/**`
- Check: `uv run pytest tests/test_equipment.py tests/test_session.py -k "host_side or read_only or equipment_root"` (shadow; runs as `decision:S-0121/D-10`, no log entry owed)

### S-0122/D-11 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

The engine drives a dsh session through headless `--json` and continues it with `--session-id`, reads tool calls, results and per-step usage from the NDJSON events in place of `report-usage`, and interrupts a session by killing the process and resuming it later

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/adapters/agent/dsh_session.py` `sandboxes/dsh/toolkit/**`

<!-- /torve:managed -->
