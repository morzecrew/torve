<!-- torve:managed src/torve/adapters/broker — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/broker/`

### S-0121/D-4 — `LOCKED` (Charter) — implementation: partial

The append-only, typed event log is the system of record: every event is a kind of a closed vocabulary checked against `AUTHORITY` before any store sees it, no update command exists, every board and report is a projection rebuilt by replay that pages to the end, and an agent writes only `divergence.recorded` and `message.sent`, through the broker route that stamps its identity

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py` `src/torve/application/divergence.py` `src/torve/application/channel.py` `src/torve/adapters/broker/local.py` `src/torve/adapters/eventstore/**`
- Check: `uv run pytest tests/test_events.py::test_every_kind_has_an_authority_row_and_a_payload_model tests/test_eventlog.py::test_an_unauthorized_write_never_reaches_the_store tests/test_eventlog.py::test_the_spec_declares_no_update_command tests/test_channel.py::test_the_host_side_channel_stamps_identity_it_is_never_given tests/test_manager.py::test_a_replayed_log_rebuilds_the_same_board` (shadow; runs as `decision:S-0121/D-4`)

### S-0121/D-8 — `LOCKED` (Charter) — implementation: partial

No sandbox holds a real credential: the host-side broker injects provider keys at its own boundary and hands the sandbox a run-scoped token, routes and meters providers, refuses requests past the run's budget, keeps counts never bodies and opens last; configuration names variables, never values; the forge token stays on the host and reaches git through a credential helper, never on argv

- Paths: `src/torve/adapters/broker/**` `src/torve/application/dispatch.py` `src/torve/config/providers.py` `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_broker.py::test_a_brokered_seat_is_handed_no_provider_key tests/test_broker.py::test_the_broker_keeps_counts_and_metadata_never_bodies tests/test_dispatch.py::test_a_brokered_dispatch_hands_the_sandbox_no_provider_key` (shadow; runs as `decision:S-0121/D-8`)

<!-- /torve:managed -->
