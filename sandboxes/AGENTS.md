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

### S-0063/D-14 — `LOCKED` (The image knows how to equip itself)

Everything a definition puts inside its image lives under `toolkit/`, and one `COPY toolkit/ /opt/torve/` installs it.

- Paths: `sandboxes/**`
- Consequence: where a file lands is where it is written, so a definition is read by looking at it rather than by following a COPY line to a destination named somewhere else
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-15 — `LOCKED` (The image knows how to equip itself)

A harness's per-model configuration is the seat's `env`, rendered into whatever form that harness reads by its own `equip`; no image bakes a model file.

- Paths: `sandboxes/**` `.torve/harnesses/**`
- Consequence: adding a model is an edit to reviewed configuration rather than a rebuilt image, and the seven overlay files this repository bakes into the dsh image go
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0063/D-16 — `LOCKED` (The image knows how to equip itself)

`skill` is a kind every harness this repository builds accepts, delivered the way that harness reads skills; S-0062/D-10's prompt paragraph stands only for a harness that reads none.

- Paths: `sandboxes/**` `.torve/harnesses/**` `src/torve/application/skills.py`
- Consequence: a skill reaches a dsh or mimo seat as a skill its harness loads, rather than as a directory the prompt names and the model may or may not read
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `sandboxes/`

- **S-0063/I-2**: Every image definition answers `/opt/torve/equip` and `/opt/torve/run`, and none carries its own copy of the CLI layer.
  - Paths: `sandboxes/**`
  - Check: `uv run pytest tests/test_sandbox_defs.py -k seam`

<!-- /torve:managed -->
