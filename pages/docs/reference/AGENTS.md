<!-- torve:managed pages/docs/reference — rendered from the corpus; do not edit by hand -->

## Decisions governing `pages/docs/reference/`

### S-0095/D-6 — `ASSUMED` (A second repository can adopt torve) — implementation: none

`pages/docs/reference/stability.md` states the upgrade (install the release, `torve init`, commit, `torve doctor`, `torve migrate --all` when the notes name a record migration), and a release that moves a `schema_version` or adds a record migration carries an Upgrade note in its changelog entry

- Paths: `pages/docs/reference/stability.md` `README.md`
- Consequence: an adopter reads one page to upgrade, and a release cannot move a format silently

<!-- /torve:managed -->
