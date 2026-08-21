from __future__ import annotations

from conftest import context_for

from torve.gates.decisions_reported import check_decisions_reported
from torve.gates.sabotage import TASK_ID, base_task, entry, log_document
from torve.gates.scope import check_scope
from torve.gates.secrets import check_secrets
from torve.models import Gate

GATE = Gate(name="test", run="@scope", state="blocking", origin="structural")  # any handle


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
        "gates": [{"name": "secrets", "run": "@secrets",
                   "state": "blocking", "origin": "structural"}],
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
    pass, per the D-21b reconciliation."""
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
    repo.write(f"logs/{TASK_ID}.yaml", log_document(with_proposal))
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


def test_decisions_empty_list_with_no_log_passes(repo):
    """decisions: [] means none apply, explicitly (D-7.5)."""
    repo.seed()
    repo.write(f"tasks/{TASK_ID}.yaml", "id: " + TASK_ID + "\ndecisions: []\n")
    repo.write("src/app.py", "print('x')\n")
    repo.commit("no decisions apply")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass"


def test_decisions_no_paths_is_skipped_never_passed(repo):
    decisions = [{"id": "D-9", "grade": "LOCKED", "text": "an area-less lock", "paths": []}]
    repo.seed()
    repo.task(base_task(allow=["src/**"], decisions=decisions), log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("touch")
    result = check_decisions_reported(GATE, context_for(repo))
    assert result.outcome == "pass"
    assert "skipped: D-9" in result.output
