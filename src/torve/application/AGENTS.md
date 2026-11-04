<!-- torve:managed src/torve/application — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/application/`

### S-0121/D-1 — `LOCKED` (Charter) — implementation: partial

Models never decide what work exists or whether it is finished: state transitions come only from recorded facts (exit codes, approvals, gate outcomes), the planner calls no model, a drafted or decomposed contract is data the deterministic lint admits or refuses, and what a critic's severity does is configuration (`review.blocks_at`), never the model's choice

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py` `src/torve/application/manager.py` `src/torve/application/planner.py` `src/torve/application/intake.py` `src/torve/application/review.py` `src/torve/domain/states.py`
- Check: `uv run pytest tests/test_intake.py::test_lint_decomposition_refuses_a_child_escaping_parent_scope tests/test_session.py::test_a_red_with_no_mapped_rung_stays_on_the_seat` (shadow; runs as `decision:S-0121/D-1`, no log entry owed)

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`, no log entry owed)

### S-0121/D-3 — `LOCKED` (Charter) — implementation: partial

Runner configuration, the gate manifest and a task's contract are read from the root the runner launched in (the manifest at the attempt's merge base), never from the worktree an agent wrote, so no agent writes configuration its own run consumes

- Paths: `src/torve/config/layout.py` `src/torve/config/runconfig.py` `src/torve/config/manifest.py` `src/torve/application/dispatch.py` `src/torve/application/runner.py`
- Check: `uv run pytest tests/test_run_loop.py::test_worktree_config_edits_never_reach_dispatch` (shadow; runs as `decision:S-0121/D-3`, no log entry owed)

### S-0121/D-4 — `LOCKED` (Charter) — implementation: partial

The append-only, typed event log is the system of record: every event is a kind of a closed vocabulary checked against `AUTHORITY` before any store sees it, no update command exists, every board and report is a projection rebuilt by replay that pages to the end, and an agent writes only `divergence.recorded` and `message.sent`, through the broker route that stamps its identity

- Paths: `src/torve/domain/events.py` `src/torve/application/eventlog.py` `src/torve/application/divergence.py` `src/torve/application/channel.py` `src/torve/adapters/broker/local.py` `src/torve/adapters/eventstore/**`
- Check: `uv run pytest tests/test_events.py::test_every_kind_has_an_authority_row_and_a_payload_model tests/test_eventlog.py::test_an_unauthorized_write_never_reaches_the_store tests/test_eventlog.py::test_the_spec_declares_no_update_command tests/test_channel.py::test_the_host_side_channel_stamps_identity_it_is_never_given tests/test_manager.py::test_a_replayed_log_rebuilds_the_same_board` (shadow; runs as `decision:S-0121/D-4`, no log entry owed)

### S-0121/D-5 — `LOCKED` (Charter) — implementation: partial

No decision reads a host-local stream: every reader of `.torve/telemetry.jsonl` lives in `application/streams.py` and serves reports only, and a committed artefact is a function of committed inputs alone

- Paths: `src/torve/application/streams.py` `src/torve/application/telemetry.py` `src/torve/application/ledger.py` `src/torve/application/projections.py` `src/torve/application/colocation.py`
- Check: `uv run pytest tests/test_layout.py -k stream tests/test_colocation.py::test_a_telemetry_stream_present_or_absent_renders_the_same_bytes` (shadow; runs as `decision:S-0121/D-5`, no log entry owed)

### S-0121/D-7 — `LOCKED` (Charter) — implementation: partial

Agents and their harnesses run only inside a sandbox, with no harness SDK in the engine process; the engine never builds an image mid-run, and mounting the host Docker socket is an explicit per-repository opt-in

- Paths: `src/torve/application/session.py` `src/torve/adapters/runtime/docker.py` `src/torve/adapters/agent/**`
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0121/D-8 — `LOCKED` (Charter) — implementation: partial

No sandbox holds a real credential: the host-side broker injects provider keys at its own boundary and hands the sandbox a run-scoped token, routes and meters providers, refuses requests past the run's budget, keeps counts never bodies and opens last; configuration names variables, never values; the forge token stays on the host and reaches git through a credential helper, never on argv

- Paths: `src/torve/adapters/broker/**` `src/torve/application/dispatch.py` `src/torve/config/providers.py` `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_broker.py::test_a_brokered_seat_is_handed_no_provider_key tests/test_broker.py::test_the_broker_keeps_counts_and_metadata_never_bodies tests/test_dispatch.py::test_a_brokered_dispatch_hands_the_sandbox_no_provider_key` (shadow; runs as `decision:S-0121/D-8`, no log entry owed)

### S-0121/D-10 — `ASSUMED` (Charter) — implementation: partial

An attempt's inputs are only what its seat declared: equipment is fetched host-side before the sandbox exists and mounted read-only, never written into a path the repository owns, and each image's `run` shuts out worktree files written for other readers (`AGENTS.md`, `CLAUDE.md`, `.claude/skills`, `.agents/skills`, `.mcp.json`) before adding its own

- Paths: `src/torve/config/equipment.py` `src/torve/application/equipment.py` `sandboxes/claude/toolkit/**` `sandboxes/dsh/toolkit/**`
- Check: `uv run pytest tests/test_equipment.py tests/test_session.py -k "host_side or read_only or equipment_root"` (shadow; runs as `decision:S-0121/D-10`, no log entry owed)

### S-0121/D-14 — `LOCKED` (Charter) — implementation: partial

`EscalationReason` is one closed enum beside `EXIT_BY_REASON`: 0 ok, 1 gates red, 2 escalated, 3 configuration, 4 infrastructure, 5 exhausted, 6 the engine moved; a new reason needs a code, and a failure that measures nothing about the model escalates as infrastructure, never as a conviction

- Paths: `src/torve/domain/states.py` `src/torve/application/session.py` `src/torve/cli/options.py`
- Check: `uv run pytest tests/test_domain.py::test_every_escalation_reason_has_an_exit_code tests/test_run_loop.py -k "failed_prepare_convicts_nothing or refused_seat_escalates"` (shadow; runs as `decision:S-0121/D-14`, no log entry owed)

### S-0121/D-18 — `ASSUMED` (Charter) — implementation: partial

The attempt loop is `drive_attempts` over `AttemptHooks` with one typed `Dispatch`, and new steps plug into the hooks, so the deterministic simulation drives the loop that ships

- Paths: `src/torve/application/runner.py` `src/torve/application/dispatch.py`
- Check: `uv run pytest tests/test_dst.py` (shadow; runs as `decision:S-0121/D-18`, no log entry owed)

### S-0121/D-19 — `LOCKED` (Charter) — implementation: partial

Git holds what should be and the store holds what happened: `TaskStore` is a thin facade over forze's leased queue, the manager is one resident process that rebuilds its board from the record each pass, a lease expires when no activity is recorded, and the repository is the partition key

- Paths: `src/torve/application/taskstore.py` `src/torve/application/manager.py` `src/torve/application/worker.py` `src/torve/adapters/store/**`
- Check: `uv run pytest tests/test_taskstore.py tests/test_manager.py::test_a_claim_that_has_gone_silent_past_its_lease_is_expired` (shadow; runs as `decision:S-0121/D-19`, no log entry owed)

### S-0121/D-20 — `LOCKED` (Charter) — implementation: partial

Torve owns the DDL for every relation it uses as owner-grouped, forward-only, checksummed SQL under `migrations/`, and the substrate group pins an exact forze version that `pyproject.toml` matches and `doctor` enforces

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/migrate.py` `pyproject.toml`
- Check: `uv run pytest tests/test_migrate.py` (shadow; runs as `decision:S-0121/D-20`, no log entry owed)

### S-0121/D-21 — `LOCKED` (Charter) — implementation: partial

`torve plan` is deterministic: it mints from one accepted, committed document, copies each inherited row's id, grade, text and paths into the contract at write time, and derives task ids max+1, never reused; a contract naming no document inherits every accepted row whose paths intersect its scope, and a task in flight is never re-minted

- Paths: `src/torve/application/planner.py` `src/torve/domain/task.py` `src/torve/domain/spec.py`
- Check: `uv run pytest tests/test_plan.py::test_inherit_decisions_copies_the_rows_intact tests/test_plan.py::test_standing_decisions_intersect_in_and_out` (shadow; runs as `decision:S-0121/D-21`, no log entry owed)

### S-0121/D-22 — `ASSUMED` (Charter) — implementation: partial

A phase's acceptance commands must be runnable where they are judged, and the mint-time lint refuses one that needs host credentials, operational state, git or a Docker daemon the configuration does not nest

- Paths: `src/torve/application/intake.py`
- Check: `uv run pytest tests/test_intake.py -k docker` (shadow; runs as `decision:S-0121/D-22`, no log entry owed)

### S-0121/D-27 — `LOCKED` (Charter) — implementation: partial

Two tasks whose scopes intersect are never in flight together, agents never communicate with each other, and the runner composes each attempt's context host-side from that task's own facts only

- Paths: `src/torve/application/manager.py` `src/torve/application/dispatch.py` `src/torve/application/contextpack.py`
- Check: `uv run pytest tests/test_manager.py::test_tasks_in_flight_hold_their_scope_against_new_dispatch tests/test_manager.py::test_two_wildcard_scopes_that_share_a_path_fence_each_other` (shadow; runs as `decision:S-0121/D-27`, no log entry owed)

### S-0121/D-28 — `LOCKED` (Charter) — implementation: partial

The critic is independent of the author: it judges a diff composed before its disposable copy exists, never receives the author's trace or any model-written context, holds no forge credential, keeps nothing it writes, and a finding whose evidence does not locate is discarded unread

- Paths: `src/torve/application/review.py` `src/torve/gates/evidence.py`
- Check: `uv run pytest tests/test_review_run.py::test_the_review_input_is_composed_before_the_copy_exists tests/test_review_role.py::test_findings_with_unlocatable_evidence_are_discarded_and_counted` (shadow; runs as `decision:S-0121/D-28`, no log entry owed)

### S-0121/D-29 — `LOCKED` (Charter) — implementation: partial

Forge thread text is an untrusted claim: it reaches an attempt only inside a nonce-delimited fence, a thread asking for anything but a change to in-scope files is refused as injection, the leg never merges, pushes or resolves a person's thread, and a re-raised finding escalates

- Paths: `src/torve/application/reviewleg.py` `src/torve/application/threads.py`
- Check: `uv run pytest tests/test_reviewleg.py -k "nonce or inject or reraised or persons"` (shadow; runs as `decision:S-0121/D-29`, no log entry owed)

### S-0121/D-30 — `LOCKED` (Charter) — implementation: partial

The landing route is configured, never inferred: under `pull_request` the lane lands candidates onto `torve/S-NNNN` cut from the remote base, pushes under lease, opens or refreshes one pull request and stops; it never moves the base, never resolves a conflict, never lands without the configured approval, and `promotion.auto_merge` defaults off

- Paths: `src/torve/application/lane.py` `src/torve/config/runconfig.py` `src/torve/cli/merge.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py::test_a_conflict_escalates_the_run_and_leaves_the_branch_for_a_human tests/test_residency.py::test_the_switch_is_off_by_default_and_an_unarmed_pass_never_lands tests/test_runconfig.py::test_auto_merge_with_no_criterion_armed_is_refused_naming_what_to_set` (shadow; runs as `decision:S-0121/D-30`, no log entry owed)

### S-0121/D-31 — `LOCKED` (Charter) — implementation: partial

A task is landed exactly when a landing file for it sits in the base tree or on the remote tip of its document branch; landing files are written once per attempt and never deleted, and `projections.landed` is the one answer every reader uses

- Paths: `src/torve/application/projections.py` `src/torve/application/decisions.py` `src/torve/application/lane.py` `src/torve/application/residency.py`
- Check: `uv run pytest tests/test_projections.py -k landed tests/test_residency.py::test_a_contract_that_already_landed_is_minted_as_landed` (shadow; runs as `decision:S-0121/D-31`, no log entry owed)

### S-0121/D-32 — `ASSUMED` (Charter) — implementation: partial

The lane works from the remote's copy of a document branch, fetched with prune: commits it did not make are rebased onto, a conflict escalates, and every push leases on the exact commit it fetched

- Paths: `src/torve/application/lane.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py -k "hand_commit or merged_while_no_pass_ran"` (shadow; runs as `decision:S-0121/D-32`, no log entry owed)

### S-0121/D-33 — `ASSUMED` (Charter) — implementation: partial

The lane's verdicts on a commit are forge commit statuses keyed by its sha, a completing landing runs the blocking battery over the branch tip and leaves the pull request in draft unless it is green, and every lane battery is recorded as a `lane.judged` event

- Paths: `src/torve/application/lane.py` `src/torve/domain/events.py` `src/torve/cli/merge.py`
- Check: `uv run pytest tests/test_lane.py -k "red_battery_at_completion or keeps_the_draft"` (shadow; runs as `decision:S-0121/D-33`, no log entry owed)

### S-0121/D-34 — `LOCKED` (Charter) — implementation: partial

The agent produces code and the runner produces every forge artefact: commits are authored by the agent identity and committed by torve, pull-request bodies and replies are composed from records, never from agent prose, and the base is never force-pushed

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/forge.py` `src/torve/application/runner.py`
- Check: `uv run pytest tests/test_forge.py::test_the_pr_is_composed_from_records_never_prose tests/test_provenance.py::test_the_commit_is_authored_by_the_agent_and_committed_by_torve` (shadow; runs as `decision:S-0121/D-34`, no log entry owed)

### S-0121/D-37 — `ASSUMED` (Charter) — implementation: partial

A night is a subject in the event log whose terms are recorded whole at open; a chained night plans only from a clean base at the remote tip, budgets bound a UTC day, and a failing pass leg is recorded and the pass carries on

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Check: `uv run pytest tests/test_residency.py -k "day or axis or broken_leg or night_with_nothing_to_start"` (shadow; runs as `decision:S-0121/D-37`, no log entry owed)

### S-0121/D-38 — `LOCKED` (Charter) — implementation: partial

The issue intake leg is off until `intake.issue_label` names a label, drafts only an issue whose label a triager applied and whose text nobody edited since, hands the drafter only the title and body, and never writes to the forge

- Paths: `src/torve/application/issues.py` `src/torve/config/runconfig.py`
- Check: `uv run pytest tests/test_issues.py -k "off or triage or edited"` (shadow; runs as `decision:S-0121/D-38`, no log entry owed)

### S-0121/D-39 — `ASSUMED` (Charter) — implementation: partial

Every attempt appends exactly one telemetry row, however it ends, carrying the hash of the regime it ran under and facts only: absent counts stay absent, and an unpriced cost is unreported, never zero

- Paths: `src/torve/application/telemetry.py` `src/torve/application/runner.py` `src/torve/application/ledger.py`
- Check: `uv run pytest tests/test_runner.py::test_a_halted_attempt_appends_its_verdict_row tests/test_ledger.py::test_rates_are_per_seat_and_an_unpriced_seat_reports_unreported tests/test_dispatch.py::test_a_priced_seat_that_reported_no_counts_stays_unreported` (shadow; runs as `decision:S-0121/D-39`, no log entry owed)

### S-0121/D-40 — `ASSUMED` (Charter) — implementation: partial

Session traces live under `.torve/traces/`, are never committed, uploaded or put into a prompt, and never drive control flow; the context pack is computed host-side with no model from the task's own facts

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/application/reaper.py` `src/torve/application/contextpack.py`
- Check: `uv run pytest tests/test_reaper.py::test_retention_touches_nothing_within_both_bounds` (shadow; runs as `decision:S-0121/D-40`, no log entry owed)

<!-- /torve:managed -->
