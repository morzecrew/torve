"""S-0007/torve-plan: the deterministic minter. Admission refuses by name with
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
    inherit_decisions,
    plan_document,
    standing_decisions,
    write_contracts,
)
from torve.base.globs import overlap
from torve.cli import app
from torve.config.spec import SpecError, load_document
from torve.domain.spec import Document
from torve.domain.task import InheritedDecision
from torve.gates.context import load_task

# ----------------------- #

TABLE = [
    ("S-0090/D-1", "LOCKED", "Widgets are idempotent", "`src/widget/**`", "Retries double-charge"),
    ("S-0090/D-2", "ASSUMED", "Frobnication is lazy", "—", "Cheap to revisit"),
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
    """One document's `S-NNNN/` directory under *spec_dir* (S-0057/D-1)."""

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
    assert [d.id for d in core.decisions] == ["S-0090/D-1", "S-0090/D-2"]
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


def test_a_phase_an_amendment_added_is_minted_alone(plan_repo):
    """bloomery S-0002/A-4: phases 3-5 minted and landed, a phase 6 added by
    amendment, and `torve plan` refused the document as already minted. With
    `--phase` only the named phase is minted, and its edge points at the
    task the phase it depends on already has."""
    root, write_doc, git = plan_repo
    specs = root / ".torve" / "specs"
    write_contracts(root, plan_document(root, specs, "0090"))  # T-0001..T-0003
    later = {
        "phase": 3,
        "title": "polish",
        "intent": "Polish the wired thing.",
        "scope": ["src/polish.py"],
        "depends_on": [2],
    }
    write_doc("0090", "Widgets", phasing=[*PHASING, later])
    git("add", "-A")
    git("commit", "-qm", "phase 3 by amendment")

    (planned,) = plan_document(root, specs, "0090", phases={3}).tasks

    assert planned.task.id == "T-0004"
    assert planned.task.phase == 3
    assert planned.task.depends_on == ["T-0003"]

    with pytest.raises(PlanError, match="already minted"):
        plan_document(root, specs, "0090", phases={1})

    with pytest.raises(PlanError, match="not in its phasing"):
        plan_document(root, specs, "0090", phases={9})


def test_draft_documents_are_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft")
    git("add", "-A")
    git("commit", "-qm", "draft")
    with pytest.raises(PlanError, match="S-0091 is draft"):
        plan_document(root, root / ".torve" / "specs", "0091")


def test_a_draft_dependency_is_refused(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft", phasing=None)
    write_doc("0092", "Leaning", depends_on=["0091"])
    git("add", "-A")
    git("commit", "-qm", "docs")
    with pytest.raises(PlanError, match="S-0092 depends on S-0091, which is draft"):
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
    path never did. S-0052's phasing block minted three contracts that
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
    ungraded = [("S-0090/D-1", "MAYBE", "Widgets are idempotent", "`src/widget/**`")]

    with pytest.raises(SpecError, match=r"decisions\.0\.grade"):
        loaded(tmp_path, rows=ungraded)


def test_rfc_check_reddens_on_a_phasing_entry_the_model_refuses(plan_repo):
    root, write_doc, _git = plan_repo
    write_doc("0098", "Broken", phasing=[{"phase": 0}])
    result = CliRunner().invoke(app, ["spec", "check", "--root", str(root)])
    assert result.exit_code == 3
    assert "phasing.0" in result.output


# ....................... #
# `after`: another document's landed tree (S-0085/D-1, S-0085/D-2)


def after(root: Path, number: str, *ids: str) -> None:
    """A top-level `after` on one document's phasing file, below its schema line."""

    path = root / ".torve" / "specs" / f"S-{number}" / "phasing.yaml"
    head, _, rest = path.read_text(encoding="utf-8").partition("\n")
    path.write_text(f"{head}\nafter: {list(ids)}\n{rest}", encoding="utf-8")


def test_after_makes_the_unpreceded_phases_wait_on_the_named_documents_tasks(plan_repo):
    root, write_doc, git = plan_repo
    spec_dir = root / ".torve" / "specs"
    write_contracts(root, plan_document(root, spec_dir, "0090"))
    write_doc("0092", "Leaning")
    after(root, "0092", "S-0090")
    git("add", "-A")
    git("commit", "-qm", "after")

    tasks = {p.task.id: p.task for p in plan_document(root, spec_dir, "0092").tasks}

    # Phase 1 has no in-document predecessor, so it waits on all of S-0090;
    # phase 2 waits through phase 1 and gains no edge of its own.
    assert tasks["T-0004"].depends_on == ["T-0001", "T-0002", "T-0003"]
    assert tasks["T-0005"].depends_on == ["T-0001", "T-0002", "T-0003"]
    assert set(tasks["T-0006"].depends_on) == {"T-0004", "T-0005"}


def test_after_naming_a_document_with_no_minted_tasks_is_refused_by_name(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0092", "Leaning")
    after(root, "0092", "S-0090")
    git("add", "-A")
    git("commit", "-qm", "after")

    with pytest.raises(PlanError, match="after names S-0090, which has no minted tasks"):
        plan_document(root, root / ".torve" / "specs", "0092")


def test_after_naming_a_complete_document_adds_no_edge(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0090", "Widgets", implementation="complete")
    write_doc("0092", "Leaning")
    after(root, "0092", "S-0090")
    git("add", "-A")
    git("commit", "-qm", "after")

    report = plan_document(root, root / ".torve" / "specs", "0092")

    assert [p.task.depends_on for p in report.tasks] == [[], [], ["T-0001", "T-0002"]]


def test_after_naming_no_document_is_refused_by_name(plan_repo):
    root, write_doc, git = plan_repo
    write_doc("0092", "Leaning")
    after(root, "0092", "S-0099")
    git("add", "-A")
    git("commit", "-qm", "after")

    with pytest.raises(PlanError, match="after names 'S-0099', no such document"):
        plan_document(root, root / ".torve" / "specs", "0092")


def test_scope_overlap_is_exact():
    assert overlap(["src/widget/**"], ["src/widget/core.py"])
    assert overlap(["src/a/**"], ["src/a/**"])
    assert not overlap(["src/a/**"], ["src/b/**"])
    assert not overlap(["tests/**"], ["src/**"])


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
        ("S-0090/D-1", "LOCKED", ["src/widget/**"]),
        ("S-0090/D-2", "ASSUMED", []),
    ]


# ....................... #
# Standing inheritance (S-0030/standing-inheritance): the document-less lane.


def test_standing_decisions_intersect_in_and_out(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0091",
        "Frobs",
        phasing=None,
        rows=[
            (
                "S-0091/D-1",
                "LOCKED",
                "Frobs are idempotent",
                "`src/frob/**`",
                "Retries double-charge",
            ),
            (
                "S-0091/D-2",
                "ASSUMED",
                "Frob names are short",
                "`tests/frob/**`",
                "Cheap to revisit",
            ),
        ],
    )
    git("add", "-A")
    git("commit", "-qm", "frobs")

    inside = standing_decisions(root / ".torve" / "specs", ["src/frob/core.py"])
    assert [d.id for d in inside] == ["S-0091/D-1"]

    outside = standing_decisions(root / ".torve" / "specs", ["src/widget/core.py"])
    assert [d.id for d in outside] == ["S-0090/D-1"]

    both = standing_decisions(root / ".torve" / "specs", ["src/**"])
    assert [d.id for d in both] == ["S-0090/D-1", "S-0091/D-1"]

    unconstrained = standing_decisions(root / ".torve" / "specs", [])
    assert unconstrained == []


def test_standing_decisions_copy_grade_and_paths_at_write_time(plan_repo):
    root, _, _ = plan_repo
    assert standing_decisions(root / ".torve" / "specs", ["src/widget/core.py"]) == [
        InheritedDecision(
            id="S-0090/D-1",
            grade="LOCKED",
            text="Widgets are idempotent",
            paths=["src/widget/**"],
            consequence="Retries double-charge",
        )
    ]


def test_standing_decisions_pathless_rows_are_never_standing(plan_repo):
    root, _, _ = plan_repo
    # S-0090/D-2 declares no paths — it governs its own document's work only and
    # can never be standing, even against an allow that would cover anything.
    rows = standing_decisions(root / ".torve" / "specs", ["src/**", "tests/**"])
    assert [d.id for d in rows] == ["S-0090/D-1"]
    assert "S-0090/D-2" not in [d.id for d in rows]


def test_standing_decisions_never_read_draft_or_superseded_documents(plan_repo):
    root, write_doc, git = plan_repo
    write_doc(
        "0092",
        "Sketch",
        status="draft",
        phasing=None,
        rows=[("S-0092/D-1", "LOCKED", "A draft's rule", "`src/widget/**`")],
    )
    write_doc(
        "0093",
        "Old",
        status="accepted",
        superseded_by="0090",
        phasing=None,
        rows=[("S-0093/D-1", "LOCKED", "A superseded rule", "`src/widget/**`")],
    )
    git("add", "-A")
    git("commit", "-qm", "non-standing docs")

    rows = standing_decisions(root / ".torve" / "specs", ["src/widget/**"])
    assert [d.id for d in rows] == ["S-0090/D-1"]


def test_a_phase_defaults_tier_variant_and_character_to_absent(tmp_path):
    """S-0034 S-0034/D-2: absent means no character, the same
    absent-means-default shape tier_variant carries. Since S-0059/D-5 the
    phase and the contract share one `Character`, so absent is None on
    both rather than the empty string on one of them."""

    doc = loaded(tmp_path, phasing=PHASING)
    assert [e.tier_variant for e in doc.phasing] == ["", "", ""]
    assert [e.character for e in doc.phasing] == [None, None, None]


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
    assert doc.phasing[1].character is None


def test_the_loader_refuses_a_character_outside_the_vocabulary(tmp_path):
    """S-0034/D-1: structural|routine is a closed vocabulary — compliance is
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
# S-0054 phase 1: the row travels whole (S-0054/D-1), and a blocking check
# needs its twin (S-0054/D-4).

DETAILS = {
    "S-0090/D-1": {
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
    details = {"S-0090/D-1": {k: v for k, v in DETAILS["S-0090/D-1"].items() if k != "check_twin"}}

    with pytest.raises(PlanError, match="no check_twin"):
        inherit_decisions(loaded(tmp_path, details=details))


# ....................... #
# S-0056 phase 3 (S-0056/D-9): with a store, plan mints into the record and
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
    first, second = written.read_text(encoding="utf-8").splitlines()[:2]
    assert first == "# yaml-language-server: $schema=../../schemas/contract.json"  # S-0057/D-5
    assert second.startswith("# Projected from the record")
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


# ....................... #
# S-0088 phase 1 (S-0088/D-1 .. S-0088/D-4): `--refresh` carries an amendment to
# the contracts the document already minted, and never to one in flight.

AMENDED_TABLE = [
    (
        "S-0090/D-1",
        "LOCKED",
        "Widgets are idempotent and counted",
        "`src/widget/**`",
        "Retries double-charge",
    ),
    ("S-0090/D-2", "ASSUMED", "Frobnication is lazy", "—", "Cheap to revisit"),
]


def amend(root, write_doc, git, **overrides) -> None:
    """The document as it now stands: the table amended, and whatever the
    case changes about its first phasing entry."""

    write_doc("0090", "Widgets", rows=AMENDED_TABLE, phasing=phasing(**overrides))
    git("add", "-A")
    git("commit", "-qm", "amend")


def contract_of(root: Path, task_id: str):
    return load_task(root / ".torve" / "tasks" / task_id / "contract.yaml")


def minted(root: Path) -> None:
    write_contracts(root, plan_document(root, root / ".torve" / "specs", "0090"))


def test_refresh_rewrites_the_amended_phases_and_keeps_id_edges_and_minted_by(plan_repo):
    from test_residency import engine_events

    from torve.application.planner import refresh_contracts, refresh_document

    root, write_doc, git = plan_repo
    spec_dir = root / ".torve" / "specs"
    minted(root)
    amend(
        root,
        write_doc,
        git,
        intent="Build the widget core, counted.",
        acceptance=["make test", "make count"],
    )

    report = refresh_document(root, spec_dir, "0090")
    core, frob, wire = report.tasks

    assert (core.task_id, core.phase) == ("T-0001", 1)
    assert core.changed == ["intent", "acceptance", "decisions"]
    # the table is inherited by every phase, so every contract differs
    assert frob.changed == ["decisions"] and wire.changed == ["decisions"]
    assert [one.held for one in report.tasks] == ["", "", ""]

    written = refresh_contracts(root, report)

    assert [p.parent.name for p in written] == ["T-0001", "T-0002", "T-0003"]

    task = contract_of(root, "T-0001")

    assert task.id == "T-0001" and task.spec == "S-0090" and task.phase == 1
    assert task.intent == "Build the widget core, counted."
    assert task.acceptance == ["make test", "make count"]
    assert task.decisions[0].text == "Widgets are idempotent and counted"
    # id, edges and minted-by are the mint's and stay it
    assert contract_of(root, "T-0003").depends_on == ["T-0001", "T-0002"]
    head = written[0].read_text(encoding="utf-8").splitlines()[:3]
    assert head[1].startswith("# Minted by `torve plan S-0090`")
    assert head[2].startswith("# Refreshed by `torve plan S-0090 --refresh` at ")

    rows = engine_events(root, "contract_refreshed")

    assert [r["task"] for r in rows] == ["T-0001", "T-0002", "T-0003"]
    assert rows[0]["spec"] == "S-0090" and rows[0]["fields"] == core.changed


def test_refresh_leaves_a_running_landed_or_branch_carried_task_alone_and_names_it(plan_repo):
    """S-0101/D-7: the three reasons come from git — a run-state file, a
    landing the base holds, and one a document branch carries — with the
    stream empty, so a refresh on any host leaves the same tasks alone."""

    from test_decisions import landed
    from test_residency import engine_events

    from torve.application.planner import refresh_contracts, refresh_document
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    root, write_doc, git = plan_repo
    minted(root)
    amend(root, write_doc, git, intent="Build the widget core, counted.")

    # T-0003 rides the document branch, whose landing the base does not hold —
    # the branch is cut before T-0002 lands on main, so it carries T-0003 only.
    git("checkout", "-q", "-b", "torve/S-0090")
    landed(root, "T-0003", spec="0090")
    git("add", "-A")
    git("commit", "-qm", "carried")
    git("checkout", "-q", "-")
    running = RunState(task_id="T-0001", path=naming.state_file(root, "T-0001"))
    running.state = TaskState.RUNNING
    running.save()
    landed(root, "T-0002", spec="0090")
    git("add", "-A")
    git("commit", "-qm", "landing")  # a landing lives in the document's directory

    report = refresh_document(root, root / ".torve" / "specs", "0090")

    assert [one.held for one in report.tasks] == [
        "running (running)",
        "landed",
        "carried by the branch torve/S-0090",
    ]
    assert report.rewritten == []
    assert refresh_contracts(root, report) == []
    assert engine_events(root, "contract_refreshed") == []
    assert contract_of(root, "T-0001").intent == "Build the widget core."


def test_refresh_rewrites_an_escalated_task_with_no_landing(plan_repo):
    from torve.application.planner import refresh_contracts, refresh_document
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import EscalationReason, TaskState

    root, write_doc, git = plan_repo
    minted(root)
    amend(root, write_doc, git, intent="Build the widget core, counted.")

    state = RunState(task_id="T-0001", path=naming.state_file(root, "T-0001"))
    state.transition(TaskState.CLAIMED, "test")
    state.escalate(EscalationReason.POISON_CEILING, "three reds")

    report = refresh_document(root, root / ".torve" / "specs", "0090")

    assert report.tasks[0].held == ""

    refresh_contracts(root, report)

    assert contract_of(root, "T-0001").intent == "Build the widget core, counted."


def test_refresh_is_a_dry_run_by_default_and_writes_nothing(plan_repo):
    root, write_doc, git = plan_repo
    minted(root)
    amend(root, write_doc, git, intent="Build the widget core, counted.")

    result = CliRunner().invoke(app, ["plan", "0090", "--refresh", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    assert contract_of(root, "T-0001").intent == "Build the widget core."


def test_a_refresh_with_nothing_to_change_writes_nothing_and_says_so(plan_repo):
    root, _write_doc, _git = plan_repo
    minted(root)
    before = contract_of(root, "T-0001").model_dump()

    result = CliRunner().invoke(
        app, ["plan", "0090", "--refresh", "--no-dry-run", "--root", str(root)]
    )

    assert result.exit_code == 0, result.output
    assert "nothing to refresh" in result.output
    assert contract_of(root, "T-0001").model_dump() == before
    assert not (root / ".torve" / "telemetry.jsonl").exists()


def test_refresh_refuses_an_unaccepted_document_as_plan_refuses_it(plan_repo):
    from torve.domain.states import EXIT_CONFIG

    root, write_doc, git = plan_repo
    write_doc("0091", "Sketch", status="draft")
    git("add", "-A")
    git("commit", "-qm", "draft")

    result = CliRunner().invoke(app, ["plan", "0091", "--refresh", "--root", str(root)])

    assert result.exit_code == EXIT_CONFIG
    assert "S-0091 is draft" in result.output


def test_refresh_under_a_partition_rewrites_the_records_contract(plan_repo):
    from test_residency import PARTITION, run

    from torve.application.manager import project
    from torve.application.planner import (
        mint_contracts,
        refresh_document,
        refresh_into_record,
    )

    root, write_doc, git = plan_repo
    spec_dir = root / ".torve" / "specs"

    async def scenario(log):
        await mint_contracts(log, plan_document(root, spec_dir, "0090"), partition=PARTITION)
        amend(root, write_doc, git, intent="Build the widget core, counted.")
        board = project(await log.since(partition=PARTITION))
        report = refresh_document(root, spec_dir, "0090", board=board)

        assert report.tasks[0].changed == ["intent", "decisions"]

        refreshed = await refresh_into_record(root, log, report, partition=PARTITION)

        assert refreshed == ["T-0001", "T-0002", "T-0003"]

        board = project(await log.since(partition=PARTITION))

        assert board.tasks["T-0001"].contract.intent == "Build the widget core, counted."
        assert board.tasks["T-0001"].contract.depends_on == []
        assert not (root / ".torve" / "tasks").exists()

    run(scenario)


def test_refresh_reads_the_document_from_the_remotes_branch_and_the_rest_from_the_checkout(
    plan_repo,
):
    """S-0094/D-1: a phase widened on the remote's document branch reaches the
    refresh, while the checkout keeps the text it had."""

    from torve.application.planner import refresh_document

    root, write_doc, git = plan_repo
    spec_dir = root / ".torve" / "specs"
    minted(root)
    git("add", "-A")
    git("commit", "-qm", "minted")
    git("checkout", "-q", "-b", "torve/S-0090")
    amend(root, write_doc, git, scope=["src/widget/**", "tests/widget/**", "pages/**"])
    git("update-ref", "refs/remotes/origin/torve/S-0090", "HEAD")
    git("checkout", "-q", "-")
    git("branch", "-q", "-D", "torve/S-0090")

    core = refresh_document(root, spec_dir, "0090", remote_branch="torve/S-0090").tasks[0]

    assert core.changed == ["scope", "decisions"]
    assert core.task is not None and "pages/**" in core.task.scope.allow
    assert refresh_document(root, spec_dir, "0090").tasks[0].changed == []


def test_refresh_reads_the_checkout_when_the_remote_has_no_branch(plan_repo):
    from torve.application.planner import refresh_document

    root, write_doc, git = plan_repo
    minted(root)
    amend(root, write_doc, git, intent="Build the widget core, counted.")

    report = refresh_document(root, root / ".torve" / "specs", "0090", remote_branch="torve/S-0090")

    assert report.tasks[0].changed == ["intent", "decisions"]


# ....................... #
# S-0104/D-1: the planner fills `scope.contingent` at mint and refresh, and
# ignores it for same-phase disjointness and sizing.


def test_minting_derives_the_contingent_neighbours(plan_repo):
    root, _, git = plan_repo
    core = root / "src" / "widget" / "core.py"
    core.parent.mkdir(parents=True)
    (core.parent / "__init__.py").write_text("", encoding="utf-8")
    core.write_text("def core():\n    return 1\n", encoding="utf-8")
    (root / "src" / "frob" / "thing.py").parent.mkdir(parents=True)
    (root / "src" / "frob" / "thing.py").write_text("thing = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "source")

    report = plan_document(root, root / ".torve" / "specs", "0090")
    frob = report.tasks[1].task

    # src/frob/** imports nothing, but its sibling phase's scope names src/app.py
    # (nothing yet) and its own `__init__` — the union is what the tree can show.
    assert frob.scope.contingent == []
    assert report.tasks[0].task.scope.allow == ["src/widget/**", "tests/widget/**"]


def test_same_phase_disjointness_ignores_the_contingent_set(plan_repo):
    root, _, git = plan_repo
    for relative in ("src/widget/__init__.py", "src/frob/__init__.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (root / "src" / "widget" / "core.py").write_text(
        "def core():\n    return 1\n", encoding="utf-8"
    )
    (root / "src" / "frob" / "thing.py").write_text("thing = 1\n", encoding="utf-8")
    # One file reaches both phases — their contingent sets now overlap.
    (root / "src" / "shared.py").write_text(
        "import widget.core\nimport frob.thing\n", encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "source")

    report = plan_document(root, root / ".torve" / "specs", "0090")

    widget, frob = report.tasks[0].task, report.tasks[1].task
    assert "src/shared.py" in widget.scope.contingent
    assert "src/shared.py" in frob.scope.contingent
    # The overlap is contingent, not allowed, so the same-phase pair still mints.
    assert not set(widget.scope.allow) & set(frob.scope.allow)


def test_refresh_rewrites_the_contingent_set(plan_repo):
    from torve.application.planner import refresh_contracts, refresh_document

    root, _, git = plan_repo
    core = root / "src" / "widget" / "core.py"
    core.parent.mkdir(parents=True)
    (core.parent / "__init__.py").write_text("", encoding="utf-8")
    core.write_text("def core():\n    return 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "source")
    minted(root)

    assert contract_of(root, "T-0001").scope.contingent == []

    # A new importer arrives outside the phase's scope, so the next derivation
    # reaches further than the mint did.
    (root / "src" / "consumer.py").write_text(
        "import widget.core\n\nconsumer = 1\n", encoding="utf-8"
    )
    git("add", "src/consumer.py")
    git("commit", "-qm", "consumer")

    report = refresh_document(root, root / ".torve" / "specs", "0090")

    assert report.tasks[0].changed == ["scope"]
    assert "src/consumer.py" in report.tasks[0].task.scope.contingent

    refresh_contracts(root, report)

    assert "src/consumer.py" in contract_of(root, "T-0001").scope.contingent


def test_the_plan_table_shows_the_contingent_count(plan_repo):
    root, _, git = plan_repo
    core = root / "src" / "widget" / "core.py"
    core.parent.mkdir(parents=True)
    (core.parent / "__init__.py").write_text("", encoding="utf-8")
    core.write_text("def core():\n    return 1\n", encoding="utf-8")
    (root / "src" / "consumer.py").write_text("import widget.core\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "source")

    result = CliRunner().invoke(app, ["plan", "0090", "--root", str(root)], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "contingent" in result.output


# ----------------------- #
# S-0116/D-4, D-5: the verify pilot. With it on, a behavioural phase mints a
# verifier ahead of its implement task; with it off, exactly today's tasks.

PILOT = (
    "schema_version: 1\n"
    "verify: {enabled: true, variant: verify, types: [feat, fix], tests_dir: tests/verify}\n"
    "tiers:\n"
    "  executor.verify: {harness: fake}\n"
)
RUN = "python3 -m pytest -q -p no:cacheprovider"


@pytest.fixture
def pilot_repo(plan_repo):
    """`plan_repo` with the verify pilot on — a verify seat configured, so the
    configuration loads."""

    root, write_doc, git = plan_repo
    (root / ".torve" / "config.yaml").write_text(PILOT, encoding="utf-8")
    harness = root / ".torve" / "harnesses"
    harness.mkdir(parents=True)
    (harness / "fake.yaml").write_text("adapter: fake\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "pilot on")

    return root, write_doc, git


def a_feat_doc(write_doc, git, number: str, phasing: list[dict]) -> None:
    write_doc(number, "Feat", phasing=phasing, extra={"change": {"type": "feat"}})
    git("add", "-A")
    git("commit", "-qm", f"doc {number}")


def test_a_feat_documents_source_phase_earns_a_verifier(pilot_repo):
    root, write_doc, git = pilot_repo
    a_feat_doc(
        write_doc,
        git,
        "0091",
        [
            {
                "phase": 1,
                "title": "core",
                "intent": "Build the core.",
                "scope": ["src/core/**"],
                "acceptance": ["make test"],
            }
        ],
    )

    report = plan_document(root, root / ".torve" / "specs", "0091")

    assert [p.task.id for p in report.tasks] == ["T-0001", "T-0002"]
    verify, implement = report.tasks[0].task, report.tasks[1].task

    assert verify.role == "verify" and verify.targets == []
    assert verify.scope.allow == ["tests/verify/test_s0091_p1.py"]
    assert verify.scope.deny == []
    assert verify.acceptance == [f"{RUN} --collect-only tests/verify/test_s0091_p1.py"]
    assert verify.tier_variant == "verify"
    assert verify.intent.endswith("Build the core.") and verify.intent != "Build the core."

    # D-5: the implementer runs the verifier's tests, depends on its task, and
    # may never edit the file they live in.
    assert implement.depends_on == ["T-0001"]
    assert implement.scope.deny == ["tests/verify/test_s0091_p1.py"]
    assert implement.acceptance == [
        "make test",
        f"{RUN} tests/verify/test_s0091_p1.py",
    ]


def test_sibling_entries_of_one_phase_get_their_own_verifiers(pilot_repo):
    root, write_doc, git = pilot_repo
    a_feat_doc(
        write_doc,
        git,
        "0092",
        [
            {"phase": 1, "title": "a", "intent": "A.", "scope": ["src/a/**"]},
            {"phase": 1, "title": "b", "intent": "B.", "scope": ["src/b/**"]},
        ],
    )

    report = plan_document(root, root / ".torve" / "specs", "0092")
    files = [p.task.scope.allow[0] for p in report.tasks if p.task.role == "verify"]

    assert files == [
        "tests/verify/test_s0092_p1.py",
        "tests/verify/test_s0092_p1_2.py",
    ]


def test_a_docs_only_phase_earns_no_verifier(pilot_repo):
    root, write_doc, git = pilot_repo
    a_feat_doc(
        write_doc,
        git,
        "0093",
        [{"phase": 1, "title": "docs", "intent": "Write it up.", "scope": ["docs/**"]}],
    )

    report = plan_document(root, root / ".torve" / "specs", "0093")

    assert [p.task.role for p in report.tasks] == ["implement"]


def test_a_refactor_document_earns_no_verifier(pilot_repo):
    root, write_doc, git = pilot_repo
    write_doc(
        "0094",
        "Refactor",
        phasing=[{"phase": 1, "title": "move", "intent": "Move it.", "scope": ["src/a/**"]}],
        extra={"change": {"type": "refactor"}},
    )
    git("add", "-A")
    git("commit", "-qm", "refactor doc")

    report = plan_document(root, root / ".torve" / "specs", "0094")

    assert [p.task.role for p in report.tasks] == ["implement"]


def test_the_pilot_off_mints_exactly_todays_tasks(plan_repo):
    root, _, _ = plan_repo

    report = plan_document(root, root / ".torve" / "specs", "0090")

    assert [p.task.role for p in report.tasks] == ["implement", "implement", "implement"]


def test_refresh_pairs_a_role_with_its_own_derivation(pilot_repo):
    """S-0116/D-4, D-5: a phase minted under the pilot holds a verifier beside
    its implementer, and `--refresh` diffs each contract against the
    derivation of its own role — an implement contract is never rewritten
    with a verify task's terms, or the reverse."""

    from torve.application.planner import refresh_contracts, refresh_document

    root, write_doc, git = pilot_repo
    entry = {
        "phase": 1,
        "title": "core",
        "intent": "Build the core.",
        "scope": ["src/core/**"],
        "acceptance": ["make test"],
    }
    a_feat_doc(write_doc, git, "0095", [entry])
    spec_dir = root / ".torve" / "specs"
    write_contracts(root, plan_document(root, spec_dir, "0095"))
    a_feat_doc(write_doc, git, "0095", [entry | {"intent": "Build the core, counted."}])

    report = refresh_document(root, spec_dir, "0095")
    verify, implement = report.tasks

    assert (verify.task_id, verify.phase, verify.changed) == ("T-0001", 1, ["intent"])
    assert (implement.task_id, implement.changed) == ("T-0002", ["intent"])

    assert [p.parent.name for p in refresh_contracts(root, report)] == ["T-0001", "T-0002"]

    rewritten = contract_of(root, "T-0001")

    assert rewritten.role == "verify" and rewritten.intent.endswith("Build the core, counted.")
    assert rewritten.tier_variant == "verify"
    assert rewritten.scope.allow == ["tests/verify/test_s0095_p1.py"]
    # the verifier's file and run survive the refresh on the implementer
    keeper = contract_of(root, "T-0002")

    assert keeper.role == "implement" and keeper.intent == "Build the core, counted."
    assert keeper.scope.deny == ["tests/verify/test_s0095_p1.py"]
    assert keeper.acceptance == ["make test", f"{RUN} tests/verify/test_s0095_p1.py"]


def test_refresh_does_not_wire_a_phase_that_holds_no_verifier(pilot_repo):
    """S-0116/D-4, D-5: a phase minted before the pilot was on holds no verify
    task, and a refresh mints none — so its implement contract is diffed
    against the derivation without a verifier's file to deny or run, and the
    refresh rewrites nothing."""

    from torve.application.planner import refresh_contracts, refresh_document

    root, write_doc, git = pilot_repo
    config = root / ".torve" / "config.yaml"
    config.write_text("schema_version: 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "pilot off")
    a_feat_doc(
        write_doc,
        git,
        "0096",
        [
            {
                "phase": 1,
                "title": "core",
                "intent": "Build the core.",
                "scope": ["src/core/**"],
                "acceptance": ["make test"],
            }
        ],
    )
    spec_dir = root / ".torve" / "specs"
    write_contracts(root, plan_document(root, spec_dir, "0096"))
    config.write_text(PILOT, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "pilot on")

    report = refresh_document(root, spec_dir, "0096")

    assert [one.changed for one in report.tasks] == [[]]
    assert refresh_contracts(root, report) == []
