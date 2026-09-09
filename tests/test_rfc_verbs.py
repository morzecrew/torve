"""`torve spec amend`, `add-decision`, `retire` and `relocate-paths` (S-0025
§5.3): each is a load-mutate-dump-check transaction that aborts whole on a
red check, leaving the document's directory untouched.
"""

from __future__ import annotations

from pathlib import Path

from test_decisions import Doc, corpus, document
from typer.testing import CliRunner

from torve.cli import app
from torve.config.spec import load_document

runner = CliRunner()

EXIT_CONFIG = 3

DOC = document(
    "0001",
    [
        ("S-0001/D-1", "ASSUMED", "Something is decided", "`src/thing/**`", "Nothing yet"),
        ("S-0001/D-2", "ASSUMED", "Something else is decided", "`src/other/**`", "Nothing yet"),
    ],
    title="Widget",
    status="draft",
    implementation="none",
    amendments=[{"id": "A-1", "at": "2026-01-01", "title": "first amendment", "md": "Prose.\n"}],
)

BROKEN_SECOND_DOC = document(
    "0002",
    [("S-0002/D-1", "MAYBE", "An ungraded row", "—")],
    title="Broken",
    status="draft",
    implementation="none",
)


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["spec", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, Doc]) -> Path:
    """Write (number, document) pairs and return the corpus dir."""

    return corpus(tmp_path, **dict(docs))


def widget(rfcs: Path):
    return load_document(rfcs / "S-0001")


def files(directory: Path) -> dict[str, str]:
    """Every file of one document's directory — what "the tree untouched"
    means now that a document is four files."""

    return {path.name: path.read_text(encoding="utf-8") for path in sorted(directory.iterdir())}


# ....................... #
# spec amend


def test_amend_appends_derived_number_and_records_amended_by(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "amend", "0001", "--title", "second amendment")
    assert result.exit_code == 0, result.output
    assert "S-0001/A-2" in result.output

    doc = widget(rfcs)
    assert doc.amended_by() == ["S-0001/A-1", "S-0001/A-2"]
    assert doc.amendments[-1].id == "S-0001/A-2"
    assert doc.amendments[-1].title == "second amendment"


def test_amend_derives_the_next_number_within_the_document(tmp_path: Path) -> None:
    # S-0058/D-1: an amendment numbers inside its own document, so what another
    # document has amended never moves this one's next number.
    second = document(
        "0002",
        [("S-0002/D-1", "ASSUMED", "Something is decided", "`src/other/**`")],
        title="Other",
        status="draft",
        implementation="none",
        amendments=[
            {"id": f"A-{n}", "at": "2026-01-01", "title": "prior", "md": "Prose.\n"}
            for n in (1, 2, 3)
        ],
    )
    rfcs = seed(tmp_path, ("0001", DOC), ("0002", second))

    result = invoke(tmp_path, "amend", "0001", "--title", "third amendment")
    assert result.exit_code == 0, result.output
    assert "S-0001/A-2" in result.output

    assert widget(rfcs).amendments[-1].id == "S-0001/A-2"


def test_amend_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC), ("0002", BROKEN_SECOND_DOC))
    before = files(rfcs / "S-0001")

    result = invoke(tmp_path, "amend", "0001", "--title", "never lands")
    assert result.exit_code == EXIT_CONFIG
    assert "PROBLEM" in result.output
    assert files(rfcs / "S-0001") == before  # the directory untouched


# ....................... #
# spec add-decision


def test_add_decision_appends_a_row_with_open_grade(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "S-0001/D-3" in result.output

    row = widget(rfcs).decision("S-0001/D-3")
    assert row is not None
    assert row.grade == "OPEN" and row.text == "<decision>" and row.paths == []


def test_add_decision_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC), ("0002", BROKEN_SECOND_DOC))
    before = files(rfcs / "S-0001")

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == EXIT_CONFIG
    assert files(rfcs / "S-0001") == before


# ....................... #
# spec retire


def test_retire_removes_the_row_and_records_it_retired(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "retire", "S-0001/D-1")
    assert result.exit_code == 0, result.output

    doc = widget(rfcs)
    assert doc.decision("S-0001/D-1") is None
    assert doc.retired == ["S-0001/D-1"]
    assert doc.decision("S-0001/D-2") is not None  # the other row is untouched

    check = invoke(tmp_path, "check")
    assert check.exit_code == 0, check.output  # a retired identifier still resolves


def test_add_decision_skips_a_retired_identifier_at_the_top_of_the_family(
    tmp_path: Path,
) -> None:
    rfcs = seed(tmp_path, ("0001", DOC))
    retired = invoke(tmp_path, "retire", "S-0001/D-2")  # S-0001/D-2 is the family's highest number
    assert retired.exit_code == 0, retired.output

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "S-0001/D-3" in result.output  # not S-0001/D-2 again — retired ids are never reused

    assert widget(rfcs).decision("S-0001/D-3") is not None


def test_retire_refuses_an_unknown_identifier(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "retire", "S-0009/D-9")
    assert result.exit_code == EXIT_CONFIG
    assert "S-0009/D-9" in result.output


def test_retire_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC), ("0002", BROKEN_SECOND_DOC))
    before = files(rfcs / "S-0001")

    result = invoke(tmp_path, "retire", "S-0001/D-1")
    assert result.exit_code == EXIT_CONFIG
    assert files(rfcs / "S-0001") == before


# ....................... #
# spec relocate-paths


def test_relocate_paths_sweeps_only_matching_rows(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == 0, result.output
    assert "S-0001/D-1" in result.output

    doc = widget(rfcs)
    first, second = doc.decision("S-0001/D-1"), doc.decision("S-0001/D-2")
    assert first is not None and first.paths == ["src/moved/**"]
    assert second is not None and second.paths == ["src/other/**"]  # untouched


def test_relocate_paths_with_no_matching_row_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/nowhere/**", "src/elsewhere/**")
    assert result.exit_code == EXIT_CONFIG


def test_relocate_paths_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001", DOC), ("0002", BROKEN_SECOND_DOC))
    before = files(rfcs / "S-0001")

    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == EXIT_CONFIG
    assert files(rfcs / "S-0001") == before
