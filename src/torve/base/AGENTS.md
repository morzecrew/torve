<!-- torve:managed src/torve/base — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/base/`

### S-0058/D-7 — `LOCKED` (One grammar and the anatomy)

The engine writes one instant, `YYYY-MM-DDTHH:MM:SSZ` in UTC, from `torve.base.clock.stamp()`, for amendments, landings, entries, telemetry, run state and their display; the dates that exist convert once to midnight UTC with the loss stated

- Paths: `src/torve/base/clock.py` `src/torve/domain/spec.py` `src/torve/application/telemetry.py` `src/torve/application/runstate.py` `src/torve/application/decisions.py` `src/torve/application/projections.py` `src/torve/cli/spec.py` `src/torve/cli/decisions.py`
- Consequence: A timeline over amendments, landings and entries sorts on one string
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

<!-- /torve:managed -->
