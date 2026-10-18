<!-- torve:managed skills/torve/references — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/torve/references/`

### S-0110/D-11 — `ASSUMED` (A second adopter runs torve from a tool install)

`pages/docs/reference/stability.md` and the operator reference `upgrading.md` state the upgrade: install the release over the old one (`uv tool install --force` with the new exact version; `uv tool upgrade` keeps an exact pin) and raise `engine`, rebuild the seat images, run `torve init`, `torve migrate --all` when the release notes name a record migration, and `torve doctor`, then commit. A release that moves a `schema_version`, adds a record migration or changes the hooks carries an Upgrade note.

- Paths: `pages/docs/reference/stability.md` `README.md` `skills/torve/references/upgrading.md`
- Consequence: an adopter upgrades from one page, and a session from one reference

<!-- /torve:managed -->
