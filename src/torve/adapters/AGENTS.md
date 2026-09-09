<!-- torve:managed src/torve/adapters — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/`

### S-0055/D-25 — `LOCKED` (Standing decisions)

Adapters never import each other and are organised `adapters/<port>/<technology>.py`

- Paths: `src/torve/adapters/**`
- Consequence: Swapping one adapter is never a rewrite of another
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0055/D-26 — `LOCKED` (Standing decisions)

The specification format terminates at the planner: gates, runtime adapters and agent adapters never import its owner

- Paths: `pyproject.toml` `src/torve/gates/**` `src/torve/adapters/**`
- Consequence: Format containment cannot break quietly; the contract is the only thing a gate reads about a task
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
