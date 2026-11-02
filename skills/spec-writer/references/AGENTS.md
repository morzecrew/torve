<!-- torve:managed skills/spec-writer/references — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/spec-writer/references/`

### S-0119/D-13 — `ASSUMED` (Issues become drafts, and a phase sees its callers) — implementation: none

The spec-writer skill tells an authoring session to search each phase's callers before acceptance, one delegated search per phase where the session can delegate. It then settles every reach line `torve spec check` prints, by moving the file into a scope, adding its test to an acceptance command, or saying in the phase's intent why it stays out.

- Paths: `skills/spec-writer/SKILL.md` `skills/spec-writer/references/workflows.md`
- Consequence: a reach line left standing is a choice the intent records, so a reviewer can tell it from an oversight

<!-- /torve:managed -->
