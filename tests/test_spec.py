"""The specification model (S-0053/the-model, S-0056 S-0056/D-1): every typed
list refuses unknown keys by name (S-0053/D-3); the fingerprint covers text,
grade and paths and nothing a human reads beside them (S-0053/D-5, S-0053/D-16);
the stamp is the content fingerprint beside the rule fingerprint; the
document tells what it defines; the corpus joins across documents and
knows which documents may be inherited from."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.config.spec import check_anatomy
from torve.domain.spec import (
    DOCUMENT_FILE,
    Alternative,
    Amendment,
    Change,
    Commit,
    Corpus,
    Decision,
    DesignSection,
    Document,
    Invariant,
    Question,
    file_of,
    fingerprint,
    is_citation,
    rule_fingerprint,
)

# ----------------------- #


def _doc(number: str, status: str = "accepted", **extra: object) -> Document:
    return Document(
        id=f"S-{number}",
        title=f"Document {number}",
        status=status,  # type: ignore[arg-type]
        owner="Test Owner",
        summary="A scratch document.",
        **extra,  # type: ignore[arg-type]
    )


# ----------------------- #


def test_the_fingerprint_covers_text_grade_and_paths_only() -> None:
    base = Decision(id="S-0001/D-1", grade="LOCKED", text="Sessions in Redis", paths=["a/**"])
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
    row = Decision(id="S-0001/D-1", grade="LOCKED", text="x", paths=["a/**"])
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
        (Decision, {"id": "S-0001/D-1", "grade": "OPEN", "text": "x", "why": "not a field"}),
        (
            Invariant,
            {"id": "S-0001/I-1", "statement": "one lander", "check": "x", "paths": [], "cmd": "y"},
        ),
        (Alternative, {"option": "x", "rejected_because": "y", "rejected": True}),
        (Question, {"id": "S-0001/Q-1", "text": "x", "state": "open"}),
        (Change, {"subject": "S-0001/D-1", "field": "grade", "from": "OPEN", "after": "LOCKED"}),
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
        Question(id="S-0001/Q-1", text="x", status="maybe")  # type: ignore[arg-type]

    assert Question(id="S-0001/Q-1", text="x").status == "open"


def test_an_invariant_needs_its_check() -> None:
    with pytest.raises(ValidationError):
        Invariant.model_validate({"id": "S-0001/I-1", "statement": "x"})


# ....................... #


def test_a_document_defines_its_number_rows_invariants_questions_amendments_and_retired() -> None:
    doc = _doc(
        "0001",
        # written by their local half alone, as the document's own files write
        # them; every one is global in memory (S-0058/D-1)
        decisions=[Decision(id="D-1", grade="OPEN", text="x")],
        invariants=[Invariant(id="I-1", statement="x", check="true")],
        questions=[Question(id="Q-1", text="x")],
        amendments=[Amendment(id="A-3")],
        retired=["D-9"],
    )

    assert doc.defined_identifiers() == {
        "S-0001",
        "S-0001/summary",  # a prose key is an item too (S-0058/D-1)
        "S-0001/D-1",
        "S-0001/I-1",
        "S-0001/Q-1",
        "S-0001/A-3",
        "S-0001/D-9",
    }
    assert doc.decision("S-0001/D-1") is not None
    assert doc.decision("S-0001/D-2") is None


def test_a_documents_change_is_a_typed_conventional_commit() -> None:
    """S-0087/D-1: the header may carry the type its landing takes, with an
    optional scope and `breaking` default false; a document without it loads
    and dumps as it always did."""

    doc = _doc("0001", change={"type": "ci", "scope": "fuzz"})

    assert doc.change == Commit(type="ci", scope="fuzz", breaking=False)
    assert file_of("change") == DOCUMENT_FILE
    assert _doc("0002").change is None
    assert "change" not in _doc("0002").model_dump(exclude_none=True)

    with pytest.raises(ValidationError):
        _doc("0003", change={"type": "chores"})

    with pytest.raises(ValidationError):
        _doc("0004", change={"type": "ci", "emoji": ":construction_worker:"})


def test_an_accepted_design_without_a_change_warns_and_a_convention_is_silent() -> None:
    """S-0087/D-1: the untyped landing is named at check; a convention lands
    nothing, so it owes no type."""

    def warned(doc: Document) -> bool:
        return any("no change" in one for one in check_anatomy(doc)[1])

    design = _doc("0001", design=[DesignSection(key="design", md="x")])

    assert warned(design)
    assert not warned(design.model_copy(update={"change": Commit(type="docs")}))
    assert not warned(_doc("0002", kind="convention"))
    assert not warned(_doc("0003", status="draft"))


def test_the_corpus_joins_a_decision_to_its_document_and_knows_what_stands() -> None:
    standing = _doc("0001", decisions=[Decision(id="S-0001/D-1", grade="OPEN", text="x")])
    draft = _doc("0002", status="draft")
    archived = _doc(
        "0003", archived=True, decisions=[Decision(id="S-0003/D-1", grade="OPEN", text="x")]
    )
    corpus = Corpus(documents=[standing, draft, archived])

    found = corpus.decision("S-0003/D-1")

    assert found is not None and found[0].archived
    assert corpus.decision("S-0009/D-9") is None
    assert [d.id for d in corpus.standing()] == ["S-0001"]
    assert "S-0003/D-1" in corpus.defined_identifiers()


def test_an_amendment_may_change_nothing_typed() -> None:
    entry = Amendment(id="A-86", title="an execution finding")

    assert entry.changes == []
    assert entry.at == ""


# ....................... #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("S-0053/D-6", True),
        ("S-0016/D-14", True),
        ("S-0001/D-10", True),
        ("S-0044/I-4", True),
        ("S-0053/Q-1", True),
        ("S-0052/A-1", True),
        ("S-0044/P-1", True),
        ("S-0058/the-grammar", True),  # a prose key is an item too (S-0058/D-1)
        ("S-0052", True),  # a document is a citation whole
        ("0052", False),  # ... but only spelt as the one grammar spells it
        ("D-53", False),  # a bare local is nobody's citation
        ("D-53.6", False),  # the old grammar (S-0058/D-2)
        ("RFC 0052", False),
        ("§5.2", False),
    ],
)
def test_what_counts_as_a_citation(value: str, expected: bool) -> None:
    assert is_citation(value) is expected


def test_a_check_state_is_closed_vocabulary_and_defaults_to_shadow() -> None:
    with pytest.raises(ValidationError):
        Decision(id="S-0001/D-1", grade="OPEN", text="x", check="true", check_state="on")  # type: ignore[arg-type]

    row = Decision(
        id="S-0001/D-1", grade="OPEN", text="x", check="true", check_twin="tests/test_x.py"
    )

    assert row.check_state == "shadow"
    assert Decision(id="S-0001/D-1", grade="OPEN", text="x").check_state == "shadow"


def test_the_loaders_fields_are_never_part_of_the_dump() -> None:
    doc = _doc("0001", path=".torve/specs/S-0001", archived=True)
    dumped = doc.model_dump()

    assert "path" not in dumped and "archived" not in dumped
    assert doc.schema_version == 4


# ----------------------- #
# S-0057 S-0057/D-7: the landing and its entries are models


def test_a_log_entry_reads_and_writes_the_logs_class_key():
    from torve.domain.spec import Landing, LogEntry, TaskLog

    entry = LogEntry.model_validate(
        {
            "decision": "S-0001/D-1",
            "grade": "LOCKED",
            "kind": "departed",
            "class": "drift",
            "claim": "c",
            "evidence": "src/a.py:1 - e",
            "action": "departed",
        }
    )

    assert entry.entry_class == "drift"
    assert entry.model_dump(mode="json", exclude_defaults=True)["class"] == "drift"
    assert "entry_class" not in entry.model_dump(mode="json")

    landing = Landing.model_validate(
        {
            "task": "T-0001",
            "commit": "abc",
            "at": "2026-09-09T00:00:00Z",
            "entries": [entry.model_dump()],
        }
    )

    assert landing.at == "2026-09-09T00:00:00Z" and landing.attempt == 1 and landing.phase == 0
    assert landing.entries[0].entry_class == "drift"
    assert "class" in TaskLog.model_json_schema()["$defs"]["LogEntry"]["properties"]


# ....................... #


def test_after_is_the_phasing_files_own_list_of_document_ids() -> None:
    """S-0085/D-1: the phasing file carries `after`, and the header's
    `depends_on` keeps its one meaning — a document may name another in both."""

    import json

    from torve.config.spec import schema_text
    from torve.domain.spec import PHASING_FILE, file_of

    assert file_of("after") == PHASING_FILE
    assert "after" in json.loads(schema_text(PHASING_FILE))["properties"]

    doc = _doc("0090", after=["S-0008"], depends_on=["S-0008"])
    assert doc.after == ["S-0008"] and doc.depends_on == ["S-0008"]
    assert _doc("0091").after == []


def test_check_refuses_an_after_naming_an_absent_or_unaccepted_document(tmp_path) -> None:
    """S-0085/D-1: `after` names a landed tree, and only an accepted document
    has one to build on."""

    from test_decisions import document, place

    from torve.config.spec import check_corpus

    spec_dir = tmp_path / ".torve" / "specs"
    rows = [("S-0090/D-1", "ASSUMED", "Widgets are lazy", "—")]

    for number, status in (("0090", "accepted"), ("0091", "draft")):
        directory = place(spec_dir, number, document(number, rows=[], status=status, phasing=None))
        (directory / "phasing.yaml").write_text("phasing: []\n", encoding="utf-8")

    place(spec_dir, "0092", document("0092", rows=rows))
    (spec_dir / "S-0092" / "phasing.yaml").write_text(
        "after: [S-0090, S-0091, S-0099]\nphasing: []\n", encoding="utf-8"
    )
    problems = check_corpus(spec_dir, tmp_path).problems

    assert any("after names 'S-0099', no such document" in one for one in problems)
    assert any("after names S-0091, which is draft" in one for one in problems)
    assert not any("S-0090" in one and "after" in one for one in problems)


def test_schema_descriptions_cover_every_property(tmp_path) -> None:
    """S-0059/D-6, I-3: every property of every schema `torve init` writes
    carries a description — the field's own docstring, carried by
    `use_attribute_docstrings` — so a field added without its words fails
    here rather than reaching an editor as a bare type."""

    import json

    from torve.cli.init import expected_schemas

    missing: list[str] = []

    for path, text in expected_schemas(tmp_path / ".torve" / "specs").items():
        schema = json.loads(text)
        models = [(path.name, schema), *schema.get("$defs", {}).items()]

        for model_name, model in models:
            for prop, shape in model.get("properties", {}).items():
                if not shape.get("description"):
                    missing.append(f"{path.name}: {model_name}.{prop}")

    assert missing == []
