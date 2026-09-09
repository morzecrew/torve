"""`torve.config.spec_emit` — the one writer of a document (RFC 0025 §5.1,
D-25.1; RFC 0056 D-56.4; RFC 0057 D-57.1): the model split into its
directory's four files and dumped as YAML in the model's own key order,
block scalars for prose, flow lists for short identifier lists and folded
scalars for long lines. Identity is the property the design leans on —
`load(dump(doc)) == doc` and `canonical(directory) == the files` — and
every document committed to the corpus is already its own canonical form.
The verbs beneath it mutate the loaded model and nothing else: they take a
`Document`, return a `Document`, and only `write_transaction` touches the
tree, and only when the whole corpus checks clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_decisions import PHASE, Doc, archived, corpus, document

from torve.config.spec import archive_dir, archive_dirs, document_dirs, load_document
from torve.config.spec_emit import (
    amend_row,
    append_amendment,
    archive_document,
    canonical,
    dump_document,
    fix_row_text,
    relocate_paths,
    retire_decision,
    stamp,
    write_document,
    write_transaction,
)
from torve.domain.spec import Document

REPO = Path(__file__).resolve().parent.parent
SPECS = REPO / ".torve" / "specs"

ROWS = [("D-1.1", "ASSUMED", "Something is decided.", "`src/torve/cli/**`", "Nothing yet.")]
SECTIONS = [{"key": "summary", "md": "A first line.\n\nA second paragraph."}]
LONG = (
    "The rule holds over every verb of the surface, and the reason it holds is "
    "written here rather than in a comment nobody can act on."
)
DETAILS = {"D-1.1": {"rationale": LONG, "cites": ["D-1.1"]}}


def loaded(tmp_path: Path, doc: Doc, number: str = "0001") -> Document:
    spec_dir = corpus(tmp_path, **{number: doc})

    return load_document(spec_dir / f"S-{number}")


def widget(tmp_path: Path) -> Document:
    return loaded(tmp_path, document("0001", ROWS, sections=SECTIONS, details=DETAILS))


def on_disk(directory: Path) -> Doc:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(directory.iterdir())}


# ----------------------- #
# identity and idempotence


def test_dump_then_load_is_identity_and_the_dump_is_its_own_canonical_form(
    tmp_path: Path,
) -> None:
    doc = widget(tmp_path)
    texts = dump_document(doc)
    directory = tmp_path / "scratch" / "S-0001"
    write_document(directory, doc)
    reloaded = load_document(directory)

    assert reloaded.model_dump() == doc.model_dump()
    assert dump_document(reloaded) == texts
    assert canonical(directory) == texts
    assert on_disk(directory) == texts


LIVE = sorted({**document_dirs(SPECS), **archive_dirs(SPECS)}.items()) if SPECS.is_dir() else []


@pytest.mark.parametrize(("number", "directory"), LIVE, ids=[n for n, _ in LIVE])
def test_every_live_document_is_its_own_canonical_form(number: str, directory: Path) -> None:
    doc = load_document(directory, archived=directory.parent != SPECS)

    assert doc.id == number
    assert dump_document(doc) == on_disk(directory)
    assert canonical(directory) == on_disk(directory)


def test_the_serializer_writes_blocks_flow_lists_and_folded_lines(tmp_path: Path) -> None:
    texts = dump_document(widget(tmp_path))

    assert "    md: |-\n      A first line.\n" in texts["document.yaml"]  # prose keeps newlines
    assert "paths: [src/torve/cli/**]" in texts["decisions.yaml"]  # short list, one line
    assert "rationale: >-\n" in texts["decisions.yaml"]  # a long single line is folded
    assert max(len(line) for text in texts.values() for line in text.splitlines()) <= 88


def test_the_authors_two_files_are_always_written_and_the_tools_only_with_content(
    tmp_path: Path,
) -> None:
    # D-57.1: a new document shows where the rows go; the tool's file and
    # the landing's appear with their first entry.
    doc = widget(tmp_path)

    assert sorted(dump_document(doc)) == ["decisions.yaml", "document.yaml"]

    bare = doc.model_copy(update={"decisions": []})

    assert "decisions: []" in dump_document(bare)["decisions.yaml"]

    amended = append_amendment(doc, "A-2", "a title", "2026-09-09", [])

    assert sorted(dump_document(amended)) == [
        "amendments.yaml",
        "decisions.yaml",
        "document.yaml",
    ]


# ----------------------- #
# the verbs: each takes a Document and returns one


def test_amend_row_replaces_the_field_stamps_the_row_and_returns_the_diff(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    mutated, changes = amend_row(
        doc, "D-1.1", grade="LOCKED", paths=["src/torve/cli/**", "tests/**"]
    )
    row = mutated.decision("D-1.1")

    assert row is not None
    assert row.grade == "LOCKED" and row.paths == ["src/torve/cli/**", "tests/**"]
    assert [c["field"] for c in changes] == ["grade", "paths", "fingerprint"]
    assert changes[0] == {
        "subject": "D-1.1",
        "field": "grade",
        "before": "ASSUMED",
        "after": "LOCKED",
    }
    assert changes[1]["before"] == ["src/torve/cli/**"]
    assert changes[2]["before"] is None and changes[2]["after"] == stamp(row)
    assert row.fingerprint == stamp(row)
    assert doc.decision("D-1.1").grade == "ASSUMED"  # type: ignore[union-attr]


def test_amend_row_with_nothing_to_change_is_refused(tmp_path: Path) -> None:
    doc = widget(tmp_path)

    with pytest.raises(ValueError, match="nothing to change"):
        amend_row(doc, "D-1.1", grade="ASSUMED")

    with pytest.raises(ValueError, match="no decision"):
        amend_row(doc, "D-1.9", grade="OPEN")


def test_the_diff_rides_the_amendment_through_dump_and_load(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    mutated, changes = amend_row(doc, "D-1.1", new_text="Something else is decided.")
    amended = append_amendment(mutated, "A-2", "the text moved", "2026-09-09", changes)
    directory = tmp_path / ".torve" / "specs" / "S-0001"
    write_document(directory, amended)
    entry = next(a for a in load_document(directory).amendments if a.id == "A-2")

    assert amended.amended_by() == ["A-2"]
    assert str(entry.at) == "2026-09-09" and entry.title == "the text moved"
    assert [c.field for c in entry.changes] == ["text", "fingerprint"]
    assert entry.changes[0].before == "Something is decided."

    with pytest.raises(ValueError, match="already exists"):
        append_amendment(amended, "A-2", "again", "2026-09-10", [])


def test_fix_row_text_appends_to_the_editorial_lane_and_restamps(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    fixed, changes = fix_row_text(doc, "D-1.1", "Something is decided, spelt right.")
    row = fixed.decision("D-1.1")

    assert row is not None and row.text == "Something is decided, spelt right."
    assert [c["field"] for c in changes] == ["text", "fingerprint"]
    assert row.fingerprint == stamp(row)
    assert [c.field for c in fixed.editorial] == ["text", "fingerprint"]
    assert fixed.amended_by() == doc.amended_by()  # the editorial lane is never an amendment

    again, _ = fix_row_text(fixed, "D-1.1", "Something is decided, spelt right twice.")

    assert [c.field for c in again.editorial] == ["text", "fingerprint"] * 2

    with pytest.raises(ValueError, match="already reads that way, and it is stamped"):
        fix_row_text(again, "D-1.1", "Something is decided, spelt right twice.")

    # the hand-edited text, as it now reads, is exactly what the lane accepts
    row = again.decision("D-1.1")
    assert row is not None
    by_hand = again.model_copy(
        update={"decisions": [row.model_copy(update={"text": "Edited by hand."})]}
    )
    restamped, changes = fix_row_text(by_hand, "D-1.1", "Edited by hand.")

    assert [c["field"] for c in changes] == ["text", "fingerprint"]
    assert restamped.decision("D-1.1").fingerprint == restamped.decision("D-1.1").stamp()  # type: ignore[union-attr]


def test_retire_decision_removes_the_row_and_records_the_identifier(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    retired = retire_decision(doc, "D-1.1", "2026-09-09", reason="path rot")

    assert retired.decision("D-1.1") is None
    assert retired.retired == ["D-1.1"]
    assert "D-1.1" in retired.defined_identifiers()  # never reused (D-16.1)
    assert "D-1.1" in dump_document(retired)["decisions.yaml"]

    with pytest.raises(ValueError, match="no decision"):
        retire_decision(retired, "D-1.1", "2026-09-09")


def test_relocate_paths_moves_the_exact_glob_and_names_the_rows(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    moved, touched = relocate_paths(doc, "src/torve/cli/**", "src/torve/surface/**")
    row = moved.decision("D-1.1")

    assert touched == ["D-1.1"]
    assert row is not None and row.paths == ["src/torve/surface/**"]
    assert row.text == doc.decision("D-1.1").text  # type: ignore[union-attr]
    assert relocate_paths(doc, "src/nowhere/**", "src/elsewhere/**")[1] == []


def test_archive_document_supersedes_and_refuses_a_second_time(tmp_path: Path) -> None:
    doc = widget(tmp_path)
    gone = archive_document(doc, "0054")

    assert gone.status == "superseded" and gone.superseded_by == "0054"
    assert gone.archived
    assert gone.decisions == doc.decisions  # every identifier still resolves (D-53.8)

    for file_name, text in dump_document(gone).items():
        # the header is the same two levels up from the corpus and the archive
        assert text.startswith(
            f"# yaml-language-server: $schema=../../schemas/{file_name.removesuffix('.yaml')}.json"
        )

    with pytest.raises(ValueError, match="already superseded"):
        archive_document(gone, "0055")


# ----------------------- #
# the transaction (D-25.2)


def _pair(tmp_path: Path) -> Path:
    """0001 and 0002, where 0002 depends on 0001."""

    return corpus(
        tmp_path,
        **{
            "0001": document("0001", ROWS, sections=SECTIONS),
            "0002": document(
                "0002", [("D-2.1", "ASSUMED", "Another decision.", "—")], depends_on=["0001"]
            ),
        },
    )


def test_a_red_check_leaves_the_tree_untouched(tmp_path: Path) -> None:
    spec_dir = _pair(tmp_path)
    doomed = spec_dir / "S-0001"
    before = on_disk(doomed)

    report = write_transaction(spec_dir, tmp_path, {}, deletions=("S-0001",))

    assert not report.ok  # 0002 depends_on the deleted document
    assert any("no such document" in problem for problem in report.problems)
    assert on_disk(doomed) == before


def test_a_deletion_rides_the_transaction(tmp_path: Path) -> None:
    spec_dir = _pair(tmp_path)

    report = write_transaction(spec_dir, tmp_path, {}, deletions=("S-0002",))

    assert report.ok, report.problems
    assert not (spec_dir / "S-0002").exists()
    assert (spec_dir / "S-0001").exists()


def test_the_transaction_checks_with_the_archive_in_view(tmp_path: Path) -> None:
    spec_dir = corpus(
        tmp_path,
        **{
            "0001": document("0001", ROWS),
            "0002": document(
                "0002",
                [("D-2.1", "ASSUMED", "Another decision.", "—")],
                sections=[{"key": "summary", "md": "Built on D-1.1."}],
            ),
        },
    )
    name = "S-0001"
    doc = load_document(spec_dir / name)

    without = write_transaction(spec_dir, tmp_path, {}, deletions=(name,))

    assert not without.ok  # 0002 cites D-1.1 and nothing would define it
    assert (spec_dir / name).exists()

    moved = write_transaction(
        spec_dir,
        tmp_path,
        {},
        deletions=(name,),
        archived={name: archive_document(doc, "0002")},
    )

    assert moved.ok, moved.problems
    assert not (spec_dir / name).exists()

    kept = load_document(archive_dir(spec_dir) / name, archived=True)

    assert kept.decision("D-1.1") is not None and kept.status == "superseded"


def test_the_transaction_writes_a_mutated_document(tmp_path: Path) -> None:
    spec_dir = corpus(tmp_path, **{"0001": document("0001", ROWS, sections=SECTIONS)})
    archived(spec_dir, "0000", document("0000", [], status="superseded", superseded_by="0001"))
    name = "S-0001"
    fixed, _ = fix_row_text(load_document(spec_dir / name), "D-1.1", "Something is decided, twice.")

    report = write_transaction(spec_dir, tmp_path, {name: fixed})

    assert report.ok, report.problems
    assert on_disk(spec_dir / name) == dump_document(fixed)
    assert (archive_dir(spec_dir) / "S-0000" / "document.yaml").exists()


# ....................... #
# RFC 0056 phase 2: the human page (D-56.7)


def test_render_markdown_is_a_page_of_the_document_and_never_the_source(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from torve.cli import app
    from torve.config.spec_emit import render_markdown

    spec_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [("D-1.1", "LOCKED", "Rows are typed", "`src/a/**`", "no parser")],
                details={"D-1.1": {"rationale": "because", "check": "pytest tests/test_a.py"}},
                sections=[{"key": "summary", "md": "What ships.\n"}],
                alternatives=[
                    {"option": "keep markdown", "rejected_because": "it is grepped whole"}
                ],
                questions=[{"id": "Q-1.1", "text": "when", "status": "open"}],
                phasing=[PHASE],
                amendments=[
                    {
                        "id": "A-1",
                        "at": "2026-09-09",
                        "title": "regraded",
                        "changes": [
                            {
                                "subject": "D-1.1",
                                "field": "grade",
                                "before": "OPEN",
                                "after": "LOCKED",
                            }
                        ],
                        "md": "Words.\n",
                    }
                ],
            )
        },
    )
    page = render_markdown(load_document(spec_dir / "S-0001"))

    assert page.startswith("# 0001 — Document 0001\n")
    assert "## 1. Summary\n\nWhat ships." in page  # the heading is the key (D-57.2)
    assert "| D-1.1 | `LOCKED` | Rows are typed | `src/a/**` | no parser |" in page
    assert "- rationale: because" in page and "`pytest tests/test_a.py` (shadow)" in page
    assert "**keep markdown** — rejected because it is grepped whole" in page
    assert "**Q-1.1** (open) when" in page
    assert "### Phase 1 — one" in page
    assert "### A-1 — 2026-09-09 — regraded" in page and "D-1.1 grade: 'OPEN' → 'LOCKED'" in page

    out = tmp_path / "pages" / "0001.md"
    result = CliRunner().invoke(
        app, ["spec", "render", "0001", "--out", str(out), "--root", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == page
    # rendering wrote nothing into the corpus
    assert sorted(p.name for p in spec_dir.iterdir()) == ["S-0001"]
