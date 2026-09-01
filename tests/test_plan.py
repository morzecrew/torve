"""RFC 0007 §3: the deterministic minter. Admission refuses by name with
exit 3; the Phasing YAML becomes contracts inheriting the document's table
grade-and-paths intact; dry-run is the default and minted contracts load
back through the same Task model the gates read."""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from torve.application.planner import (
    PlanError,
    globs_intersect,
    inherit_decisions,
    plan_document,
    standing_decisions,
    write_contracts,
)
from torve.cli import app
from torve.config.rfc_parse import parse_phasing
from torve.domain.task import InheritedDecision
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
    '  scope: ["src/widget/**", "tests/widget/**"]\n'
    '  acceptance: ["make test"]\n'
    "- phase: 1\n"
    "  title: frob-side\n"
    "  intent: >-\n"
    "    Build the frobnicator beside it.\n"
    '  scope: ["src/frob/**"]\n'
    "- phase: 2\n"
    "  title: wire-together\n"
    "  intent: >-\n"
    "    Wire core and frobnicator together.\n"
    '  scope: ["src/app.py"]\n'
    "  depends_on: [1]\n"
    "```\n"
)


def rfc_doc(
    number: str,
    title: str,
    status: str = "accepted",
    *,
    depends: str = "[]",
    superseded_by: str = "null",
    body: str = "",
) -> str:
    slug = title.lower().replace(" ", "-")
    return (
        f'---\nid: "{number}"\ntitle: {title}\nstatus: {status}\n'
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
            rfc_doc(number, title, **kwargs), encoding="utf-8"
        )

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
    write_doc("0093", "Old", status="superseded", superseded_by='"0090"', body=TABLE + PHASING)
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
    text = (
        "## Phasing\n\n```yaml\n- phase: 2\n  title: t\n  intent: i\n"
        '  scope: ["src/**"]\n  depends_on: [1]\n```\n'
    )
    with pytest.raises(ValueError, match="undefined phase"):
        parse_phasing(text)


def test_parse_phasing_rejects_missing_intent():
    text = '## Phasing\n\n```yaml\n- phase: 1\n  title: t\n  scope: ["src/**"]\n```\n'
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
# CLI and decision inheritance


def test_plan_cli_dry_run_by_default(plan_repo):
    root, _, _ = plan_repo
    result = CliRunner().invoke(app, ["plan", "0090", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "dry run — nothing written" in result.output
    assert (
        not list((root / ".torve" / "tasks").glob("T-*"))
        if (root / ".torve" / "tasks").is_dir()
        else True
    )


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


def test_inherit_decisions_copies_the_table_intact():
    # One helper mints for both `torve plan` and adoption (A-47): grade, text
    # and paths as the row stands, pathless rows included.
    rows = inherit_decisions(TABLE, "0090-widgets.md")
    assert [(r.id, r.grade, r.paths) for r in rows] == [
        ("D-90.1", "LOCKED", ["src/widget/**"]),
        ("D-90.2", "ASSUMED", []),
    ]


def test_inherit_decisions_refuses_an_ungraded_row():
    ungraded = TABLE.replace("`ASSUMED`", "`MAYBE`")
    with pytest.raises(PlanError, match="not mintable"):
        inherit_decisions(ungraded, "0090-widgets.md")


# ....................... #
# Standing inheritance (RFC 0030 §5.1): the document-less lane.


def test_standing_decisions_intersect_in_and_out(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0091",
        "Frobs",
        body=(
            "## 7. Decisions\n\n"
            "| # | Grade | Decision | Paths | Consequence |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| D-91.1 | `LOCKED` | Frobs are idempotent | `src/frob/**` | Retries double-charge |\n"
            "| D-91.2 | `ASSUMED` | Frob names are short | `tests/frob/**` | Cheap to revisit |\n"
        ),
    )
    git("add", "-A")
    git("commit", "-qm", "frobs")

    inside = standing_decisions(root / "rfcs", ["src/frob/core.py"])
    assert [d.id for d in inside] == ["D-91.1"]

    outside = standing_decisions(root / "rfcs", ["src/widget/core.py"])
    assert [d.id for d in outside] == ["D-90.1"]

    both = standing_decisions(root / "rfcs", ["src/**"])
    assert [d.id for d in both] == ["D-90.1", "D-91.1"]

    unconstrained = standing_decisions(root / "rfcs", [])
    assert unconstrained == []


def test_standing_decisions_copy_grade_and_paths_at_write_time(plan_repo):
    root, _, _ = plan_repo
    assert standing_decisions(root / "rfcs", ["src/widget/core.py"]) == [
        InheritedDecision(
            id="D-90.1", grade="LOCKED", text="Widgets are idempotent", paths=["src/widget/**"]
        )
    ]


def test_standing_decisions_pathless_rows_are_never_standing(plan_repo):
    root, _, _ = plan_repo
    # D-90.2 declares no paths — it governs its own document's work only and
    # can never be standing, even against an allow that would cover anything.
    rows = standing_decisions(root / "rfcs", ["src/**", "tests/**"])
    assert [d.id for d in rows] == ["D-90.1"]
    assert "D-90.2" not in [d.id for d in rows]


def test_standing_decisions_never_read_draft_or_superseded_documents(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0092",
        "Sketch",
        status="draft",
        body=(
            "## 7. Decisions\n\n"
            "| # | Grade | Decision | Paths | Consequence |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| D-92.1 | `LOCKED` | A draft's rule | `src/widget/**` | — |\n"
        ),
    )
    write_doc(
        "0093",
        "Old",
        status="accepted",
        superseded_by='"0090"',
        body=(
            "## 7. Decisions\n\n"
            "| # | Grade | Decision | Paths | Consequence |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| D-93.1 | `LOCKED` | A superseded rule | `src/widget/**` | — |\n"
        ),
    )
    git("add", "-A")
    git("commit", "-qm", "non-standing docs")

    rows = standing_decisions(root / "rfcs", ["src/widget/**"])
    assert [d.id for d in rows] == ["D-90.1"]


def test_parse_phasing_defaults_tier_variant_empty():
    entries = parse_phasing(PHASING)
    assert entries is not None
    assert all(e.tier_variant == "" for e in entries)


def test_parse_phasing_accepts_tier_variant():
    text = PHASING.replace(
        "  title: widget-core\n", "  title: widget-core\n  tier_variant: copywriter\n"
    )
    entries = parse_phasing(text)
    assert entries is not None
    assert entries[0].tier_variant == "copywriter"
    assert entries[1].tier_variant == ""


def test_minting_copies_tier_variant_onto_the_contract(plan_repo):
    root, write_doc, git = plan_repo
    variant_phasing = PHASING.replace(
        "  title: widget-core\n", "  title: widget-core\n  tier_variant: copywriter\n"
    )
    write_doc("0099", "Personas", body=TABLE + variant_phasing)
    git("add", "-A")
    git("commit", "-qm", "personas")
    report = plan_document(root, root / "rfcs", "0099")
    equipped, plain = report.tasks[0].task, report.tasks[1].task
    assert equipped.tier_variant == "copywriter"
    assert plain.tier_variant is None


# ....................... #


def test_parse_phasing_defaults_character_empty():
    """RFC 0034 D-34.2: absent means no character, same absent-means-default
    shape tier_variant already carries."""

    entries = parse_phasing(PHASING)
    assert entries is not None
    assert all(e.character == "" for e in entries)


def test_parse_phasing_accepts_the_closed_character_vocabulary():
    text = PHASING.replace(
        "  title: widget-core\n", "  title: widget-core\n  character: structural\n"
    )
    entries = parse_phasing(text)
    assert entries is not None
    assert entries[0].character == "structural"
    assert entries[1].character == ""


def test_parse_phasing_refuses_a_character_outside_the_vocabulary():
    """D-34.1: structural|routine is a closed vocabulary — compliance is
    measured, never declarable, and a typo is not a third option."""

    text = PHASING.replace(
        "  title: widget-core\n", "  title: widget-core\n  character: compliance\n"
    )

    with pytest.raises(ValueError):
        parse_phasing(text)


def test_minting_copies_character_onto_the_contract(plan_repo):
    root, write_doc, git = plan_repo
    character_phasing = PHASING.replace(
        "  title: widget-core\n", "  title: widget-core\n  character: routine\n"
    )
    write_doc("0098", "Characters", body=TABLE + character_phasing)
    git("add", "-A")
    git("commit", "-qm", "characters")
    report = plan_document(root, root / "rfcs", "0098")
    marked, plain = report.tasks[0].task, report.tasks[1].task
    assert marked.character == "routine"
    assert plain.character is None


def test_minted_contract_carries_a_title_and_block_intent(plan_repo):
    """A-69: the phase title reaches the contract as its short name, and a
    multiline intent dumps as a literal block — never the single-quoted
    style whose newlines read as blank-line escapes."""
    import yaml

    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / "rfcs", "0090"))
    contract = next((root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    text = contract.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert document["title"]
    assert "\n\n  " not in text.split("intent:")[1].split("depends_on:")[0]
