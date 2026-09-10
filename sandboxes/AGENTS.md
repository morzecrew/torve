<!-- torve:managed sandboxes — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/`

### S-0063/D-3 — `LOCKED` (The image knows how to equip itself)

A sandbox image carries `/opt/torve/equip`, which translates the equipment manifest into whatever its harness needs; S-0062/D-2's flag templates retire and `kinds` is what remains of the capability map.

- Paths: `src/torve/config/agents.py` `sandboxes/**`
- Consequence: equipment reaches a harness the way that harness takes it, decided beside the harness rather than by a renderer keeping up with three of them across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-6 — `LOCKED` (The image knows how to equip itself)

Image definitions live at `sandboxes/<name>/` in the repository root and build to `<name>-sandbox`; `.torve/sandbox/` stays the hook for a consuming repository's own.

- Paths: `sandboxes/**` `src/torve/cli/sandbox.py`
- Consequence: torve's own source stops living in the directory torve creates inside repositories it works on, and an image gets a name worth publishing
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-7 — `LOCKED` (The image knows how to equip itself)

One base image carries `git`, `uv` and the engine's CLI; every definition inherits it, and `_torve-cli.dockerfile` with the test pinning five copies against it retires.

- Paths: `sandboxes/**` `tests/test_sandbox_defs.py`
- Consequence: the layer five images duplicate is built once and inherited, and the test that stood in for inheritance goes with it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-9 — `LOCKED` (The image knows how to equip itself)

Claude's `equip` and `run` land first; dsh's and mimo's land after, and they are the proof that the contract is not claude-shaped.

- Paths: `sandboxes/**`
- Consequence: the first landing is written against a harness this repository can reach, and the contract's generality is tested rather than asserted
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `sandboxes/`

- **S-0063/I-2**: Every image definition answers `/opt/torve/equip` and `/opt/torve/run`, and none carries its own copy of the CLI layer.
  - Paths: `sandboxes/**`
  - Check: `uv run pytest tests/test_sandbox_defs.py -k seam`

<!-- /torve:managed -->
