<!-- torve:managed src/torve/gates — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/gates/`

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`)

### S-0121/D-24 — `LOCKED` (Charter) — implementation: partial

The secrets gate reads added lines and no bypass lifts it; any other blocking gate is lifted only by a person's `Torve-Bypass: <gate>: <reason>` trailer, recorded with its author and counted, and secrets false positives are suppressed only through the reviewed manifest

- Paths: `src/torve/gates/secrets.py` `src/torve/gates/runner.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_runner.py::test_bypass_is_appended_to_the_task_log tests/test_gates.py::test_secrets_allow_patterns_suppress_reviewed_false_positives` (shadow; runs as `decision:S-0121/D-24`)

### S-0121/D-25 — `ASSUMED` (Charter) — implementation: partial

The blocking suite gate convicts only a test still red at the head and not red on the merge base after a rerun of the failing files; flaky and inherited reds pass, and inherited node ids ride the gate result

- Paths: `src/torve/gates/suite.py` `src/torve/gates/runner.py`
- Check: `uv run pytest tests/test_suite.py` (shadow; runs as `decision:S-0121/D-25`)

### S-0121/D-26 — `ASSUMED` (Charter) — implementation: partial

The battery reports what it judged: outcomes are pass, fail, flaky, skipped, bypassed or error, a gate whose paths the diff misses is reported skipped and never omitted, a command that runs no test fails, and shadow gates report without touching the exit code

- Paths: `src/torve/gates/runner.py` `src/torve/gates/acceptance.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_gates.py -k "judges_nothing_the_attempt_changed or acceptance" tests/test_runner.py::test_quarantined_gate_failure_does_not_gate` (shadow; runs as `decision:S-0121/D-26`)

### S-0121/D-28 — `LOCKED` (Charter) — implementation: partial

The critic is independent of the author: it judges a diff composed before its disposable copy exists, never receives the author's trace or any model-written context, holds no forge credential, keeps nothing it writes, and a finding whose evidence does not locate is discarded unread

- Paths: `src/torve/application/review.py` `src/torve/gates/evidence.py`
- Check: `uv run pytest tests/test_review_run.py::test_the_review_input_is_composed_before_the_copy_exists tests/test_review_role.py::test_findings_with_unlocatable_evidence_are_discarded_and_counted` (shadow; runs as `decision:S-0121/D-28`)

### S-0122/D-1 — `LOCKED` (Judge-and-select rebuild) — implementation: partial

The engine, not a model, turns a critic's findings into block or pass: a candidate is blocked when a finding that survives the location filter is at or above `review.blocks_at`, is not `weak_test`, and its repro exits non-zero when the engine re-runs it against the candidate in a gates sandbox, or when an owned row is `violated` and its evidence's command fails so; a `gaming` finding at or above `review.blocks_at` and a `violated` verdict on a LOCKED row block on located evidence alone; every other finding and row is recorded advisory, and an `unknown` row never blocks (S-0127/D-1, S-0127/D-2); on a revision attempt such a finding counts only when it also locates within `threads.WINDOW` lines of a line the revision changed or of an earlier round's blocker, and every other is recorded non-blocking; on a review round's attempt, a finding or `violated` row whose every cited `path:line` locates within `threads.WINDOW` lines of a claim that attempt rejected (S-0126/D-2) is recorded non-blocking, and a re-raise of the claim escalates (S-0121/D-29)

- Paths: `src/torve/application/review.py` `src/torve/application/critic.py` `src/torve/gates/evidence.py`

### S-0122/D-2 — `LOCKED` (Judge-and-select rebuild) — implementation: partial

The executor reaches the judge only through its tree and one `torve abort` record: every status, reading and message in the evidence package is produced by the host or a throwaway gates sandbox, and commit messages, pull-request text, log entries and traces never reach the critic

- Paths: `src/torve/gates/evidence_package.py` `src/torve/application/review.py` `src/torve/application/critic.py`

### S-0122/D-3 — `LOCKED` (Judge-and-select rebuild) — implementation: partial

Protected paths (tests written for the unit by anyone but its executor, `conftest.py`, pytest configuration, `.torve/**` and the gate manifest) are mounted read-only in the executor sandbox, an edit to one blocks, and evidence runs use the base versions of test-harness files unless the contract licenses a change

- Paths: `src/torve/adapters/runtime/docker.py` `src/torve/application/session.py` `src/torve/gates/no_test_tampering.py`

### S-0122/D-4 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The attempt battery judges code only: secrets, scope, no-test-tampering, lint, layering, mypy, the suite with attribution and the task's acceptance; a seat's finish command applies the formatter's fixes before the tree leaves the sandbox, and a contract row's check runs at document completion, not on every attempt

- Paths: `.torve/gates.yaml` `src/torve/gates/runner.py` `src/torve/gates/scope.py` `src/torve/application/session.py`

### S-0122/D-5 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

The evidence package covers only the rows a unit owns, those whose paths meet its files or tests, plus LOCKED rows its diff touches as constraints; each owned row gets a status from base and head runs, and the package is capped at 24 KB

- Paths: `src/torve/gates/evidence_package.py`

### S-0122/D-12 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

Contract v3 splits scope into `files`, `tests` (paired tests derived at mint and licensed), `fence` and `contingent`; only the fence convicts, edits outside files and tests become evidence, and a v2 contract loads mapped for one release

- Paths: `src/torve/domain/task.py` `src/torve/application/planner.py` `src/torve/application/neighbours.py` `src/torve/gates/scope.py`

### S-0122/D-17 — `ASSUMED` (Judge-and-select rebuild) — implementation: partial

Evals and arms, shadow arms, fleet, standing work, notify, the opensandbox runtime, serve and the web dashboard, survey, the verify role with held-out and verify-tests, the mutation, coverage-delta, decisions-reported, self-audit, user-facing-text, source-layout, red-on-base and row-evidence gates, the evidence matrix and the mimo, codex and opencode sandboxes are deleted, and a removed configuration key is accepted with a warning for one release

- Paths: `src/torve/application/evals.py` `src/torve/application/shadow.py` `src/torve/application/fleet.py` `src/torve/application/standing.py` `src/torve/application/notify.py` `src/torve/application/survey.py` `src/torve/application/evidence_matrix.py` `src/torve/gates/mutation.py` `src/torve/gates/held_out.py` `src/torve/gates/verify_tests.py` `src/torve/gates/decisions_reported.py` `src/torve/gates/red_on_base.py` `src/torve/gates/row_evidence.py` `web/**` `sandboxes/mimo/**`

### S-0127/D-3 — `ASSUMED` (A verdict comes from what ran) — implementation: none

`locate` accepts evidence that carries, anywhere in its text, a `path:line` or `path:start-end` citation that resolves under the root, as `evidence.citations` reads them with the grammar `critic._citations` uses today, or a backticked command followed by output; evidence holding neither is refused with today's messages, and `filter_findings` and the execution log's evidence check, which share `locate`, both accept a citation that follows prose

- Paths: `src/torve/gates/evidence.py` `src/torve/gates/log_entry.py`
- Consequence: a probe or mutation sentence citing the line it changed survives the location filter; a divergence entry whose citation follows prose is accepted too, and the log's teaching rejection no longer asks for a leading citation
- Check: `uv run pytest -q tests/test_review_role.py tests/test_gates.py -k locates_anywhere` (shadow; runs as `decision:S-0127/D-3`)

<!-- /torve:managed -->
