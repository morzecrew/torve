<!-- torve:managed skills — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/`

### D-55.36 — `ASSUMED` (RFC 0055 — Standing decisions)

Skills ship as package data under `skills/` and are materialised role-scoped into the sandbox; a vendored skill lives under `.torve/skills-vendor/`; a name in both refuses, a name in neither is a configuration error

- Paths: `skills/**` `.torve/skills-vendor/**` `src/torve/application/skills.py`
- Consequence: What an agent knows is versioned and named, never ambient

<!-- /torve:managed -->
