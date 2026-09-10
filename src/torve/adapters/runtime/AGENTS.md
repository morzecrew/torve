<!-- torve:managed src/torve/adapters/runtime — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/runtime/`

### S-0055/D-3 — `LOCKED` (Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0061/D-6 — `ASSUMED` (The agent profile, the harness manifest, and the seat that names them)

The runtime adapter renders the profile's plugins into the harness's own seeding format at dispatch, and refuses a non-empty list for a harness with no renderer.

- Paths: `src/torve/adapters/runtime/**` `.torve/sandbox/**`
- Consequence: a plugin list that cannot be honoured is a configuration error rather than an attempt that quietly ran without it

<!-- /torve:managed -->
