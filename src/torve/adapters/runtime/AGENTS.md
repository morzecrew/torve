<!-- torve:managed src/torve/adapters/runtime — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/runtime/`

### D-55.3 — `LOCKED` (RFC 0055 — Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
