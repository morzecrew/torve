<!-- torve:managed src/torve/adapters/broker — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/broker/`

### D-55.4 — `LOCKED` (RFC 0055 — Standing decisions)

Agents hold no provider credentials; the broker injects them at its own boundary, routes providers at the wire and meters spend; every configuration file carries variable names, never values

- Paths: `src/torve/adapters/broker/**` `src/torve/config/runconfig.py` `.torve/config.yaml`
- Consequence: A sandbox that is compromised leaks nothing it was not given; a committed file never holds a secret
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
