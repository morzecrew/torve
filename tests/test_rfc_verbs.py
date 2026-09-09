"""`torve rfc amend`, `add-decision`, `retire` and `relocate-paths` (RFC 0025
§5.3): each is a load-mutate-dump-check transaction that aborts whole on a
red check, leaving the tree untouched.
"""

from __future__ import annotations

from pathlib import Path

from test_decisions import document
from typer.testing import CliRunner

from torve.cli import app
from torve.config.spec import load_document

runner = CliRunner()

EXIT_CONFIG = 3

DOC = document(
    "0001",
    [
        ("D-1.1", "ASSUMED", "Something is decided", "`src/thing/**`", "Nothing yet"),
        ("D-1.2", "ASSUMED", "Something else is decided", "`src/other/**`", "Nothing yet"),
    ],
    title="Widget",
    status="draft",
    implementation="none",
    amendments=[{"id": "A-1", "at": "2026-01-01", "title": "first amendment", "md": "Prose.\n"}],
)

BROKEN_SECOND_DOC = document(
    "0002",
    [("D-2.1", "MAYBE", "An ungraded row", "—")],
    title="Broken",
    status="draft",
    implementation="none",
)


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, str]) -> Path:
    """Write (filename, text) documents and return the corpus dir."""

    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)

    for name, text in docs:
        (rfcs / name).write_text(text, encoding="utf-8")

    return rfcs


def widget(rfcs: Path):
    return load_document(rfcs / "0001-widget.yaml")


# ....................... #
# rfc amend


def test_amend_appends_derived_number_and_records_amended_by(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "amend", "0001", "--title", "second amendment")
    assert result.exit_code == 0, result.output
    assert "A-2" in result.output

    doc = widget(rfcs)
    assert doc.amended_by == ["A-1", "A-2"]
    assert doc.amendments[-1].id == "A-2"
    assert doc.amendments[-1].title == "second amendment"


def test_amend_derives_the_next_number_corpus_wide(tmp_path: Path) -> None:
    second = document(
        "0002",
        [("D-2.1", "ASSUMED", "Something is decided", "`src/other/**`")],
        title="Other",
        status="draft",
        implementation="none",
        amendments=[{"id": "A-9", "at": "2026-01-01", "title": "ninth", "md": "Prose.\n"}],
    )
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC), ("0002-other.yaml", second))

    result = invoke(tmp_path, "amend", "0001", "--title", "third amendment")
    assert result.exit_code == 0, result.output
    assert "A-10" in result.output

    assert widget(rfcs).amendments[-1].id == "A-10"


def test_amend_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC), ("0002-broken.yaml", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.yaml").read_text(encoding="utf-8")

    result = invoke(tmp_path, "amend", "0001", "--title", "never lands")
    assert result.exit_code == EXIT_CONFIG
    assert "PROBLEM" in result.output
    assert (rfcs / "0001-widget.yaml").read_text(encoding="utf-8") == before  # tree untouched


# ....................... #
# rfc add-decision


def test_add_decision_appends_a_row_with_open_grade(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "D-1.3" in result.output

    row = widget(rfcs).decision("D-1.3")
    assert row is not None
    assert row.grade == "OPEN" and row.text == "<decision>" and row.paths == []


def test_add_decision_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC), ("0002-broken.yaml", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.yaml").read_text(encoding="utf-8")

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.yaml").read_text(encoding="utf-8") == before


# ....................... #
# rfc retire


def test_retire_removes_the_row_and_records_it_retired(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "retire", "D-1.1")
    assert result.exit_code == 0, result.output

    doc = widget(rfcs)
    assert doc.decision("D-1.1") is None
    assert doc.retired == ["D-1.1"]
    assert doc.decision("D-1.2") is not None  # the other row is untouched

    check = invoke(tmp_path, "check")
    assert check.exit_code == 0, check.output  # a retired identifier still resolves


def test_add_decision_skips_a_retired_identifier_at_the_top_of_the_family(
    tmp_path: Path,
) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC))
    retired = invoke(tmp_path, "retire", "D-1.2")  # D-1.2 is the family's highest number
    assert retired.exit_code == 0, retired.output

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "D-1.3" in result.output  # not D-1.2 again — retired ids are never reused

    assert widget(rfcs).decision("D-1.3") is not None


def test_retire_refuses_an_unknown_identifier(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "retire", "D-9.9")
    assert result.exit_code == EXIT_CONFIG
    assert "D-9.9" in result.output


def test_retire_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC), ("0002-broken.yaml", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.yaml").read_text(encoding="utf-8")

    result = invoke(tmp_path, "retire", "D-1.1")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.yaml").read_text(encoding="utf-8") == before


# ....................... #
# rfc relocate-paths


def test_relocate_paths_sweeps_only_matching_rows(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == 0, result.output
    assert "D-1.1" in result.output

    doc = widget(rfcs)
    first, second = doc.decision("D-1.1"), doc.decision("D-1.2")
    assert first is not None and first.paths == ["src/moved/**"]
    assert second is not None and second.paths == ["src/other/**"]  # untouched


def test_relocate_paths_with_no_matching_row_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/nowhere/**", "src/elsewhere/**")
    assert result.exit_code == EXIT_CONFIG


def test_relocate_paths_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.yaml", DOC), ("0002-broken.yaml", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.yaml").read_text(encoding="utf-8")

    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.yaml").read_text(encoding="utf-8") == before
