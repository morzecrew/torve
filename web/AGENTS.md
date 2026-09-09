<!-- torve:managed web — rendered from the corpus; do not edit by hand -->

## Decisions governing `web/`

### S-0055/D-55 — `ASSUMED` (Standing decisions)

The web bundle is vendored under `src/torve/_web/` and the release job rebuilds it from `web/` and fails when the committed bundle differs

- Paths: `src/torve/_web/**` `web/**` `.github/workflows/publish.yml`
- Consequence: A stale bundle cannot ship silently

<!-- /torve:managed -->
