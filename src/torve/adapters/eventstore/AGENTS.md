<!-- torve:managed src/torve/adapters/eventstore — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/eventstore/`

### S-0121/D-4 — `LOCKED` (Charter) — implementation: partial

The append-only, typed event log is the system of record: every event is a kind of a closed vocabulary checked against `AUTHORITY` before any store sees it, no update command exists, every board and report is a projection rebuilt by replay that pages to the end, and an agent writes only `divergence.recorded` and `message.sent`, through the broker route that stamps its identity

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py` `src/torve/application/divergence.py` `src/torve/application/channel.py` `src/torve/adapters/broker/local.py` `src/torve/adapters/eventstore/**`
- Check: `uv run pytest tests/test_events.py::test_every_kind_has_an_authority_row_and_a_payload_model tests/test_eventlog.py::test_an_unauthorized_write_never_reaches_the_store tests/test_eventlog.py::test_the_spec_declares_no_update_command tests/test_channel.py::test_the_host_side_channel_stamps_identity_it_is_never_given tests/test_manager.py::test_a_replayed_log_rebuilds_the_same_board` (shadow; runs as `decision:S-0121/D-4`)

<!-- /torve:managed -->
