<!-- torve:managed sandboxes/claude/toolkit — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/claude/toolkit/`

### S-0121/D-10 — `ASSUMED` (Charter) — implementation: partial

An attempt's inputs are only what its seat declared: equipment is fetched host-side before the sandbox exists and mounted read-only, never written into a path the repository owns, and each image's `run` shuts out worktree files written for other readers (`AGENTS.md`, `CLAUDE.md`, `.claude/skills`, `.agents/skills`, `.mcp.json`) before adding its own

- Paths: `src/torve/config/equipment.py` `src/torve/application/equipment.py` `sandboxes/claude/toolkit/**` `sandboxes/dsh/toolkit/**`
- Check: `uv run pytest tests/test_equipment.py tests/test_session.py -k "host_side or read_only or equipment_root"` (shadow; runs as `decision:S-0121/D-10`)

### S-0124/D-14 — `ASSUMED` (Candidates side by side, and S-0122's revision)

A harness manifest's `session: true` says its image's `run` continues the session `TORVE_SESSION_ID` names, with `TORVE_PROMPT` as the next turn's task, and a seat whose manifest says so keeps its session for D-8's revision turn, as a dsh seat always does through its own driver (S-0122/D-11); any other seat falls back to `fresh` as before. The Claude Code manifests declare it and their image passes the id to `claude -p --resume`; the engine reads the id off the attempt's trace, runs `run` alone so nothing is equipped again, appends the turn's stream to the trace, and adds the turn's cost and token counts, and the receipt its context curve is checked against, to the earlier turns', so those are the attempt's whole as a dsh session's totals are

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py` `src/torve/config/runconfig.py` `sandboxes/claude/toolkit/run` `.torve/harnesses/claude-subscription.yaml` `.torve/harnesses/claude-subscription-night.yaml`
- Consequence: a Claude executor's revision under the default `review.revision: session` is a turn in its own session, judged by re-running its blockers (S-0128/D-3), instead of a new attempt the critic reviews again
- Check: `uv run pytest -q tests/test_dsh_session.py tests/test_agents.py tests/test_sandbox_defs.py -k "session and (keep or resume)"` (shadow; runs as `decision:S-0124/D-14`)

<!-- /torve:managed -->
