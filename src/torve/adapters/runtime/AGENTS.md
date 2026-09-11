<!-- torve:managed src/torve/adapters/runtime — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/runtime/`

### S-0055/D-3 — `LOCKED` (Standing decisions)

The sandbox is the unit of lifecycle; the engine never executes agent code on the host

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/session.py`
- Consequence: Killing a worker costs its lease and nothing else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0062/D-5 — `ASSUMED` (Equipment is declared, and the harness is told how to take it)

The equipment cache mounts read-only into the sandbox, one directory per item, and the image's own `/opt/torve/equip` composes whatever its harness needs from the manifest at the mount root (S-0063/D-3, S-0063/D-12).

- Paths: `src/torve/adapters/runtime/**` `src/torve/application/ports.py`
- Consequence: nothing inside an attempt can edit what it was equipped with, and the cache stays derived state that deleting costs only wall clock

### S-0062/D-9 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

S-0061/D-5 is superseded by D-1 and S-0061/D-6's renderer retires; the harness is told about a plugin by its own flag, not by files torve writes into its state.

- Paths: `src/torve/adapters/runtime/plugins.py` `.torve/sandbox/**`
- Consequence: torve stops keeping a second copy of a harness's internal bookkeeping in step with it across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-11 — `LOCKED` (The image knows how to equip itself)

`torve sandbox build` and `stage` retire, and `RuntimePort.build_image` with them; the verb keeps `list` and `digest`, and `just images` is the build.

- Paths: `src/torve/cli/sandbox.py` `src/torve/application/ports.py` `src/torve/adapters/runtime/**`
- Consequence: the engine loses its last way to build an image, which is the rule S-0017/D-3 already stated and could not enforce while a verb of its own did it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-12 — `LOCKED` (The image knows how to equip itself)

The equipment manifest is `manifest.json` at the root of the read-only equipment mount, named by `TORVE_EQUIPMENT` alone — never a second variable, never a file in the workspace.

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/**`
- Consequence: the attempt cannot rewrite the description of what it was equipped with, and one variable names one root rather than two disagreeing about which is authoritative
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `src/torve/adapters/runtime/`

- **S-0062/I-1**: No equipment is fetched while an attempt is running — every fetch is host-side, before the sandbox exists.
  - Paths: `src/torve/application/equipment.py` `src/torve/adapters/runtime/**`
  - Check: `uv run pytest tests/test_equipment.py -k host_side`

<!-- /torve:managed -->
