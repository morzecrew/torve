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

- Paths: `src/torve/cli/spec.py` `src/torve/cli/spec.py` `src/torve/config/spec_emit.py` `skills/**` `.torve/gates.yaml`
- Consequence: One namespace for the corpus; old prose and test file names keep the old word
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.2 — `LOCKED` (RFC 0058 — One grammar and the anatomy)

Every existing identifier converts once through a mapping committed as `.torve/archive/identifiers.yaml`, applied to the corpus, the archive, `src/`, `pages/`, `skills/`, the README, the local task files and the projections, with "RFC NNNN §n" converted to the section key at that position; a parity script gates the commit and is not committed

- Paths: `.torve/specs/**` `.torve/archive/**` `src/**` `pages/**` `skills/**` `README.md`
- Consequence: No legacy identifier stands anywhere the check reads; history keeps its words and resolves through the mapping
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### D-58.10 — `ASSUMED` (RFC 0058 — One grammar and the anatomy)

The skill and its template, the schemas `torve init` writes, the projections beside the code and the operating page follow the grammar and the anatomy in the same phase that changes them

- Paths: `skills/**` `src/torve/application/colocation.py` `src/torve/cli/init.py` `pages/docs/operating.md`
- Consequence: Nothing a harness or a person reads names an identifier the check refuses

<!-- /torve:managed -->
