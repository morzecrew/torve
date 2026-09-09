<!-- torve:managed src — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/`

### D-58.2 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

Every existing identifier converts once through a mapping committed as `.torve/archive/identifiers.yaml`, applied to the corpus, the archive, `src/`, `pages/`, `skills/`, the README, the local task files and the projections, with "RFC NNNN §n" converted to the section key at that position; a parity script gates the commit and is not committed

- Paths: `.torve/specs/**` `.torve/archive/**` `src/**` `pages/**` `skills/**` `README.md`
- Consequence: No legacy identifier stands anywhere the check reads; history keeps its words and resolves through the mapping
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `src/`

- **I-58.1** (RFC 0058): Every identifier the corpus defines and every citation the tree carries matches the one grammar, `S-NNNN`, `S-NNNN/<F>-<n>` or `S-NNNN/<key>`; a legacy shape is a check problem
  - Paths: `.torve/specs/**` `src/**` `pages/**`
  - Check: `uv run torve spec check`

<!-- /torve:managed -->
