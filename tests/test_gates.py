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
