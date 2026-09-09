"""The loader beside the parser (RFC 0053 §5.2, D-53.13): over every
document of the real corpus, the model carries exactly the rows, phasing,
frontmatter and amendment headings `rfc_parse` yields today — the parity
that licenses readers to switch in phase 2 — and, beyond the parser, the
fenced kinds, fence-aware sections, typed amendment diffs, the archive
and number derivation over it (D-53.8, D-53.10)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from torve.config import rfc_parse
from torve.config.spec import (
    SpecError,
    archive_dir,
    load_corpus,
    load_document,
    load_sections,
    next_number,
    slugify,
)

REPO = Path(__file__).resolve().parent.parent
RFCS = REPO / "rfcs"

# ----------------------- #


def _document(
    number: str,
    *,
    status: str = "accepted",
    rows: str = "| D-1.1 | `OPEN` | A scratch decision | — | — |",
    body: str = "",
) -> str:
    return (
        "---\n"
        f'id: "{number}"\n'
        f"title: Scratch {number}\n"
        f"status: {status}\n"
        "implementation: none\n"
        "depends_on: []\n"
        "informed_by: []\n"
        "supersedes: []\n"
        "superseded_by: null\n"
        "amended_by: []\n"
        "owner: Test Owner\n"
        "description: >-\n"
        f"  Scratch document {number}.\n"
        "schema_version: 1\n"
        "---\n"
        "\n"
        f"# RFC {number} — Scratch {number}\n"
        "\n"
        f"{body}"
        "## Decisions\n"
        "\n"
        "| # | Grade | Decision | Paths | Consequence |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{rows}\n"
    ).replace("D-1.", f"D-{int(number)}.")


def _corpus(tmp_path: Path, **docs: str) -> Path:
    rfc_dir = tmp_path / "rfcs"
    rfc_dir.mkdir()

    for number, text in docs.items():
        (rfc_dir / f"{number}-scratch-{number}.md").write_text(text, encoding="utf-8")

    return rfc_dir


# ----------------------- #

# Parity holds over the archive too: an archived document is the same
# format, read by the same loader, marked archived (D-53.8).
REAL_DOCUMENTS = (
    sorted({**rfc_parse.rfc_files(RFCS), **rfc_parse.archive_files(RFCS)}.items())
    if RFCS.is_dir()
    else []
)


@pytest.mark.parametrize(("number", "path"), REAL_DOCUMENTS, ids=[n for n, _ in REAL_DOCUMENTS])
def test_parity_with_the_parser_over_the_real_corpus(number: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    doc = load_document(path, archived=path.parent != RFCS)
    fm = rfc_parse.parse_frontmatter(text) or {}

    assert doc.archived == (path.parent != RFCS)

    assert doc.id == number == str(fm["id"])
    assert doc.title == fm["title"]
    assert doc.status == fm["status"]
    assert doc.implementation == fm.get("implementation", "none")
    assert doc.depends_on == rfc_parse.fm_list(fm, "depends_on")
    assert doc.amended_by == rfc_parse.fm_list(fm, "amended_by")
    assert doc.retired == rfc_parse.fm_list(fm, "retired")

    rows = rfc_parse.decision_table(text)

    assert [(d.id, d.grade, d.text, d.paths, d.consequence) for d in doc.decisions] == [
        (r.identifier, r.grade, r.text, r.paths, r.consequence) for r in rows
    ]

    entries = rfc_parse.parse_phasing(text) or []

    assert [p.model_dump() for p in doc.phasing] == [e.model_dump() for e in entries]
    assert (doc.contract_example is None) == (rfc_parse.parse_contract_example(text) is None)

    section = rfc_parse.AMENDMENTS_SECTION.search(text)
    headings = rfc_parse.AMENDMENT_HEADING.findall(text[section.end() :]) if section else []

    assert [a.id for a in doc.amendments] == headings


def test_the_real_corpus_loads_as_one_and_resolves_every_citation() -> None:
    if not RFCS.is_dir():
        pytest.skip("no corpus beside the tests")

    corpus = load_corpus(RFCS)

    assert len(corpus.documents) == len(REAL_DOCUMENTS)
    assert corpus.decision("D-53.1") is not None
    assert all(d.archived for d in corpus.documents if Path(d.path).parent != RFCS)
    assert {d.id for d in corpus.standing()} <= set(rfc_parse.rfc_files(RFCS))


def test_the_fenced_kinds_of_rfc_0053_load_non_empty() -> None:
    path = RFCS / "0053-the-item-model-and-the-rebuilt-corpus.md"

    if not path.is_file():
        pytest.skip("RFC 0053 is not in this tree")

    doc = load_document(path)

    assert doc.alternatives and doc.questions
    assert doc.decision("D-53.1") is not None
    assert doc.decision("D-53.1").cites  # type: ignore[union-attr]
    assert doc.decision("D-53.2").rationale == ""  # type: ignore[union-attr]


# ....................... #


def test_a_heading_inside_a_fence_is_not_a_section() -> None:
    text = (
        "## 5. Design\n\nprose\n\n```markdown\n## Decisions governing `x/`\n### D-1.1 — row\n```\n\n"
        "## 6. Tests\n\nmore\n"
    )
    sections = load_sections(text)

    assert [s.key for s in sections] == ["design", "tests"]
    assert "## Decisions governing" in sections[0].md
    assert sections[0].level == 2 and sections[0].order == 0


def test_section_keys_drop_the_number_and_fold_punctuation() -> None:
    assert slugify("5.4 The projections beside the code") == "the-projections-beside-the-code"
    assert slugify("Alternatives considered") == "alternatives-considered"
    assert slugify("10. Unresolved questions") == "unresolved-questions"


# ....................... #


def test_an_unknown_key_in_a_fence_is_refused_by_fence_entry_and_field(tmp_path: Path) -> None:
    body = "## 5. Design\n\n```yaml invariants\n- id: I-7.1\n  statement: one\n  check: 'true'\n  cmd: nope\n```\n\n"
    path = _corpus(tmp_path, **{"0007": _document("0007", body=body)}) / "0007-scratch-0007.md"

    with pytest.raises(SpecError) as caught:
        load_document(path)

    (problem,) = caught.value.problems

    assert "`yaml invariants` entry 1, cmd" in problem


def test_a_fenced_kind_the_model_does_not_know_is_refused(tmp_path: Path) -> None:
    body = "## 5. Design\n\n```yaml risks\n- text: x\n```\n\n"
    path = _corpus(tmp_path, **{"0007": _document("0007", body=body)}) / "0007-scratch-0007.md"

    with pytest.raises(SpecError, match=re.escape("`yaml risks` is not a fenced kind")):
        load_document(path)


def test_decision_details_join_their_row_and_a_stray_id_is_refused(tmp_path: Path) -> None:
    body = (
        "```yaml decision-details\n"
        "- id: D-7.1\n  rationale: because\n  cites: [D-7.1]\n  check: pytest tests/test_x.py\n"
        "```\n\n"
    )
    rfc_dir = _corpus(tmp_path, **{"0007": _document("0007", body=body)})
    doc = load_document(rfc_dir / "0007-scratch-0007.md")
    row = doc.decision("D-7.1")

    assert row is not None
    assert row.rationale == "because" and row.check == "pytest tests/test_x.py"

    stray = _document("0007", body=body.replace("id: D-7.1", "id: D-7.9"))
    (rfc_dir / "0007-scratch-0007.md").write_text(stray, encoding="utf-8")

    with pytest.raises(SpecError, match=re.escape("names D-7.9, which the table does not define")):
        load_document(rfc_dir / "0007-scratch-0007.md")


def test_an_amendment_carries_its_typed_diff_and_its_words(tmp_path: Path) -> None:
    body = (
        "## Amendments\n\n"
        "### A-9 — 2026-09-09 — the grade moved\n\n"
        "Words a person wrote.\n\n"
        "```yaml changes\n- subject: D-7.1\n  field: grade\n  before: OPEN\n  after: ASSUMED\n```\n\n"
        "### A-10 — an execution finding\n\nOnly words.\n"
    )
    text = _document("0007", rows="| D-7.1 | `ASSUMED` | A scratch decision | — | — |")
    text = text + "\n" + body
    rfc_dir = _corpus(
        tmp_path, **{"0007": text.replace("amended_by: []", 'amended_by: ["A-9", "A-10"]')}
    )
    doc = load_document(rfc_dir / "0007-scratch-0007.md")

    first, second = doc.amendments

    assert first.id == "A-9" and str(first.at) == "2026-09-09" and first.title == "the grade moved"
    assert first.changes[0].model_dump() == {
        "subject": "D-7.1",
        "field": "grade",
        "before": "OPEN",
        "after": "ASSUMED",
    }
    assert "Words a person wrote." in first.body_md
    assert second.id == "A-10" and second.at is None and second.changes == []


# ....................... #


def test_a_citation_that_nothing_defines_refuses_the_whole_load(tmp_path: Path) -> None:
    body = "```yaml decision-details\n- id: D-7.1\n  cites: [D-9.9]\n```\n\n"
    rfc_dir = _corpus(tmp_path, **{"0007": _document("0007", body=body)})

    with pytest.raises(SpecError, match=re.escape("D-7.1 cites D-9.9, which nothing defines")):
        load_corpus(rfc_dir)


def test_a_citation_into_the_archive_resolves_and_the_archive_never_stands(tmp_path: Path) -> None:
    body = "```yaml decision-details\n- id: D-8.1\n  cites: [D-7.1]\n```\n\n"
    rfc_dir = _corpus(tmp_path, **{"0008": _document("0008", body=body)})
    archive = archive_dir(rfc_dir)
    archive.mkdir(parents=True)
    (archive / "0007-scratch-0007.md").write_text(
        _document("0007", status="superseded"), encoding="utf-8"
    )

    corpus = load_corpus(rfc_dir)

    assert [d.id for d in corpus.documents] == ["0008", "0007"]
    assert corpus.document("0007").archived  # type: ignore[union-attr]
    assert [d.id for d in corpus.standing()] == ["0008"]


def test_the_next_number_derives_over_corpus_and_archive(tmp_path: Path) -> None:
    rfc_dir = _corpus(tmp_path, **{"0002": _document("0002")})

    assert next_number(rfc_dir) == 3

    archive = archive_dir(rfc_dir)
    archive.mkdir(parents=True)
    shutil.copy(rfc_dir / "0002-scratch-0002.md", archive / "0011-scratch-0011.md")

    assert next_number(rfc_dir) == 12
    assert rfc_parse.next_number(rfc_dir) == 3  # the parser never sees the archive


def test_a_document_without_frontmatter_says_so(tmp_path: Path) -> None:
    path = tmp_path / "0001-x.md"
    path.write_text("# RFC 0001 — x\n", encoding="utf-8")

    with pytest.raises(SpecError, match="no YAML frontmatter"):
        load_document(path)
