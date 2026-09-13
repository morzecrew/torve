<!-- torve:managed sandboxes/battery — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/battery/`

### S-0071/D-2 — `LOCKED` (The battery costs what it costs for reasons unrelated to what it judges)

The battery image is rebuilt in the same change that adds the dependency, and the moved digest is recorded rather than waived

- Paths: `sandboxes/battery/Dockerfile`
- Consequence: an attempt's `uv run` reconciles against a populated environment instead of reaching a network it may not have
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
