<!-- torve:managed skills/hooks — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/hooks/`

### S-0072/D-1 — `LOCKED` (A hook is one intent, declared once per harness)

An equipment item carries its payload at the root and one directory per harness beside it; a harness reads only its own, and an item with no directory for a harness a seat could use is refused at load, naming the profile and the manifest

- Paths: `skills/hooks/**` `src/torve/config/agents.py`
- Consequence: the disagreement between two harnesses' idea of one kind becomes a message before an image is pulled, instead of an exception inside a container
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0072/D-2 — `LOCKED` (A hook is one intent, declared once per harness)

The judgement is one implementation both harnesses invoke; a per-harness declaration names how to reach it and never restates what it decides

- Paths: `skills/hooks/**`
- Consequence: a refusal cannot differ by harness, so an attempt's guard is a property of the role rather than of the seat that happened to run it
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0110/D-3 — `ASSUMED` (A second adopter runs torve from a tool install) — implementation: none

A repository installs torve as a uv tool, not as a project dependency, and every command torve writes or ships for a host or a sandbox calls bare `torve`: the stub `init` writes, the shipped hooks, and the operating docs and skill.

- Paths: `src/torve/cli/init.py` `skills/torve/**` `skills/hooks/**` `pages/docs/**`
- Consequence: torve's pins and an application's never meet in one lock

### S-0110/D-6 — `ASSUMED` (A second adopter runs torve from a tool install) — implementation: none

The pre-write, read and finish hooks and their `claude/settings.json` live in `skills/hooks/implement/` and ship in the wheel. A profile equips them as `source: torve:hooks/implement`, mounted as `implement`. torve's own implement profile and the starter implement profile equip them; the load-time hook check covers `torve:` items.

- Paths: `skills/hooks/**` `.torve/agents/*.yaml` `src/torve/cli/init.py` `src/torve/config/agents.py`
- Consequence: an upgrade brings its hooks, and no adopter carries a copy

<!-- /torve:managed -->
