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

### S-0062/D-5 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

The cache mounts read-only into the sandbox, one directory per item, and the harness's flag templates are composed against those paths.

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/ports.py`
- Consequence: nothing inside an attempt can edit what it was equipped with, and the cache stays derived state that deleting costs only wall clock

### S-0062/D-9 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

S-0061/D-5 is superseded by D-1 and S-0061/D-6's renderer retires; the harness is told about a plugin by its own flag, not by files torve writes into its state.

- Paths: `src/torve/adapters/runtime/plugins.py` `.torve/sandbox/**`
- Consequence: torve stops keeping a second copy of a harness's internal bookkeeping in step with it across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `src/torve/adapters/runtime/`

- **S-0062/I-1**: No equipment is fetched while an attempt is running — every fetch is host-side, before the sandbox exists.
  - Paths: `src/torve/application/equipment.py` `src/torve/adapters/runtime/**`
  - Check: `uv run pytest tests/test_equipment.py -k host_side`

<!-- /torve:managed -->
