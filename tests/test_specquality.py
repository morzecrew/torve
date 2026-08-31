"""RFC 0022 §5: the attribution join and the decision-level report.

Facts written directly as `.torve/tasks/T-nnnn/{contract,log}.yaml` and run
state — the same shape `torve plan` and the runner write, built by hand here
so each test seeds exactly the population it means to exercise (the shape
RFC 0005's review-corpus fixtures already use for calibration)."""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from torve.application.runstate import RunState
from torve.application.specquality import (
    QUASI_EXPERIMENT_CAVEAT,
    decision_report,
    dispatch_envelope,
    identifiers_for_document,
    operator_attention,
    render_envelope,
    render_operator_attention,
)
from torve.base import naming
from torve.cli import app
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


def write_contract(root, task_id: str, *, rfc=None, decisions=(), scope_allow=(), acceptance=()) -> None:
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
                "acceptance": list(acceptance),
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


def landed_state_with(root, task_id: str, *, attempts: int, start_at: str, end_at: str) -> None:
    """A READY state with hand-set `attempts` and `history` bounds — the two
    inputs `dispatch_envelope` reads for attempts and wall minutes, held
    deterministic rather than timed through real transitions."""

    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.state = TaskState.READY
    state.attempts = attempts
    state.history = [
        {"at": start_at, "from": "queued", "to": "claimed", "fact": "t"},
        {"at": end_at, "from": "reviewed", "to": "ready", "fact": "t"},
    ]
    state.save()


def land_commit(root, task_id: str) -> None:
    """The landing trailer git carries forever (D-10.4) — the persistent
    record `read_tasks` now reads instead of the run-state file the reaper
    deletes."""

    if not (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.example"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "torve-test"], check=True)

    marker = root / f".landed-{task_id}"
    marker.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", str(marker)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", f"land {task_id}\n\nTorve-Task: {task_id}"],
        check=True,
    )


def write_cost(root, task_id: str, cost_usd: float, *, adapter: str = "harness") -> None:
    telemetry = root / ".torve" / "telemetry.jsonl"
    telemetry.parent.mkdir(parents=True, exist_ok=True)

    with telemetry.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"task_id": task_id, "agent": {"adapter": adapter, "cost_usd": cost_usd}}) + "\n"
        )


def requeued_state(root, task_id: str) -> None:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.LOCKED_CONFLICT, "d")
    state.transition(TaskState.QUEUED, "human requeue")
    state.save()


def write_tracker_command_event(root, verb: str, task_id: str, *, applied: bool = True) -> None:
    telemetry = root / ".torve" / "telemetry.jsonl"
    telemetry.parent.mkdir(parents=True, exist_ok=True)

    with telemetry.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": "engine",
                    "event": "tracker_command",
                    "verb": verb,
                    "task": task_id,
                    "actor": "human",
                    "applied": applied,
                    "detail": "d",
                }
            )
            + "\n"
        )


def write_feedback(root, task_id: str, human_minutes: int) -> None:
    path = root / ".torve" / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "task_id": task_id,
                    "human_minutes": human_minutes,
                    "rework_after_review": False,
                }
            )
            + "\n"
        )


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
    land_commit(tmp_path, "T-0001")
    write_contract(tmp_path, "T-0002", decisions=[("D-1.1", "ASSUMED", ["src/a.py"])])
    abandoned_state(tmp_path, "T-0002")
    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["inherited"] == 2
    assert pop["inherited_landed"] == 1  # only the ready task counts as landed


def test_landed_survives_the_reap_sweep_of_the_run_state_file(tmp_path):
    """T-0133: the reaper deletes a terminal run's state file (D-3.4) — a
    population read afterwards must still see what actually shipped, from
    git's own landing trailer rather than the file that is gone."""

    write_contract(tmp_path, "T-0001", decisions=[("D-1.1", "ASSUMED", ["src/a.py"])])
    ready_state(tmp_path, "T-0001")
    land_commit(tmp_path, "T-0001")
    naming.state_file(tmp_path, "T-0001").unlink()  # the reap sweep

    report = decision_report(tmp_path, tmp_path / "rfcs")
    pop = next(p for p in report["populations"] if p["identifier"] == "D-1.1")
    assert pop["inherited_landed"] == 1


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


# ....................... #
# dispatch_envelope (D-22.11, A-62): the join read prospectively by size class


def test_dispatch_envelope_is_silent_below_the_floor(tmp_path):
    for i, task_id in enumerate(("T-0001", "T-0002"), start=1):
        write_contract(tmp_path, task_id, scope_allow=["src/a.py"])
        landed_state_with(
            tmp_path, task_id, attempts=i, start_at="2026-08-20T10:00:00.000000Z",
            end_at="2026-08-20T10:10:00.000000Z",
        )
        land_commit(tmp_path, task_id)

    envelope = dispatch_envelope(tmp_path, "ok", floor=3)
    assert envelope["n"] == 2  # the denominator prints regardless (D-22.8)
    assert envelope["attempts_median"] is None
    assert envelope["cost_usd_median"] is None
    assert envelope["wall_minutes_median"] is None


def test_dispatch_envelope_reports_medians_once_the_floor_is_met(tmp_path):
    starts_ends = [
        ("2026-08-20T10:00:00.000000Z", "2026-08-20T10:05:00.000000Z"),  # 5m
        ("2026-08-20T10:00:00.000000Z", "2026-08-20T10:10:00.000000Z"),  # 10m
        ("2026-08-20T10:00:00.000000Z", "2026-08-20T10:15:00.000000Z"),  # 15m
    ]

    for i, task_id in enumerate(("T-0001", "T-0002", "T-0003"), start=1):
        write_contract(tmp_path, task_id, scope_allow=["src/a.py"])
        start, end = starts_ends[i - 1]
        landed_state_with(tmp_path, task_id, attempts=i, start_at=start, end_at=end)
        land_commit(tmp_path, task_id)
        write_cost(tmp_path, task_id, float(i))

    envelope = dispatch_envelope(tmp_path, "ok", floor=3)
    assert envelope["n"] == 3
    assert envelope["attempts_median"] == 2
    assert envelope["cost_usd_median"] == 2.0 and envelope["cost_usd_n"] == 3
    assert envelope["wall_minutes_median"] == 10.0 and envelope["wall_minutes_n"] == 3
    assert "quasi-experiment" in envelope["caveat"]


def test_dispatch_envelope_only_pools_the_matching_size_class(tmp_path):
    write_contract(tmp_path, "T-0001", scope_allow=["src/a.py"])
    landed_state_with(
        tmp_path, "T-0001", attempts=1, start_at="2026-08-20T10:00:00.000000Z",
        end_at="2026-08-20T10:05:00.000000Z",
    )
    land_commit(tmp_path, "T-0001")
    # Two top-level modules in scope.allow reads as too_large (sizing.py's
    # MAX_MODULES=1; "tests" is excluded from the count so it must be a
    # second non-test module here), so this task must never join the "ok"
    # population.
    write_contract(tmp_path, "T-0002", scope_allow=["src/a.py", "lib/a.py"])
    landed_state_with(
        tmp_path, "T-0002", attempts=1, start_at="2026-08-20T10:00:00.000000Z",
        end_at="2026-08-20T10:05:00.000000Z",
    )
    land_commit(tmp_path, "T-0002")

    assert dispatch_envelope(tmp_path, "ok", floor=1)["n"] == 1
    assert dispatch_envelope(tmp_path, "too_large", floor=1)["n"] == 1


def test_dispatch_envelope_excludes_tasks_that_never_landed(tmp_path):
    write_contract(tmp_path, "T-0001", scope_allow=["src/a.py"])
    abandoned_state(tmp_path, "T-0001")
    envelope = dispatch_envelope(tmp_path, "ok", floor=1)
    assert envelope["n"] == 0


# ....................... #
# render_envelope


def test_render_envelope_below_the_floor_names_the_floor_and_the_caveat():
    envelope = {
        "size": "ok",
        "n": 2,
        "floor": 5,
        "attempts_median": None,
        "attempts_n": 0,
        "cost_usd_median": None,
        "cost_usd_n": 0,
        "wall_minutes_median": None,
        "wall_minutes_n": 0,
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }
    text = render_envelope(envelope)
    assert "size ok envelope" in text
    assert "n=2" in text and "below the observation floor of 5" in text
    assert "quasi-experiment" in text


def test_render_envelope_above_the_floor_carries_the_medians():
    envelope = {
        "size": "ok",
        "n": 3,
        "floor": 3,
        "attempts_median": 2.0,
        "attempts_n": 3,
        "cost_usd_median": 2.0,
        "cost_usd_n": 3,
        "wall_minutes_median": 10.0,
        "wall_minutes_n": 3,
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }
    text = render_envelope(envelope)
    assert "2.0 attempt(s)" in text
    assert "$2.00" in text
    assert "10m" in text
    assert "quasi-experiment" in text


# ....................... #
# CLI surface: the envelope prints beside `torve run`'s size verdict


def test_run_cli_prints_the_envelope_beside_the_size_verdict(tmp_path):
    # A real dispatch needs a sandbox runtime (docker); this asserts the
    # wiring for real rather than stubbing the leg, so it skips where the
    # daemon this environment's CI provides is absent (test_runtime_
    # conformance.py's own guard, repeated locally rather than imported
    # across test modules).
    import shutil
    import subprocess

    from torve.gates.sabotage import TASK_ID, Repo, base_task

    if shutil.which("docker") is None or subprocess.run(
        ["docker", "info"], capture_output=True, check=False
    ).returncode != 0:
        pytest.skip("docker daemon not available")

    (tmp_path / "json_repo").mkdir()
    json_repo = Repo(tmp_path / "json_repo")
    json_repo.seed()
    # The fake agent's default scenario writes TORVE_FAKE.md at the root —
    # the fence must admit it or the run is red by construction. (The
    # in-sandbox battery masked this: a nested socket-mode run's worktree
    # mount resolves against the host daemon, so the write landed in a void
    # and the diff came back empty.)
    json_repo.task(base_task(allow=["src/**", "TORVE_FAKE.md"]), None)

    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(json_repo.root), "--format", "json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["size"] == "ok"
    assert document["envelope"]["size"] == "ok"
    assert "quasi-experiment" in document["envelope"]["caveat"]

    (tmp_path / "text_repo").mkdir()
    text_repo = Repo(tmp_path / "text_repo")
    text_repo.seed()
    text_repo.task(base_task(allow=["src/**", "TORVE_FAKE.md"]), None)

    text_result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(text_repo.root)])
    assert text_result.exit_code == 0, text_result.output
    assert "size ok envelope" in text_result.output


# ....................... #
# operator_attention (D-22.12, A-73): the corpus-wide join


def test_operator_attention_counts_landed_changes(tmp_path):
    write_contract(tmp_path, "T-0001")
    ready_state(tmp_path, "T-0001")
    land_commit(tmp_path, "T-0001")
    write_contract(tmp_path, "T-0002")  # never ran: not landed

    report = operator_attention(tmp_path)
    assert report["landed"] == 1


def test_operator_attention_counts_tracker_command_events_applied_or_not(tmp_path):
    write_tracker_command_event(tmp_path, "approve", "T-0001", applied=True)
    write_tracker_command_event(tmp_path, "retry", "T-0002", applied=False)
    # Not a tracker_command: a different engine event must not be counted.
    (tmp_path / ".torve").mkdir(parents=True, exist_ok=True)
    with (tmp_path / ".torve" / "telemetry.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "engine", "event": "tracker_divergence"}) + "\n")

    report = operator_attention(tmp_path)
    assert report["command_events"] == 2


def test_operator_attention_counts_escalations_triaged_both_exits(tmp_path):
    write_contract(tmp_path, "T-0001")
    requeued_state(tmp_path, "T-0001")  # escalated -> queued
    write_contract(tmp_path, "T-0002")
    abandoned_state(tmp_path, "T-0002")  # escalated -> abandoned

    report = operator_attention(tmp_path)
    assert report["escalations_triaged"] == 2


def test_operator_attention_human_minutes_suppressed_below_floor(tmp_path):
    write_feedback(tmp_path, "T-0001", 10)
    write_feedback(tmp_path, "T-0002", 20)

    report = operator_attention(tmp_path, floor=3)
    assert report["human_minutes_median"] is None
    assert report["human_minutes_n"] == 2  # denominator prints regardless (D-22.8)


def test_operator_attention_human_minutes_reported_once_floor_met(tmp_path):
    write_feedback(tmp_path, "T-0001", 10)
    write_feedback(tmp_path, "T-0002", 20)
    write_feedback(tmp_path, "T-0003", 30)

    report = operator_attention(tmp_path, floor=3)
    assert report["human_minutes_median"] == 20
    assert report["human_minutes_n"] == 3


def test_operator_attention_carries_the_caveat(tmp_path):
    report = operator_attention(tmp_path)
    assert "quasi-experiment" in report["caveat"]


# ....................... #
# render_operator_attention


def test_render_operator_attention_below_the_floor_names_the_floor():
    report = {
        "landed": 4,
        "command_events": 6,
        "escalations_triaged": 1,
        "human_minutes_median": None,
        "human_minutes_n": 2,
        "floor": 5,
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }
    text = render_operator_attention(report)
    assert "4 landed change(s)" in text
    assert "6 command/approval event(s)" in text
    assert "1 escalation(s) triaged" in text
    assert "below the observation floor of 5" in text and "n=2" in text
    assert "quasi-experiment" in text


def test_render_operator_attention_above_the_floor_carries_the_median():
    report = {
        "landed": 4,
        "command_events": 6,
        "escalations_triaged": 1,
        "human_minutes_median": 20.0,
        "human_minutes_n": 5,
        "floor": 5,
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }
    text = render_operator_attention(report)
    assert "20m human effort (n=5)" in text


# ....................... #
# CLI surface: the operator-attention line in the corpus summary


def test_health_cli_corpus_summary_prints_operator_attention_text(tmp_path):
    _seed_cli_repo(tmp_path)
    write_tracker_command_event(tmp_path, "approve", "T-0001")
    result = CliRunner().invoke(app, ["rfc", "health", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "operator attention" in result.output
    assert "1 command/approval event(s)" in result.output


def test_health_cli_corpus_summary_carries_operator_attention_json(tmp_path):
    _seed_cli_repo(tmp_path)
    write_tracker_command_event(tmp_path, "approve", "T-0001")
    result = CliRunner().invoke(app, ["rfc", "health", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["operator_attention"]["command_events"] == 1


def test_health_cli_document_filter_has_no_operator_attention(tmp_path):
    """D-22.12: the operator-attention line is a corpus-wide fact — a
    single-document view is decision-level and has no bearing on it."""
    _seed_cli_repo(tmp_path)
    result = CliRunner().invoke(app, ["rfc", "health", "0001", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["operator_attention"] is None
