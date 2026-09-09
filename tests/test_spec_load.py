"""The loader over the YAML corpus (S-0053/the-authoring-surface-unchanged-where-it-matters, S-0053/D-13; S-0056
S-0056/D-1; S-0057 S-0057/D-1): one directory of four files is one `Document`,
refused by the model's own validator with the file, the list entry and the
field named, and the corpus is that load over `.torve/specs/` and the
archive beside it — every citation resolving, an archived document
standing for nobody (S-0053/D-8), numbers derived over both (S-0053/D-10), and the
schema header the only comment a file carries (S-0056/D-4)."""

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
SPECS = REPO / ".torve" / "specs"

ROWS = [("S-0007/D-1", "ASSUMED", "A scratch decision.", "—")]


def _only(path: Path) -> Path:
    return path / "S-0007"


# ----------------------- #
# the real corpus


def test_the_real_corpus_loads_as_one_and_resolves_every_citation() -> None:
    if not SPECS.is_dir():
        pytest.skip("no corpus beside the tests")

    found = {**spec.document_dirs(SPECS), **spec.archive_dirs(SPECS)}
    loaded = load_corpus(SPECS)  # every unresolved citation would raise here

    assert len(loaded.documents) == len(found)
    assert loaded.decision("S-0053/D-1") is not None
    assert all(d.archived for d in loaded.documents if Path(d.path).parent != SPECS)
    assert {d.id for d in loaded.standing()} <= {
        spec.dirname_of(n) for n in spec.document_dirs(SPECS)
    }


# ----------------------- #
# what the loader refuses


def test_a_schema_version_1_document_is_refused_naming_the_conversion(tmp_path: Path) -> None:
    spec_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, schema_version=1)})

    with pytest.raises(SpecError, match="schema_version 1") as caught:
        load_document(_only(spec_dir))

    (problem,) = caught.value.problems

    assert "converts once through S-0057 phase 1" in problem


def test_an_unknown_key_is_refused_by_list_index_and_field(tmp_path: Path) -> None:
    spec_dir = corpus(
        tmp_path, **{"0007": document("0007", ROWS, details={"S-0007/D-1": {"cmd": "nope"}})}
    )

    with pytest.raises(SpecError) as caught:
        load_document(_only(spec_dir))

    (problem,) = caught.value.problems

    assert problem.startswith("S-0007/decisions.yaml: decisions.0.cmd: ")


@pytest.mark.parametrize("key", ["path", "archived"])
def test_the_loaders_own_keys_are_never_written(tmp_path: Path, key: str) -> None:
    doc = document("0007", ROWS, extra={key: "S-0007" if key == "path" else True})
    spec_dir = corpus(tmp_path, **{"0007": doc})

    with pytest.raises(SpecError, match=re.escape(f"document.yaml: {key}: no file carries it")):
        load_document(_only(spec_dir))


def test_a_key_in_the_wrong_file_is_refused_naming_the_file_that_owns_it(tmp_path: Path) -> None:
    # S-0057/D-1: each of the four files carries the slice of the model one
    # hand writes; a key in the wrong one is a misfiled edit, never merged.
    doc = document("0007", ROWS)
    doc["decisions.yaml"] = doc["decisions.yaml"] + "sections: []\n"
    spec_dir = corpus(tmp_path, **{"0007": doc})

    with pytest.raises(SpecError) as caught:
        load_document(_only(spec_dir))

    assert caught.value.problems == ["S-0007/decisions.yaml: sections belongs in document.yaml"]


def test_a_directory_without_a_document_file_is_not_a_document(tmp_path: Path) -> None:
    doc = document("0007", ROWS)
    del doc["document.yaml"]
    spec_dir = corpus(tmp_path, **{"0007": doc})

    with pytest.raises(SpecError, match=re.escape("no document.yaml")):
        load_document(_only(spec_dir))


# ----------------------- #
# the one legal comment (S-0056/D-4)


def test_the_schema_header_is_not_a_comment() -> None:
    doc = document("0007", ROWS)

    assert sorted(doc) == ["decisions.yaml", "document.yaml"]

    for text in doc.values():
        assert text.startswith(SCHEMA_HEADER)
        assert find_comments(text) == []


def test_a_comment_beyond_the_header_is_found_and_reddens_the_check(tmp_path: Path) -> None:
    doc = document("0007", ROWS)
    lines = doc["document.yaml"].splitlines()
    lines.insert(2, "# a note nobody can act on")
    doc["document.yaml"] = "\n".join(lines) + "\n"

    assert find_comments(doc["document.yaml"]) == [3]

    spec_dir = corpus(tmp_path, **{"0007": doc})
    report = check_corpus(spec_dir, tmp_path)

    assert not report.ok
    assert any(p.startswith("S-0007/document.yaml:3: a comment") for p in report.problems)


# ----------------------- #
# the corpus, the archive and the numbers


def test_a_citation_that_nothing_defines_refuses_the_whole_load(tmp_path: Path) -> None:
    spec_dir = corpus(
        tmp_path,
        **{"0007": document("0007", ROWS, details={"S-0007/D-1": {"cites": ["S-0009/D-9"]}})},
    )

    with pytest.raises(
        SpecError, match=re.escape("S-0007/D-1 cites S-0009/D-9, which nothing defines")
    ):
        load_corpus(spec_dir)


def test_a_citation_into_the_archive_resolves_and_the_archive_never_stands(tmp_path: Path) -> None:
    spec_dir = corpus(
        tmp_path,
        **{
            "0008": document(
                "0008",
                [("S-0008/D-1", "ASSUMED", "A newer decision.", "—")],
                details={"S-0008/D-1": {"cites": ["S-0007/D-1"]}},
            )
        },
    )
    archived(
        spec_dir,
        "0007",
        document("0007", ROWS, status="superseded", superseded_by="0008"),
    )

    loaded = load_corpus(spec_dir)
    old = loaded.document("0007")

    assert [d.id for d in loaded.documents] == ["S-0008", "S-0007"]
    assert old is not None and old.archived
    assert [d.id for d in loaded.standing()] == ["S-0008"]


def test_the_next_number_derives_over_corpus_and_archive(tmp_path: Path) -> None:
    spec_dir = corpus(tmp_path, **{"0002": document("0002", [])})

    assert next_number(spec_dir) == 3

    archived(spec_dir, "0011", document("0011", []))

    assert next_number(spec_dir) == 12


# ----------------------- #
# what a row and an amendment carry


def test_a_row_carries_its_check_state_and_twin(tmp_path: Path) -> None:
    details = {
        "S-0007/D-1": {
            "check": "pytest tests/test_x.py",
            "check_state": "blocking",
            "check_twin": "tests/test_x_sabotage.py",
        }
    }
    spec_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, details=details)})
    row = load_document(_only(spec_dir)).decision("S-0007/D-1")

    assert row is not None
    assert row.check == "pytest tests/test_x.py"
    assert row.check_state == "blocking" and row.check_twin == "tests/test_x_sabotage.py"


def test_an_amendment_carries_its_typed_diff_and_its_words(tmp_path: Path) -> None:
    amendments = [
        {
            "id": "A-1",
            "at": "2026-09-09",
            "title": "the grade moved",
            "changes": [
                {"subject": "S-0007/D-1", "field": "grade", "before": "OPEN", "after": "ASSUMED"}
            ],
            "md": "Words a person wrote.",
        },
        {"id": "A-2", "title": "an execution finding", "md": "Only words."},
    ]
    spec_dir = corpus(tmp_path, **{"0007": document("0007", ROWS, amendments=amendments)})
    loaded = load_document(_only(spec_dir))
    first, second = loaded.amendments

    assert first.id == "S-0007/A-1" and str(first.at) == "2026-09-09"
    assert first.title == "the grade moved"
    assert first.changes[0].model_dump() == {
        "subject": "S-0007/D-1",
        "field": "grade",
        "before": "OPEN",
        "after": "ASSUMED",
    }
    assert first.md == "Words a person wrote."
    assert second.id == "S-0007/A-2" and second.at is None and second.changes == []
    # derived, never a field (S-0057/D-1), and global in memory (S-0058/D-1)
    assert loaded.amended_by() == ["S-0007/A-1", "S-0007/A-2"]


# ----------------------- #
# S-0057 S-0057/D-7: execution.yaml is the landing's file


def test_the_execution_file_loads_as_landings_and_belongs_to_no_other_file(tmp_path: Path) -> None:
    from test_decisions import as_text, corpus, document, place

    landing = {
        "task": "T-0001",
        "phase": 1,
        "commit": "abc",
        "at": "2026-09-09",
        "agent": "session/x",
        "entries": [
            {
                "decision": "S-0001/D-1",
                "grade": "LOCKED",
                "class": "drift",
                "claim": "c",
                "evidence": "src/a.py:1 - e",
                "action": "decided",
            }
        ],
    }
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001", [("S-0001/D-1", "LOCKED", "x", "`src/**`")], landings=[landing]
            )
        },
    )

    doc = load_document(rfc_dir / "S-0001")

    assert [one.task for one in doc.landings] == ["T-0001"]
    assert doc.landings[0].entries[0].entry_class == "drift"
    assert doc.landings[0].at is not None and doc.landings[0].at.isoformat() == "2026-09-09"

    misplaced = document("0001", [("S-0001/D-1", "LOCKED", "x", "`src/**`")])
    misplaced["document.yaml"] += as_text("execution.yaml", {"landings": [landing]}).split("\n", 1)[
        1
    ]
    place(rfc_dir, "0001", misplaced)

    with pytest.raises(SpecError, match=r"landings belongs in execution\.yaml"):
        load_document(rfc_dir / "S-0001")
