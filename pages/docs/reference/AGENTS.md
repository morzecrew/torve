<!-- torve:managed pages/docs/reference — rendered from the corpus; do not edit by hand -->

## Decisions governing `pages/docs/reference/`

### S-0095/D-6 — `ASSUMED` (A second repository can adopt torve)

`pages/docs/reference/stability.md` states the upgrade (install the release, `torve init`, commit, `torve doctor`, `torve migrate --all` when the notes name a record migration), and a release that moves a `schema_version` or adds a record migration carries an Upgrade note in its changelog entry

- Paths: `pages/docs/reference/stability.md` `README.md`
- Consequence: an adopter reads one page to upgrade, and a release cannot move a format silently

### S-0110/D-11 — `ASSUMED` (A second adopter runs torve from a tool install)

`pages/docs/reference/stability.md` and the operator reference `upgrading.md` state the upgrade: install the release over the old one (`uv tool install --force` with the new exact version; `uv tool upgrade` keeps an exact pin) and raise `engine`, rebuild the seat images, run `torve init`, `torve migrate --all` when the release notes name a record migration, and `torve doctor`, then commit. A release that moves a `schema_version`, adds a record migration or changes the hooks carries an Upgrade note.

- Paths: `pages/docs/reference/stability.md` `README.md` `skills/torve/references/upgrading.md`
- Consequence: an adopter upgrades from one page, and a session from one reference

<!-- /torve:managed -->
