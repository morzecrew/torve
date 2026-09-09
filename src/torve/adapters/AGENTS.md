<!-- torve:managed src/torve/adapters — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/`

### D-55.25 — `LOCKED` (RFC 0055 — Standing decisions)

Adapters never import each other and are organised `adapters/<port>/<technology>.py`

- Paths: `src/torve/adapters/**`
- Consequence: Swapping one adapter is never a rewrite of another
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-55.26 — `LOCKED` (RFC 0055 — Standing decisions)

The specification format terminates at the planner: gates, runtime adapters and agent adapters never import its owner

- Paths: `pyproject.toml` `src/torve/gates/**` `src/torve/adapters/**`
- Consequence: Format containment cannot break quietly; the contract is the only thing a gate reads about a task
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
