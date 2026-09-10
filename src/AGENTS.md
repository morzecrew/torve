<!-- torve:managed src — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/`

### S-0058/D-2 — `LOCKED` (One grammar and the anatomy)

Every existing identifier converts once through a mapping committed as `.torve/archive/identifiers.yaml`, applied to the corpus, the archive, `src/`, `pages/`, `skills/`, the README, the local task files and the projections, with "RFC NNNN §n" converted to the section key at that position; a parity script gates the commit and is not committed

- Paths: `.torve/specs/**` `.torve/archive/**` `src/**` `pages/**` `skills/**` `README.md`
- Consequence: No legacy identifier stands anywhere the check reads; history keeps its words and resolves through the mapping
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `src/`

- **S-0058/I-1**: Every identifier the corpus defines and every citation the tree carries matches the one grammar, `S-NNNN`, `S-NNNN/<F>-<n>` or `S-NNNN/<key>`; a legacy shape is a check problem
  - Paths: `.torve/specs/**` `src/**` `pages/**`
  - Check: `uv run torve spec check`
- **S-0059/I-1**: No file under `src/torve` writes a `Torve-Task`, `Torve-Attempt`, `Torve-Agent`, `Torve-Config` or `Torve-Decisions` trailer; a backticked mention in prose is a record of what these used to say, not a writer
  - Paths: `src/**`
  - Check: `! grep -rnE --include=*.py '[^`]Torve-(Task|Attempt|Agent|Config|Decisions):' src/torve`
- **S-0059/I-2**: The word `rfc` stands in `src/torve` only where the retired key is refused (`domain/task.py`) or folded from a recorded mint (`application/manager.py`)
  - Paths: `src/**`
  - Check: `! grep -rnw --include=*.py rfc src/torve | grep -v 'domain/task.py\|application/manager.py'`

<!-- /torve:managed -->
