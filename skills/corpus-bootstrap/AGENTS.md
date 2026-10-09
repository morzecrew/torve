<!-- torve:managed skills/corpus-bootstrap — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/corpus-bootstrap/`

### S-0100/D-9 — `ASSUMED` (A session learns torve from the torve it runs) — implementation: none

corpus-bootstrap becomes the operator skill's `references/adopting.md`, written for the YAML corpus, and its fixture moves under `tests/`; `.torve/skills-vendor/reading-isnt-proof` and `runconfig.ROLE_SKILLS` are deleted.

- Paths: `skills/corpus-bootstrap/**` `skills/torve/**` `.torve/skills-vendor/**` `src/torve/config/runconfig.py`
- Consequence: onboarding a repository is taught in the format the engine reads, by the skill that teaches the rest of operating it

<!-- /torve:managed -->
