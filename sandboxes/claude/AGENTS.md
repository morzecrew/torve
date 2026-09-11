<!-- torve:managed sandboxes/claude — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/claude/`

### S-0063/D-18 — `LOCKED` (The image knows how to equip itself)

A subscription seat authenticates by variable name and mounts no credential: the claude image takes `CLAUDE_CODE_OAUTH_TOKEN` and no auth volume.

- Paths: `sandboxes/claude/**` `.torve/harnesses/**`
- Consequence: an attempt holds one token for its own lifetime instead of reading a refresh token out of a volume that outlives every sandbox that ever mounted it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
