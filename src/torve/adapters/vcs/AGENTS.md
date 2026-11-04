<!-- torve:managed src/torve/adapters/vcs — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/vcs/`

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`, no log entry owed)

### S-0121/D-8 — `LOCKED` (Charter) — implementation: partial

No sandbox holds a real credential: the host-side broker injects provider keys at its own boundary and hands the sandbox a run-scoped token, routes and meters providers, refuses requests past the run's budget, keeps counts never bodies and opens last; configuration names variables, never values; the forge token stays on the host and reaches git through a credential helper, never on argv

- Paths: `src/torve/adapters/broker/**` `src/torve/application/dispatch.py` `src/torve/config/providers.py` `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_broker.py::test_a_brokered_seat_is_handed_no_provider_key tests/test_broker.py::test_the_broker_keeps_counts_and_metadata_never_bodies tests/test_dispatch.py::test_a_brokered_dispatch_hands_the_sandbox_no_provider_key` (shadow; runs as `decision:S-0121/D-8`, no log entry owed)

### S-0121/D-30 — `LOCKED` (Charter) — implementation: partial

The landing route is configured, never inferred: under `pull_request` the lane lands candidates onto `torve/S-NNNN` cut from the remote base, pushes under lease, opens or refreshes one pull request and stops; it never moves the base, never resolves a conflict, never lands without the configured approval, and `promotion.auto_merge` defaults off

- Paths: `src/torve/application/lane.py` `src/torve/config/runconfig.py` `src/torve/cli/merge.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py::test_a_conflict_escalates_the_run_and_leaves_the_branch_for_a_human tests/test_residency.py::test_the_switch_is_off_by_default_and_an_unarmed_pass_never_lands tests/test_runconfig.py::test_auto_merge_with_no_criterion_armed_is_refused_naming_what_to_set` (shadow; runs as `decision:S-0121/D-30`, no log entry owed)

### S-0121/D-32 — `ASSUMED` (Charter) — implementation: partial

The lane works from the remote's copy of a document branch, fetched with prune: commits it did not make are rebased onto, a conflict escalates, and every push leases on the exact commit it fetched

- Paths: `src/torve/application/lane.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py -k "hand_commit or merged_while_no_pass_ran"` (shadow; runs as `decision:S-0121/D-32`, no log entry owed)

### S-0121/D-34 — `LOCKED` (Charter) — implementation: partial

The agent produces code and the runner produces every forge artefact: commits are authored by the agent identity and committed by torve, pull-request bodies and replies are composed from records, never from agent prose, and the base is never force-pushed

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/forge.py` `src/torve/application/runner.py`
- Check: `uv run pytest tests/test_forge.py::test_the_pr_is_composed_from_records_never_prose tests/test_provenance.py::test_the_commit_is_authored_by_the_agent_and_committed_by_torve` (shadow; runs as `decision:S-0121/D-34`, no log entry owed)

<!-- /torve:managed -->
