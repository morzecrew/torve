<!-- torve:managed src/torve/config — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/config/`

### S-0121/D-3 — `LOCKED` (Charter) — implementation: partial

Runner configuration, the gate manifest and a task's contract are read from the root the runner launched in (the manifest at the attempt's merge base), never from the worktree an agent wrote, so no agent writes configuration its own run consumes

- Paths: `src/torve/config/layout.py` `src/torve/config/runconfig.py` `src/torve/config/manifest.py` `src/torve/application/dispatch.py` `src/torve/application/runner.py`
- Check: `uv run pytest tests/test_run_loop.py::test_worktree_config_edits_never_reach_dispatch` (shadow; runs as `decision:S-0121/D-3`)

### S-0121/D-8 — `LOCKED` (Charter) — implementation: partial

No sandbox holds a real credential: the host-side broker injects provider keys at its own boundary and hands the sandbox a run-scoped token, routes and meters providers, refuses requests past the run's budget, keeps counts never bodies and opens last; configuration names variables, never values; the forge token stays on the host and reaches git through a credential helper, never on argv

- Paths: `src/torve/adapters/broker/**` `src/torve/application/dispatch.py` `src/torve/config/providers.py` `src/torve/config/agents.py` `src/torve/adapters/agent/harness.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_broker.py::test_a_brokered_seat_is_handed_no_provider_key tests/test_broker.py::test_the_broker_keeps_counts_and_metadata_never_bodies tests/test_dispatch.py::test_a_brokered_dispatch_hands_the_sandbox_no_provider_key` (shadow; runs as `decision:S-0121/D-8`)

### S-0121/D-9 — `LOCKED` (Charter) — implementation: partial

Every sandbox image answers `/opt/torve/equip` and `/opt/torve/run`, and the engine talks to it only through flat `TORVE_*` scalars in its own vocabulary; brokered and direct runs differ only in values, no manifest carries a shell line, and a seat's `env` reaches the image as written with nothing under `src/` reading it

- Paths: `src/torve/adapters/agent/harness.py` `src/torve/config/agents.py` `sandboxes/base/**` `sandboxes/claude/**` `sandboxes/dsh/**`
- Check: `uv run pytest tests/test_sandbox_defs.py -k "seam or broker"` (shadow; runs as `decision:S-0121/D-9`)

### S-0121/D-10 — `ASSUMED` (Charter) — implementation: partial

An attempt's inputs are only what its seat declared: equipment is fetched host-side before the sandbox exists and mounted read-only, never written into a path the repository owns, and each image's `run` shuts out worktree files written for other readers (`AGENTS.md`, `CLAUDE.md`, `.claude/skills`, `.agents/skills`, `.mcp.json`) before adding its own

- Paths: `src/torve/config/equipment.py` `src/torve/application/equipment.py` `sandboxes/claude/toolkit/**` `sandboxes/dsh/toolkit/**`
- Check: `uv run pytest tests/test_equipment.py tests/test_session.py -k "host_side or read_only or equipment_root"` (shadow; runs as `decision:S-0121/D-10`)

### S-0121/D-11 — `LOCKED` (Charter) — implementation: partial

Each configuration key has one home: a profile says what the agent is, a harness how a model is reached, a provider the key name, routes and roster, and a seat which harness, profile, model and clocks; any other file refuses the key naming where it moved, configuration only appends to the working rules, and a seat is refused at load unless its roster lists its model and its harness speaks the route's dialect

- Paths: `src/torve/config/agents.py` `src/torve/config/runconfig.py` `src/torve/config/providers.py` `.torve/agents/**` `.torve/harnesses/**` `.torve/providers/**`
- Check: `uv run pytest tests/test_agents.py tests/test_runconfig.py -k "moved_key or may_not_name_a_prompt or roster_does_not_list or cannot_speak"` (shadow; runs as `decision:S-0121/D-11`)

### S-0121/D-15 — `LOCKED` (Charter) — implementation: partial

Every YAML model checks `schema_version` through `base.model.SchemaVersion`, and an `engine:` range in `config.yaml` stops any configuration-loading command under a torve outside it

- Paths: `src/torve/base/model.py` `src/torve/config/runconfig.py` `src/torve/domain/task.py` `src/torve/domain/spec.py`
- Check: `uv run pytest tests/test_versions.py && uv run pytest tests/test_runconfig.py -k "engine_range"` (shadow; runs as `decision:S-0121/D-15`)

### S-0121/D-23 — `ASSUMED` (Charter) — implementation: partial

Every spec-authoring verb is one parse, mutate, emit and check transaction through the single writer in `config/spec_emit.py` that aborts whole on a red check, and verbs write structure and identifiers, never a chosen grade, prose or status

- Paths: `src/torve/config/spec_emit.py` `src/torve/cli/spec.py`
- Check: `uv run pytest tests/test_rfc_verbs.py` (shadow; runs as `decision:S-0121/D-23`)

### S-0121/D-24 — `LOCKED` (Charter) — implementation: partial

The secrets gate reads added lines and no bypass lifts it; any other blocking gate is lifted only by a person's `Torve-Bypass: <gate>: <reason>` trailer, recorded with its author and counted, and secrets false positives are suppressed only through the reviewed manifest

- Paths: `src/torve/gates/secrets.py` `src/torve/gates/runner.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_runner.py::test_bypass_is_appended_to_the_task_log tests/test_gates.py::test_secrets_allow_patterns_suppress_reviewed_false_positives` (shadow; runs as `decision:S-0121/D-24`)

### S-0121/D-26 — `ASSUMED` (Charter) — implementation: partial

The battery reports what it judged: outcomes are pass, fail, flaky, skipped, bypassed or error, a gate whose paths the diff misses is reported skipped and never omitted, a command that runs no test fails, and shadow gates report without touching the exit code

- Paths: `src/torve/gates/runner.py` `src/torve/gates/acceptance.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_gates.py -k "judges_nothing_the_attempt_changed or acceptance" tests/test_runner.py::test_quarantined_gate_failure_does_not_gate` (shadow; runs as `decision:S-0121/D-26`)

### S-0121/D-30 — `LOCKED` (Charter) — implementation: partial

The landing route is configured, never inferred: under `pull_request` the lane lands candidates onto `torve/S-NNNN` cut from the remote base, pushes under lease, opens or refreshes one pull request and stops; it never moves the base, never resolves a conflict, never lands without the configured approval, and `promotion.auto_merge` defaults off

- Paths: `src/torve/application/lane.py` `src/torve/config/runconfig.py` `src/torve/cli/merge.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py::test_a_conflict_escalates_the_run_and_leaves_the_branch_for_a_human tests/test_residency.py::test_the_switch_is_off_by_default_and_an_unarmed_pass_never_lands tests/test_runconfig.py::test_auto_merge_with_no_criterion_armed_is_refused_naming_what_to_set` (shadow; runs as `decision:S-0121/D-30`)

### S-0121/D-36 — `LOCKED` (Charter) — implementation: partial

The engine never changes its own code under a running process: a chain whose `torve` package resolves under the repository root neither fast-forwards nor plans, and a fast-forward that crosses `night.engine_paths` makes `manager serve` exit 6 for its launcher to reinstall

- Paths: `src/torve/cli/manager.py` `src/torve/domain/states.py` `src/torve/config/runconfig.py`
- Check: `uv run pytest tests/test_manager.py -k "engine_moved or imports_from_the_root"` (shadow; runs as `decision:S-0121/D-36`)

### S-0121/D-38 — `LOCKED` (Charter) — implementation: partial

The issue intake leg is off until `intake.issue_label` names a label, drafts only an issue whose label a triager applied and whose text nobody edited since, hands the drafter only the title and body, and never writes to the forge

- Paths: `src/torve/application/issues.py` `src/torve/config/runconfig.py`
- Check: `uv run pytest tests/test_issues.py -k "off or triage or edited"` (shadow; runs as `decision:S-0121/D-38`)

### S-0122/D-7 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

A unit's class sets its width (S one candidate, M two, L three, never more), stragglers are cut at 1.5 times the first green finisher's wall time capped at 15 minutes, identical normalised diffs collapse, and a deterministic pre-rank orders the rest before any critic call

- Paths: `src/torve/application/selection.py` `src/torve/config/runconfig.py`

### S-0122/D-21 — `OPEN` (Judge-and-select rebuild) — implementation: partial

What makes intake demand a document now that one charter is the only standing document: the `document_required` arm counts LOCKED rows from distinct documents and can no longer fire

- Paths: `src/torve/application/intake.py` `src/torve/config/runconfig.py`

### S-0123/D-7 — `ASSUMED` (What the first 0.9.0 run left)

`torve spec archive` carries the document's `execution/` directory into the archive unchanged, in the same transaction as the document's own files

- Paths: `src/torve/cli/spec.py` `src/torve/config/spec_emit.py`
- Consequence: a lane or planner that reads an archived document's landings still finds them
- Check: `uv run pytest -q tests/test_rfc_archive.py -k execution` (shadow; runs as `decision:S-0123/D-7`)

### S-0124/D-2 — `ASSUMED` (Candidates side by side, and S-0122's revision)

Under `review.mode: critic` a surviving blocker spends `review.blocker_revisions` revision rounds when the configuration names it, else 2; classic mode keeps 1

- Paths: `src/torve/config/runconfig.py` `src/torve/application/review.py`
- Consequence: a unit gets the two rounds S-0122/D-8 promises before it escalates
- Check: `uv run pytest -q tests/test_runconfig.py tests/test_review_run.py -k revision_rounds` (shadow; runs as `decision:S-0124/D-2`)

### S-0124/D-8 — `ASSUMED` (Candidates side by side, and S-0122's revision)

Under `review.revision: session` a revision round continues the selected candidate in its own still-open session, on that candidate's tier and not `revision_variant`, with the blocking findings, after the same spend and clock checks a fresh attempt passes, then judges its gates and asks the critic again, in place of a fresh revision attempt; `fresh` is the default, and a seat whose harness keeps no session falls back to it

- Paths: `src/torve/application/runner.py` `src/torve/application/session.py` `src/torve/config/runconfig.py`
- Consequence: the night can run the in-session arm S-0122/D-9 asks about, one configuration key away
- Check: `uv run pytest -q tests/test_runner.py tests/test_runconfig.py -k session_revision` (shadow; runs as `decision:S-0124/D-8`)

### S-0125/D-3 — `ASSUMED` (A unit that converges) — implementation: none

Every review round carries at most `threads.findings_per_round` findings, whether its head was triaged or not, and the default is 5; `batch_claims` still never splits a file across rounds

- Paths: `src/torve/application/reviewleg.py` `src/torve/config/runconfig.py`
- Consequence: fifteen bot findings become three or more small rounds; a critic block in one leaves the others' fixes to land
- Check: `uv run pytest -q tests/test_reviewleg.py tests/test_runconfig.py -k findings_per_round` (shadow; runs as `decision:S-0125/D-3`)

### S-0125/D-8 — `ASSUMED` (A unit that converges) — implementation: none

A tier's `context_window` keeps the value its seat states and is merged off the roster only when the seat states none

- Paths: `src/torve/config/runconfig.py`
- Consequence: a seat can hand its harness a smaller window than the model's, which a harness that compacts against its window compacts sooner
- Check: `uv run pytest -q tests/test_runconfig.py tests/test_providers.py -k context_window` (shadow; runs as `decision:S-0125/D-8`)

### S-0125/D-9 — `OPEN` (A unit that converges) — implementation: none

Whether dsh compacts against the context window torve passes and at what fraction of it, read from the image's `dsh-compaction-basic`, and which window the night's dsh seats state; the phase records the answer in its log

- Paths: `src/torve/config/runconfig.py`
- Consequence: if dsh does not compact against the passed window, D-8 bounds nothing for dsh and a per-attempt token budget comes back into scope

<!-- /torve:managed -->
