<!-- torve:managed src/torve/domain — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/domain/`

### S-0121/D-1 — `LOCKED` (Charter) — implementation: partial

Models never decide what work exists or whether it is finished: state transitions come only from recorded facts (exit codes, approvals, gate outcomes), the planner calls no model, a drafted or decomposed contract is data the deterministic lint admits or refuses, and what a critic's severity does is configuration (`review.blocks_at`), never the model's choice

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py` `src/torve/application/manager.py` `src/torve/application/planner.py` `src/torve/application/intake.py` `src/torve/application/review.py` `src/torve/domain/states.py`
- Check: `uv run pytest tests/test_intake.py::test_lint_decomposition_refuses_a_child_escaping_parent_scope tests/test_session.py::test_a_red_with_no_mapped_rung_stays_on_the_seat` (shadow; runs as `decision:S-0121/D-1`, no log entry owed)

### S-0121/D-4 — `LOCKED` (Charter) — implementation: partial

The append-only, typed event log is the system of record: every event is a kind of a closed vocabulary checked against `AUTHORITY` before any store sees it, no update command exists, every board and report is a projection rebuilt by replay that pages to the end, and an agent writes only `divergence.recorded` and `message.sent`, through the broker route that stamps its identity

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py` `src/torve/application/divergence.py` `src/torve/application/channel.py` `src/torve/adapters/broker/local.py` `src/torve/adapters/eventstore/**`
- Check: `uv run pytest tests/test_events.py::test_every_kind_has_an_authority_row_and_a_payload_model tests/test_eventlog.py::test_an_unauthorized_write_never_reaches_the_store tests/test_eventlog.py::test_the_spec_declares_no_update_command tests/test_channel.py::test_the_host_side_channel_stamps_identity_it_is_never_given tests/test_manager.py::test_a_replayed_log_rebuilds_the_same_board` (shadow; runs as `decision:S-0121/D-4`, no log entry owed)

### S-0121/D-14 — `LOCKED` (Charter) — implementation: partial

`EscalationReason` is one closed enum beside `EXIT_BY_REASON`: 0 ok, 1 gates red, 2 escalated, 3 configuration, 4 infrastructure, 5 exhausted, 6 the engine moved; a new reason needs a code, and a failure that measures nothing about the model escalates as infrastructure, never as a conviction

- Paths: `src/torve/domain/states.py` `src/torve/application/session.py` `src/torve/cli/options.py`
- Check: `uv run pytest tests/test_domain.py::test_every_escalation_reason_has_an_exit_code tests/test_run_loop.py -k "failed_prepare_convicts_nothing or refused_seat_escalates"` (shadow; runs as `decision:S-0121/D-14`, no log entry owed)

### S-0121/D-15 — `LOCKED` (Charter) — implementation: partial

Every YAML model checks `schema_version` through `base.model.SchemaVersion`, and an `engine:` range in `config.yaml` stops any configuration-loading command under a torve outside it

- Paths: `src/torve/base/model.py` `src/torve/config/runconfig.py` `src/torve/domain/task.py` `src/torve/domain/spec.py`
- Check: `uv run pytest tests/test_versions.py && uv run pytest tests/test_runconfig.py -k "engine_range"` (shadow; runs as `decision:S-0121/D-15`, no log entry owed)

### S-0121/D-16 — `ASSUMED` (Charter) — implementation: partial

The engine writes one instant format, `YYYY-MM-DDTHH:MM:SSZ`, from `base.clock.stamp()`, each closed vocabulary is defined once in `domain/vocabulary.py`, and every strict model uses `base.model.STRICT` with described fields, so `torve init` writes schemas an editor checks

- Paths: `src/torve/base/clock.py` `src/torve/base/model.py` `src/torve/domain/vocabulary.py`
- Check: `uv run pytest tests/test_clock.py::test_no_module_but_the_clock_spells_the_instant tests/test_spec.py::test_schema_descriptions_cover_every_property` (shadow; runs as `decision:S-0121/D-16`, no log entry owed)

### S-0121/D-21 — `LOCKED` (Charter) — implementation: partial

`torve plan` is deterministic: it mints from one accepted, committed document, copies each inherited row's id, grade, text and paths into the contract at write time, and derives task ids max+1, never reused; a contract naming no document inherits every accepted row whose paths intersect its scope, and a task in flight is never re-minted

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py` `src/torve/domain/spec.py`
- Check: `uv run pytest tests/test_plan.py::test_inherit_decisions_copies_the_rows_intact tests/test_plan.py::test_standing_decisions_intersect_in_and_out` (shadow; runs as `decision:S-0121/D-21`, no log entry owed)

### S-0121/D-33 — `ASSUMED` (Charter) — implementation: partial

The lane's verdicts on a commit are forge commit statuses keyed by its sha, a completing landing runs the blocking battery over the branch tip and leaves the pull request in draft unless it is green, and every lane battery is recorded as a `lane.judged` event

- Paths: `src/torve/application/lane.py` `src/torve/domain/events.py` `src/torve/cli/merge.py`
- Check: `uv run pytest tests/test_lane.py -k "red_battery_at_completion or keeps_the_draft"` (shadow; runs as `decision:S-0121/D-33`, no log entry owed)

### S-0121/D-36 — `LOCKED` (Charter) — implementation: partial

The engine never changes its own code under a running process: a chain whose `torve` package resolves under the repository root neither fast-forwards nor plans, and a fast-forward that crosses `night.engine_paths` makes `manager serve` exit 6 for its launcher to reinstall

- Paths: `src/torve/cli/manager.py` `src/torve/domain/states.py` `src/torve/config/runconfig.py`
- Check: `uv run pytest tests/test_manager.py -k "engine_moved or imports_from_the_root"` (shadow; runs as `decision:S-0121/D-36`, no log entry owed)

### S-0122/D-12 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

Contract v3 splits scope into `files`, `tests` (paired tests derived at mint and licensed), `fence` and `contingent`; only the fence convicts, edits outside files and tests become evidence, and a v2 contract loads mapped for one release

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py` `src/torve/application/neighbours.py` `src/torve/gates/scope.py`

### S-0122/D-13 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

A phasing file may set `unit: document` to mint one contract for the whole document, the default when it has three phases or fewer, and a contract carries its own document's rows plus at most ten LOCKED rows of other documents whose paths it touches

- Paths: `src/torve/application/planner.py` `src/torve/domain/spec.py`

### S-0122/D-14 — `ASSUMED` (Judge-and-select rebuild) — implementation: none

The divergence log keeps `halted`, written by `torve abort`, and `departed` with a proposal; a justified abort is never counted as a failure, and the `decided` and `resolved` attestations, `torve log owed` and the silence check are removed

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `src/torve/domain/spec.py`

<!-- /torve:managed -->
