"""The importer over the model and the archive (D-53.9, D-53.13), coverage
as a per-path fact (D-53.6), path rot (D-53.7) and fingerprint drift
told apart by field (D-53.4) — the application half of RFC 0053 phase 2,
tested without a record: the importer's comparison against an empty
graph is the whole of what it would append."""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.application.decisions import (
    Graph,
    PlanError,
    coverage,
    fingerprint_drift,
    import_corpus,
    load_corpus,
    path_rot,
)
from torve.config.rfc_emit import amend_row, fix_row_text, stamp
from torve.config.rfc_parse import decision_table
from torve.config.spec import archive_dir
from torve.domain.events import EventKind

# ----------------------- #


def document(
    number: str,
    rows: list[tuple[str, str, str, str]],
    *,
    status: str = "accepted",
    implementation: str = "complete",
    superseded_by: str | None = None,
    phasing: str = "",
    extra: str = "",
) -> str:
    table = "\n".join(
        f"| {ident} | `{grade}` | {text} | {paths} | — |" for ident, grade, text, paths in rows
    )
    superseded = f'superseded_by: "{superseded_by}"' if superseded_by else "superseded_by: null"

    return (
        "---\n"
        f'id: "{number}"\n'
        f"title: Document {number}\n"
        f"status: {status}\n"
        f"implementation: {implementation}\n"
        "depends_on: []\n"
        "informed_by: []\n"
        "supersedes: []\n"
        f"{superseded}\n"
        "amended_by: []\n"
        "owner: tester\n"
        "description: >-\n"
        "  A document.\n"
        "schema_version: 1\n"
        f"{extra}"
        "---\n"
        "\n"
        f"# RFC {number} — Document {number}\n"
        "\n"
        "## Decisions\n"
        "\n"
        "| # | Grade | Decision | Paths | Consequence |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{table}\n"
        f"{phasing}"
    )


def corpus(tmp_path: Path, **docs: str) -> Path:
    rfc_dir = tmp_path / "rfcs"
    rfc_dir.mkdir(exist_ok=True)

    for number, text in docs.items():
        (rfc_dir / f"{number}-document-{number}.md").write_text(text, encoding="utf-8")

    return rfc_dir


def archived(rfc_dir: Path, number: str, text: str) -> Path:
    target = archive_dir(rfc_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{number}-document-{number}.md"
    path.write_text(text, encoding="utf-8")

    return path


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

    assert retired.payload["reason"] == "archived in 0001-document-0001.md, superseded by 0002"
    assert kinds.index((EventKind.DECISION_RECORDED, "D-1.1")) < kinds.index(
        (EventKind.DECISION_RETIRED, "D-1.1")
    )


def test_a_grade_outside_the_vocabulary_is_refused_as_not_mintable(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "MAYBE", "x", "—")])})

    with pytest.raises(PlanError, match="not mintable"):
        import_corpus(Graph(), rfc_dir)


def test_a_fence_the_model_refuses_is_refused_as_a_plan_error(tmp_path: Path) -> None:
    text = document("0001", [("D-1.1", "OPEN", "x", "—")]) + (
        "\n```yaml questions\n- id: Q-1.1\n  text: x\n  state: open\n```\n"
    )
    rfc_dir = corpus(tmp_path, **{"0001": text})

    with pytest.raises(PlanError, match="state"):
        load_corpus(rfc_dir)


# ....................... #


PHASING = (
    "\n## Phasing\n\n```yaml\n- phase: 1\n  title: one\n  intent: >-\n    Build it.\n"
    '  scope: ["src/torve/cli/**"]\n  acceptance: []\n  depends_on: []\n```\n'
)


def test_coverage_is_governed_by_a_row_or_by_a_phase_scope(tmp_path: Path) -> None:
    rfc_dir = corpus(
        tmp_path,
        **{
            "0001": document(
                "0001", [("D-1.1", "LOCKED", "x", "`src/torve/domain/**`")], phasing=PHASING
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
    assert "torve rfc amend 0001 --retire D-1.3 --reason path-rot" in rotted[0].line()


# ....................... #


def test_fingerprint_drift_tells_a_hand_edited_grade_from_a_hand_edited_text(
    tmp_path: Path,
) -> None:
    text = document("0001", [("D-1.1", "OPEN", "A rule.", "`src/a.py`")]) + "\n## Amendments\n"
    stamped, _ = amend_row(text, "D-1.1", grade="ASSUMED")
    rfc_dir = corpus(tmp_path, **{"0001": stamped})

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])

    by_hand = stamped.replace("`ASSUMED`", "`LOCKED`")
    (rfc_dir / "0001-document-0001.md").write_text(by_hand, encoding="utf-8")
    problems, warnings = fingerprint_drift(load_corpus(rfc_dir))

    assert warnings == []
    assert len(problems) == 1 and "grade or paths changed by hand" in problems[0]

    typo = stamped.replace("A rule.", "A rule, reworded.")
    (rfc_dir / "0001-document-0001.md").write_text(typo, encoding="utf-8")
    problems, warnings = fingerprint_drift(load_corpus(rfc_dir))

    assert problems == []
    assert len(warnings) == 1 and "editorial drift" in warnings[0]

    fixed, _ = fix_row_text(typo, "D-1.1", "A rule, reworded.")
    (rfc_dir / "0001-document-0001.md").write_text(fixed, encoding="utf-8")

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])


def test_a_row_never_stamped_is_never_compared(tmp_path: Path) -> None:
    rfc_dir = corpus(tmp_path, **{"0001": document("0001", [("D-1.1", "OPEN", "x", "—")])})

    assert fingerprint_drift(load_corpus(rfc_dir)) == ([], [])


def test_the_stamp_is_the_row_fingerprint_beside_its_rule_fingerprint() -> None:
    (row,) = decision_table(document("0001", [("D-1.1", "OPEN", "x", "`a`")]))
    full, rule = stamp(row).split("/")

    assert len(full) == 16 and len(rule) == 16
