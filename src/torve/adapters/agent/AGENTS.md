<!-- torve:managed src/torve/adapters/agent — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/agent/`

### S-0121/D-7 — `LOCKED` (Charter) — implementation: partial

Agents and their harnesses run only inside a sandbox, with no harness SDK in the engine process; the engine never builds an image mid-run, and mounting the host Docker socket is an explicit per-repository opt-in

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/docker.py` `src/torve/adapters/agent/**`

### S-0121/D-8 — `LOCKED` (Charter) — implementation: partial

No sandbox holds a real credential: the host-side broker injects provider keys at its own boundary and hands the sandbox a run-scoped token, routes and meters providers, refuses requests past the run's budget, keeps counts never bodies and opens last; configuration names variables, never values; the forge token stays on the host and reaches git through a credential helper, never on argv

- Paths: `src/torve/adapters/broker/**` `src/torve/application/dispatch.py` `src/torve/config/providers.py` `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_broker.py::test_a_brokered_seat_is_handed_no_provider_key tests/test_broker.py::test_the_broker_keeps_counts_and_metadata_never_bodies tests/test_dispatch.py::test_a_brokered_dispatch_hands_the_sandbox_no_provider_key` (shadow; runs as `decision:S-0121/D-8`)

### S-0121/D-9 — `LOCKED` (Charter) — implementation: partial

Every sandbox image answers `/opt/torve/equip` and `/opt/torve/run`, and the engine talks to it only through flat `TORVE_*` scalars in its own vocabulary; brokered and direct runs differ only in values, no manifest carries a shell line, and a seat's `env` reaches the image as written with nothing under `src/` reading it

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py` `sandboxes/base/**` `sandboxes/claude/**` `sandboxes/dsh/**`
- Check: `uv run pytest tests/test_sandbox_defs.py -k "seam or broker"` (shadow; runs as `decision:S-0121/D-9`)

### S-0121/D-40 — `ASSUMED` (Charter) — implementation: partial

Session traces live under `.torve/traces/`, are never committed, uploaded or put into a prompt, and never drive control flow; the context pack is computed host-side with no model from the task's own facts

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/reaper.py` `src/torve/application/contextpack.py`
- Check: `uv run pytest tests/test_reaper.py::test_retention_touches_nothing_within_both_bounds` (shadow; runs as `decision:S-0121/D-40`)

### S-0122/D-11 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The engine drives a dsh session through headless `--json` and continues it with `--session-id`, reads tool calls, results and per-step usage from the NDJSON events in place of `report-usage`, and interrupts a session by killing the process and resuming it later

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/adapters/agent/dsh_session.py` `sandboxes/dsh/toolkit/**`

### S-0129/D-3 — `ASSUMED` (Acceptance compiled before the session)

The kept probes are stored once per unit under its task directory, and a resumed or requeued unit reads the store and never compiles again, except after a seat that failed to run or answered with a provider error, which stores nothing; every candidate's worktree gets the stored files planted at their paths with `/probes/acceptance/` excluded from what the judge commits, their commands are appended to the acceptance the executor is shown and never to the declared acceptance the gates run, and the prompt says the probes were compiled from the document, fail on the base, should pass, and that an edit to one changes nothing

- Paths: `src/torve/application/session.py` `src/torve/application/runner.py` `src/torve/adapters/agent/harness.py`
- Consequence: every candidate and revision turn works against the same probes, and nothing it writes can change the copy that judges it
- Check: `uv run pytest -q tests/test_session.py tests/test_tiering.py tests/test_runner.py -k "compiled_probes or probe_failing or probe_passing"` (shadow; runs as `decision:S-0129/D-3`)

<!-- /torve:managed -->
