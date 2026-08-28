"""RFC 0022 §5: the attribution join and the decision-level report.

Facts written directly as `.torve/tasks/T-nnnn/{contract,log}.yaml` and run
state — the same shape `torve plan` and the runner write, built by hand here
so each test seeds exactly the population it means to exercise (the shape
RFC 0005's review-corpus fixtures already use for calibration)."""

from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from torve.application.runstate import RunState
from torve.application.specquality import decision_report, identifiers_for_document
from torve.base import naming
from torve.cli import app
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


def write_contract(root, task_id: str, *, rfc=None, decisions=(), scope_allow=()) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "contract.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": task_id,
                "rfc": rfc,
                "phase": 1,
                "role": "implement",
                "intent": "test",
                "depends_on": [],
                "targets": [],
                "scope": {"allow": list(scope_allow), "deny": []},
                "acceptance": [],
                "decisions": [
                    {"id": d[0], "grade": d[1], "text": "x", "paths": list(d[2])} for d in decisions
                ],
                "budget": {"iterations": None, "wallclock_minutes": None, "tokens": None},
                "tier": "executor",
            }
        ),
        encoding="utf-8",
    )


def write_log(root, task_id: str, entries: list[dict]) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "log.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "task": task_id, "drift_count": 0, "entries": entries}),
        encoding="utf-8",
    )


def entry(decision: str, grade: str, action: str, *, kind="departed", claim="c", evidence="a.py:1-2"):
    return {
        "decision": decision,
        "grade": grade,
        "kind": kind,
        "class": "spec-gap",
        "at": "2026-08-22T10:00:00Z",
        "attempt": 1,
        "claim": claim,
        "evidence": evidence,
        "action": action,
    }


def ready_state(root, task_id: str) -> None:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    for to in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED, TaskState.READY):
        state.transition(to, "t")
    state.save()


def abandoned_state(root, task_id: str) -> None:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.BLOCKER_FINDING, "d")
    state.transition(TaskState.ABANDONED, "t")
    state.save()


def requeued_state(root, task_id: str) -> None:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.LOCKED_CONFLICT, "d")
    state.transition(TaskState.QUEUED, "human requeue")
    state.save()


# ....................... #
# the join: grade compared is always the one minted, never the table today (D-22.2)


def test_the_grade_compared_is_the_one_copied_at_mint_time(tmp_path):
    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "LOCKED", ["src/a.py"])])
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["grade"] == "LOCKED"
    assert pop["inherited"] == 1


def test_populations_are_keyed_by_identifier_not_document(tmp_path):
    write_contract(tmp_path, "T-0001", rfc="rfcs/0001-a.md", decisions=[("D-1.1", "ASSUMED", [])])
    write_contract(tmp_path, "T-0002", rfc=None, decisions=[("D-1.1", "ASSUMED", [])])
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["inherited"] == 2
    assert sorted(pop["inherited_tasks"]) == ["T-0001", "T-0002"]


# ....................... #
# touched: the declared scope.allow intersecting the decision's declared paths


def test_a_task_whose_scope_covers_the_paths_is_touched(tmp_path):
    write_contract(
        tmp_path,
        "T-0001",
        decisions=[("D-1.1", "LOCKED", ["src/widget/**"])],
        scope_allow=["src/widget/core.py"],
    )
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 1


def test_a_task_whose_scope_misses_the_paths_is_not_touched(tmp_path):
    write_contract(
        tmp_path,
        "T-0001",
        decisions=[("D-1.1", "LOCKED", ["src/widget/**"])],
        scope_allow=["src/other/**"],
    )
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 0


def test_unconstrained_scope_counts_as_touched(tmp_path):
    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "LOCKED", ["src/widget/**"])], scope_allow=[])
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 1


def test_a_pathless_decision_is_never_touched(tmp_path):
    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "ASSUMED", [])], scope_allow=["src/**"])
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 0


# ....................... #
# §5.2's four readings, each suppressed below the floor


def test_decoration_reading_names_both_causes_once_the_floor_is_met(tmp_path):
    for i in range(1, 4):
        write_contract(
            tmp_path,
            f"T-000{i}",
            decisions=[("D-1.1", "LOCKED", ["src/a.py"])],
            scope_allow=["src/a.py"],
        )
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 3 and pop["cited"] == 0
    assert pop["reading"] == "decoration-or-paths-defect"
    assert "wrong area" in pop["reading_detail"] and "not reaching it" in pop["reading_detail"]


def test_decoration_reading_is_suppressed_below_the_floor(tmp_path):
    write_contract(
        tmp_path, "T-0001", decisions=[("D-1.1", "LOCKED", ["src/a.py"])], scope_allow=["src/a.py"]
    )
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 1 and pop["cited"] == 0
    assert pop["reading"] is None  # denominator printed regardless (D-22.8)


def test_a_locked_row_never_touched_is_not_decoration(tmp_path):
    """The Tests section of RFC 0022 §6: declared paths never touched by any
    task is silence about nothing, and must not be reported."""
    for i in range(1, 6):
        write_contract(
            tmp_path,
            f"T-000{i}",
            decisions=[("D-1.1", "LOCKED", ["src/never-touched.py"])],
            scope_allow=["src/somewhere-else.py"],
        )
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 0
    assert pop["reading"] is None


def test_assumed_departed_majority_proposes_open(tmp_path):
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(
            tmp_path,
            task_id,
            decisions=[("D-1.1", "ASSUMED", ["src/a.py"])],
            scope_allow=["src/a.py"],
        )
        write_log(tmp_path, task_id, [entry("D-1.1", "ASSUMED", "departed")])
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["reading"] == "propose-open"
    assert "3/3" in pop["reading_detail"]


def test_assumed_departed_minority_asserts_no_reading(tmp_path):
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(
            tmp_path,
            task_id,
            decisions=[("D-1.1", "ASSUMED", ["src/a.py"])],
            scope_allow=["src/a.py"],
        )
        # Only the first task departed; the other two complied silently
        # (ASSUMED owes no entry when the executor never diverges).
        if i == 1:
            write_log(tmp_path, task_id, [entry("D-1.1", "ASSUMED", "departed")])
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["touched"] == 3
    assert pop["reading"] is None


def test_open_decided_claims_are_surfaced_without_asserting_identical(tmp_path):
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(tmp_path, task_id, decisions=[("D-1.1", "OPEN", [])])
        write_log(
            tmp_path,
            task_id,
            [entry("D-1.1", "OPEN", "decided", claim=f"claim {i}")],
        )
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["decided"] == 3
    assert pop["reading"] == "review-decided-claims"
    assert "no automatic judgement" in pop["reading_detail"]  # D-22.1: never asserted, only shown
    assert {c["claim"] for c in pop["decided_claims"]} == {"claim 1", "claim 2", "claim 3"}


def test_locked_halted_and_amended_reads_as_over_grade(tmp_path):
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-a.md").write_text(
        "---\nid: \"0001\"\ntitle: A\nstatus: accepted\nimplementation: none\n"
        "depends_on: []\ninformed_by: []\nsupersedes: []\nsuperseded_by: null\n"
        'amended_by: ["A-1"]\nowner: t\ndescription: d\nschema_version: 1\n---\n\n'
        "# RFC 0001 — A\n\n## Decisions\n\n"
        "| # | Grade | Decision | Paths | Consequence |\n| --- | --- | --- | --- | --- |\n"
        "| D-1.1 | `LOCKED` | x | `src/a.py` | — |\n\n"
        "## Amendments\n\n### A-1 — regrading D-1.1 after repeated halts\n\nSee D-1.1.\n",
        encoding="utf-8",
    )
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(
            tmp_path, task_id, decisions=[("D-1.1", "LOCKED", ["src/a.py"])], scope_allow=["src/a.py"]
        )
        write_log(tmp_path, task_id, [entry("D-1.1", "LOCKED", "halted", kind="blocked")])
    report = decision_report(tmp_path, rfcs, floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["reading"] == "over-grade-or-wrong-boundary"


def test_locked_halted_and_requeued_reads_as_healthy(tmp_path):
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(
            tmp_path, task_id, decisions=[("D-1.1", "LOCKED", ["src/a.py"])], scope_allow=["src/a.py"]
        )
        write_log(tmp_path, task_id, [entry("D-1.1", "LOCKED", "halted", kind="blocked")])
        requeued_state(tmp_path, task_id)
        # requeued_state escalates on locked_conflict, not this decision, but
        # the reading only needs the transition shape, not a matching reason.
    report = decision_report(tmp_path, tmp_path / "rfcs", floor=3)
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["reading"] == "healthy-boundary"


# ....................... #
# D-22.10 (OPEN): both landed and all-tasks denominators are reported


def test_landed_and_abandoned_denominators_are_both_reported(tmp_path):
    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "ASSUMED", ["src/a.py"])])
    ready_state(tmp_path, "T-0001")
    write_contract(tmp_path, "T-0002", decisions=[("D-1.1", "ASSUMED", ["src/a.py"])])
    abandoned_state(tmp_path, "T-0002")
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["inherited"] == 2
    assert pop["inherited_landed"] == 1  # only the ready task counts as landed


# ....................... #
# unlisted entries cite no declared row (D-22.9's neighbour: nothing merges in)


def test_an_unlisted_entry_is_never_attributed_to_a_declared_row(tmp_path):
    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "OPEN", [])])
    write_log(tmp_path, "T-0001", [entry("unlisted", "UNLISTED", "decided", claim="something else")])
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["cited"] == 0


# ....................... #
# identifiers_for_document


def test_identifiers_for_document_filters_by_rfc_number(tmp_path):
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-a.md").write_text(
        "---\nid: \"0001\"\ntitle: A\nstatus: accepted\nimplementation: none\n"
        "depends_on: []\ninformed_by: []\nsupersedes: []\nsuperseded_by: null\n"
        "amended_by: []\nowner: t\ndescription: d\nschema_version: 1\n---\n\n"
        "# RFC 0001 — A\n\n## Decisions\n\n"
        "| # | Grade | Decision | Paths | Consequence |\n| --- | --- | --- | --- | --- |\n"
        "| D-1.1 | `ASSUMED` | x | — | — |\n",
        encoding="utf-8",
    )
    assert identifiers_for_document(rfcs, "0001") == {"D-1.1"}
    assert identifiers_for_document(rfcs, "0002") is None


# ....................... #
# CLI surface: `torve rfc health`


def _seed_cli_repo(tmp_path):
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-a.md").write_text(
        "---\nid: \"0001\"\ntitle: A\nstatus: accepted\nimplementation: none\n"
        "depends_on: []\ninformed_by: []\nsupersedes: []\nsuperseded_by: null\n"
        "amended_by: []\nowner: t\ndescription: d\nschema_version: 1\n---\n\n"
        "# RFC 0001 — A\n\n## Decisions\n\n"
        "| # | Grade | Decision | Paths | Consequence |\n| --- | --- | --- | --- | --- |\n"
        "| D-1.1 | `LOCKED` | x | `src/a.py` | — |\n",
        encoding="utf-8",
    )
    for i in range(1, 4):
        task_id = f"T-000{i}"
        write_contract(
            tmp_path,
            task_id,
            rfc="rfcs/0001-a.md",
            decisions=[("D-1.1", "LOCKED", ["src/a.py"])],
            scope_allow=["src/a.py"],
        )
    return rfcs


def test_health_cli_json_carries_the_populations_and_caveat(tmp_path):
    _seed_cli_repo(tmp_path)
    result = CliRunner().invoke(
        app, ["rfc", "health", "--root", str(tmp_path), "--format", "json", "--floor", "3"]
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["floor"] == 3
    assert "quasi-experiment" in document["caveat"]
    pop = next(p for p in document["populations"] if p["identifier"] == "D-1.1")
    assert pop["reading"] == "decoration-or-paths-defect"
    assert pop["touched"] == 3  # the denominator prints regardless of the reading


def test_health_cli_text_prints_the_caveat_and_floor_and_no_score(tmp_path):
    _seed_cli_repo(tmp_path)
    result = CliRunner().invoke(app, ["rfc", "health", "--root", str(tmp_path), "--floor", "3"])
    assert result.exit_code == 0, result.output
    assert "quasi-experiment" in result.output
    assert "no single corpus score is computed" in result.output
    assert "D-1.1" in result.output
    # The reading's own text (content, not the table cell, which folds a long
    # identifier across lines at this column width — D-18.1).
    assert "either the Paths cell names the wrong area" in result.output


def test_health_cli_filters_by_document(tmp_path):
    _seed_cli_repo(tmp_path)
    result = CliRunner().invoke(app, ["rfc", "health", "0001", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "D-1.1" in result.output

    missing = CliRunner().invoke(app, ["rfc", "health", "0002", "--root", str(tmp_path)])
    assert missing.exit_code == 3
    assert "no RFC" in missing.output


def test_health_cli_empty_corpus_reports_nothing_inherited(tmp_path):
    (tmp_path / "rfcs").mkdir()
    (tmp_path / ".torve").mkdir()
    result = CliRunner().invoke(app, ["rfc", "health", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no decisions inherited" in result.output
