<!-- torve:managed skills/torve/references — rendered from the corpus; do not edit by hand -->

## Decisions governing `skills/torve/references/`

### S-0110/D-11 — `ASSUMED` (A second adopter runs torve from a tool install)

`pages/docs/reference/stability.md` and the operator reference `upgrading.md` state the upgrade: install the release over the old one (`uv tool install --force` with the new exact version; `uv tool upgrade` keeps an exact pin) and raise `engine`, rebuild the seat images, run `torve init`, `torve migrate --all` when the release notes name a record migration, and `torve doctor`, then commit. A release that moves a `schema_version`, adds a record migration or changes the hooks carries an Upgrade note.

- Paths: `pages/docs/reference/stability.md` `README.md` `skills/torve/references/upgrading.md`
- Consequence: an adopter upgrades from one page, and a session from one reference

### S-0112/D-8 — `ASSUMED` (A release publishes its seat images) — implementation: none

The adoption and upgrading references and the configuration reference say to pull the published images, name them with `{torve}`, and derive from `ghcr.io/morzecrew/sandbox-base`.

- Paths: `skills/torve/references/**` `pages/docs/**`
- Consequence: an adopter's first image step is a pull, not a copy

### S-0116/D-13 — `ASSUMED` (An independent verifier) — implementation: none

The operating guide and the torve skill's night reference say how to turn the pilot on, read a held-out failure and handle a disputed test, and the spec-writer guide asks a phase's intent to name the public surface its tests drive.

- Paths: `pages/docs/operating.md` `skills/torve/references/night.md` `skills/spec-writer/**`
- Consequence: a phase written for a verifier names what to test, so the verifier does not invent an API

<!-- /torve:managed -->
