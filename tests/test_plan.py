"""RFC 0007 §3: the deterministic minter. Admission refuses by name with
exit 3; the document's `phasing` list becomes contracts inheriting its
decisions grade-and-paths intact; dry-run is the default and minted
contracts load back through the same Task model the gates read."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest
from test_decisions import document, place
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
from torve.config.spec import SpecError, load_document
from torve.domain.spec import Document
from torve.domain.task import InheritedDecision
from torve.gates.context import load_task

# ----------------------- #

TABLE = [
    ("D-90.1", "LOCKED", "Widgets are idempotent", "`src/widget/**`", "Retries double-charge"),
    ("D-90.2", "ASSUMED", "Frobnication is lazy", "—", "Cheap to revisit"),
]

PHASING = [
    {
        "phase": 1,
        "title": "widget-core",
        "intent": "Build the widget core.",
        "scope": ["src/widget/**", "tests/widget/**"],
        "acceptance": ["make test"],
    },
    {
        "phase": 1,
        "title": "frob-side",
        "intent": "Build the frobnicator beside it.",
        "scope": ["src/frob/**"],
    },
    {
        "phase": 2,
        "title": "wire-together",
        "intent": "Wire core and frobnicator together.",
        "scope": ["src/app.py"],
        "depends_on": [1],
    },
]


def phasing(**overrides) -> list[dict]:
    """The three entries above, with keys merged onto the first one."""

    entries = copy.deepcopy(PHASING)
    entries[0].update(overrides)

    return entries


def written(spec_dir: Path, number: str = "0090", title: str = "Widgets", **kwargs) -> Path:
    """One document's `S-NNNN/` directory under *spec_dir* (D-57.1)."""

    kwargs.setdefault("rows", TABLE)
    kwargs.setdefault("implementation", "none")

    return place(spec_dir, number, document(number, title=title, **kwargs))


def loaded(tmp_path: Path, **kwargs) -> Document:
    """One document through the loader — every refusal here is a SpecError."""

    return load_document(written(tmp_path, **kwargs))


@pytest.fixture
def plan_repo(tmp_path):
    root = tmp_path / "repo"
    spec_dir = root / ".torve" / "specs"
    spec_dir.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")

    def write_doc(number: str, title: str, **kwargs) -> None:
        kwargs.setdefault("phasing", PHASING)
        written(spec_dir, number, title, **kwargs)

    write_doc("0090", "Widgets")
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "corpus")
    return root, write_doc, git


def test_minting_inherits_the_table_at_write_time(plan_repo):
    root, _, _ = plan_repo
    report = plan_document(root, root / ".torve" / "specs", "0090")
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
    report = plan_document(root, root / ".torve" / "specs", "0090")
    written_paths = write_contracts(root, report)
    assert len(written_paths) == 3
    task = load_task(written_paths[0])
    assert task.id == "T-0001" and task.decisions[0].grade == "LOCKED"


def test_replanning_a_minted_phase_is_refused(plan_repo):
    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / ".torve" / "specs", "0090"))
    with pytest.raises(PlanError, match="already minted"):
        plan_document(root, root / ".torve" / "specs", "0090")


def test_draft_documents_are_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft")
    git("add", "-A")
    git("commit", "-qm", "draft")
    with pytest.raises(PlanError, match="0091 is draft"):
        plan_document(root, root / ".torve" / "specs", "0091")


def test_a_draft_dependency_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft", phasing=None)
    write_doc("0092", "Leaning", depends_on=["0091"])
    git("add", "-A")
    git("commit", "-qm", "docs")
    with pytest.raises(PlanError, match="depends on 0091, which is draft"):
        plan_document(root, root / ".torve" / "specs", "0092")


def test_supersession_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0093", "Old", status="superseded", superseded_by="0090")
    git("add", "-A")
    git("commit", "-qm", "superseded")
    with pytest.raises(PlanError, match="superseded"):
        plan_document(root, root / ".torve" / "specs", "0093")


def test_a_dependency_cycle_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0094", "Chicken", depends_on=["0095"])
    write_doc("0095", "Egg", depends_on=["0094"], phasing=None)
    git("add", "-A")
    git("commit", "-qm", "cycle")
    with pytest.raises(PlanError, match="cycle"):
        plan_document(root, root / ".torve" / "specs", "0094")


def test_uncommitted_changes_are_refused(plan_repo):
    root, _, _ = plan_repo
    doc = root / ".torve" / "specs" / "S-0090" / "document.yaml"
    doc.write_text(doc.read_text(encoding="utf-8") + "\nowner: edited\n", encoding="utf-8")
    with pytest.raises(PlanError, match="uncommitted changes"):
        plan_document(root, root / ".torve" / "specs", "0090")


def test_plan_refuses_a_document_whose_contracts_do_not_lint(plan_repo):
    """A-132: the three drafting paths ran the contract lint and the minting
    path never did. RFC 0052's phasing block minted three contracts that
    could not be satisfied — a deliverable outside every scope, an
    acceptance command no sandbox can run, a module allowed without its test
    file — and two of them cost a full poison ceiling to find. A reviewed
    document is not a linted one."""

    from torve.cli.main import app
    from torve.domain.states import EXIT_CONFIG

    root, write_doc, git = plan_repo
    write_doc(
        "0097",
        "Ungitable",
        phasing=[
            {
                "phase": 1,
                "title": "t",
                "intent": "i",
                "scope": ["src/widget/**"],
                "acceptance": ["uv run torve gates run"],
            }
        ],
    )

    git("add", "-A")
    git("commit", "-qm", "the ungitable document")

    result = CliRunner().invoke(app, ["plan", "0097", "--root", str(root), "--no-dry-run"])

    assert result.exit_code == EXIT_CONFIG
    assert "needs git" in result.output
    # And nothing was written: the refusal precedes the mint, which is the
    # whole point of moving the check here.
    assert not (root / ".torve" / "tasks").exists()


def test_intersecting_same_phase_scopes_are_refused(plan_repo):
    root, write_doc, git = plan_repo
    clash = copy.deepcopy(PHASING)
    clash[1]["scope"] = ["src/widget/core.py"]
    write_doc("0096", "Clashing", phasing=clash)
    git("add", "-A")
    git("commit", "-qm", "clash")
    with pytest.raises(PlanError, match="intersect"):
        plan_document(root, root / ".torve" / "specs", "0096")


def test_a_document_without_phasing_is_not_mintable(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0097", "Prosey", phasing=None)
    git("add", "-A")
    git("commit", "-qm", "no phasing")
    with pytest.raises(PlanError, match="no phasing"):
        plan_document(root, root / ".torve" / "specs", "0097")


# ....................... #
# The phasing list itself


def test_a_phase_depending_on_an_undefined_phase_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0097",
        "Dangling",
        phasing=[
            {
                "phase": 2,
                "title": "t",
                "intent": "i",
                "scope": ["src/**"],
                "depends_on": [1],
            }
        ],
    )
    git("add", "-A")
    git("commit", "-qm", "dangling")
    with pytest.raises(PlanError, match="which no entry defines"):
        plan_document(root, root / ".torve" / "specs", "0097")


def test_the_loader_refuses_a_phase_without_an_intent(tmp_path):
    with pytest.raises(SpecError, match=r"phasing\.0\.intent"):
        loaded(tmp_path, phasing=[{"phase": 1, "title": "t", "scope": ["src/**"]}])


def test_the_loader_refuses_a_grade_outside_the_vocabulary(tmp_path):
    ungraded = [("D-90.1", "MAYBE", "Widgets are idempotent", "`src/widget/**`")]

    with pytest.raises(SpecError, match=r"decisions\.0\.grade"):
        loaded(tmp_path, rows=ungraded)


def test_rfc_check_reddens_on_a_phasing_entry_the_model_refuses(plan_repo):
    root, write_doc, _git = plan_repo
    write_doc("0098", "Broken", phasing=[{"phase": 0}])
    result = CliRunner().invoke(app, ["spec", "check", "--root", str(root)])
    assert result.exit_code == 3
    assert "phasing.0" in result.output


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

    write_doc("0091", "Sketch", status="draft")
    git("add", "-A")
    git("commit", "-qm", "draft")
    refused = CliRunner().invoke(app, ["plan", "0091", "--root", str(root)])
    assert refused.exit_code == 3
    assert "no settled decisions" in refused.stderr


def test_inherit_decisions_copies_the_rows_intact(tmp_path):
    # One helper mints for both `torve plan` and adoption (A-47): grade, text
    # and paths as the row stands, pathless rows included.
    rows = inherit_decisions(loaded(tmp_path))
    assert [(r.id, r.grade, r.paths) for r in rows] == [
        ("D-90.1", "LOCKED", ["src/widget/**"]),
        ("D-90.2", "ASSUMED", []),
    ]


# ....................... #
# Standing inheritance (RFC 0030 §5.1): the document-less lane.


def test_standing_decisions_intersect_in_and_out(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0091",
        "Frobs",
        phasing=None,
        rows=[
            ("D-91.1", "LOCKED", "Frobs are idempotent", "`src/frob/**`", "Retries double-charge"),
            ("D-91.2", "ASSUMED", "Frob names are short", "`tests/frob/**`", "Cheap to revisit"),
        ],
    )
    git("add", "-A")
    git("commit", "-qm", "frobs")

    inside = standing_decisions(root / ".torve" / "specs", ["src/frob/core.py"])
    assert [d.id for d in inside] == ["D-91.1"]

    outside = standing_decisions(root / ".torve" / "specs", ["src/widget/core.py"])
    assert [d.id for d in outside] == ["D-90.1"]

    both = standing_decisions(root / ".torve" / "specs", ["src/**"])
    assert [d.id for d in both] == ["D-90.1", "D-91.1"]

    unconstrained = standing_decisions(root / ".torve" / "specs", [])
    assert unconstrained == []


def test_standing_decisions_copy_grade_and_paths_at_write_time(plan_repo):
    root, _, _ = plan_repo
    assert standing_decisions(root / ".torve" / "specs", ["src/widget/core.py"]) == [
        InheritedDecision(
            id="D-90.1",
            grade="LOCKED",
            text="Widgets are idempotent",
            paths=["src/widget/**"],
            consequence="Retries double-charge",
        )
    ]


def test_standing_decisions_pathless_rows_are_never_standing(plan_repo):
    root, _, _ = plan_repo
    # D-90.2 declares no paths — it governs its own document's work only and
    # can never be standing, even against an allow that would cover anything.
    rows = standing_decisions(root / ".torve" / "specs", ["src/**", "tests/**"])
    assert [d.id for d in rows] == ["D-90.1"]
    assert "D-90.2" not in [d.id for d in rows]


def test_standing_decisions_never_read_draft_or_superseded_documents(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0092",
        "Sketch",
        status="draft",
        phasing=None,
        rows=[("D-92.1", "LOCKED", "A draft's rule", "`src/widget/**`")],
    )
    write_doc(
        "0093",
        "Old",
        status="accepted",
        superseded_by="0090",
        phasing=None,
        rows=[("D-93.1", "LOCKED", "A superseded rule", "`src/widget/**`")],
    )
    git("add", "-A")
    git("commit", "-qm", "non-standing docs")

    rows = standing_decisions(root / ".torve" / "specs", ["src/widget/**"])
    assert [d.id for d in rows] == ["D-90.1"]


def test_a_phase_defaults_tier_variant_and_character_to_empty(tmp_path):
    """RFC 0034 D-34.2: absent means no character, the same
    absent-means-default shape tier_variant carries."""

    doc = loaded(tmp_path, phasing=PHASING)
    assert [e.tier_variant for e in doc.phasing] == ["", "", ""]
    assert [e.character for e in doc.phasing] == ["", "", ""]


def test_the_loader_accepts_a_tier_variant(tmp_path):
    doc = loaded(tmp_path, phasing=phasing(tier_variant="copywriter"))
    assert doc.phasing[0].tier_variant == "copywriter"
    assert doc.phasing[1].tier_variant == ""


def test_minting_copies_tier_variant_onto_the_contract(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0099", "Personas", phasing=phasing(tier_variant="copywriter"))
    git("add", "-A")
    git("commit", "-qm", "personas")
    report = plan_document(root, root / ".torve" / "specs", "0099")
    equipped, plain = report.tasks[0].task, report.tasks[1].task
    assert equipped.tier_variant == "copywriter"
    assert plain.tier_variant is None


@pytest.mark.parametrize("character", ["structural", "routine"])
def test_the_loader_accepts_the_closed_character_vocabulary(tmp_path, character):
    doc = loaded(tmp_path, phasing=phasing(character=character))
    assert doc.phasing[0].character == character
    assert doc.phasing[1].character == ""


def test_the_loader_refuses_a_character_outside_the_vocabulary(tmp_path):
    """D-34.1: structural|routine is a closed vocabulary — compliance is
    measured, never declarable, and a typo is not a third option."""

    with pytest.raises(SpecError, match=r"phasing\.0\.character"):
        loaded(tmp_path, phasing=phasing(character="compliance"))


def test_minting_copies_character_onto_the_contract(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0098", "Characters", phasing=phasing(character="routine"))
    git("add", "-A")
    git("commit", "-qm", "characters")
    report = plan_document(root, root / ".torve" / "specs", "0098")
    marked, plain = report.tasks[0].task, report.tasks[1].task
    assert marked.character == "routine"
    assert plain.character is None


def test_minted_contract_carries_a_title_and_block_intent(plan_repo):
    """A-69: the phase title reaches the contract as its short name, and a
    multiline intent dumps as a literal block — never the single-quoted
    style whose newlines read as blank-line escapes."""
    import yaml

    root, _, _ = plan_repo
    write_contracts(root, plan_document(root, root / ".torve" / "specs", "0090"))
    contract = next((root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    text = contract.read_text(encoding="utf-8")
    minted = yaml.safe_load(text)
    assert minted["title"]
    assert "\n\n  " not in text.split("intent:")[1].split("depends_on:")[0]


# ....................... #
# RFC 0054 phase 1: the row travels whole (D-54.1), and a blocking check
# needs its twin (D-54.4).

DETAILS = {
    "D-90.1": {
        "rationale": "because",
        "check": "pytest tests/test_widget.py",
        "check_state": "blocking",
        "check_twin": "tests/test_widget_sabotage.py",
    }
}


def test_inherit_decisions_carries_consequence_and_check(tmp_path):
    rows = inherit_decisions(loaded(tmp_path, details=DETAILS))
    first = rows[0]

    assert first.consequence == "Retries double-charge"
    assert first.check == "pytest tests/test_widget.py"
    assert first.check_state == "blocking" and first.check_twin == "tests/test_widget_sabotage.py"
    assert rows[1].check is None and rows[1].check_state == "shadow"


def test_inherit_decisions_refuses_a_blocking_check_without_a_twin(tmp_path):
    details = {"D-90.1": {k: v for k, v in DETAILS["D-90.1"].items() if k != "check_twin"}}

    with pytest.raises(PlanError, match="no check_twin"):
        inherit_decisions(loaded(tmp_path, details=details))


# ....................... #
# RFC 0056 phase 3 (D-56.9): with a store, plan mints into the record and
# writes no file; dispatch projects the contract into the worktree.


def test_minting_into_the_record_writes_no_file_and_numbers_from_the_board(plan_repo):
    from test_residency import PARTITION, run

    from torve.application.manager import project
    from torve.application.planner import mint_contracts
    from torve.application.residency import mint
    from torve.domain.task import Task

    root, _write_doc, _git = plan_repo

    async def scenario(log):
        # the board already holds T-0007 from an earlier mint elsewhere
        await mint(
            log,
            {"T-0007": Task(id="T-0007", intent="elsewhere", decisions=[])},
            partition=PARTITION,
            actor_id="m",
        )
        board = project(await log.since(partition=PARTITION))
        report = plan_document(root, root / ".torve" / "specs", "0090", board=board)

        assert [p.task.id for p in report.tasks] == ["T-0008", "T-0009", "T-0010"]

        minted = await mint_contracts(log, report, partition=PARTITION)

        assert minted == ["T-0008", "T-0009", "T-0010"]
        assert not (root / ".torve" / "tasks").exists()

        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-0008"].contract is not None
        assert board.tasks["T-0008"].contract.intent == "Build the widget core."

        # a second plan sees the board's mints as minted
        with pytest.raises(PlanError, match="already minted"):
            plan_document(root, root / ".torve" / "specs", "0090", board=board)

    run(scenario)


def test_the_contract_is_projected_into_a_worktree_that_lacks_it(tmp_path):
    from torve.application.planner import project_contract
    from torve.domain.task import Task

    task = Task(id="T-0042", intent="Hold the line.", decisions=[], title="hold")

    written = project_contract(tmp_path, task)

    assert written == tmp_path / ".torve" / "tasks" / "T-0042" / "contract.yaml"
    assert written.read_text(encoding="utf-8").startswith("# Projected from the record")
    assert load_task(written).intent == "Hold the line."
    # the file mode: a contract already there is the contract
    assert project_contract(tmp_path, task) is None


def test_plan_with_a_partition_mints_into_the_record_and_writes_nothing(plan_repo):
    root, _write_doc, _git = plan_repo

    result = CliRunner().invoke(
        app, ["plan", "0090", "--root", str(root), "--no-dry-run", "--partition", "p/q"]
    )

    assert result.exit_code == 0, result.output
    assert "no file written" in result.output
    assert not (root / ".torve" / "tasks").exists()
