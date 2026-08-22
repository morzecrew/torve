"""RFC 0007 §3: the deterministic minter. Admission refuses by name with
exit 3; the Phasing YAML becomes contracts inheriting the document's table
grade-and-paths intact; dry-run is the default and minted contracts load
back through the same Task model the gates read."""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from torve.adapters.decisions.rfc_directory import RfcDirectory
from torve.application.planner import PlanError, globs_intersect, plan_document, write_contracts
from torve.cli import app
from torve.config.rfc_parse import parse_phasing
from torve.gates.context import load_task

# ----------------------- #

TABLE = (
    "## 7. Decisions\n\n"
    "| # | Grade | Decision | Paths | Consequence |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| D-90.1 | `LOCKED` | Widgets are idempotent | `src/widget/**` | Retries double-charge |\n"
    "| D-90.2 | `ASSUMED` | Frobnication is lazy | — | Cheap to revisit |\n"
)

PHASING = (
    "## 8. Phasing\n\n"
    "```yaml\n"
    "- phase: 1\n"
    "  title: widget-core\n"
    "  intent: >-\n"
    "    Build the widget core.\n"
    "  scope: [\"src/widget/**\", \"tests/widget/**\"]\n"
    "  acceptance: [\"make test\"]\n"
    "- phase: 1\n"
    "  title: frob-side\n"
    "  intent: >-\n"
    "    Build the frobnicator beside it.\n"
    "  scope: [\"src/frob/**\"]\n"
    "- phase: 2\n"
    "  title: wire-together\n"
    "  intent: >-\n"
    "    Wire core and frobnicator together.\n"
    "  scope: [\"src/app.py\"]\n"
    "  depends_on: [1]\n"
    "```\n"
)


def rfc_doc(number: str, title: str, status: str = "accepted", *,
            depends: str = "[]", superseded_by: str = "null",
            body: str = "") -> str:
    slug = title.lower().replace(" ", "-")
    return (
        f"---\nid: \"{number}\"\ntitle: {title}\nstatus: {status}\n"
        f"implementation: none\ndepends_on: {depends}\ninformed_by: []\n"
        f"supersedes: []\nsuperseded_by: {superseded_by}\namended_by: []\n"
        f"owner: Test\ndescription: {slug}\nschema_version: 1\n---\n\n"
        f"# RFC {number} — {title}\n\nProse.\n\n" + body
    )


@pytest.fixture
def plan_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "rfcs").mkdir(parents=True)
    (root / ".torve").mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")

    def write_doc(number: str, title: str, **kwargs) -> None:
        slug = title.lower().replace(" ", "-")
        (root / "rfcs" / f"{number}-{slug}.md").write_text(
            rfc_doc(number, title, **kwargs), encoding="utf-8")

    write_doc("0090", "Widgets", body=TABLE + PHASING)
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "corpus")
    return root, write_doc, git


def test_minting_inherits_the_table_at_write_time(plan_repo):
    root, _, _ = plan_repo
    report = plan_document(root, root / "rfcs", "0090")
    assert [p.task.id for p in report.tasks] == ["T-0001", "T-0002", "T-0003"]
    core = report.tasks[0].task
    assert core.intent == "Build the widget core."
    assert core.role == "implement"
    assert core.acceptance == ["make test"]
    assert [d.id for d in core.decisions] == ["D-90.1", "D-90.2"]
    locked = core.decisions[0]
    assert locked.grade == "LOCKED" and locked.paths == ["src/widget/**"]
    wired = report.tasks[2].task
    assert wired.phase == 2
    assert set(wired.depends_on) == {"T-0001", "T-0002"}  # every phase-1 task


def test_written_contracts_load_through_the_gates_model(plan_repo):
    root, _, _ = plan_repo
    report = plan_document(root, root / "rfcs", "0090")
    written = write_contracts(root, report)
    assert len(written) == 3
    task = load_task(written[0])
    assert task.id == "T-0001" and task.decisions[0].grade == "LOCKED"


def test_replanning_a_minted_phase_is_refused(plan_repo):
    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / "rfcs", "0090"))
    with pytest.raises(PlanError, match="already minted"):
        plan_document(root, root / "rfcs", "0090")


def test_draft_documents_are_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft", body=TABLE + PHASING)
    git("add", "-A")
    git("commit", "-qm", "draft")
    with pytest.raises(PlanError, match="0091 is draft"):
        plan_document(root, root / "rfcs", "0091")


def test_a_draft_dependency_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft")
    write_doc("0092", "Leaning", depends='["0091"]', body=TABLE + PHASING)
    git("add", "-A")
    git("commit", "-qm", "docs")
    with pytest.raises(PlanError, match="depends on 0091, which is draft"):
        plan_document(root, root / "rfcs", "0092")


def test_supersession_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0093", "Old", status="superseded", superseded_by='"0090"',
              body=TABLE + PHASING)
    git("add", "-A")
    git("commit", "-qm", "superseded")
    with pytest.raises(PlanError, match="superseded"):
        plan_document(root, root / "rfcs", "0093")


def test_a_dependency_cycle_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0094", "Chicken", depends='["0095"]', body=TABLE + PHASING)
    write_doc("0095", "Egg", depends='["0094"]')
    git("add", "-A")
    git("commit", "-qm", "cycle")
    with pytest.raises(PlanError, match="cycle"):
        plan_document(root, root / "rfcs", "0094")


def test_uncommitted_changes_are_refused(plan_repo):
    root, _, _ = plan_repo
    doc = next((root / "rfcs").glob("0090-*.md"))
    doc.write_text(doc.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    with pytest.raises(PlanError, match="uncommitted changes"):
        plan_document(root, root / "rfcs", "0090")


def test_intersecting_same_phase_scopes_are_refused(plan_repo):
    root, write_doc, git = plan_repo
    clash = PHASING.replace('scope: ["src/frob/**"]', 'scope: ["src/widget/core.py"]')
    write_doc("0096", "Clashing", body=TABLE + clash)
    git("add", "-A")
    git("commit", "-qm", "clash")
    with pytest.raises(PlanError, match="intersect"):
        plan_document(root, root / "rfcs", "0096")


def test_prose_only_phasing_is_not_mintable(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0097", "Prosey", body=TABLE + "## 8. Phasing\n\nFirst A, then B.\n")
    git("add", "-A")
    git("commit", "-qm", "prose")
    with pytest.raises(PlanError, match="no mintable Phasing"):
        plan_document(root, root / "rfcs", "0097")


# ....................... #
# The Phasing format itself


def test_parse_phasing_absent_and_prose_are_none():
    assert parse_phasing("# Doc\n\nNo phasing here.\n") is None
    assert parse_phasing("## Phasing\n\nProse only.\n") is None


def test_parse_phasing_rejects_undefined_phase_dependency():
    text = ("## Phasing\n\n```yaml\n- phase: 2\n  title: t\n  intent: i\n"
            "  scope: [\"src/**\"]\n  depends_on: [1]\n```\n")
    with pytest.raises(ValueError, match="undefined phase"):
        parse_phasing(text)


def test_parse_phasing_rejects_missing_intent():
    text = ("## Phasing\n\n```yaml\n- phase: 1\n  title: t\n  scope: [\"src/**\"]\n```\n")
    with pytest.raises(ValueError):
        parse_phasing(text)


def test_rfc_check_reddens_on_a_broken_phasing_fence(plan_repo):
    root, write_doc, _git = plan_repo
    write_doc("0098", "Broken", body=TABLE + "## 8. Phasing\n\n```yaml\n- phase: 0\n```\n")
    result = CliRunner().invoke(app, ["rfc", "check", "--root", str(root)])
    assert result.exit_code == 3
    assert "Phasing section does not mint" in result.output


def test_globs_intersect_is_conservative():
    assert globs_intersect(["src/widget/**"], ["src/widget/core.py"])
    assert globs_intersect(["src/a/**"], ["src/a/**"])
    assert not globs_intersect(["src/a/**"], ["src/b/**"])
    assert not globs_intersect(["tests/**"], ["src/**"])


# ....................... #
# CLI and the DecisionSource port


def test_plan_cli_dry_run_by_default(plan_repo):
    root, _, _ = plan_repo
    result = CliRunner().invoke(app, ["plan", "0090", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "dry run — nothing written" in result.output
    assert not list((root / ".torve" / "tasks").glob("T-*")) if (
        root / ".torve" / "tasks").is_dir() else True


def test_plan_cli_mints_and_refuses_drafts_with_exit_3(plan_repo):
    root, write_doc, git = plan_repo
    result = CliRunner().invoke(app, ["plan", "0090", "--root", str(root), "--no-dry-run"])
    assert result.exit_code == 0, result.output
    assert (root / ".torve" / "tasks" / "T-0001" / "contract.yaml").is_file()

    write_doc("0091", "Sketch", status="draft", body=TABLE + PHASING)
    git("add", "-A")
    git("commit", "-qm", "draft")
    refused = CliRunner().invoke(app, ["plan", "0091", "--root", str(root)])
    assert refused.exit_code == 3
    assert "no settled decisions" in refused.stderr


def test_rfc_directory_returns_standing_rows_for_an_area(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft", body=TABLE)  # draft: never consulted
    git("add", "-A")
    git("commit", "-qm", "draft")
    source = RfcDirectory(root / "rfcs")
    rows = source.standing("org/repo", ["src/widget/handlers.py"])
    assert [r.id for r in rows] == ["D-90.1"]  # pathless and draft rows excluded
    assert rows[0].grade == "LOCKED"
    assert source.standing("org/repo", ["docs/**"]) == []


# ....................... #
# --reconcile (§3.3, charter A-22)


def supersede_0090(root, write_doc, git):
    write_doc("0099", "Widgets Two", body=TABLE + PHASING)
    doc = next((root / "rfcs").glob("0090-*.md"))
    text = doc.read_text(encoding="utf-8")
    text = text.replace("status: accepted", "status: superseded")
    text = text.replace("superseded_by: null", 'superseded_by: "0099"')
    doc.write_text(text, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "supersede 0090")


def test_reconcile_escalates_stale_tasks(plan_repo):
    from torve.application.planner import reconcile
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    root, write_doc, git = plan_repo
    write_contracts(root, plan_document(root, root / "rfcs", "0090"))
    # T-0001 ran to ready (terminal — untouched); T-0002 never ran; T-0003 untouched too.
    done = RunState(task_id="T-0001", path=naming.state_file(root, "T-0001"))
    for to, fact in ((TaskState.CLAIMED, "t"), (TaskState.RUNNING, "t"),
                     (TaskState.GATED, "t"), (TaskState.REVIEWED, "t"),
                     (TaskState.READY, "t")):
        done.transition(to, fact)
    done.save()
    supersede_0090(root, write_doc, git)

    preview = reconcile(root, root / "rfcs", dry_run=True)
    assert {s.task_id: s.action for s in preview} == {
        "T-0001": "skipped (terminal)",
        "T-0002": "would escalate", "T-0003": "would escalate"}
    assert not naming.state_file(root, "T-0002").exists()  # dry run wrote nothing

    applied = reconcile(root, root / "rfcs", dry_run=False)
    assert {s.task_id: s.action for s in applied} == {
        "T-0001": "skipped (terminal)",
        "T-0002": "escalated", "T-0003": "escalated"}
    stale = RunState.load(naming.state_file(root, "T-0002"))
    assert stale.state is TaskState.ESCALATED
    assert stale.escalation is not None
    assert stale.escalation.reason == "stale_inheritance"
    assert "0099" in stale.escalation.detail

    again = reconcile(root, root / "rfcs", dry_run=False)
    assert {s.task_id: s.action for s in again} == {
        "T-0001": "skipped (terminal)",
        "T-0002": "already escalated (stale_inheritance)",
        "T-0003": "already escalated (stale_inheritance)"}


def test_reconcile_cli_is_exclusive_with_a_document(plan_repo):
    root, _, _ = plan_repo
    both = CliRunner().invoke(app, ["plan", "0090", "--reconcile", "--root", str(root)])
    assert both.exit_code == 3
    clean = CliRunner().invoke(app, ["plan", "--reconcile", "--root", str(root)])
    assert clean.exit_code == 0
    assert "nothing to reconcile" in clean.output
