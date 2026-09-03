"""RFC 0007 §4: the projection. Facts in files — contracts, run states,
logs, telemetry, the corpus — one report out, rendered as markdown for a
planning session or JSON for machines; progress derived on demand and
stored nowhere (D-A.12)."""

from __future__ import annotations

import json

import yaml
from test_plan import PHASING, TABLE, plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.application.planner import plan_document, write_contracts
from torve.application.projections import (
    QUASI_EXPERIMENT_CAVEAT,
    context_report,
    render_markdown,
    status_report,
    why_report,
)
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
    for to in (
        TaskState.CLAIMED,
        TaskState.RUNNING,
        TaskState.GATED,
        TaskState.REVIEWED,
        TaskState.READY,
    ):
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
        encoding="utf-8",
    )

    telemetry = root / ".torve" / "telemetry.jsonl"
    records = [
        {
            "schema_version": 1,
            "task_id": "T-0001",
            "config_hash": "abc123",
            "agent": {"adapter": "api", "cost_usd": 0.5, "model_version": "m-1", "shadow": False},
            "results": [
                {"name": "scope", "outcome": "pass", "duration_s": 0.1},
                {"name": "acceptance", "outcome": "fail", "duration_s": 3.0},
            ],
        },
        {
            "schema_version": 1,
            "kind": "shadow",
            "task_id": "T-0001",
            "config_hash": "abc123",
            "cost_usd_total": 0.25,
            "attempts": 1,
            "state": "ready",
        },
    ]
    telemetry.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


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
    doc.write_text(
        doc.read_text(encoding="utf-8").replace("implementation: none", "implementation: complete"),
        encoding="utf-8",
    )
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
    document = CliRunner().invoke(app, ["context", "--root", str(root), "--format", "markdown"])
    assert document.exit_code == 0, document.output
    for heading in (
        "## Programme",
        "## Tasks by state",
        "## Escalations by reason",
        "## Proposals awaiting the author",
        "## Gate health",
        "## Cost and iterations",
    ):
        assert heading in document.output
    assert "underspecified (1): T-0002" in document.output

    # Default text: rich sections — asserted by content, never layout (D-18.1).
    result = CliRunner().invoke(app, ["context", "--root", str(root)])
    assert result.exit_code == 0, result.output
    for content in (
        "Programme",
        "Tasks by state",
        "Escalations by reason",
        "underspecified",
        "T-0002",
        "0090",
        "acceptance",
    ):
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
    path.write_text(
        path.read_text().replace("implementation: none", "implementation: complete"),
        encoding="utf-8",
    )
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
    # T-0003 never ran through the engine, but history records its shipping
    # by the provenance trailer (RFC 0010) — a mere id mention in a chore
    # subject no longer counts, which is what keeps mint commits from
    # shipping whole phases in the programme view.
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "feat: wire together by hand\n\nTorve-Task: T-0003",
        ],
        capture_output=True,
        check=True,
    )
    report = context_report(root, root / "rfcs")
    states = {t["id"]: t["state"] for t in report["tasks"]}
    assert states["T-0003"] == "shipped"
    assert states["T-0001"] == "ready"  # a run state still outranks history
    doc = next(d for d in report["programme"] if d["rfc"] == "0090")
    assert doc["progress"]["2"] == "shipped"  # phase 2's only task shipped


def test_a_consumed_drafting_contract_is_not_unstarted(plan_repo):  # noqa: F811
    """A drafting contract (intake, decompose) with no live run was consumed
    by its adoption — 'unstarted' would claim work is still owed."""
    root, _, _ = plan_repo
    task_dir = root / ".torve" / "tasks" / "T-0500"
    task_dir.mkdir(parents=True)
    (task_dir / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0500\nrole: draft\n"
        "intent: decompose something\nscope: {allow: []}\n",
        encoding="utf-8",
    )
    report = context_report(root, root / "rfcs")
    states = {t["id"]: t["state"] for t in report["tasks"]}
    assert states["T-0500"] == "consumed"


def test_costs_are_newest_first_and_carry_the_model(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    telemetry = root / ".torve" / "telemetry.jsonl"
    records = [
        {
            "schema_version": 1,
            "at": "2026-08-30T10:00:00Z",
            "task_id": "T-0001",
            "config_hash": "aaa",
            "agent": {"adapter": "harness", "model": "claude-sonnet-5", "cost_usd": 1.0},
            "results": [],
        },
        {
            "schema_version": 1,
            "at": "2026-08-31T10:00:00Z",
            "task_id": "T-0002",
            "config_hash": "bbb",
            "agent": {"adapter": "harness", "model": "deepseek-chat", "cost_usd": 2.0},
            "results": [],
        },
    ]
    telemetry.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    report = context_report(root, root / "rfcs")
    assert [c["task"] for c in report["costs"]] == ["T-0002", "T-0001"]
    assert report["costs"][0]["model"] == "deepseek-chat"
    rendered = render_markdown(report)
    assert "2026-08-31T10:00:00Z · attempt T-0002 @ bbb: $2.0000, harness · deepseek-chat" in rendered


def test_a_chore_subject_citing_ids_ships_nothing(tmp_path):
    """D-7.26: only a landing citation — a parenthesized (T-nnnn), the
    merge-branch shape torve/T-nnnn, or the Torve-Task trailer — ships a
    task. A bare prose mention must not: a mint chore whose subject says
    'T-0097–T-0104' shipped a whole phase in the programme view once."""
    import subprocess

    from torve.application.projections import shipped_ids

    root = tmp_path
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "a").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "chore: mint T-0097 and T-0104"],
        check=True,
    )
    (root / "b").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "commit", "-q",
            "-m", "feat: the broker meters the wire (T-0105, A-56)",
        ],
        check=True,
    )
    (root / "c").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "commit", "-q",
            "-m", "merge torve/T-0106 into main\n\nTorve-Task: T-0107",
        ],
        check=True,
    )

    assert shipped_ids(root) == {"T-0105", "T-0106", "T-0107"}


# ----------------------- #
# RFC 0022 §5.3: the document-level half of the specification-quality
# report, joined into `torve context` as its own section (D-22.6). Tasks
# written directly as `.torve/tasks/T-nnnn/{contract,log}.yaml` — the shape
# `test_specquality.py` already uses — so each test seeds exactly the
# population it means to exercise, with no dependency on an rfc document
# actually existing on disk.


def _write_task(
    root,
    task_id: str,
    *,
    rfc: str | None,
    parent: str | None = None,
    character: str | None = None,
) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    document: dict = {
        "schema_version": 1,
        "id": task_id,
        "rfc": rfc,
        "phase": 1,
        "role": "implement",
        "intent": "test",
        "depends_on": [],
        "targets": [],
        "scope": {"allow": [], "deny": []},
        "acceptance": [],
        "decisions": [],
        "budget": {"iterations": None, "wallclock_minutes": None, "tokens": None},
        "tier": "executor",
    }
    if parent:
        document["parent"] = parent
    if character:
        document["character"] = character
    (task_dir / "contract.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def _write_log(root, task_id: str, entries: list[dict]) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "log.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "task": task_id, "drift_count": 0, "entries": entries}),
        encoding="utf-8",
    )


def _drift_entry(claim: str) -> dict:
    return {
        "decision": "D-1.1",
        "grade": "ASSUMED",
        "kind": "departed",
        "class": "drift",
        "at": "2026-08-22T10:00:00Z",
        "attempt": 1,
        "claim": claim,
        "evidence": "a.py:1-2",
        "action": "departed",
    }


def _write_feedback(root, task_id: str, human_minutes: int, rework: bool) -> None:
    path = root / ".torve" / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "at": "2026-08-22T10:00:00Z",
        "task_id": task_id,
        "human_minutes": human_minutes,
        "rework_after_review": rework,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _ready_state(root, task_id: str, attempts: int = 1) -> None:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    for to in (TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED):
        state.transition(to, "t")
    state.attempts = attempts
    state.transition(TaskState.READY, "t")
    state.save()


def _spec_quality_doc(report, rfc):
    return next(d for d in report["spec_quality"]["documents"] if d["rfc"] == rfc)


def test_tasks_without_an_rfc_are_excluded_from_document_signals(tmp_path):
    _write_task(tmp_path, "T-0001", rfc=None)
    report = context_report(tmp_path, tmp_path / "rfcs")
    assert report["spec_quality"]["documents"] == []


def test_minted_counts_every_task_regardless_of_state(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_task(tmp_path, "T-0002", rfc="rfcs/0090-a.md")
    report = context_report(tmp_path, tmp_path / "rfcs")
    assert _spec_quality_doc(report, "rfcs/0090-a.md")["minted"] == 2


def test_children_are_grouped_under_their_parent(tmp_path):
    # RFC 0026 D-26.5/D-26.6: the parent field is projection-only — this is
    # the one place it is read.
    _write_task(tmp_path, "T-0100", rfc=None)
    _write_task(tmp_path, "T-0101", rfc=None, parent="T-0100")
    _write_task(tmp_path, "T-0102", rfc=None, parent="T-0100")
    _write_task(tmp_path, "T-0200", rfc=None)  # no parent: not grouped

    report = context_report(tmp_path, tmp_path / "rfcs")
    assert report["decompositions"] == {"T-0100": ["T-0101", "T-0102"]}

    markdown = render_markdown(report)
    assert "## Decompositions" in markdown
    assert "T-0100 (integration task) -> T-0101, T-0102" in markdown


def test_attempts_to_green_only_counts_tasks_that_landed(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _ready_state(tmp_path, "T-0001", attempts=3)
    _write_task(tmp_path, "T-0002", rfc="rfcs/0090-a.md")  # never ran: no run state at all
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["attempts_to_green_median"] == 3
    assert doc["attempts_to_green_n"] == 1  # T-0002 contributes nothing: it never went green


def test_document_indicting_reasons_are_always_on_their_own_line(tmp_path):
    """RFC 0022 §5.3: underspecified and stale_inheritance print even at
    zero, because they are the two reasons that indict the document rather
    than the code that executed it (charter A-21, A-22)."""
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    state = RunState(task_id="T-0001", path=naming.state_file(tmp_path, "T-0001"))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.BLOCKER_FINDING, "d")
    state.save()
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["escalations_by_reason"]["underspecified"] == 0
    assert doc["escalations_by_reason"]["stale_inheritance"] == 0
    assert doc["escalations_by_reason"]["blocker_finding"] == 1


def test_spec_drift_findings_are_class_drift_log_entries(tmp_path):
    """`class: drift` is the same field `decisions-reported` checks a
    task's declared `drift_count` against (RFC 0022's own spec-drift
    signal), reused here rather than a second reading of the word."""
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_log(
        tmp_path,
        "T-0001",
        [_drift_entry("built otherwise than the row said"), {**_drift_entry("second"), "class": "spec-gap"}],
    )
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["drift_count"] == 1
    assert doc["spec_drift_findings"] == [
        {"task": "T-0001", "claim": "built otherwise than the row said"}
    ]


def test_human_minutes_and_rework_rate_from_feedback(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_task(tmp_path, "T-0002", rfc="rfcs/0090-a.md")
    _write_feedback(tmp_path, "T-0001", 10, rework=False)
    _write_feedback(tmp_path, "T-0002", 30, rework=True)
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["human_minutes_median"] == 20
    assert doc["human_minutes_n"] == 2
    assert doc["rework_rate"] == 0.5
    assert doc["rework_n"] == 2


def test_feedback_stream_is_append_only_latest_wins(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_feedback(tmp_path, "T-0001", 10, rework=False)
    _write_feedback(tmp_path, "T-0001", 25, rework=True)  # a later, corrected entry
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["human_minutes_median"] == 25
    assert doc["rework_rate"] == 1.0


def test_no_feedback_reports_none_not_zero(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    report = context_report(tmp_path, tmp_path / "rfcs")
    doc = _spec_quality_doc(report, "rfcs/0090-a.md")
    assert doc["human_minutes_median"] is None
    assert doc["rework_rate"] is None
    assert doc["rework_n"] == 0


def test_spec_quality_caveat_is_the_quasi_experiment_warning(tmp_path):
    report = context_report(tmp_path, tmp_path / "rfcs")
    assert "quasi-experiment" in report["spec_quality"]["caveat"]


def test_context_cli_renders_specification_quality_in_all_three_formats(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _ready_state(tmp_path, "T-0001", attempts=2)
    _write_log(tmp_path, "T-0001", [_drift_entry("a defect")])

    markdown = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "markdown"])
    assert markdown.exit_code == 0, markdown.output
    assert "## Specification quality" in markdown.output
    assert "quasi-experiment" in markdown.output
    assert "rfcs/0090-a.md" in markdown.output

    text = CliRunner().invoke(app, ["context", "--root", str(tmp_path)])
    assert text.exit_code == 0, text.output
    assert "Specification quality" in text.output
    assert "quasi-experiment" in text.output

    raw = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "json"])
    assert raw.exit_code == 0
    parsed = json.loads(raw.stdout)
    doc = _spec_quality_doc(parsed, "rfcs/0090-a.md")
    assert doc["drift_count"] == 1
    assert doc["attempts_to_green_median"] == 2


# ....................... #
# operator_attention (D-22.12, A-73): the corpus-wide line beside the
# document-level signals above


def test_operator_attention_is_present_with_no_tasks(tmp_path):
    report = context_report(tmp_path, tmp_path / "rfcs")
    attention = report["spec_quality"]["operator_attention"]
    assert attention["landed"] == 0
    assert attention["feedback"] == {"joined": 0, "total": 0}
    assert attention["command_events"] == {"joined": 0, "total": 0}
    assert attention["escalations_triaged"] == {"joined": 0, "total": 0}
    assert attention["human_minutes_n"] == 0
    assert "quasi-experiment" in attention["caveat"]


def test_operator_attention_human_minutes_suppressed_below_the_default_floor(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_task(tmp_path, "T-0002", rfc="rfcs/0090-a.md")
    _write_feedback(tmp_path, "T-0001", 10, rework=False)
    _write_feedback(tmp_path, "T-0002", 20, rework=False)

    report = context_report(tmp_path, tmp_path / "rfcs")
    attention = report["spec_quality"]["operator_attention"]
    assert attention["human_minutes_median"] is None  # 2 observations, default floor is 5
    assert attention["human_minutes_n"] == 2  # denominator prints regardless (D-22.8)


def _land_commit(root, task_id: str) -> None:
    import subprocess

    if not (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    marker = root / f".landed-{task_id}"
    marker.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", str(marker)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", f"land {task_id}\n\nTorve-Task: {task_id}"],
        check=True,
    )


def test_operator_attention_joins_interventions_to_landed_changes(tmp_path):
    """D-22.12: the interventions behind landed changes — feedback, commands
    and escalations — join per task id, with the raw total carrying whatever
    never landed in the window."""
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _ready_state(tmp_path, "T-0001")
    _land_commit(tmp_path, "T-0001")
    _write_feedback(tmp_path, "T-0001", 10, rework=False)
    _write_feedback(tmp_path, "T-0002", 20, rework=False)  # never landed: raw only
    _write_task(tmp_path, "T-0002", rfc="rfcs/0090-a.md")

    telemetry = tmp_path / ".torve" / "telemetry.jsonl"
    telemetry.parent.mkdir(parents=True, exist_ok=True)
    telemetry.write_text(
        json.dumps({"kind": "engine", "event": "tracker_command", "task": "T-0001"})
        + "\n"
        + json.dumps({"kind": "engine", "event": "tracker_command", "task": "T-0002"})
        + "\n",
        encoding="utf-8",
    )

    attention = context_report(tmp_path, tmp_path / "rfcs")["spec_quality"]["operator_attention"]
    assert attention["landed"] == 1
    assert attention["feedback"] == {"joined": 1, "total": 2}
    assert attention["command_events"] == {"joined": 1, "total": 2}


def test_operator_attention_reads_the_configured_telemetry_path(tmp_path):
    """The context section reads the telemetry stream where gates.yaml puts
    it, not a hardcoded default — a relocated stream must not read as zero."""
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    custom = tmp_path / ".torve" / "custom-telemetry.jsonl"
    custom.parent.mkdir(parents=True, exist_ok=True)
    custom.write_text(
        json.dumps({"kind": "engine", "event": "tracker_command", "task": "T-0001"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".torve" / "gates.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "telemetry": ".torve/custom-telemetry.jsonl"}),
        encoding="utf-8",
    )

    attention = context_report(tmp_path, tmp_path / "rfcs")["spec_quality"]["operator_attention"]
    assert attention["command_events"] == {"joined": 0, "total": 1}


def test_render_markdown_prints_the_operator_attention_line_with_no_documents(tmp_path):
    report = context_report(tmp_path, tmp_path / "rfcs")
    markdown = render_markdown(report)
    assert "## Specification quality" in markdown
    assert "operator attention:" in markdown
    assert "0 landed change(s)" in markdown


def test_context_cli_json_carries_operator_attention(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md")
    _write_feedback(tmp_path, "T-0001", 10, rework=False)

    result = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    attention = json.loads(result.output)["spec_quality"]["operator_attention"]
    assert attention["human_minutes_n"] == 1


def test_context_cli_markdown_prints_operator_attention_line(tmp_path):
    result = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "markdown"])
    assert result.exit_code == 0, result.output
    assert "operator attention:" in result.output


# ....................... #
# D-5.15 (A-75): the findings ledger — every kept non-blocking finding
# from a landed target's review, read from the review records telemetry
# already carries, marked possibly_addressed under D-7.24's weak-citation
# discipline applied to findings.


def _write_review_telemetry(root, review_id: str, target: str, findings: list[dict]) -> None:
    path = root / ".torve" / "telemetry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "kind": "review",
        "at": "2026-09-01T10:00:00Z",
        "config_hash": "abc123",
        "task_id": review_id,
        "target": target,
        "findings": findings,
        "discarded": [],
        "unparseable": False,
        "agent": {"tier": "reviewer", "adapter": "api"},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_findings_ledger_lists_kept_non_blocking_findings_from_landed_targets(tmp_path):
    _land_commit(tmp_path, "T-0001")
    _write_review_telemetry(
        tmp_path,
        "T-0101",
        "T-0001",
        [
            {"severity": "major", "claim": "the retry loop swallows errors", "evidence": "x.py:1 — loop"},
            {"severity": "minor", "claim": "a nit", "evidence": "x.py:2 — nit"},
            {"severity": "blocker", "claim": "unsafe", "evidence": "x.py:3 — bad"},
        ],
    )
    report = context_report(tmp_path, tmp_path / "rfcs")
    findings = report["findings"]
    # Blockers escalate their target and never land beside it: non-blocking only.
    assert [f["severity"] for f in findings] == ["major", "minor"]
    assert all(f["review"] == "T-0101" for f in findings)
    assert all(f["target"] == "T-0001" for f in findings)
    assert findings[0]["claim"] == "the retry loop swallows errors"
    assert findings[0]["evidence"] == "x.py:1 — loop"
    assert all(f["possibly_addressed"] is False for f in findings)


def test_findings_from_unlanded_targets_stay_out(tmp_path):
    _write_review_telemetry(
        tmp_path, "T-0101", "T-0001", [{"severity": "major", "claim": "c", "evidence": "x.py:1 — c"}]
    )
    assert context_report(tmp_path, tmp_path / "rfcs")["findings"] == []


def test_a_finding_is_possibly_addressed_when_a_contract_cites_the_review(tmp_path):
    _land_commit(tmp_path, "T-0001")
    _write_review_telemetry(
        tmp_path, "T-0101", "T-0001", [{"severity": "major", "claim": "c", "evidence": "x.py:1 — c"}]
    )
    assert context_report(tmp_path, tmp_path / "rfcs")["findings"][0]["possibly_addressed"] is False

    # The review's own contract cites its id — self-citation must not count.
    task_dir = tmp_path / ".torve" / "tasks" / "T-0101"
    task_dir.mkdir(parents=True)
    (task_dir / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0101\nrole: review\ntargets: [T-0001]\n",
        encoding="utf-8",
    )
    assert context_report(tmp_path, tmp_path / "rfcs")["findings"][0]["possibly_addressed"] is False

    # A later contract citing the review id is evidence the finding was
    # followed up — possibly addressed, never "resolved".
    later = tmp_path / ".torve" / "tasks" / "T-0102"
    later.mkdir(parents=True)
    (later / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0102\nintent: address T-0101's major finding\n",
        encoding="utf-8",
    )
    assert context_report(tmp_path, tmp_path / "rfcs")["findings"][0]["possibly_addressed"] is True


def test_findings_render_in_all_three_formats(tmp_path):
    _land_commit(tmp_path, "T-0001")
    _write_review_telemetry(
        tmp_path,
        "T-0101",
        "T-0001",
        [{"severity": "major", "claim": "the retry loop swallows errors", "evidence": "x.py:1 — loop"}],
    )

    markdown = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "markdown"])
    assert markdown.exit_code == 0, markdown.output
    assert "## Findings awaiting the operator" in markdown.output
    assert "[major] T-0101: the retry loop swallows errors" in markdown.output

    text = CliRunner().invoke(app, ["context", "--root", str(tmp_path)])
    assert text.exit_code == 0, text.output
    assert "Findings awaiting the operator" in text.output
    assert "T-0101" in text.output
    assert "the retry loop swallows errors" in text.output

    raw = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "json"])
    assert raw.exit_code == 0
    assert json.loads(raw.output)["findings"][0]["review"] == "T-0101"


def test_addressed_findings_collapse_to_the_plus_line(tmp_path):
    _land_commit(tmp_path, "T-0001")
    _write_review_telemetry(
        tmp_path, "T-0101", "T-0001", [{"severity": "major", "claim": "c", "evidence": "x.py:1 — c"}]
    )
    later = tmp_path / ".torve" / "tasks" / "T-0102"
    later.mkdir(parents=True)
    (later / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0102\nintent: address T-0101's finding\n",
        encoding="utf-8",
    )

    markdown = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "markdown"])
    assert markdown.exit_code == 0, markdown.output
    assert "## Findings awaiting the operator" in markdown.output
    assert "possibly addressed; the JSON report carries them all" in markdown.output
    # The addressed finding itself leaves the fresh list.
    assert "[major] T-0101: c" not in markdown.output

    text = CliRunner().invoke(app, ["context", "--root", str(tmp_path)])
    assert text.exit_code == 0, text.output
    assert "possibly addressed (see JSON)" in text.output


# ....................... #
# RFC 0034 §5.5: the character calibration section — declared character
# against the realized conviction profile grouped by gate axis, attempts and
# token shape. Measurement, never enforcement, and part of the report serve
# re-exposes verbatim.


def _write_gates(root, gates, **extra) -> None:
    path = root / ".torve" / "gates.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "gates": gates, **extra}), encoding="utf-8"
    )


def _append_attempt(root, task_id, results, agent=None, kind=None, stream=".torve/telemetry.jsonl"):
    record = {
        "schema_version": 1,
        "at": "2026-09-02T10:00:00Z",
        "task_id": task_id,
        "results": results,
    }
    if agent is not None:
        record["agent"] = agent
    if kind is not None:
        record["kind"] = kind
    path = root / stream
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


# acceptance is declared here unlabeled on purpose: the default reading is
# the whole fail-safe of D-34.4.
_LABELED_GATES = [
    {"name": "acceptance", "run": "@task.acceptance", "state": "blocking", "origin": "structural"},
    {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural", "axis": "boundary"},
    {
        "name": "decisions-reported",
        "run": "@decisions-reported",
        "state": "blocking",
        "origin": "structural",
        "axis": "compliance",
    },
    {
        "name": "self-audit",
        "run": "@self-audit",
        "state": "shadow",
        "origin": "structural",
        "axis": "compliance",
    },
]


def _calibration_rows(root):
    return {row["task"]: row for row in context_report(root, root / "rfcs")["character"]}


def test_character_calibration_joins_declaration_convictions_attempts_and_tokens(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md", character="structural")
    _ready_state(tmp_path, "T-0001", attempts=3)
    _write_gates(tmp_path, _LABELED_GATES)

    harness = {"adapter": "harness", "tier": "executor"}
    _append_attempt(
        tmp_path,
        "T-0001",
        [
            {"name": "acceptance", "outcome": "fail", "state": "blocking"},  # unlabeled: functional
            {"name": "decisions-reported", "outcome": "fail", "state": "blocking"},
            {"name": "scope", "outcome": "pass", "state": "blocking"},
            {"name": "self-audit", "outcome": "fail", "state": "shadow"},  # reports, never convicts
        ],
        {**harness, "input_tokens": 100, "output_tokens": 20},
    )
    _append_attempt(
        tmp_path,
        "T-0001",
        [
            {"name": "acceptance", "outcome": "fail", "state": "blocking"},
            {
                "name": "acceptance",
                "outcome": "error",
                "state": "blocking",
            },  # a broken fence is not a conviction
            {"name": "ghost-gate", "outcome": "fail"},  # no state: reads as blocking
        ],
        {**harness, "cache_read_tokens": 500},
    )
    _append_attempt(
        tmp_path,
        "T-0001",
        [{"name": "acceptance", "outcome": "fail", "state": "blocking"}],
        kind="shadow",  # a replay measures regimes, it convicts nobody
    )
    _append_attempt(
        tmp_path,
        "T-0001",
        [{"name": "acceptance", "outcome": "fail", "state": "blocking"}],
        {"adapter": "fake"},  # simulation is not spend either
    )

    # A declaration earns a row with a clean profile; a conviction earns one
    # undeclared; neither, and the task belongs in no row at all.
    _write_task(tmp_path, "T-0002", rfc=None, character="routine")
    _write_task(tmp_path, "T-0003", rfc=None)
    _append_attempt(
        tmp_path, "T-0003", [{"name": "acceptance", "outcome": "pass", "state": "blocking"}], harness
    )
    _write_task(tmp_path, "T-0004", rfc=None)
    _append_attempt(
        tmp_path,
        "T-0004",
        [{"name": "decisions-reported", "outcome": "fail", "state": "blocking"}],
        harness,
    )

    rows = _calibration_rows(tmp_path)

    assert rows["T-0001"] == {
        "task": "T-0001",
        "character": "structural",
        "convictions": {"functional": 3, "compliance": 1},
        "attempts": 3,
        "tokens": {"input_tokens": 100, "cache_read_tokens": 500, "output_tokens": 20},
    }
    # Vocabulary order, deterministically, for every reader of the report.
    assert list(rows["T-0001"]["convictions"]) == ["functional", "compliance"]
    assert list(rows["T-0001"]["tokens"]) == ["input_tokens", "cache_read_tokens", "output_tokens"]
    assert rows["T-0002"] == {
        "task": "T-0002",
        "character": "routine",
        "convictions": {},
        "attempts": 0,
        "tokens": {},
    }
    assert rows["T-0004"]["character"] == "undeclared"
    assert rows["T-0004"]["convictions"] == {"compliance": 1}
    assert "T-0003" not in rows


def test_a_missing_gates_manifest_reads_every_conviction_functional(tmp_path):
    _write_task(tmp_path, "T-0001", rfc=None, character="routine")
    _append_attempt(
        tmp_path,
        "T-0001",
        [{"name": "decisions-reported", "outcome": "fail", "state": "blocking"}],
    )
    row = _calibration_rows(tmp_path)["T-0001"]
    assert row["convictions"] == {"functional": 1}  # unlabeled reads functional
    assert row["attempts"] == 0


def test_character_calibration_reads_the_configured_telemetry_stream(tmp_path):
    _write_task(tmp_path, "T-0001", rfc=None)
    _write_gates(tmp_path, [], telemetry=".torve/other.jsonl")
    _append_attempt(
        tmp_path,
        "T-0001",
        [{"name": "acceptance", "outcome": "fail", "state": "blocking"}],
        stream=".torve/other.jsonl",
    )
    assert _calibration_rows(tmp_path)["T-0001"]["convictions"] == {"functional": 1}


def test_character_calibration_renders_in_all_three_formats(tmp_path):
    _write_task(tmp_path, "T-0001", rfc="rfcs/0090-a.md", character="structural")
    _ready_state(tmp_path, "T-0001", attempts=2)
    _write_gates(tmp_path, _LABELED_GATES)
    _append_attempt(
        tmp_path,
        "T-0001",
        [
            {"name": "acceptance", "outcome": "fail", "state": "blocking"},
            {"name": "decisions-reported", "outcome": "fail", "state": "blocking"},
        ],
        {"adapter": "harness", "input_tokens": 100, "output_tokens": 20},
    )

    markdown = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "markdown"])
    assert markdown.exit_code == 0, markdown.output
    assert "## Character calibration" in markdown.output
    assert (
        "- **T-0001** structural — convictions: functional 1, compliance 1 — "
        "2 attempt(s), tokens: input 100, output 20" in markdown.output
    )

    text = CliRunner().invoke(app, ["context", "--root", str(tmp_path)])
    assert text.exit_code == 0, text.output
    assert "Character calibration" in text.output
    assert "T-0001" in text.output
    assert "structural" in text.output

    raw = CliRunner().invoke(app, ["context", "--root", str(tmp_path), "--format", "json"])
    assert raw.exit_code == 0, raw.output
    parsed = json.loads(raw.output)
    assert parsed["character"][0]["character"] == "structural"
    # The JSON payload — what serve re-exposes verbatim — round-trips through
    # the markdown renderer with its section intact.
    assert "## Character calibration" in render_markdown(parsed)


def test_status_report_is_the_status_json_envelope(plan_repo):  # noqa: F811
    root, _, _ = plan_repo

    # A fresh checkout ships no run states: the empty envelope, exactly what
    # `torve status --format json` prints (test_status_json_carries_persisted_records).
    assert status_report(root) == {"schema_version": 1, "runs": []}

    seed_facts(root)
    report = status_report(root)

    assert report["schema_version"] == 1
    assert {r["task_id"] for r in report["runs"]} == {"T-0001", "T-0002"}
    assert {r["state"] for r in report["runs"]} == {"ready", "escalated"}


def test_status_cli_json_consumes_the_projection(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_facts(root)

    cli = CliRunner().invoke(app, ["status", "--root", str(root), "--format", "json"])

    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output) == status_report(root)


# ----------------------- #
# The why projection: one task's history joined from the durable streams —
# the attempt stamp's grouping and the pre-stamp fallback, the engine's
# events and the reviews of the task, totals, and the same-regime cost
# comparator with its caveat in-envelope. `seed_why_facts` is the fixture
# stream: mixed record kinds over three minted tasks, shared with the CLI,
# MCP and serve surface tests so all four pin the same envelope.

WHY_ROWS = [
    # Pre-0038 history: no attempt stamp, no verdict.
    {
        "schema_version": 1,
        "at": "2026-08-01T10:00:00Z",
        "task_id": "T-0001",
        "config_hash": "hashA",
        "agent": {"adapter": "harness", "tier": "executor", "model": "old-m", "cost_usd": 0.5},
        "results": [{"name": "scope", "outcome": "fail", "duration_s": 1.0}],
        "exit_code": 1,
    },
    {
        "schema_version": 1,
        "at": "2026-09-01T10:00:00Z",
        "task_id": "T-0001",
        "config_hash": "hashA",
        "agent": {
            "attempt": 1,
            "adapter": "harness",
            "tier": "executor",
            "model": "deepseek-v4-flash",
            "cost_usd": 0.348,
            "input_tokens": 392421,
            "output_tokens": 74099,
            "wall_time_s": 897.3,
            "trace_ref": ".torve/traces/T-0001.a1.trace.log",
        },
        "results": [
            {"name": "decisions-reported", "outcome": "fail", "state": "blocking"},
            {"name": "self-audit", "outcome": "fail", "state": "blocking"},
            {"name": "scope", "outcome": "pass", "state": "blocking"},
        ],
        "exit_code": 1,
        "verdict": "gates_red",
    },
    # A red-path row: gates never ran, no config_hash, the escalation
    # reason rides the row, and the trace file actually exists.
    {
        "schema_version": 1,
        "at": "2026-09-01T11:00:00Z",
        "task_id": "T-0001",
        "config_hash": None,
        "agent": {
            "attempt": 2,
            "adapter": "harness",
            "tier": "executor",
            "model": "deepseek-v4-flash",
            "cost_usd": 0.2,
            "wall_time_s": 1200.0,
            "trace_ref": ".torve/traces/T-0001.a2.trace.log",
        },
        "results": [],
        "exit_code": None,
        "gates_run": False,
        "timed_out": True,
        "verdict": "agent_timeout",
        "escalation": "poison_ceiling",
    },
    # The same regime under another task — comparator population.
    {
        "schema_version": 1,
        "at": "2026-09-01T10:30:00Z",
        "task_id": "T-0002",
        "config_hash": "hashA",
        "agent": {"attempt": 1, "adapter": "harness", "tier": "executor", "cost_usd": 0.9},
        "results": [],
        "exit_code": 0,
        "verdict": "green",
    },
    # Another regime: its price must never leak into the comparator.
    {
        "schema_version": 1,
        "at": "2026-09-01T10:40:00Z",
        "task_id": "T-0400",
        "config_hash": "hashB",
        "agent": {"attempt": 1, "adapter": "harness", "tier": "executor", "cost_usd": 99.0},
        "results": [],
        "exit_code": 1,
        "verdict": "gates_red",
    },
    # Noise every stream carries: a fake run, a shadow replay, and a
    # review — of T-0001 but recorded under its own task id.
    {
        "schema_version": 1,
        "at": "2026-09-01T10:50:00Z",
        "task_id": "T-0001",
        "config_hash": "hashA",
        "agent": {"attempt": 9, "adapter": "fake", "cost_usd": 500.0},
        "results": [],
    },
    {
        "schema_version": 1,
        "kind": "shadow",
        "at": "2026-09-01T10:55:00Z",
        "task_id": "T-0001",
        "config_hash": "hashA",
        "cost_usd_total": 1.0,
    },
    {
        "schema_version": 1,
        "kind": "review",
        "at": "2026-09-01T13:00:00Z",
        "task_id": "T-7001",
        "target": "T-0001",
        "config_hash": "hashA",
        "unparseable": False,
        "findings": [{"severity": "major", "claim": "c"}, {"severity": "blocker", "claim": "b"}],
        "agent": {"adapter": "harness", "cost_usd": 0.1},
    },
    {
        "schema_version": 1,
        "kind": "engine",
        "at": "2026-09-01T10:15:00Z",
        "event": "oversize_dispatch",
        "task": "T-0001",
        "reasons": ["tokens over"],
    },
    {
        "schema_version": 1,
        "kind": "engine",
        "at": "2026-09-01T12:00:00Z",
        "event": "escalation",
        "task": "T-0001",
        "reason": "poison_ceiling",
        "detail": "repeated identical failure",
        "run_id": "6fdbe1a8",
    },
    # Another task's escalation must not join this timeline.
    {
        "schema_version": 1,
        "kind": "engine",
        "at": "2026-09-01T11:30:00Z",
        "event": "escalation",
        "task": "T-0400",
        "reason": "underspecified",
        "detail": "elsewhere",
    },
]


def seed_why_facts(root):
    """Contracts for T-0001..3 (the plan's own minting), the fixture stream,
    one present trace file, and one feedback record. Idempotent: a root can
    be read through as often as a test likes."""
    if not (root / ".torve" / "tasks" / "T-0001" / "contract.yaml").exists():
        write_contracts(root, plan_document(root, root / "rfcs", "0090"))

    (root / ".torve" / "telemetry.jsonl").write_text(
        "\n".join(json.dumps(r) for r in WHY_ROWS) + "\n", encoding="utf-8"
    )
    traces = root / ".torve" / "traces"
    traces.mkdir(exist_ok=True)
    (traces / "T-0001.a2.trace.log").write_text("attempt two, timed out", encoding="utf-8")
    (root / ".torve" / "feedback.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "at": "2026-09-02T10:00:00Z",
                "task_id": "T-0001",
                "human_minutes": 30,
                "rework_after_review": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _why_env(root):
    seed_why_facts(root)
    return why_report(root, "T-0001")


def test_why_groups_attempts_by_the_attempt_stamp(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    envelope = _why_env(root)

    assert envelope["found"] is True
    assert envelope["task"] == "T-0001"
    assert envelope["rfc"] == "rfcs/0090-widgets.md"

    attempts = envelope["attempts"]
    assert [a["attempt"] for a in attempts] == [None, 1, 2]  # chronological

    first, second, third = attempts

    assert first["attempt"] is None  # the 2026-08-01 row leads, chronologically

    # The stamped rows carry what the engine recorded: verdict, the tier
    # that actually ran, convictions, cost, clock, tokens.
    assert second["verdict"] == "gates_red"
    assert second["tier"] == "executor"
    assert second["model"] == "deepseek-v4-flash"
    assert second["convictions"] == ["decisions-reported", "self-audit"]
    assert second["cost_usd"] == 0.348
    assert second["input_tokens"] == 392421
    assert second["output_tokens"] == 74099
    assert second["wall_time_s"] == 897.3

    # The red-path row: gates never ran, the escalation reason rides along.
    assert third["verdict"] == "agent_timeout"
    assert third["gates_run"] is False
    assert third["escalation"] == "poison_ceiling"


def test_why_marks_unstamped_history_pre_verdict_and_never_retrofits(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    attempts = _why_env(root)["attempts"]
    pre_verdict, stamped = attempts[0], attempts[1]

    # The unstamped row keeps its own place in timestamp order and says what
    # it is; the stamped rows beside it are marked by their number only.
    assert pre_verdict["attempt"] is None
    assert pre_verdict["pre_verdict"] is True
    assert "verdict" not in pre_verdict  # nothing to retrofit it from
    assert pre_verdict["convictions"] == ["scope"]
    assert pre_verdict["cost_usd"] == 0.5
    assert "pre_verdict" not in stamped
    assert stamped["attempt"] == 1

def test_why_reports_trace_presence_per_attempt(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    attempts = _why_env(root)["attempts"]

    assert attempts[1]["trace_ref"] == ".torve/traces/T-0001.a1.trace.log"
    assert attempts[1]["trace_present"] is False  # the stream outlived the file
    assert attempts[2]["trace_present"] is True  # seeded present
    assert attempts[0]["trace_ref"] is None
    assert attempts[0]["trace_present"] is False


def test_why_events_and_reviews_join_the_task_chronologically(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    envelope = _why_env(root)

    assert [(e["event"], e["at"]) for e in envelope["events"]] == [
        ("oversize_dispatch", "2026-09-01T10:15:00Z"),
        ("escalation", "2026-09-01T12:00:00Z"),
    ]
    escalation = envelope["events"][1]
    assert escalation["reason"] == "poison_ceiling"
    assert escalation["detail"] == "repeated identical failure"
    assert envelope["events"][0]["reasons"] == ["tokens over"]

    assert envelope["reviews"] == [
        {
            "at": "2026-09-01T13:00:00Z",
            "review": "T-7001",
            "verdict_findings": 2,
            "blockers": 1,
        }
    ]


def test_why_totals_sum_only_what_was_reported(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    totals = _why_env(root)["totals"]

    assert totals == {
        "attempts": 3,
        "cost_usd": 1.048,  # 0.5 + 0.348 + 0.2 — the fake and shadow rows stay out
        "input_tokens": 392421,
        "output_tokens": 74099,
        "wall_time_s": 2097.3,
        "human_minutes": 30,
    }


def test_why_regime_compares_only_same_hash_rows_and_carries_the_caveat(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    regime = _why_env(root)["regime"]

    # The anchor is the task's newest attempt row that recorded a hash: the
    # red-path row has none, so attempt 1's hashA governs. The population is
    # that regime's attempt rows across tasks — 0.5, 0.348 and T-0002's 0.9 —
    # and the $99 hashB row must not move the numbers.
    assert regime["config_hash"] == "hashA"
    assert regime["attempt_cost_median_usd"] == 0.5
    assert regime["attempt_cost_p90_usd"] == 0.9
    assert regime["attempt_cost_n"] == 3
    assert regime["caveat"] == QUASI_EXPERIMENT_CAVEAT


def test_why_state_is_only_what_the_stream_proves(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)

    # T-0001: escalated last (the event follows its final attempt row).
    assert why_report(root, "T-0001")["state"] == "escalated"
    # T-0002: its only row is green — ready.
    assert why_report(root, "T-0002")["state"] == "ready"
    # T-0003: a contract and no stream — unstarted.
    assert why_report(root, "T-0003")["state"] == "unstarted"
    # T-0002 totals prove absence reads as absence, not zero.
    assert why_report(root, "T-0002")["totals"]["input_tokens"] is None


def test_why_unknown_task_is_a_found_false_envelope(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)

    assert why_report(root, "T-9999") == {
        "schema_version": 1,
        "task": "T-9999",
        "found": False,
    }


def test_why_envelope_is_deterministic_across_reads(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)

    assert why_report(root, "T-0001") == why_report(root, "T-0001")

