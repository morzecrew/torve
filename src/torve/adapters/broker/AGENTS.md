<!-- torve:managed src/torve/adapters/broker — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/broker/`

### S-0055/D-4 — `LOCKED` (Standing decisions)

Agents hold no provider credentials; the broker injects them at its own boundary, routes providers at the wire and meters spend; every configuration file carries variable names, never values

- Paths: `src/torve/adapters/broker/**` `src/torve/config/runconfig.py` `.torve/config.yaml`
- Consequence: A sandbox that is compromised leaks nothing it was not given; a committed file never holds a secret
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0099/D-7 — `ASSUMED` (Every fact has one carrier, and no decision reads telemetry) — implementation: none

The broker meters streamed responses, and `seat.consumed` carries input, output, cache-read and cache-write tokens beside the total for every attempt and review.

- Paths: `src/torve/adapters/broker/local.py` `src/torve/application/eventlog.py` `src/torve/application/ports.py` `src/torve/domain/events.py`
- Consequence: cost per landing is a query for every seat, including plan-billed ones

<!-- /torve:managed -->
