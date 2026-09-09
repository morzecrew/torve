"""The parser knows the archive (RFC 0053 A-140): a citation into an
archived document resolves, a reference to an archived number warns
rather than refuses, the next number derives over both directories, and
`lookup` answers an archived identifier marked archived. Nothing here
makes an archived document stand."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app
from torve.config import spec
from torve.config.rfc_parse import archive_dir, check_corpus, lookup, next_number

runner = CliRunner()

# ----------------------- #


def document(
    number: str,
    decision: str,
    *,
    status: str = "accepted",
    refs: str = "depends_on: []\ninformed_by: []\n",
) -> str:
    return (
        "---\n"
        f'id: "{number}"\n'
        f"title: Doc {number}\n"
        f"status: {status}\n"
        "implementation: complete\n"
        f"{refs}"
        "supersedes: []\n"
        "superseded_by: null\n"
        "amended_by: []\n"
        "owner: Test Owner\n"
        "description: >-\n"
        f"  Scratch document {number}.\n"
        "schema_version: 1\n"
        "---\n"
        "\n"
        f"# RFC {number} — Doc {number}\n"
        "\n"
        "## Decisions\n"
        "\n"
        "| # | Grade | Decision | Paths | Consequence |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| {decision} | `ASSUMED` | Something is decided | — | — |\n"
    )


def seed(tmp_path: Path, corpus: dict[str, str], archived: dict[str, str]) -> Path:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()

    for name, text in corpus.items():
        (rfcs / name).write_text(text, encoding="utf-8")

    if archived:
        archive = archive_dir(rfcs)
        archive.mkdir(parents=True)

        for name, text in archived.items():
            (archive / name).write_text(text, encoding="utf-8")

    generated = runner.invoke(app, ["rfc", "index", "--root", str(tmp_path)])
    assert generated.exit_code == 0, generated.output

    return rfcs


# ----------------------- #


def test_a_citation_into_the_archive_resolves(tmp_path: Path) -> None:
    citing = document("0002", "D-2.1").replace(
        "## Decisions", "This document builds on D-1.1 and D-1.2.\n\n## Decisions"
    )
    rfcs = seed(
        tmp_path,
        {"0002-doc-0002.md": citing},
        {"0001-doc-0001.md": document("0001", "D-1.1", status="superseded")},
    )

    report = check_corpus(rfcs, tmp_path)

    assert [p for p in report.problems if "D-1.1" in p] == []
    assert any("cites D-1.2" in p for p in report.problems)


def test_a_reference_to_an_archived_number_warns_and_an_unknown_one_refuses(
    tmp_path: Path,
) -> None:
    refs = 'depends_on: ["0001"]\ninformed_by: ["0009"]\n'
    rfcs = seed(
        tmp_path,
        {"0002-doc-0002.md": document("0002", "D-2.1", refs=refs)},
        {"0001-doc-0001.md": document("0001", "D-1.1", status="superseded")},
    )

    report = check_corpus(rfcs, tmp_path)

    assert any("depends_on names 0001, which is archived" in w for w in report.warnings)
    assert any("informed_by names '0009', no such RFC" in p for p in report.problems)
    assert not any("0001" in p for p in report.problems)


def test_the_next_number_derives_over_corpus_and_archive(tmp_path: Path) -> None:
    rfcs = seed(
        tmp_path,
        {"0002-doc-0002.md": document("0002", "D-2.1")},
        {"0007-doc-0007.md": document("0007", "D-7.1", status="superseded")},
    )

    assert spec.next_number(rfcs) == 8  # what `torve rfc new` uses (D-53.10)
    assert next_number(rfcs) == 3  # the parser's own stays corpus-only until phase 5


def test_lookup_answers_an_archived_identifier_marked_archived(tmp_path: Path) -> None:
    rfcs = seed(
        tmp_path,
        {"0002-doc-0002.md": document("0002", "D-2.1")},
        {"0001-doc-0001.md": document("0001", "D-1.1", status="superseded")},
    )

    live = lookup(rfcs, "D-2.1")
    gone = lookup(rfcs, "D-1.1")
    doc = lookup(rfcs, "0001")

    assert live is not None and "archived" not in live
    assert gone is not None and gone["archived"] is True
    assert doc is not None and doc["archived"] is True and doc["status"] == "superseded"
    assert lookup(rfcs, "D-9.9") is None


def test_an_archived_document_never_counts_as_standing_in_the_check(tmp_path: Path) -> None:
    rfcs = seed(
        tmp_path,
        {"0002-doc-0002.md": document("0002", "D-2.1")},
        {"0001-doc-0001.md": document("0001", "D-1.1", status="superseded")},
    )

    report = check_corpus(rfcs, tmp_path)

    assert report.count == 1
    assert report.ok, report.problems
