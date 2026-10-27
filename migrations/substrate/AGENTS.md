<!-- torve:managed migrations/substrate — rendered from the corpus; do not edit by hand -->

## Decisions governing `migrations/substrate/`

### S-0110/D-1 — `ASSUMED` (A second adopter runs torve from a tool install)

torve pins `forze==0.16.2`, in its dependencies, its extras and `migrations/substrate/FORZE_VERSION`, and the release that ships the bump bakes its seat images (S-0112/D-1).

- Paths: `pyproject.toml` `uv.lock` `migrations/substrate/FORZE_VERSION`
- Consequence: torve runs on the forze its adopters run, and the substrate pin check reads the new version

<!-- /torve:managed -->
