<!-- torve:managed src/torve/cli — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/cli/`

### S-0121/D-6 — `ASSUMED` (Charter) — implementation: partial

The MCP surface serves projections and the per-task pack as read-only tools, registers no tool that writes, and is never wired into a sandbox

- Paths: `src/torve/cli/mcp.py`
- Check: `uv run pytest tests/test_mcp.py` (shadow; runs as `decision:S-0121/D-6`)

### S-0121/D-13 — `ASSUMED` (Charter) — implementation: partial

Every result-producing verb offers `--format json` in the persisted record's shape, results go to stdout and diagnostics to stderr, no verb prompts interactively, and presentation lives only in `cli/`

- Paths: `src/torve/cli/**`

### S-0121/D-14 — `LOCKED` (Charter) — implementation: partial

`EscalationReason` is one closed enum beside `EXIT_BY_REASON`: 0 ok, 1 gates red, 2 escalated, 3 configuration, 4 infrastructure, 5 exhausted, 6 the engine moved; a new reason needs a code, and a failure that measures nothing about the model escalates as infrastructure, never as a conviction

- Paths: `src/torve/domain/states.py` `src/torve/application/session.py` `src/torve/cli/options.py`
- Check: `uv run pytest tests/test_domain.py::test_every_escalation_reason_has_an_exit_code tests/test_run_loop.py -k "failed_prepare_convicts_nothing or refused_seat_escalates"` (shadow; runs as `decision:S-0121/D-14`)

### S-0121/D-20 — `LOCKED` (Charter) — implementation: partial

Torve owns the DDL for every relation it uses as owner-grouped, forward-only, checksummed SQL under `migrations/`, and the substrate group pins an exact forze version that `pyproject.toml` matches and `doctor` enforces

- Paths: `migrations/**` `src/torve/application/migrate.py` `src/torve/cli/migrate.py` `pyproject.toml`
- Check: `uv run pytest tests/test_migrate.py` (shadow; runs as `decision:S-0121/D-20`)

### S-0121/D-23 — `ASSUMED` (Charter) — implementation: partial

Every spec-authoring verb is one parse, mutate, emit and check transaction through the single writer in `config/spec_emit.py` that aborts whole on a red check, and verbs write structure and identifiers, never a chosen grade, prose or status

- Paths: `src/torve/config/spec_emit.py` `src/torve/cli/spec.py`
- Check: `uv run pytest tests/test_rfc_verbs.py` (shadow; runs as `decision:S-0121/D-23`)

### S-0121/D-30 — `LOCKED` (Charter) — implementation: partial

The landing route is configured, never inferred: under `pull_request` the lane lands candidates onto `torve/S-NNNN` cut from the remote base, pushes under lease, opens or refreshes one pull request and stops; it never moves the base, never resolves a conflict, never lands without the configured approval, and `promotion.auto_merge` defaults off

- Paths: `src/torve/application/lane.py` `src/torve/config/runconfig.py` `src/torve/cli/merge.py` `src/torve/adapters/vcs/git.py`
- Check: `uv run pytest tests/test_lane.py::test_a_conflict_escalates_the_run_and_leaves_the_branch_for_a_human tests/test_residency.py::test_the_switch_is_off_by_default_and_an_unarmed_pass_never_lands tests/test_runconfig.py::test_auto_merge_with_no_criterion_armed_is_refused_naming_what_to_set` (shadow; runs as `decision:S-0121/D-30`)

### S-0121/D-33 — `ASSUMED` (Charter) — implementation: partial

The lane's verdicts on a commit are forge commit statuses keyed by its sha, a completing landing runs the blocking battery over the branch tip and leaves the pull request in draft unless it is green, and every lane battery is recorded as a `lane.judged` event

- Paths: `src/torve/application/lane.py` `src/torve/domain/events.py` `src/torve/cli/merge.py`
- Check: `uv run pytest tests/test_lane.py -k "red_battery_at_completion or keeps_the_draft"` (shadow; runs as `decision:S-0121/D-33`)

### S-0121/D-35 — `LOCKED` (Charter) — implementation: partial

Accepting a document, grading or amending a LOCKED row, merging a pull request, resolving a person's review thread, force-pushing and deleting a branch are the owner's acts, and the shipped operator skill says so

- Paths: `skills/torve/SKILL.md` `src/torve/cli/init.py`

### S-0121/D-36 — `LOCKED` (Charter) — implementation: partial

The engine never changes its own code under a running process: a chain whose `torve` package resolves under the repository root neither fast-forwards nor plans, and a fast-forward that crosses `night.engine_paths` makes `manager serve` exit 6 for its launcher to reinstall

- Paths: `src/torve/cli/manager.py` `src/torve/domain/states.py` `src/torve/config/runconfig.py`
- Check: `uv run pytest tests/test_manager.py -k "engine_moved or imports_from_the_root"` (shadow; runs as `decision:S-0121/D-36`)

### S-0121/D-37 — `ASSUMED` (Charter) — implementation: partial

A night is a subject in the event log whose terms are recorded whole at open; a chained night plans only from a clean base at the remote tip, budgets bound a UTC day, and a failing pass leg is recorded and the pass carries on

- Paths: `src/torve/application/residency.py` `src/torve/cli/manager.py`
- Check: `uv run pytest tests/test_residency.py -k "day or axis or broken_leg or night_with_nothing_to_start"` (shadow; runs as `decision:S-0121/D-37`)

### S-0122/D-14 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The divergence log keeps `halted`, written by `torve abort`, and `departed` with a proposal; a justified abort is never counted as a failure, and the `decided` and `resolved` attestations, `torve log owed` and the silence check are removed

- Paths: `src/torve/application/divergence.py` `src/torve/cli/log.py` `src/torve/domain/spec.py`

### S-0123/D-7 — `ASSUMED` (What the first 0.9.0 run left)

`torve spec archive` carries the document's `execution/` directory into the archive unchanged, in the same transaction as the document's own files

- Paths: `src/torve/cli/spec.py` `src/torve/config/spec_emit.py`
- Consequence: a lane or planner that reads an archived document's landings still finds them
- Check: `uv run pytest -q tests/test_rfc_archive.py -k execution` (shadow; runs as `decision:S-0123/D-7`)

### S-0124/D-10 — `ASSUMED` (Candidates side by side, and S-0122's revision)

A document is complete when its landings cover every phase the root's phasing or the phasing at the ref being landed names; an absent phasing names none, a phasing that is present but cannot be read or validated leaves the document incomplete, and the pull request lane, the local lane and the publisher's draft flag read the same union

- Paths: `src/torve/application/forge.py` `src/torve/application/lane.py` `src/torve/cli/merge.py`
- Consequence: a phase added on the branch keeps the document a draft until it lands, and the root's phases still count when the branch was cut before an amendment
- Check: `uv run pytest -q tests/test_lane.py tests/test_forge.py -k phasing_union` (shadow; runs as `decision:S-0124/D-10`)

<!-- /torve:managed -->

## The night verb, beside the manager's

`torve manager serve --night` opens a night and `torve night show` reads one
back. They are the two halves of one mechanism, and neither stores anything:
the terms are recorded on `night.opened` at the open (S-0079/D-2) and the
report is a projection of the window, computed on every call and stored
nowhere (S-0079/D-4).

- `serve --night` reads the configuration's `night:` section once, at the open,
  refuses a board with nothing startable (S-0079/D-6), and stops on a budget,
  on the wall-clock end or on the first escalation of a class the terms name
  (S-0079/D-7). Every bound is soft: an attempt in flight finishes, and the
  close is written even when the end fell inside a pass (S-0079/D-12).
- `night show <partition>` folds the window into landed, convicted, ended and
  waiting-on-a-person. `--night` picks one by id; omitted takes the most
  recent open. A night with an open and no close reads as unfinished rather
  than as lost, which is what a manager killed at 04:00 leaves behind.

The report holds no field prose can occupy, so no line of it is a model's
account of its own night. Adding one is the way that rule is broken.

What the knobs cost is written where they are set, `.torve/config.yaml`; what
a night seat runs under is declared in `.torve/harnesses/` as seat `env`
(S-0079/D-8), and torve interprets none of those three variables (S-0079/D-9).
