<!-- torve:managed src/torve/adapters/eventstore — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/eventstore/`

### S-0113/D-4 — `ASSUMED` (A gate in the sandbox sees what the host sees)

A store runtime a CLI verb opens closes its Postgres client when the runtime's scope ends, and `torve manager board --until-change` opens one runtime for the whole wait.

- Paths: `src/torve/adapters/eventstore/document.py` `src/torve/cli/manager.py` `src/torve/cli/decisions.py` `src/torve/cli/fleet.py` `src/torve/cli/options.py`
- Consequence: a watcher holds one connection pool however long it waits

<!-- /torve:managed -->
