"""The loader over the YAML corpus (RFC 0053 §5.2, D-53.13; RFC 0056
D-56.1): one file is one `Document`, refused by the model's own validator
with the file, the list entry and the field named, and the corpus is that
load over `rfcs/` and the archive beside it — every citation resolving,
an archived document standing for nobody (D-53.8), numbers derived over
both (D-53.10), and the schema header the only comment a document carries
(D-56.4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_decisions import archived, corpus, document

from torve.config import spec
from torve.config.spec import (
    SCHEMA_HEADER,
    SpecError,
    check_corpus,
    find_comments,
    load_corpus,
    load_document,
    next_number,
)

REPO = Path(__file__).resolve().parent.parent
RFCS = REPO / "rfcs"

ROWS = [("D-7.1", "ASSUMED", "A scratch decision.", "—")]


def _only(path: Path) -> Path:
    return path / "0007-document-0007.yaml"


# ----------------------- #
# the real corpus


def test_the_real_corpus_loads_as_one_and_resolves_every_citation() -> None:
    if not RFCS.is_dir():
        pytest.skip("no corpus beside the tests")

    found = {**spec.rfc_files(RFCS), **spec.archive_files(RFCS)}
    loaded = load_corpus(RFCS)  # every unresolved citation would raise here

    assert len(loaded.documents) == len(found)
    assert loaded.decision("D-53.1") is not None
    assert all(d.archived for d in loaded.documents if Path(d.path).parent != RFCS)
    assert {d.id for d in loaded.standing()} <= set(spec.rfc_files(RFCS))


# ----------------------- #
# what the loader refuses


def test_a_schema_version_1_document_is_refused_naming_the_conversion(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, schema_version=1)})

    with pytest.raises(SpecError, match="schema_version 1") as caught:
        load_document(_only(rfc_dir))

    (problem,) = caught.value.problems

    assert "converts once through RFC 0056 phase 1" in problem


def test_an_unknown_key_is_refused_by_list_index_and_field(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path, **{"0007": document("0007", ROWS, details={"D-7.1": {"cmd": "nope"}})}
    )

    with pytest.raises(SpecError) as caught:
        load_document(_only(rfc_dir))

    (problem,) = caught.value.problems

    assert problem.startswith("0007-document-0007.yaml: decisions.0.cmd: ")


@pytest.mark.parametrize("key", ["path", "archived"])
def test_the_loaders_own_keys_are_never_written(tmp_path: Path, key: str) -> None:
    text = document("0007", ROWS, extra={key: "0007-document-0007.yaml" if key == "path" else True})
    rfc_dir = corpus(tmp_path, **{"0007": text})

    with pytest.raises(SpecError, match=re.escape(f"{key} is the loader's, never written")):
        load_document(_only(rfc_dir))


# ----------------------- #
# the one legal comment (D-56.4)


def test_the_schema_header_is_not_a_comment() -> None:
    text = document("0007", ROWS)

    assert text.startswith(SCHEMA_HEADER)
    assert find_comments(text) == []


def test_a_comment_beyond_the_header_is_found_and_reddens_the_check(tmp_path: Path) -> None:
    lines = document("0007", ROWS).splitlines()
    lines.insert(2, "# a note nobody can act on")
    text = "\n".join(lines) + "\n"

    assert find_comments(text) == [3]

    rfc_dir = corpus(tmp_path, **{"0007": text})
    report = check_corpus(rfc_dir, tmp_path)

    assert not report.ok
    assert any(p.startswith("0007-document-0007.yaml:3: a comment") for p in report.problems)


# ----------------------- #
# the corpus, the archive and the numbers


def test_a_citation_that_nothing_defines_refuses_the_whole_load(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path, **{"0007": document("0007", ROWS, details={"D-7.1": {"cites": ["D-9.9"]}})}
    )

    with pytest.raises(SpecError, match=re.escape("D-7.1 cites D-9.9, which nothing defines")):
        load_corpus(rfc_dir)


def test_a_citation_into_the_archive_resolves_and_the_archive_never_stands(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path,
        **{
            "0008": document(
                "0008",
                [("D-8.1", "ASSUMED", "A newer decision.", "—")],
                details={"D-8.1": {"cites": ["D-7.1"]}},
            )
        },
    )
    archived(
        rfc_dir,
        "0007",
        document("0007", ROWS, status="superseded", superseded_by="0008"),
    )

    loaded = load_corpus(rfc_dir)
    old = loaded.document("0007")

    assert [d.id for d in loaded.documents] == ["0008", "0007"]
    assert old is not None and old.archived
    assert [d.id for d in loaded.standing()] == ["0008"]


def test_the_next_number_derives_over_corpus_and_archive(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0002": document("0002", [])})

    assert next_number(rfc_dir) == 3

    archived(rfc_dir, "0011", document("0011", []))

    assert next_number(rfc_dir) == 12


# ----------------------- #
# what a row and an amendment carry


def test_a_row_carries_its_check_state_and_twin(tmp_path: Path) -> None:
    details = {
        "D-7.1": {
            "check": "pytest tests/test_x.py",
            "check_state": "blocking",
            "check_twin": "tests/test_x_sabotage.py",
        }
    }
    rfc_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, details=details)})
    row = load_document(_only(rfc_dir)).decision("D-7.1")

    assert row is not None
    assert row.check == "pytest tests/test_x.py"
    assert row.check_state == "blocking" and row.check_twin == "tests/test_x_sabotage.py"


def test_an_amendment_carries_its_typed_diff_and_its_words(tmp_path: Path) -> None:
    amendments = [
        {
            "id": "A-9",
            "at": "2026-09-09",
            "title": "the grade moved",
            "changes": [
                {"subject": "D-7.1", "field": "grade", "before": "OPEN", "after": "ASSUMED"}
            ],
            "md": "Words a person wrote.",
        },
        {"id": "A-10", "title": "an execution finding", "md": "Only words."},
    ]
    rfc_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, amendments=amendments)})
    first, second = load_document(_only(rfc_dir)).amendments

    assert first.id == "A-9" and str(first.at) == "2026-09-09"
    assert first.title == "the grade moved"
    assert first.changes[0].model_dump() == {
        "subject": "D-7.1",
        "field": "grade",
        "before": "OPEN",
        "after": "ASSUMED",
    }
    assert first.md == "Words a person wrote."
    assert second.id == "A-10" and second.at is None and second.changes == []
