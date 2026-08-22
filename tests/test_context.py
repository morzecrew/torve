"""RFC 0007 §4: the projection. Facts in files — contracts, run states,
logs, telemetry, the corpus — one report out, rendered as markdown for a
planning session or JSON for machines; progress derived on demand and
stored nowhere (D-A.12)."""

from __future__ import annotations

import json

from test_plan import PHASING, TABLE, plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.application.planner import plan_document, write_contracts
from torve.application.projections import context_report, render_markdown
from torve.application.runstate import RunState
from torve.base import naming
from torve.cli import app
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


def seed_facts(root):
    """Three minted tasks: one shipped, one escalated underspecified, one
    unstarted — plus a log proposal and two telemetry records."""
    write_contracts(root, plan_document(root, root / "rfcs", "0090"))

    done = RunState(task_id="T-0001", path=naming.state_file(root, "T-0001"))
    for to in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED,
               TaskState.REVIEWED, TaskState.READY):
        done.transition(to, "t")
    done.save()

    stuck = RunState(task_id="T-0002", path=naming.state_file(root, "T-0002"))
    stuck.transition(TaskState.CLAIMED, "t")
    stuck.transition(TaskState.RUNNING, "t")
    stuck.escalate(EscalationReason.UNDERSPECIFIED, "three unsettled decisions")

    log_dir = root / ".torve" / "tasks" / "T-0001"
    (log_dir / "log.yaml").write_text(
        "schema_version: 1\ntask: T-0001\ndrift_count: 0\nentries:\n"
        "  - decision: unlisted\n    grade: UNLISTED\n    kind: resolved\n"
        "    class: spec-gap\n    at: 2026-08-22T10:00:00Z\n    attempt: 1\n"
        "    claim: retry policy was unsettled\n"
        "    evidence: src/widget/core.py:10-20 — the loop\n"
        "    action: decided\n"
        "    proposal: 0090 row — retries are bounded at three\n",
        encoding="utf-8")

    telemetry = root / ".torve" / "telemetry.jsonl"
    records = [
        {"schema_version": 1, "task_id": "T-0001", "config_hash": "abc123",
         "agent": {"adapter": "api", "cost_usd": 0.5, "model_version": "m-1",
                   "shadow": False},
         "results": [
             {"name": "scope", "outcome": "pass", "duration_s": 0.1},
             {"name": "acceptance", "outcome": "fail", "duration_s": 3.0}]},
        {"schema_version": 1, "kind": "shadow", "task_id": "T-0001",
         "config_hash": "abc123", "cost_usd_total": 0.25, "attempts": 1,
         "state": "ready"},
    ]
    telemetry.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_context_report_projects_the_facts(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_facts(root)
    report = context_report(root, root / "rfcs")

    states = {t["id"]: t["state"] for t in report["tasks"]}
    assert states == {"T-0001": "ready", "T-0002": "escalated", "T-0003": "unstarted"}

    assert list(report["escalations"]) == ["underspecified"]
    assert report["escalations"]["underspecified"][0]["task"] == "T-0002"
    assert report["escalations"]["underspecified"][0]["at"] is not None

    assert len(report["proposals"]) == 1
    proposal = report["proposals"][0]
    assert proposal["task"] == "T-0001" and "bounded at three" in proposal["proposal"]

    gates = report["gates"]
    assert gates["acceptance"]["failures"] == 1 and gates["scope"]["runs"] == 1

    kinds = {c["kind"] for c in report["costs"]}
    assert kinds == {"attempt", "shadow"}

    doc = next(d for d in report["programme"] if d["rfc"] == "0090")
    # Phase 1 holds T-0001 (ready) and T-0002 (escalated) -> blocked wins;
    # phase 2 holds unstarted T-0003 -> planned.
    assert doc["progress"] == {"1": "blocked", "2": "planned"}
    assert doc["plannable"] is False  # every declared phase is minted


def test_disagreement_is_flagged(plan_repo):  # noqa: F811
    root, _write_doc, _git = plan_repo
    seed_facts(root)
    doc = next((root / "rfcs").glob("0090-*.md"))
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "implementation: none", "implementation: complete"), encoding="utf-8")
    report = context_report(root, root / "rfcs")
    entry = next(d for d in report["programme"] if d["rfc"] == "0090")
    assert entry["disagreement"] == "asserted complete, but a phase is not shipped"


def test_unminted_accepted_document_is_plannable(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    report = context_report(root, root / "rfcs")  # nothing minted yet
    entry = next(d for d in report["programme"] if d["rfc"] == "0090")
    assert entry["plannable"] is True
    assert entry["declared_phases"] == [1, 2]


def test_markdown_json_and_rich_render_one_report(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_facts(root)
    # Markdown: the pasteable document (D-18.6), first-class on this command.
    document = CliRunner().invoke(app, ["context", "--root", str(root),
                                        "--format", "markdown"])
    assert document.exit_code == 0, document.output
    for heading in ("## Programme", "## Tasks by state", "## Escalations by reason",
                    "## Proposals awaiting the author", "## Gate health",
                    "## Cost and iterations"):
        assert heading in document.output
    assert "underspecified (1): T-0002" in document.output

    # Default text: rich sections — asserted by content, never layout (D-18.1).
    result = CliRunner().invoke(app, ["context", "--root", str(root)])
    assert result.exit_code == 0, result.output
    for content in ("Programme", "Tasks by state", "Escalations by reason",
                    "underspecified", "T-0002", "0090", "acceptance"):
        assert content in result.output

    raw = CliRunner().invoke(app, ["context", "--root", str(root), "--format", "json"])
    assert raw.exit_code == 0
    parsed = json.loads(raw.stdout)
    assert parsed["schema_version"] == 1
    assert render_markdown(parsed).startswith("# torve context")


def test_settled_documents_leave_the_programme_table_for_a_count(plan_repo):  # noqa: F811
    root, write_doc, git = plan_repo
    seed_facts(root)
    write_doc("0091", "Doneware", status="accepted")
    path = root / "rfcs" / "0091-doneware.md"
    path.write_text(path.read_text().replace("implementation: none",
                                             "implementation: complete"),
                    encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "done doc")

    report = context_report(root, root / "rfcs")
    # The report itself keeps every document — hiding is presentation.
    assert any(d["rfc"] == "0091" for d in report["programme"])

    result = CliRunner().invoke(app, ["context", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "accepted and complete" in result.output
    assert "0091" in result.output
    assert "Doneware" not in result.output  # the row itself is gone


def test_a_shipping_commit_derives_shipped_without_a_run_state(plan_repo):  # noqa: F811
    import subprocess

    root, _, _git = plan_repo
    seed_facts(root)
    # T-0003 never ran through the engine, but history records its shipping —
    # both spellings: mid-parenthesis and trailer.
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty",
                    "-m", "feat: wire together (A-1, T-0003, minted by torve plan)"],
                   capture_output=True, check=True)
    report = context_report(root, root / "rfcs")
    states = {t["id"]: t["state"] for t in report["tasks"]}
    assert states["T-0003"] == "shipped"
    assert states["T-0001"] == "ready"  # a run state still outranks history
    doc = next(d for d in report["programme"] if d["rfc"] == "0090")
    assert doc["progress"]["2"] == "shipped"  # phase 2's only task shipped
