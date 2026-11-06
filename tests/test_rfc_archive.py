"""The loader knows the archive (S-0053 A-140): a citation into an
archived document resolves, a reference to an archived number warns
rather than refuses, the next number derives over both directories, and
`lookup` answers an archived identifier marked archived. Nothing here
makes an archived document stand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_decisions import Doc, archived, corpus
from test_decisions import document as _document

from torve.config.spec import archive_dir, check_corpus, lookup, next_number

# ----------------------- #


def document(number: str, decision: str, *, status: str = "accepted", **kw: Any) -> Doc:
    """One scratch document: the shared builder with this suite's row."""

    return _document(
        number,
        [(decision, "ASSUMED", "Something is decided", "—")],
        status=status,
        superseded_by="0002" if status == "superseded" else None,
        **kw,
    )


def seed(tmp_path: Path, standing: dict[str, Doc], gone: dict[str, Doc]) -> Path:
    specs = corpus(tmp_path, **standing)

    for number, doc in gone.items():
        archived(specs, number, doc)

    return specs


# ----------------------- #


def test_a_citation_into_the_archive_resolves(tmp_path: Path) -> None:
    citing = document(
        "0002",
        "S-0002/D-1",
        sections=[
            {
                "key": "design",
                "md": "This document builds on S-0001/D-1 and S-0001/D-2.\n",
            }
        ],
    )
    specs = seed(
        tmp_path, {"0002": citing}, {"0001": document("0001", "S-0001/D-1", status="superseded")}
    )

    report = check_corpus(specs, tmp_path)

    assert [p for p in report.problems if "S-0001/D-1" in p] == []
    assert any("cites S-0001/D-2" in p for p in report.problems)


def test_a_reference_to_an_archived_number_warns_and_an_unknown_one_refuses(
    tmp_path: Path,
) -> None:
    specs = seed(
        tmp_path,
        {"0002": document("0002", "S-0002/D-1", depends_on=["0001"], informed_by=["0009"])},
        {"0001": document("0001", "S-0001/D-1", status="superseded")},
    )

    report = check_corpus(specs, tmp_path)

    assert any("depends_on names S-0001, which is archived" in w for w in report.warnings)
    assert any("informed_by names 'S-0009', no such document" in p for p in report.problems)
    assert not any("0001" in p for p in report.problems)


def test_the_next_number_derives_over_corpus_and_archive(tmp_path: Path) -> None:
    specs = seed(
        tmp_path,
        {"0002": document("0002", "S-0002/D-1")},
        {"0007": document("0007", "S-0007/D-1", status="superseded")},
    )

    assert next_number(specs) == 8  # one derivation, over corpus and archive (S-0053/D-10)


def test_lookup_answers_an_archived_identifier_marked_archived(tmp_path: Path) -> None:
    specs = seed(
        tmp_path,
        {"0002": document("0002", "S-0002/D-1")},
        {"0001": document("0001", "S-0001/D-1", status="superseded")},
    )

    live = lookup(specs, "S-0002/D-1")
    gone = lookup(specs, "S-0001/D-1")
    doc = lookup(specs, "0001")

    assert live is not None and live["archived"] is False
    assert gone is not None and gone["archived"] is True
    assert doc is not None and doc["archived"] is True and doc["status"] == "superseded"
    assert lookup(specs, "S-0009/D-9") is None


def test_an_archived_document_never_counts_as_standing_in_the_check(tmp_path: Path) -> None:
    specs = seed(
        tmp_path,
        {"0002": document("0002", "S-0002/D-1")},
        {"0001": document("0001", "S-0001/D-1", status="superseded")},
    )

    report = check_corpus(specs, tmp_path)

    assert report.count == 1
    assert report.ok, report.problems


def test_execution(tmp_path: Path) -> None:
    """`archive` carries the document's `execution/` landings into the archive
    unchanged, in the transaction that moves its own files (S-0123/D-7)."""

    from typer.testing import CliRunner

    from torve.cli import app

    landings = [
        {"task": "T-0001", "at": "2026-09-09"},
        {"task": "T-0002", "at": "2026-09-10"},
    ]
    specs = corpus(
        tmp_path,
        **{
            "0001": document("0001", "S-0001/D-1", landings=landings),
            "0002": document("0002", "S-0002/D-1"),
        },
    )
    execution = specs / "S-0001" / "execution"
    before = {path.name: path.read_bytes() for path in execution.iterdir()}

    result = CliRunner().invoke(
        app,
        ["spec", "archive", "0001", "--superseded-by", "0002", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert len(before) == 2
    archived_execution = archive_dir(specs) / "S-0001" / "execution"
    assert {path.name: path.read_bytes() for path in archived_execution.iterdir()} == before


def test_a_red_check_carries_no_execution_into_the_archive(tmp_path: Path) -> None:
    """The landings travel in the transaction that moves the document, not
    beside it: a red check leaves the `execution/` directory where it was
    and writes nothing into the archive (S-0123/D-7)."""

    from typer.testing import CliRunner

    from torve.cli import app

    specs = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                "S-0001/D-1",
                landings=[{"task": "T-0001", "at": "2026-09-09"}],
            ),
            "0002": document("0002", "S-0002/D-1", informed_by=["0009"]),
        },
    )
    execution = specs / "S-0001" / "execution"
    before = {path.name: path.read_bytes() for path in execution.iterdir()}

    result = CliRunner().invoke(
        app,
        ["spec", "archive", "0001", "--superseded-by", "0002", "--root", str(tmp_path)],
    )

    assert result.exit_code != 0
    assert "does not check clean" in result.output
    assert execution.is_dir()
    assert {path.name: path.read_bytes() for path in execution.iterdir()} == before
    assert not (archive_dir(specs) / "S-0001").exists()
