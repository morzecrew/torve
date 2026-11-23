<!-- torve:managed src/torve/application/prompts — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/application/prompts/`

### S-0129/D-1 — `ASSUMED` (Acceptance compiled before the session)

With `review.compile: true`, refused outside `review.mode: critic` and off by default, a unit compiles its document into acceptance probes once, before its first candidate: one reviewer-tier seat call under its own frozen prompt `compile/v2`, staged the document whole and the contract's rows, asked for probes that each name a row the contract carries, quote the passage stating the behaviour, write one file under `probes/acceptance/` and give one command exiting 0 when the behaviour holds, every row its probe before any row a second

- Paths: `src/torve/config/runconfig.py` `src/torve/application/acceptance.py` `src/torve/application/prompts/compile-v2.md` `src/torve/application/prompts/compile-v1.schema.json`
- Consequence: a document's stated rules are executable before any code, at about one critic call's cost per unit; a configuration that does not name the key runs as before
- Check: `uv run pytest -q tests/test_acceptance.py tests/test_runconfig.py -k "compile"` (shadow; runs as `decision:S-0129/D-1`)

### S-0129/D-9 — `ASSUMED` (Acceptance compiled before the session)

A probe that fails every run of a decision is put to one reviewer-tier seat call under its own frozen prompt `dispute/v1`, over a scrubbed copy of the candidate, staged the document whole and the probe with its row, quote, claim, command, file and runs; the seat answers `upheld`, or `disputed` with the passage of the document the probe contradicts and a counter-probe. The engine keeps a dispute only when that passage occurs in one of the document's files with whitespace collapsed, the counter-probe passes a compiled probe's written rules on the probe's row (D-2) in a file no kept probe holds, and its command, run alone with its file, exits 0 on the candidate and executes red on the unit's start tree, which the store records, and not for want of a module that tree's commit holds. A kept dispute records the probe advisory, as a `minor` `contract` finding that names the dispute, withdraws it from the store and from what later attempts are shown and planted, and keeps the counter-probe in its place under a new id, so every later decision and recheck of the unit is judged by the counter-probe. A unit makes at most two disputes, and none at its cap or clock with the asking critic call counted or without a recorded start tree; an upheld or refused dispute, a seat that cannot run and a dispute not made leave the probe blocking. The call is unit spend, counted in its attempt's, and a `review` with `mode: dispute`, its prompt sha, the probe and what came of it

- Paths: `src/torve/application/acceptance.py` `src/torve/application/critic.py` `src/torve/application/runner.py` `src/torve/application/ports.py` `src/torve/adapters/workspace/git.py` `src/torve/application/prompts/dispute-v1.md` `src/torve/application/prompts/dispute-v1.schema.json`
- Consequence: a probe that misreads the document, a placeholder read as data, a count or an order the document gives differently, no longer escalates correct work: the engine measures the passage's own behaviour on the candidate in its place, and the misread probe never judges the unit again
- Check: `uv run pytest -q tests/test_acceptance.py tests/test_critic.py tests/test_runner.py tests/test_judge_checkout.py -k "dispute or given_commit"` (shadow; runs as `decision:S-0129/D-9`)

<!-- /torve:managed -->
