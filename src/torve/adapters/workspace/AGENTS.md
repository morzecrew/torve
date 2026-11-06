<!-- torve:managed src/torve/adapters/workspace — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/workspace/`

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`)

### S-0123/D-3 — `ASSUMED` (What the first 0.9.0 run left)

`GitWorkspace.snapshot` returns the task worktree's HEAD when `git add -A` would stage the tree HEAD already holds, and makes a snapshot commit only over uncommitted work

- Paths: `src/torve/adapters/workspace/git.py`
- Consequence: a unit that starts clean lands no empty "judge snapshot" commit
- Check: `uv run pytest -q tests/test_judge_checkout.py -k clean_snapshot` (shadow; runs as `decision:S-0123/D-3`)

<!-- /torve:managed -->
