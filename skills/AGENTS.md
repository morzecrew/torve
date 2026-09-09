<!-- torve:managed skills — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/`

### D-55.36 — `ASSUMED` (RFC 0055 — Standing decisions)

Skills ship as package data under `skills/` and are materialised role-scoped into the sandbox; a vendored skill lives under `.torve/skills-vendor/`; a name in both refuses, a name in neither is a configuration error

- Paths: `skills/**` `.torve/skills-vendor/**` `src/torve/application/skills.py`
- Consequence: What an agent knows is versioned and named, never ambient

### D-56.8 — `ASSUMED` (RFC 0056 — Structure for everything)

`SKILL.md`, `AGENTS.md`, the colocated sections and the pack are unchanged: projections rendered from the model, read by harnesses and people

- Paths: `skills/**` `src/torve/application/colocation.py`
- Consequence: Nothing a harness reads changes shape

### D-57.4 — `LOCKED` (RFC 0057 — The specification is a directory)

`torve spec` absorbs every `torve rfc` verb and `cli/rfc.py` is deleted; the gate is `spec-valid`; the skill is `spec-writer`; `rfc_emit.py` becomes `spec_emit.py`; new prose says specification or document

- Paths: `src/torve/cli/spec.py` `src/torve/cli/rfc.py` `src/torve/config/rfc_emit.py` `skills/**` `.torve/gates.yaml`
- Consequence: One namespace for the corpus; old prose and test file names keep the old word
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
