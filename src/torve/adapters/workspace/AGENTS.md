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

### S-0129/D-9 — `ASSUMED` (Acceptance compiled before the session)

A probe that fails every run of a decision is put to one reviewer-tier seat call under its own frozen prompt `dispute/v1`, over a scrubbed copy of the candidate, staged the document whole and the probe with its row, quote, claim, command, file and runs; the seat answers `upheld`, or `disputed` with the passage of the document the probe contradicts and a counter-probe. The engine keeps a dispute only when that passage occurs in one of the document's files with whitespace collapsed, the counter-probe passes a compiled probe's written rules on the probe's row (D-2) in a file no kept probe holds, and its command, run alone with its file, exits 0 on the candidate and executes red on the unit's start tree, which the store records, and not for want of a module that tree's commit holds. A kept dispute records the probe advisory, as a `minor` `contract` finding that names the dispute, withdraws it from the store and from what later attempts are shown and planted, and keeps the counter-probe in its place under a new id, so every later decision and recheck of the unit is judged by the counter-probe. A unit makes at most two disputes, and none at its cap or clock with the asking critic call counted or without a recorded start tree; an upheld or refused dispute, a seat that cannot run and a dispute not made leave the probe blocking. The call is unit spend, counted in its attempt's, and a `review` with `mode: dispute`, its prompt sha, the probe and what came of it

- Paths: `src/torve/application/acceptance.py` `src/torve/application/critic.py` `src/torve/application/runner.py` `src/torve/application/ports.py` `src/torve/adapters/workspace/git.py` `src/torve/application/prompts/dispute-v1.md` `src/torve/application/prompts/dispute-v1.schema.json`
- Consequence: a probe that misreads the document, a placeholder read as data, a count or an order the document gives differently, no longer escalates correct work: the engine measures the passage's own behaviour on the candidate in its place, and the misread probe never judges the unit again
- Check: `uv run pytest -q tests/test_acceptance.py tests/test_critic.py tests/test_runner.py tests/test_judge_checkout.py -k "dispute or given_commit"` (shadow; runs as `decision:S-0129/D-9`)

<!-- /torve:managed -->
