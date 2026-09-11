<!-- torve:managed sandboxes/dsh — rendered from the corpus; do not edit by hand -->

## Decisions governing `sandboxes/dsh/`

### S-0063/D-17 — `LOCKED` (The image knows how to equip itself)

dsh's `--patch` configures an entry the profile already carries and cannot add one, so equipment that would add a plugin installs it first and patches it after.

- Paths: `sandboxes/dsh/**`
- Consequence: an item naming a plugin dsh has not got is refused where it is declared, instead of failing at boot with `patch: entry "..." not found` inside an attempt
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
