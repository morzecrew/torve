A committed file is a function of committed inputs: what the record derives — a count, a rate, a recent history — reaches its reader through the pack or through a verb, never through a file a gate diffs against a fresh render, because a clean clone must render the committed half identically or the drift check judges a number nobody wrote (S-0070/D-1, LOCKED).

An image shuts its own door: the worktree arrives carrying `.mcp.json`, `.claude/skills`, `.agents/skills` and an `AGENTS.md` per directory, none of which any profile declared, and a sandbox reads only what its profile declared (S-0066/D-1, LOCKED). A fourth definition is expected to close one too, with whatever switch its harness has — the engine composes no flag for this and there is no environment variable to set. The three that exist: claude narrows `--setting-sources` to `user`, which shuts the project tree while leaving the equipment root under `$HOME` open, and hands `--strict-mcp-config` an empty `--mcp-config` so `.mcp.json` is ignored and a declared server is not; dsh patches its instruction-file candidate list empty and narrows its skill roots to the one the engine named, because dsh has one configuration channel and everything travels it; mimo disables the four skill roots it reads that the engine did not name, one disable per root, since the root equipment is copied into is a root as well. Probe a switch in the built image before declaring it — a flag that is accepted and does nothing moves the regime digest without moving the behaviour, which is worse than not setting it, and one of the two knobs measured for this did exactly that.

<!-- torve:managed sandboxes — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/`

### S-0062/D-9 — `LOCKED` (Equipment is declared, and the harness is told how to take it)

S-0061/D-5 is superseded by D-1 and S-0061/D-6's renderer retires; the harness is told about a plugin by its own flag, not by files torve writes into its state.

- Paths: `sandboxes/**` `src/torve/cli/sandbox.py` `src/torve/application/ports.py`
- Consequence: torve stops keeping a second copy of a harness's internal bookkeeping in step with it across versions
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

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

### S-0063/D-19 — `LOCKED` (The image knows how to equip itself)

Equipment never writes into a path the repository owns. A harness that reads equipment from the workspace declares its own root on the manifest as `equip_root`; the engine excludes that root in the worktree and names it to the image as `TORVE_EQUIP_ROOT`.

- Paths: `src/torve/config/agents.py` `src/torve/application/session.py` `sandboxes/**` `.torve/harnesses/**`
- Consequence: an attempt commits its own work and nothing else, and a repository's reviewed skills are never overwritten by a packaged copy of the same name
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-7 — `LOCKED` (A provider is a record, and the seam carries scalars)

Every parameter crosses the seam as a scalar in torve's own vocabulary and units, and the image assembles its harness's representation from them; `DSH_MODEL` retires.

- Paths: `src/torve/adapters/agent/harness.py` `sandboxes/**`
- Consequence: one uniform set of names reaches three harnesses, and a unit or a spelling that only makes sense to one of them is converted in the file that knows which one it is
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0064/D-8 — `LOCKED` (A provider is a record, and the seam carries scalars)

Brokered and direct differ by the value of `TORVE_BASE_URL` and `TORVE_API_KEY` and never by a variable's presence; `TORVE_BROKER_URL` and `TORVE_BROKER_TOKEN` retire and no image tests whether a broker is in force.

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/session.py` `sandboxes/**`
- Consequence: nine broker branches across three definitions go, and mimo becomes brokerable by deletion rather than by implementation
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

## Invariants holding over `sandboxes/`

- **S-0063/I-2**: Every image definition answers `/opt/torve/equip` and `/opt/torve/run`, and none carries its own copy of the CLI layer.
  - Paths: `sandboxes/**`
  - Check: `uv run pytest tests/test_sandbox_defs.py -k seam`
- **S-0064/I-2**: No sandbox definition tests whether a broker is in force.
  - Paths: `sandboxes/**`
  - Check: `uv run pytest tests/test_sandbox_defs.py -k broker`

<!-- /torve:managed -->
