"""The specification model (RFC 0053 §5.1, RFC 0056 D-56.1): every typed
list refuses unknown keys by name (D-53.3); the fingerprint covers text,
grade and paths and nothing a human reads beside them (D-53.5, D-53.16);
the stamp is the content fingerprint beside the rule fingerprint; the
document tells what it defines; the corpus joins across documents and
knows which documents may be inherited from."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.domain.spec import (
    Alternative,
    Amendment,
    Change,
    Corpus,
    Decision,
    Document,
    Invariant,
    Question,
    fingerprint,
    is_citation,
    rule_fingerprint,
)

# ----------------------- #


def _doc(number: str, status: str = "accepted", **extra: object) -> Document:
    return Document(
        id=number,
        title=f"Document {number}",
        status=status,  # type: ignore[arg-type]
        owner="Test Owner",
        description="A scratch document.",
        **extra,  # type: ignore[arg-type]
    )


# ----------------------- #


def test_the_fingerprint_covers_text_grade_and_paths_only() -> None:
    base = Decision(id="D-1.1", grade="LOCKED", text="Sessions in Redis", paths=["a/**"])
    same_with_consequence = base.model_copy(update={"consequence": "reopening is expensive"})
    same_with_check = base.model_copy(update={"check": "pytest tests/test_a.py"})

    assert same_with_consequence.content_fingerprint() == base.content_fingerprint()
    assert same_with_check.content_fingerprint() == base.content_fingerprint()

    assert (
        base.model_copy(update={"text": "Sessions in Postgres"}).content_fingerprint()
        != base.content_fingerprint()
    )
    assert (
        base.model_copy(update={"grade": "ASSUMED"}).content_fingerprint()
        != base.content_fingerprint()
    )
    assert (
        base.model_copy(update={"paths": ["b/**"]}).content_fingerprint()
        != base.content_fingerprint()
    )
    # the stored stamp is empty until the tool writes it, and never computed
    assert base.fingerprint == ""


def test_the_stamp_pairs_the_content_with_the_rule() -> None:
    row = Decision(id="D-1.1", grade="LOCKED", text="x", paths=["a/**"])
    content, rule = row.stamp().split("/")

    assert content == row.content_fingerprint()
    assert rule == rule_fingerprint("LOCKED", ["a/**"])
    # a text change moves the content half only; a grade change moves both
    assert row.model_copy(update={"text": "y"}).stamp().split("/")[1] == rule
    assert row.model_copy(update={"grade": "OPEN"}).stamp().split("/")[1] != rule


def test_the_fingerprint_is_order_and_whitespace_stable() -> None:
    assert fingerprint(" x ", "OPEN", ["b", "a"]) == fingerprint("x", "OPEN", ["a", "b"])
    assert len(fingerprint("x", "OPEN", [])) == 16


# ....................... #


@pytest.mark.parametrize(
    ("model", "entry"),
    [
        (Decision, {"id": "D-1.1", "grade": "OPEN", "text": "x", "why": "not a field"}),
        (
            Invariant,
            {"id": "I-1.1", "statement": "one lander", "check": "x", "paths": [], "cmd": "y"},
        ),
        (Alternative, {"option": "x", "rejected_because": "y", "rejected": True}),
        (Question, {"id": "Q-1.1", "text": "x", "state": "open"}),
        (Change, {"subject": "D-1.1", "field": "grade", "from": "OPEN", "after": "LOCKED"}),
    ],
)
def test_an_unknown_key_in_a_typed_entry_is_refused_by_name(
    model: type[object], entry: dict[str, object]
) -> None:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(entry)  # type: ignore[attr-defined]

    unknown = [e for e in caught.value.errors() if e["type"] == "extra_forbidden"]

    assert unknown, caught.value.errors()


def test_a_question_status_is_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        Question(id="Q-1.1", text="x", status="maybe")  # type: ignore[arg-type]

    assert Question(id="Q-1.1", text="x").status == "open"


def test_an_invariant_needs_its_check() -> None:
    with pytest.raises(ValidationError):
        Invariant.model_validate({"id": "I-1.1", "statement": "x"})


# ....................... #


def test_a_document_defines_its_number_rows_invariants_questions_amendments_and_retired() -> None:
    doc = _doc(
        "0001",
        decisions=[Decision(id="D-1.1", grade="OPEN", text="x")],
        invariants=[Invariant(id="I-1.1", statement="x", check="true")],
        questions=[Question(id="Q-1.1", text="x")],
        amendments=[Amendment(id="A-3")],
        retired=["D-1.9"],
    )

    assert doc.defined_identifiers() == {"0001", "D-1.1", "I-1.1", "Q-1.1", "A-3", "D-1.9"}
    assert doc.decision("D-1.1") is not None
    assert doc.decision("D-1.2") is None


def test_the_corpus_joins_a_decision_to_its_document_and_knows_what_stands() -> None:
    standing = _doc("0001", decisions=[Decision(id="D-1.1", grade="OPEN", text="x")])
    draft = _doc("0002", status="draft")
    archived = _doc("0003", archived=True, decisions=[Decision(id="D-3.1", grade="OPEN", text="x")])
    corpus = Corpus(documents=[standing, draft, archived])

    found = corpus.decision("D-3.1")

    assert found is not None and found[0].archived
    assert corpus.decision("D-9.9") is None
    assert [d.id for d in corpus.standing()] == ["0001"]
    assert "D-3.1" in corpus.defined_identifiers()


def test_an_amendment_may_change_nothing_typed() -> None:
    entry = Amendment(id="A-86", title="an execution finding")

    assert entry.changes == []
    assert entry.at is None


# ....................... #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("D-53.6", True),
        ("D-A.3", True),
        ("D-A.1a", True),
        ("D-2", True),
        ("I-44.4", True),
        ("Q-53.1", True),
        ("A-130", True),
        ("0052", True),
        ("RFC 0052", False),
        ("§5.2", False),
        ("D-53", True),
    ],
)
def test_what_counts_as_a_citation(value: str, expected: bool) -> None:
    assert is_citation(value) is expected


def test_a_check_state_is_closed_vocabulary_and_defaults_to_shadow() -> None:
    with pytest.raises(ValidationError):
        Decision(id="D-1.1", grade="OPEN", text="x", check="true", check_state="on")  # type: ignore[arg-type]

    row = Decision(id="D-1.1", grade="OPEN", text="x", check="true", check_twin="tests/test_x.py")

    assert row.check_state == "shadow"
    assert Decision(id="D-1.1", grade="OPEN", text="x").check_state == "shadow"


def test_the_loaders_fields_are_never_part_of_the_dump() -> None:
    doc = _doc("0001", path="rfcs/0001-x.yaml", archived=True)
    dumped = doc.model_dump()

    assert "path" not in dumped and "archived" not in dumped
    assert doc.schema_version == 2
