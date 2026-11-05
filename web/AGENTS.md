<!-- torve:managed web — rendered from the corpus; do not edit by hand -->

## Decisions governing `web/`

### S-0122/D-17 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

Evals and arms, shadow arms, fleet, standing work, notify, the opensandbox runtime, serve and the web dashboard, survey, the verify role with held-out and verify-tests, the mutation, coverage-delta, decisions-reported, self-audit, user-facing-text, source-layout, red-on-base and row-evidence gates, the evidence matrix and the mimo, codex and opencode sandboxes are deleted, and a removed configuration key is accepted with a warning for one release

- Paths: `src/torve/application/evals.py` `src/torve/application/shadow.py` `src/torve/application/fleet.py` `src/torve/application/standing.py` `src/torve/application/notify.py` `src/torve/application/survey.py` `src/torve/application/evidence_matrix.py` `src/torve/gates/mutation.py` `src/torve/gates/held_out.py` `src/torve/gates/verify_tests.py` `src/torve/gates/decisions_reported.py` `src/torve/gates/red_on_base.py` `src/torve/gates/row_evidence.py` `web/**` `sandboxes/mimo/**`

<!-- /torve:managed -->
