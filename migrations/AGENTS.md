<!-- torve:managed migrations — rendered from the corpus; do not edit by hand -->

## Decisions governing `migrations/`

### D-55.43 — `ASSUMED` (RFC 0055 — Standing decisions)

Migrations are owner-grouped, forward-only SQL under `migrations/`; the substrate is pinned by `FORZE_VERSION`, which `torve doctor` enforces and `config_hash` digests

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/doctor.py`
- Consequence: A substrate surface change fails at the pin bump, not in production

<!-- /torve:managed -->
