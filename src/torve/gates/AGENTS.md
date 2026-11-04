<!-- torve:managed src/torve/gates — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/gates/`

### S-0121/D-2 — `LOCKED` (Charter) — implementation: partial

Gates run outside the agent session, in a gates sandbox, against a standalone checkout of a snapshot commit the attempt left (`refs/torve/judge/<task>`), so an agent cannot report a gate outcome; no candidate code and no repository hook runs on the host, and every git command torve runs carries `core.hooksPath=/dev/null`

- Paths: `src/torve/gates/**` `src/torve/application/runner.py` `src/torve/application/lane.py` `src/torve/adapters/workspace/git.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/runtime/docker.py`
- Check: `uv run pytest tests/test_judge_checkout.py` (shadow; runs as `decision:S-0121/D-2`, no log entry owed)

### S-0121/D-24 — `LOCKED` (Charter) — implementation: partial

The secrets gate reads added lines and no bypass lifts it; any other blocking gate is lifted only by a person's `Torve-Bypass: <gate>: <reason>` trailer, recorded with its author and counted, and secrets false positives are suppressed only through the reviewed manifest

- Paths: `src/torve/gates/secrets.py` `src/torve/gates/runner.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_runner.py::test_bypass_is_appended_to_the_task_log tests/test_gates.py::test_secrets_allow_patterns_suppress_reviewed_false_positives` (shadow; runs as `decision:S-0121/D-24`, no log entry owed)

### S-0121/D-25 — `ASSUMED` (Charter) — implementation: partial

The blocking suite gate convicts only a test still red at the head and not red on the merge base after a rerun of the failing files; flaky and inherited reds pass, and inherited node ids ride the gate result

- Paths: `src/torve/gates/suite.py` `src/torve/gates/runner.py`
- Check: `uv run pytest tests/test_suite.py` (shadow; runs as `decision:S-0121/D-25`, no log entry owed)

### S-0121/D-26 — `ASSUMED` (Charter) — implementation: partial

The battery reports what it judged: outcomes are pass, fail, flaky, skipped, bypassed or error, a gate whose paths the diff misses is reported skipped and never omitted, a command that runs no test fails, and shadow gates report without touching the exit code

- Paths: `src/torve/gates/runner.py` `src/torve/gates/acceptance.py` `src/torve/config/manifest.py`
- Check: `uv run pytest tests/test_gates.py -k "judges_nothing_the_attempt_changed or acceptance" tests/test_runner.py::test_quarantined_gate_failure_does_not_gate` (shadow; runs as `decision:S-0121/D-26`, no log entry owed)

### S-0121/D-28 — `LOCKED` (Charter) — implementation: partial

The critic is independent of the author: it judges a diff composed before its disposable copy exists, never receives the author's trace or any model-written context, holds no forge credential, keeps nothing it writes, and a finding whose evidence does not locate is discarded unread

- Paths: `src/torve/application/review.py` `src/torve/gates/evidence.py`
- Check: `uv run pytest tests/test_review_run.py::test_the_review_input_is_composed_before_the_copy_exists tests/test_review_role.py::test_findings_with_unlocatable_evidence_are_discarded_and_counted` (shadow; runs as `decision:S-0121/D-28`, no log entry owed)

<!-- /torve:managed -->
