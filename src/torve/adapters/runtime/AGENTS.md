<!-- torve:managed src/torve/adapters/runtime — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/runtime/`

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`)

### S-0121/D-7 — `LOCKED` (Charter) — implementation: partial

Agents and their harnesses run only inside a sandbox, with no harness SDK in the engine process; the engine never builds an image mid-run, and mounting the host Docker socket is an explicit per-repository opt-in

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/docker.py` `src/torve/adapters/agent/**`

### S-0122/D-3 — `LOCKED` (Judge-and-select rebuild) — implementation: partial

Protected paths (tests written for the unit by anyone but its executor, `conftest.py`, pytest configuration, `.torve/**` and the gate manifest) are mounted read-only in the executor sandbox, an edit to one blocks, and evidence runs use the base versions of test-harness files unless the contract licenses a change

- Paths: `src/torve/adapters/runtime/docker.py` `src/torve/application/session.py` `src/torve/gates/no_test_tampering.py`

<!-- /torve:managed -->
