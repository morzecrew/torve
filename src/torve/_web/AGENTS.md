<!-- torve:managed src/torve/_web — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/_web/`

### D-55.55 — `ASSUMED` (RFC 0055 — Standing decisions)

The web bundle is vendored under `src/torve/_web/` and the release job rebuilds it from `web/` and fails when the committed bundle differs

- Paths: `src/torve/_web/**` `web/**` `.github/workflows/publish.yml`
- Consequence: A stale bundle cannot ship silently

<!-- /torve:managed -->
