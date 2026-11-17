<!-- torve:managed src/torve/application/prompts — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/application/prompts/`

### S-0129/D-1 — `ASSUMED` (Acceptance compiled before the session)

With `review.compile: true`, refused outside `review.mode: critic` and off by default, a unit compiles its document into acceptance probes once, before its first candidate: one reviewer-tier seat call under its own frozen prompt `compile/v1`, staged the document whole and the contract's rows, asked for probes that each name a row the contract carries, quote the passage stating the behaviour, write one file under `probes/acceptance/` and give one command exiting 0 when the behaviour holds

- Paths: `src/torve/config/runconfig.py` `src/torve/application/acceptance.py` `src/torve/application/prompts/compile-v1.md` `src/torve/application/prompts/compile-v1.schema.json`
- Consequence: a document's stated rules are executable before any code, at about one critic call's cost per unit; a configuration that does not name the key runs as before
- Check: `uv run pytest -q tests/test_acceptance.py tests/test_runconfig.py -k "compile"` (shadow; runs as `decision:S-0129/D-1`)

<!-- /torve:managed -->
