"""The importer over the model and the archive (D-53.9, D-53.13), coverage
as a per-path fact (D-53.6), path rot (D-53.7) and fingerprint drift
told apart by field (D-53.4) — the application half of RFC 0053 phase 2,
tested without a record: the importer's comparison against an empty
graph is the whole of what it would append.

`document`, `corpus`, `place` and `archived` are the corpus builders every
suite shares (RFC 0057 D-57.1): a document is its four files as text,
keyed by file name, written from plain dicts so a test can also write
what the model refuses; `corpus` lays them out as `S-NNNN/` directories
under `.torve/specs/`, `archived` under `.torve/archive/`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from torve.application.decisions import (
    Graph,
    PlanError,
    coverage,
    fingerprint_drift,
    import_corpus,
    load_corpus,
    path_rot,
)
from torve.config.spec import archive_dir, load_document, schema_header
from torve.config.spec_emit import amend_row, dump_document, fix_row_text, stamp
from torve.domain.events import EventKind

# ----------------------- #

Row = tuple[str, str, str, str] | tuple[str, str, str, str, str]
Doc = dict[str, str]  # file name -> text, as `corpus` writes it


def paths_of(cell: str | list[str]) -> list[str]:
    """A paths cell as the old table wrote it (`` `a` `b` `` or `—`), or a
    list already."""

    if isinstance(cell, list):
        return cell

    cleaned = cell.replace("`", " ").strip()

    if not cleaned or set(cleaned) <= set("—- "):
        return []

    return [token for token in (one.strip(",;") for one in cleaned.split()) if token]


def as_text(file_name: str, data: dict[str, Any]) -> str:
    """One file as `corpus` writes it: the schema line, then plain YAML."""

    return f"{schema_header(file_name)}\n" + yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, width=1000
    )


def document(
    number: str,
    rows: list[Row],
    *,
    status: str = "accepted",
    implementation: str = "complete",
    superseded_by: str | None = None,
    phasing: list[dict[str, Any]] | None = None,
    title: str | None = None,
    kind: str = "design",
    owner: str = "tester",
    depends_on: list[str] | None = None,
    informed_by: list[str] | None = None,
    retired: list[str] | None = None,
    details: dict[str, dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
    invariants: list[dict[str, Any]] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    amendments: list[dict[str, Any]] | None = None,
    editorial: list[dict[str, Any]] | None = None,
    contract_example: dict[str, Any] | None = None,
    schema_version: int = 3,
    extra: dict[str, Any] | None = None,
) -> Doc:
    """One document as its files: `rows` as (id, grade, text, paths[,
    consequence]), `details` merged onto the row by id, `extra` merged
    into `document.yaml`. The rows go to `decisions.yaml`, the amendments
    and editorial to `amendments.yaml`; a file with nothing to say is not
    written."""

    decisions: list[dict[str, Any]] = []

    for row in rows:
        ident, grade, text, cell = row[:4]
        one: dict[str, Any] = {"id": ident, "grade": grade, "text": text}
        paths = paths_of(cell)

        if paths:
            one["paths"] = paths

        if len(row) > 4 and row[4] not in ("", "—"):
            one["consequence"] = row[4]

        one.update((details or {}).get(ident, {}))
        decisions.append(one)

    head: dict[str, Any] = {
        "id": number,
        "title": title or f"Document {number}",
        "kind": kind,
        "status": status,
        "implementation": implementation,
        "depends_on": depends_on or [],
        "informed_by": informed_by or [],
        "supersedes": [],
        "superseded_by": superseded_by,
        "owner": owner,
        "description": "A document.",
        "schema_version": schema_version,
    }

    if sections:
        head["sections"] = sections

    for name, value in (
        ("alternatives", alternatives),
        ("questions", questions),
        ("phasing", phasing),
        ("contract_example", contract_example),
    ):
        if value:
            head[name] = value

    head.update(extra or {})

    rows_file: dict[str, Any] = {"decisions": decisions}

    for name, value in (("invariants", invariants), ("retired", retired)):
        if value:
            rows_file[name] = value

    files: Doc = {"document.yaml": as_text("document.yaml", head)}
    files["decisions.yaml"] = as_text("decisions.yaml", rows_file)

    tool_file: dict[str, Any] = {}

    for name, value in (("amendments", amendments), ("editorial", editorial)):
        if value:
            tool_file[name] = value

    if tool_file:
        files["amendments.yaml"] = as_text("amendments.yaml", tool_file)

    return files


def place(spec_dir: Path, number: str, doc: Doc) -> Path:
    """One document's directory written (or overwritten) under *spec_dir*;
    a file the new text lacks is removed, so a shrunken document is the
    document a test asked for."""

    directory = spec_dir / f"S-{number}"
    directory.mkdir(parents=True, exist_ok=True)

    for stale in directory.iterdir():
        if stale.name not in doc:
            stale.unlink()

    for file_name, text in doc.items():
        (directory / file_name).write_text(text, encoding="utf-8")

    return directory


def corpus(tmp_path: Path, **docs: Doc) -> Path:
    """The corpus at `tmp_path/.torve/specs/`, one `S-NNNN/` per document."""

    spec_dir = tmp_path / ".torve" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)

    for number, doc in docs.items():
        place(spec_dir, number, doc)

    return spec_dir


def archived(spec_dir: Path, number: str, doc: Doc) -> Path:
    return place(archive_dir(spec_dir), number, doc)


PHASE = {
    "phase": 1,
    "title": "one",
    "intent": "Build it.",
    "scope": ["src/torve/cli/**"],
    "acceptance": [],
    "depends_on": [],
}


# ----------------------- #


def test_the_importer_reads_standing_rows_through_the_model(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path, **{"0001": document("0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])}
    )

    pending = import_corpus(Graph(), rfc_dir)

    assert [p.kind for p in pending] == [EventKind.SOURCE_IMPORTED, EventKind.DECISION_RECORDED]
    assert pending[1].payload == {
        "grade": "LOCKED",
        "text": "A rule.",
        "paths": ["src/a.py"],
        "source_id": pending[0].subject_id,
        "consequence": "",
        "check": None,
    }


def test_an_archived_document_is_a_source_whose_rows_retire_with_the_archive_named(
    tmp_path: Path,
) -> None:
    rfc_dir = corpus(tmp_path, **{"0002": document("0002", [("D-2.1", "OPEN", "Stands.", "—")])})
    archived(
        rfc_dir,
        "0001",
        document(
            "0001",
            [("D-1.1", "LOCKED", "Was a rule.", "`src/a.py`")],
            status="superseded",
            superseded_by="0002",
        ),
    )

    pending = import_corpus(Graph(), rfc_dir)
    kinds = [(p.kind, p.subject_id) for p in pending]

    assert (EventKind.DECISION_RECORDED, "D-1.1") in kinds
    assert (EventKind.DECISION_RETIRED, "D-1.1") in kinds

    retired = next(p for p in pending if p.kind is EventKind.DECISION_RETIRED)

    assert retired.payload["reason"] == "archived in S-0001, superseded by 0002"
    assert kinds.index((EventKind.DECISION_RECORDED, "D-1.1")) < kinds.index(
        (EventKind.DECISION_RETIRED, "D-1.1")
    )


def test_a_grade_outside_the_vocabulary_is_refused_as_not_mintable(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "MAYBE", "x", "—")])})

    with pytest.raises(PlanError, match=r"decisions\.0\.grade"):
        import_corpus(Graph(), rfc_dir)


def test_a_key_the_model_refuses_is_refused_as_a_plan_error(tmp_path: Path) -> None:
    text = document(
        "0001",
        [("D-1.1", "OPEN", "x", "—")],
        questions=[{"id": "Q-1.1", "text": "x", "state": "open"}],
    )
    rfc_dir = corpus(tmp_path, **{"0001": text})

    with pytest.raises(PlanError, match=r"questions\.0\.state"):
        load_corpus(rfc_dir)


# ....................... #


def test_coverage_is_governed_by_a_row_or_by_a_phase_scope(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001", [("D-1.1", "LOCKED", "x", "`src/torve/domain/**`")], phasing=[PHASE]
            )
        },
    )
    loaded = load_corpus(rfc_dir)

    assert coverage(loaded, "src/torve/domain/task.py") == "governed"
    assert coverage(loaded, "src/torve/domain/") == "governed"
    assert coverage(loaded, "src/torve/cli/rfc.py") == "governed"  # a phase reaches it
    assert coverage(loaded, "src/torve/gates/scope.py") == "ungoverned"


def test_coverage_is_retired_when_only_the_archive_ever_named_a_path(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0002": document("0002", [("D-2.1", "OPEN", "x", "—")])})
    archived(
        rfc_dir,
        "0001",
        document("0001", [("D-1.1", "LOCKED", "x", "`src/old/**`")], status="superseded"),
    )
    loaded = load_corpus(rfc_dir)

    assert coverage(loaded, "src/old/thing.py") == "retired"
    assert coverage(loaded, "src/new/thing.py") == "ungoverned"


def test_a_draft_or_superseded_document_governs_nothing(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document("0001", [("D-1.1", "LOCKED", "x", "`src/a/**`")], status="draft"),
            "0002": document(
                "0002", [("D-2.1", "LOCKED", "x", "`src/b/**`")], superseded_by="0003"
            ),
            "0003": document("0003", [("D-3.1", "OPEN", "x", "—")]),
        },
    )
    loaded = load_corpus(rfc_dir)

    assert coverage(loaded, "src/a/x.py") == "ungoverned"
    assert coverage(loaded, "src/b/x.py") == "ungoverned"


# ....................... #


def test_path_rot_names_rows_whose_every_glob_matches_nothing(tmp_path: Path) -> None:
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "a" / "x.py").write_text("", encoding="utf-8")
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001",
                [
                    ("D-1.1", "LOCKED", "alive", "`src/a/**`"),
                    ("D-1.2", "LOCKED", "half", "`src/a/**` `src/gone/**`"),
                    ("D-1.3", "ASSUMED", "rotted", "`src/gone/**`"),
                    ("D-1.4", "OPEN", "pathless", "—"),
                ],
            ),
            "0002": document(
                "0002", [("D-2.1", "LOCKED", "intended", "`src/soon/**`")], implementation="none"
            ),
        },
    )

    rotted = path_rot(load_corpus(rfc_dir), tmp_path)

    assert [(r.identifier, r.grade) for r in rotted] == [("D-1.3", "ASSUMED")]
    assert "torve spec amend 0001 --retire D-1.3 --reason path-rot" in rotted[0].line()


# ....................... #


def _write(rfc_dir: Path, doc: Any) -> None:
    place(rfc_dir, "0001", dump_document(doc))


def test_fingerprint_drift_tells_a_hand_edited_grade_from_a_hand_edited_text(
    tmp_path: Path,
) -> None:
    rfc_dir = corpus(
        tmp_path, **{"0001": document("0001", [("D-1.1", "OPEN", "A rule.", "`src/a.py`")])}
    )
    stamped, _ = amend_row(load_document(rfc_dir / "S-0001"), "D-1.1", grade="ASSUMED")
    _write(rfc_dir, stamped)

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])

    row = stamped.decision("D-1.1")
    assert row is not None
    _write(
        rfc_dir,
        stamped.model_copy(update={"decisions": [row.model_copy(update={"grade": "LOCKED"})]}),
    )
    problems, warnings = fingerprint_drift(load_corpus(rfc_dir))

    assert warnings == []
    assert len(problems) == 1 and "grade or paths changed by hand" in problems[0]

    typo = stamped.model_copy(
        update={"decisions": [row.model_copy(update={"text": "A rule, reworded."})]}
    )
    _write(rfc_dir, typo)
    problems, warnings = fingerprint_drift(load_corpus(rfc_dir))

    assert problems == []
    assert len(warnings) == 1 and "editorial drift" in warnings[0]

    fixed, _ = fix_row_text(typo, "D-1.1", "A rule, reworded!")
    _write(rfc_dir, fixed)

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])


def test_a_row_never_stamped_is_never_compared(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "OPEN", "x", "—")])})

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])


def test_the_stamp_is_the_row_fingerprint_beside_its_rule_fingerprint(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "OPEN", "x", "`a`")])})
    (row,) = load_document(rfc_dir / "S-0001").decisions
    full, rule = stamp(row).split("/")

    assert len(full) == 16 and len(rule) == 16


# ....................... #
# RFC 0054 phase 1: decision.recorded carries the consequence and the check
# (D-54.1), and a record written without them is brought level once.


def test_the_importer_carries_consequence_and_check(tmp_path: Path) -> None:
    text = document(
        "0001",
        [("D-1.1", "LOCKED", "A rule.", "`src/a.py`", "because it holds")],
        details={"D-1.1": {"check": "pytest tests/test_a.py"}},
    )
    rfc_dir = corpus(tmp_path, **{"0001": text})

    pending = import_corpus(Graph(), rfc_dir)
    recorded = next(p for p in pending if p.kind is EventKind.DECISION_RECORDED)

    assert recorded.payload["consequence"] == "because it holds"
    assert recorded.payload["check"] == "pytest tests/test_a.py"


def test_a_record_without_the_consequence_is_re_recorded_once(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from torve.application.decisions import DecisionState
    from torve.domain.source import corpus_source_id

    text = document("0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`", "because it holds")])
    rfc_dir = corpus(tmp_path, **{"0001": text})
    source_id = corpus_source_id("0001")
    graph = Graph()
    graph.versions["D-1.1"] = [
        DecisionState(
            id="D-1.1",
            grade="LOCKED",
            text="A rule.",
            paths=["src/a.py"],
            source_id=source_id,
            version=1,
            at=datetime.now(UTC),
        )
    ]

    first = [p for p in import_corpus(graph, rfc_dir) if p.kind is EventKind.DECISION_RECORDED]

    assert len(first) == 1 and first[0].payload["consequence"] == "because it holds"

    graph.versions["D-1.1"].append(
        DecisionState(
            id="D-1.1",
            grade="LOCKED",
            text="A rule.",
            paths=["src/a.py"],
            source_id=source_id,
            version=2,
            at=datetime.now(UTC),
            consequence="because it holds",
        )
    )

    assert [p for p in import_corpus(graph, rfc_dir) if p.kind is EventKind.DECISION_RECORDED] == []
