from __future__ import annotations

from pathlib import Path

import pytest
from conftest import context_for

from torve.config.manifest import Gate
from torve.gates.decisions_reported import check_decisions_reported, parse_log
from torve.gates.sabotage import TASK_ID, base_task, entry, log_document
from torve.gates.scope import check_scope
from torve.gates.secrets import check_secrets

GATE = Gate(name="test", run="@scope", state="blocking", origin="structural")  # any handle


def test_a_contract_may_name_both_its_document_and_its_source(tmp_path):
    """S-0060/D-3: `spec` says whose rows it inherits, `source` says what
    asked; a contract may carry both, either or neither, and a source the
    grammar refuses does not load."""

    import yaml
    from pydantic import ValidationError

    from torve.gates.context import load_task

    def contract(**extra) -> dict:
        return {"schema_version": 2, "id": "T-0001", "decisions": [], **extra}

    path = tmp_path / "contract.yaml"

    for record in (
        contract(spec="S-0060", source="audit/soc2-2026"),
        contract(source="audit/soc2-2026"),
        contract(spec="S-0060"),
        contract(),
    ):
        path.write_text(yaml.safe_dump(record), encoding="utf-8")
        loaded = load_task(path)

        assert loaded.source == record.get("source")
        assert loaded.spec == record.get("spec")

    path.write_text(yaml.safe_dump(contract(source="specification/s-0060")), encoding="utf-8")

    with pytest.raises((ValueError, ValidationError)):
        load_task(path)


def test_scope_implicitly_allows_task_and_log_files(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("src/app.py", "print('in scope')\n")
    repo.commit("task branch")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_scope_untracked_file_is_visible(repo):
    """A stray new file that was never committed must still redden scope."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.commit("task branch")
    (repo.root / "stray.txt").write_text("uncommitted\n", encoding="utf-8")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "stray.txt" in result.output


def test_the_patch_carries_an_untracked_file(repo):
    """The reviewer reads the gate pass's patch, so a file the attempt created
    and never staged has to appear there as an added file — `git diff` alone
    leaves it out, and a review then judges the module absent."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.commit("task branch")
    (repo.root / "src" / "fresh.py").write_text("VALUE = 1\n", encoding="utf-8")
    patch = context_for(repo).patch
    assert "diff --git a/src/fresh.py b/src/fresh.py" in patch
    assert "new file mode" in patch
    assert "+VALUE = 1" in patch


def test_secrets_reports_file_and_line(repo):
    repo.seed()
    repo.write("src/config.py", "# comment\nkey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n")
    repo.commit("leak")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "src/config.py:2: aws access key id" in result.output


def test_secrets_allow_patterns_suppress_reviewed_false_positives(repo):
    manifest = {
        "schema_version": 1,
        "secrets": {"allow_patterns": ["EXAMPLE'"]},
        "gates": [
            {"name": "secrets", "run": "@secrets", "state": "blocking", "origin": "structural"}
        ],
    }
    repo.seed(manifest)
    repo.write("src/config.py", "key = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n")
    repo.commit("documented example key")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_secrets_scans_untracked_files(repo):
    repo.seed()
    (repo.root / "notes.txt").write_text("token " + "AKIA" + "IOSFODNN7EXAMPLE" + "\n", "utf-8")
    result = check_secrets(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "notes.txt:1" in result.output


def test_decisions_skill_style_entry_is_accepted(repo):
    """flag-dont-flip logs use `class` instead of `kind`; both vocabularies
    pass, per the S-0001/D-26 reconciliation."""
    skill_entry = entry(
        decision="unlisted",
        grade="UNLISTED",
        kind=None,  # class-only, skill style
        claim="spec silent on retry budget",
        action="decided",
    )
    skill_entry["class"] = "spec-gap"
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=[]), log_document(skill_entry))
    repo.write("src/app.py", "print('x')\n")
    repo.commit("skill-style log")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "fail"  # unlisted owes a proposal
    assert "owes a proposal" in result.output

    with_proposal = dict(skill_entry, proposal="ASSUMED — retries capped at 3")
    repo.write(f".torve/tasks/{TASK_ID}/log.yaml", log_document(with_proposal))
    repo.commit("proposal added")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_decisions_drift_count_must_match_entries(repo):
    drift_entry = entry(
        decision="D-1",
        grade="LOCKED",
        kind="contradicted",
        action="halted",
        claim="built otherwise anyway",
    )
    drift_entry["class"] = "drift"
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=[]),
        log_document(drift_entry),  # declared count stays 0
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("drifted")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "drift count 0 != 1" in result.output


@pytest.mark.parametrize(
    ("evidence", "defect"),
    [
        ("src/app.py:1 the guard", "separator missing after the citation"),
        (
            "src/app.py:1; src/app.py:2 — the guard",
            "multiple semicolon-joined citations where prose belongs",
        ),
        ("no redis service in this deployment", "no citation at all"),
    ],
)
def test_decisions_evidence_rejection_teaches_the_repair(repo, evidence, defect):
    """T-0203: the grammar rejection quotes the offending line, prints the
    expected grammar, and diagnoses the first defect — the judgement itself
    is unchanged (still a rejection)."""
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=[]),
        log_document(entry(evidence=evidence)),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("unlocatable evidence")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert f"evidence {evidence!r} is not locatable" in result.output
    assert "expected: a single leading `path:line — one sentence` citation" in result.output
    assert defect in result.output


def test_decisions_evidence_path_rejection_keeps_the_locators_message(repo):
    """A locatable-format line pointing nowhere is not a grammar rejection —
    it keeps the locator's message; only the grammar rejection teaches."""
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=[]),
        log_document(entry(evidence="ghost.py:1")),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("evidence points nowhere")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "fail"
    assert "does not exist" in result.output
    assert "expected: a single leading" not in result.output


def test_decisions_evidence_with_separator_still_passes(repo):
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=[]),
        log_document(entry(evidence="src/app.py:1 — the guard")),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("citation with prose after the separator")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_decisions_empty_list_with_no_log_passes(repo):
    """decisions: [] means none apply, explicitly (S-0007/D-5)."""
    repo.seed()
    repo.write(f".torve/tasks/{TASK_ID}/contract.yaml", "id: " + TASK_ID + "\ndecisions: []\n")
    repo.write("src/app.py", "print('x')\n")
    repo.commit("no decisions apply")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass"


def test_a_later_blocking_gate_still_reports_after_an_earlier_one_fails(repo):
    """T-0234: the runner short-circuited every blocking gate after the
    first blocking failure, and it orders them cheapest-timeout-first — so a
    form gate failing in under a second hid the functional verdict behind
    it. `retry_rung_for` then saw one axis where the ladder assumes several,
    and S-0034/D-7's boundary masking could never fire from under a lighter
    gate. Nothing new blocks: the exit code was already 1."""

    from torve.gates.runner import run_gates

    manifest = {
        "schema_version": 1,
        "scope": {"allow": [], "deny": []},
        "gates": [
            # Ordered cheapest-first by the runner, so `cheap` runs first.
            {
                "name": "cheap",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 1,
                "origin": "structural",
            },
            {
                "name": "dear",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 900,
                "origin": "structural",
            },
        ],
    }
    repo.seed(manifest=manifest)
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(context_for(repo))
    outcomes = {r.name: r.outcome for r in report.results}

    # Both convictions are on the record, not just the cheap one.
    assert outcomes == {"cheap": "fail", "dear": "fail"}
    assert report.exit_code == 1
    assert not any("an earlier blocking gate failed" in (r.output or "") for r in report.results)


def test_a_gate_that_judges_nothing_the_attempt_changed_is_skipped(repo):
    """S-0071/D-6: `coverage-delta` measures `--cov=src` and spent 11,786 of its
    43,448 seconds on attempts that changed nothing under `src/`, seventeen of
    them going red over drift the attempt had not caused.

    Skipped, never passed: a gate that reports a pass it did not compute is the
    same fault as a cache whose key cannot move."""

    from torve.gates.runner import run_gates

    manifest = {
        "schema_version": 1,
        "scope": {"allow": [], "deny": []},
        "gates": [
            {
                "name": "judges-src",
                "run": "sh -c 'exit 1'",
                "state": "blocking",
                "timeout": 1,
                "origin": "structural",
                "paths": ["src/**"],
            },
            {
                "name": "judges-everything",
                "run": "sh -c 'exit 0'",
                "state": "blocking",
                "timeout": 2,
                "origin": "structural",
            },
        ],
    }
    repo.seed(manifest=manifest)
    repo.write("pages/docs/thing.md", "prose\n")
    repo.commit("nothing under src")

    outcomes = {r.name: r.outcome for r in run_gates(context_for(repo)).results}

    # It would have failed had it run, so a pass here would be a lie and a
    # green exit code would be one the battery had not earned.
    assert outcomes == {"judges-src": "skipped", "judges-everything": "pass"}
    assert run_gates(context_for(repo)).exit_code == 0

    # The same gate against a diff it does judge: it runs, and it convicts.
    repo.write("src/app.py", "print('x')\n")
    repo.commit("under src")
    report = run_gates(context_for(repo))

    assert {r.name: r.outcome for r in report.results}["judges-src"] == "fail"
    assert report.exit_code == 1


def test_decisions_no_paths_is_skipped_never_passed(repo):
    decisions = [{"id": "D-9", "grade": "LOCKED", "text": "an area-less lock", "paths": []}]
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=decisions), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("touch")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass"
    assert "skipped: D-9" in result.output


def test_repository_logs_parse_under_the_gate():
    # Every execution log this repository carries must satisfy its own gate's
    # parser (A-1 format; per-task directories per A-12).
    #
    # `.torve/tasks/` is gitignored, so a clean checkout carries none and this
    # has nothing to judge — it skips rather than failing. Asserting they exist
    # made the acceptance command of every task pass on a machine that happens
    # to hold leftover task directories and fail in the sandbox that does not,
    # which is a green that means nothing.
    root = Path(__file__).resolve().parent.parent
    logs = sorted((root / ".torve" / "tasks").glob("*/log.yaml"))

    if not logs:
        pytest.skip("no execution logs in this checkout — `.torve/tasks/` is not committed")

    for log in logs:
        document, error = parse_log(log.read_text(encoding="utf-8"))
        assert error is None, f"{log.name}: {error}"
        assert isinstance(document.get("drift_count"), int), f"{log.name}: no drift_count"
        assert document["entries"], f"{log.name}: an empty log would simply not exist (A-13)"


def test_run_gates_reports_progress_by_gate_name(repo):
    # S-0018/live-status-for-long-waits via T-0029: the live status names the gate it is inside —
    # one timer over a pass that is 95% acceptance explains nothing.
    from torve.gates.runner import run_gates

    repo.seed()
    repo.write("src/app.py", "print('progress')\n")
    repo.commit("change")
    seen: list[str] = []
    report = run_gates(context_for(repo), progress=seen.append)
    # Every gate runs now (T-0234), so every result announced itself.
    assert seen == [r.name for r in report.results]
    # (a degraded-mode gate runs and reports skipped — it still announces)
    assert "scope" in seen and "secrets" in seen


# ....................... #
# The {base} substitution: a shell command receives the battery's own
# computed base — the same context every builtin judges against — and never
# resolves a ref in shell itself.

ECHO_BASE_COMMAND = 'printf "%s" "{base}"'


def _single_gate_manifest(run: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "gates": [{"name": "g", "run": run, "state": "blocking", "origin": "S-0036"}],
    }


def test_a_shell_gate_receives_the_batterys_computed_base(repo):
    from torve.gates.runner import run_gates

    repo.seed(_single_gate_manifest(ECHO_BASE_COMMAND))
    repo.write("src/app.py", "print('substituted')\n")
    repo.commit("change")
    ctx = context_for(repo)
    report = run_gates(ctx, only={"g"})
    result = report.results[0]
    assert result.outcome == "pass", result.output
    # The SHA of the merge-base — the value every diff-input builtin judges
    # against — not the ref name it was resolved from.
    assert result.output.strip() == ctx.merge_base
    assert ctx.merge_base != ctx.base


def test_the_flaky_record_keeps_the_declared_command(repo):
    # An embedded SHA would make each run's flaky identity unique and
    # unquarantinable; the record is the declared command, placeholder intact.
    from torve.gates.runner import run_gates

    flaky = '[ -e .seen ] || { touch .seen; printf "%s" "{base}" > .base-leak; exit 1; }'
    repo.seed(_single_gate_manifest(flaky))
    report = run_gates(context_for(repo), only={"g"})
    result = report.results[0]
    assert result.outcome == "flaky", result.output
    assert result.flaky_commands == [flaky]
    assert report.flaky_count_by_command == {flaky: 1}


def test_a_base_requesting_gate_on_an_unresolvable_base_errors(tmp_path):
    # A fresh repository has no computed base; `{base}` has nothing honest to
    # stand for, and the gate errors instead of inventing a ref.
    from torve.config.manifest import Manifest
    from torve.gates.context import GateContext
    from torve.gates.runner import run_gates

    gate = Gate(name="g", run=ECHO_BASE_COMMAND, state="blocking", origin="S-0036")
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[gate]),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
    )
    report = run_gates(ctx, only={"g"})
    assert report.results[0].outcome == "error"
    assert "no base is resolvable" in report.results[0].output


def test_the_shipped_coverage_gate_judges_changed_lines_from_the_battery_base():
    # The manifest is configuration the battery reads; its shape is checked
    # here so a later edit cannot silently widen the judgment surface.
    from torve.config.manifest import load_manifest

    root = Path(__file__).resolve().parent.parent
    gates = {g.name: g for g in load_manifest(root / ".torve" / "gates.yaml").resolved_gates()}
    gate = gates["coverage-delta"]
    assert gate.state == "shadow"  # every gate enters shadow (S-0002/D-18)
    assert "{base}" in gate.run  # the battery's base, never a shell-resolved ref
    for ref_resolver in ("merge-base", "rev-parse", "origin/", "git diff"):
        assert ref_resolver not in gate.run


# ----------------------- #
# S-0054 phase 1: a decision with a check is a gate (S-0054/D-2, S-0054/D-4), and
# a row a gate covers owes no attestation (S-0054/D-3).


def _checked(check: str, state: str = "shadow", twin: str | None = None) -> list[dict]:
    return [
        {
            "id": "S-0009/D-1",
            "grade": "LOCKED",
            "text": "the check decides",
            "paths": ["src/**"],
            "check": check,
            "check_state": state,
            "check_twin": twin,
        }
    ]


def test_a_checkable_row_runs_as_a_shadow_gate_and_convicts_nothing(repo):
    from torve.gates.runner import run_gates

    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=_checked("sh -c 'exit 1'")), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(context_for(repo))
    by_name = {r.name: r for r in report.results}

    assert by_name["decision:S-0009/D-1"].outcome == "fail"
    assert by_name["decision:S-0009/D-1"].state == "shadow"
    assert by_name["decisions-reported"].outcome == "pass"  # covered by its check, no entry owed
    assert report.exit_code == 0


def test_a_promoted_checkable_row_blocks_when_its_check_is_red(repo):
    from torve.gates.runner import run_gates

    repo.seed()
    repo.task(
        base_task(
            allow=["src/**"],
            decisions=_checked("sh -c 'exit 1'", state="blocking", twin="tests/test_gates.py"),
        ),
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    report = run_gates(context_for(repo))
    gate = next(r for r in report.results if r.name == "decision:S-0009/D-1")

    assert gate.outcome == "fail" and gate.state == "blocking"
    assert report.exit_code == 1


def test_decision_gates_carry_their_row_as_origin_and_the_compliance_axis(repo):
    from torve.gates.runner import decision_gates

    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=_checked("true", twin="tests/test_x.py"))
        | {"spec": "S-0054"},
        log_document(),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")

    (gate,) = decision_gates(context_for(repo).task)

    assert gate.name == "decision:S-0009/D-1" and gate.run == "true"
    assert gate.origin == "S-0009/D-1" and gate.axis == "compliance"
    assert gate.sabotage == "tests/test_x.py" and gate.input == "worktree"


def test_a_row_with_a_check_is_covered_and_owes_no_entry():
    from torve.domain.task import InheritedDecision
    from torve.gates.decisions_reported import owed

    checked = InheritedDecision(
        id="S-0009/D-1", grade="LOCKED", text="x", paths=["src/**"], check="pytest tests/test_x.py"
    )
    silent = InheritedDecision(id="S-0009/D-2", grade="LOCKED", text="y", paths=["src/**"])

    problems, skipped = owed([checked, silent], ["src/app.py"], [])

    assert [p.split(":")[1].strip() for p in problems] == [
        "LOCKED, and the diff touches 1 file(s) it governs (src/app.py), with no entry in the log"
    ]
    assert "S-0009/D-2" in problems[0] and "S-0009/D-1" not in problems[0]
    assert skipped == ["S-0009/D-1: covered by its check, which runs as a gate"]


# ----------------------- #
# S-0057 S-0057/D-7: the landing file is the task's own, like its log


def test_scope_implicitly_allows_the_named_documents_execution_directory(repo):
    from dataclasses import replace

    from torve.gates.context import load_task

    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    contract = repo.root / ".torve" / "tasks" / TASK_ID / "contract.yaml"
    task = load_task(contract).model_copy(update={"spec": "S-0001"})
    repo.write(".torve/specs/S-0001/execution/T-0001-1-20260101T000000Z.yaml", "task: T-0001\n")
    repo.write(".torve/specs/S-0001/document.yaml", "id: '0001'\n")
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("the work and its landing")

    outcome = check_scope(GATE, replace(context_for(repo), task=task))

    assert "execution/" not in outcome.output
    # the author's file is not the task's, and stays outside allow
    assert ".torve/specs/S-0001/document.yaml" in outcome.output


# ----------------------- #
# S-0070/D-6: the acceptance verdict names the suite it judged — a battery
# that ran fewer tests than the tree holds reports how many and why, so
# `pass` never stands for two different suites.


def _acceptance_over(tmp_path, output: str, exit_code: int = 0):
    from torve.config.manifest import Manifest
    from torve.domain.task import Task
    from torve.gates.acceptance import check_acceptance
    from torve.gates.context import GateContext

    gate = Gate(name="acceptance", run="@task.acceptance", state="blocking", origin="structural")
    task = Task(id="T-0001", role="implement", decisions=[], acceptance=["uv run pytest"])
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[gate]),
        head_sha="",
        base=None,
        merge_base=None,
        task=task,
        execute=lambda command, timeout: (exit_code, output),
    )
    return check_acceptance(gate, ctx)


def test_acceptance_reports_how_many_tests_it_skipped_and_why(tmp_path):
    outcome = _acceptance_over(
        tmp_path,
        "SKIPPED [33] tests/test_runtime.py:12: no docker daemon\n"
        "120 passed, 33 skipped in 4.53s\n",
    )
    assert outcome.outcome == "pass"
    # the count leads the verdict, before any command log
    assert outcome.output.startswith("suite: 33 tests in the tree did not run")
    assert "suite: 120 of 153 tests ran, 33 skipped" in outcome.output
    assert "[33] tests/test_runtime.py:12: no docker daemon" in outcome.output


def test_acceptance_says_when_the_skips_carry_no_reason(tmp_path):
    # A command that was never asked for reasons has none to give; the gate
    # names that rather than inventing one (S-0070/D-3).
    outcome = _acceptance_over(tmp_path, "120 passed, 33 skipped in 4.53s\n")
    assert "33 skipped" in outcome.output
    assert "no reason reported" in outcome.output


def test_acceptance_names_a_whole_suite_and_flags_nothing(tmp_path):
    outcome = _acceptance_over(tmp_path, "153 passed in 4.53s\n")
    assert outcome.outcome == "pass"
    assert "suite: 153 of 153 tests ran, none skipped" in outcome.output
    assert "did not run" not in outcome.output


def test_acceptance_reports_an_empty_suite_rather_than_a_silent_green(tmp_path):
    outcome = _acceptance_over(tmp_path, "no tests ran in 0.01s\n")
    assert outcome.outcome == "pass"
    assert "no tests ran" in outcome.output


def test_acceptance_counts_deselected_tests_as_absent_too(tmp_path):
    outcome = _acceptance_over(tmp_path, "10 passed, 4 deselected in 0.30s\n")
    assert outcome.output.startswith("suite: 4 tests in the tree did not run")
    assert "4 deselected" in outcome.output


def test_acceptance_over_a_command_with_no_summary_says_nothing_about_a_suite(tmp_path):
    # A build or a linter is not a test suite; the gate does not invent counts.
    outcome = _acceptance_over(tmp_path, "Success: no issues found in 42 source files\n")
    assert "suite:" not in outcome.output


def test_acceptance_reports_the_suite_on_a_red_verdict_too(tmp_path):
    outcome = _acceptance_over(tmp_path, "1 failed, 119 passed, 33 skipped in 4.53s\n", exit_code=1)
    assert outcome.outcome == "fail"
    assert "33 skipped" in outcome.output


# ----------------------- #
# `red-on-base`: a test that is green against the base tree proved nothing
# about the change under it (S-0081/D-2, S-0081/D-3, S-0081/D-4).

RED_ON_BASE_GATE = Gate(
    name="red-on-base", run="@red-on-base", state="shadow", origin="S-0081", timeout=120
)


@pytest.fixture
def red_on_base(monkeypatch):
    """The gate with its runner pointed at this interpreter, so the scratch
    repository's suite runs without the project's own launcher."""

    import shlex
    import sys

    from torve.gates import red_on_base as module

    launcher = f"PYTHONPATH=src {shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
    monkeypatch.setattr(module, "TEST_COMMAND", launcher)

    return module.check_red_on_base


def _changed(repo, source: str | None, test: str) -> None:
    repo.seed()

    if source is not None:
        repo.write("src/app.py", source)

    repo.write("tests/test_app.py", test)
    repo.commit("the attempt")


def test_red_on_base_convicts_a_test_that_passes_on_the_base_tree(repo, red_on_base):
    _changed(
        repo,
        "def sign(x):\n    return 1 if x >= 0 else -1\n",
        "def test_arithmetic():\n    assert 1 + 1 == 2\n",
    )

    outcome = red_on_base(RED_ON_BASE_GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "tests/test_app.py" in outcome.output  # the conviction names the files


def test_red_on_base_passes_a_test_the_change_had_to_make_green(repo, red_on_base):
    _changed(
        repo,
        "def sign(x):\n    return 1 if x >= 0 else -1\n",
        "from app import sign\n\n\ndef test_sign():\n    assert sign(-1) == -1\n",
    )

    outcome = red_on_base(RED_ON_BASE_GATE, context_for(repo))

    assert outcome.outcome == "pass", outcome.output


def test_red_on_base_reads_test_functions_not_the_diffs_file_list(repo, red_on_base):
    # S-0081/D-2: a comment and a blank line leave every test function's shape
    # untouched, so nothing qualifies and nothing is run.
    _changed(
        repo,
        "def sign(x):\n    return 1 if x >= 0 else -1\n",
        "# a note for the reader\n\n\ndef test_app():\n    assert True\n",
    )

    outcome = red_on_base(RED_ON_BASE_GATE, context_for(repo))

    assert outcome.outcome == "skipped"
    assert "no test function differs" in outcome.output


def test_red_on_base_does_not_judge_a_diff_that_changes_no_source(repo, red_on_base):
    # S-0081/D-4: a contract whose whole job is adding tests is not convicted.
    _changed(repo, None, "def test_app():\n    assert True\n\n\ndef test_more():\n    assert 2\n")

    outcome = red_on_base(RED_ON_BASE_GATE, context_for(repo))

    assert outcome.outcome == "skipped"
    assert "no source outside the test patterns" in outcome.output


def test_red_on_base_leaves_a_failing_candidate_to_acceptance(repo, red_on_base):
    # S-0081/D-3: green on base, red here — one failing test is never filed
    # under two gate names.
    repo.seed()
    repo.git("checkout", "-q", "main")
    repo.write("src/app.py", "VALUE = 1\n")
    repo.commit("the base value")
    repo.git("checkout", "-q", f"torve/{TASK_ID}")
    repo.git("merge", "-q", "main")
    repo.write("src/app.py", "VALUE = 2\n")
    repo.write(
        "tests/test_app.py", "from app import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n"
    )
    repo.commit("a change its own test refutes")

    outcome = red_on_base(RED_ON_BASE_GATE, context_for(repo))

    assert outcome.outcome == "skipped"
    assert "acceptance judges" in outcome.output


def test_red_on_base_runs_both_trees_through_the_passs_own_executor(repo, red_on_base):
    # S-0081/D-5: no gate command runs where the agent could have staged a
    # shim, and only the qualifying files are named to it.
    from dataclasses import replace

    _changed(
        repo,
        "def sign(x):\n    return 1 if x >= 0 else -1\n",
        "def test_arithmetic():\n    assert 1 + 1 == 2\n",
    )

    seen: list[str] = []

    def execute(command: str, timeout: float) -> tuple[int, str]:
        seen.append(command)
        return 0, ""

    ctx = replace(context_for(repo), execute=execute)
    outcome = red_on_base(RED_ON_BASE_GATE, ctx)

    assert outcome.outcome == "fail"
    assert len(seen) == 2  # the base tree, then this attempt's own
    assert f"git archive {ctx.merge_base}" in seen[0]
    assert "mktemp -d" in seen[0] and 'rm -rf "$work"' in seen[0]  # nothing outlives the call
    assert all(command.rstrip().endswith("tests/test_app.py") for command in seen)
    assert "tests/test_other.py" not in seen[0]  # only the qualifying files run


def test_decisions_a_bare_local_id_names_the_tasks_own_row(repo):
    """A log entry may cite the row as the contract spells it (`S-0012/D-2`)
    or as the document's prose does (`D-2`). bloomery T-0007 wrote the bare
    form for three rows and was convicted for having written nothing."""
    locked = {"id": "S-0012/D-2", "grade": "LOCKED", "text": "a rung", "paths": ["src/**"]}
    repo.seed()
    repo.task(
        base_task(allow=["src/**"], decisions=[locked]),
        log_document(entry(decision="D-2", grade="LOCKED", action="decided")),
    )
    repo.write("src/app.py", "print('x')\n")
    repo.commit("touches the governed file")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass", result.output


def test_scope_admits_the_lock_file_of_a_manifest_it_admits(repo):
    """A scope that admits `pyproject.toml` admits the `uv.lock` a dependency
    change rewrites; refusing the lock refuses the change the scope allowed
    (bloomery T-0008, 2026-09-19). A lock without its manifest in scope is
    still outside."""
    repo.seed()
    repo.task(base_task(allow=["pyproject.toml", "src/**"]), log_document())
    repo.write("pyproject.toml", "[project]\nname = 'x'\n")
    repo.write("uv.lock", "version = 1\n")
    repo.commit("a dependency and its lock")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "pass", result.output

    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("uv.lock", "version = 2\n")
    repo.commit("the lock alone")
    result = check_scope(GATE, context_for(repo))
    assert result.outcome == "fail" and "uv.lock" in result.output
