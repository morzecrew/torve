"""`torve rfc` — the corpus checks of RFC 0007 §3a and charter A-15, each
observed to fail (D-2.2 discipline): sabotage the model refuses (ungraded
row, unknown vocabulary, a key outside the schema), sabotage only the
corpus can see (LOCKED without paths, duplicate identifier, cycle, a
comment), directory contents with routing messages (D-A.18), derived
numbering over a hole and over the archive (D-A.17/D-A.19), and line-cite
rot.

Inheriting from a non-accepted document is a problem (D-A.10, hardened once
the corpus's one violation was resolved). Citation resolution is a problem
too: retired identifiers resolve through `retired:` (D-16.1), so an
unresolvable citation is a typo, and a retired identifier can never be
redefined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_decisions import document
from typer.testing import CliRunner

from torve.cli import app
from torve.config.spec import load_document

runner = CliRunner()

EXIT_CONFIG = 3


def rfc_text(
    number: str,
    title: str,
    decision: str = "D-T.1",
    status: str = "draft",
    implementation: str = "none",
    *,
    rows: list[Any] | None = None,
    **kw: Any,
) -> str:
    """One scratch document as YAML: the shared builder with this suite's
    defaults — a draft nobody has built, carrying one ungoverned row."""

    return document(
        number,
        rows or [(decision, "ASSUMED", "Something is decided", "—")],
        title=title,
        status=status,
        implementation=implementation,
        **kw,
    )


def prose(md: str) -> list[dict[str, str]]:
    return [{"key": "design", "heading": "Design", "md": md}]


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, str]) -> Path:
    """Write (filename, text) documents and return the corpus dir. There is
    no index to generate any more (D-56.7)."""

    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)

    for name, text in docs or (("0001-widget.yaml", rfc_text("0001", "Widget")),):
        (rfcs / name).write_text(text, encoding="utf-8")

    return rfcs


def loaded(rfcs: Path, name: str):
    return load_document(rfcs / name)


# ....................... #
# format sabotage (0007 §3a): each check observed to fail


def test_a_conforming_corpus_passes(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_an_ungraded_row_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", rows=[("D-T.1", "PROBABLY", "Something is decided", "—")])
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "decisions.0.grade" in result.output


def test_a_locked_row_without_paths_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", rows=[("D-T.1", "LOCKED", "Something is decided", "—")])
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "declares no paths" in result.output


def test_a_duplicate_identifier_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.1")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "already used" in result.output


def test_a_two_document_cycle_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1", depends_on=["0002"])),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "cycle" in result.output


def test_a_comment_reddens_naming_the_line(tmp_path: Path) -> None:
    # D-56.4: the schema header is the one legal comment; anything else is
    # meaning outside the model.
    text = rfc_text("0001", "Widget")
    head, rest = text.split("\n", 1)
    seed(tmp_path, ("0001-widget.yaml", f"{head}\n# a note nobody can read\n{rest}"))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "0001-widget.yaml:2: a comment" in result.output


def test_schema_version_one_is_refused_naming_the_conversion(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", rfc_text("0001", "Widget", schema_version=1)))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "RFC 0056 phase 1" in result.output


def test_inheriting_from_a_draft_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        (
            "0001-alpha.yaml",
            rfc_text("0001", "Alpha", "D-T.1", status="accepted", depends_on=["0002"]),
        ),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.2")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "D-A.10" in result.output


def test_check_reports_a_key_the_model_refuses(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", questions=[{"id": "Q-1.1", "text": "x", "state": "open"}])
    seed(tmp_path, ("0001-widget.yaml", doc))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "questions.0.state" in result.output


# ....................... #
# the contract example (RFC 0025 §5.4, D-25.10): typed, so a schema change
# reddens the example rather than letting it rot


def test_a_valid_contract_example_passes(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", contract_example={"id": "T-9999", "decisions": []})
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_an_invalid_contract_example_reddens(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        contract_example={"id": "T-9999", "decisions": [], "extra_unknown_field": True},
    )
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "contract_example.extra_unknown_field" in result.output


def test_a_document_without_a_contract_example_passes(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose("The contract example lives in 0001.\n"))
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# directory contents (charter A-15, D-A.18): refusals that route


def test_a_stray_file_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "notes.txt").write_text("scratch\n", encoding="utf-8")
    (rfcs / "0016.yaml").write_text(rfc_text("0016", "Slugless"), encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "notes.txt: not NNNN-slug.yaml" in result.output
    assert "0016.yaml: not NNNN-slug.yaml" in result.output


def test_a_subdirectory_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "draft").mkdir()
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "a subdirectory in the corpus path" in result.output


def test_a_backup_file_is_told_to_die(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "old-0004.yaml.bak").write_text("x\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "a stray file in the corpus path" in result.output


def test_a_markdown_document_is_told_to_convert(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "0002-legacy.md").write_text("---\nid: '0002'\n---\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "a markdown document — convert it" in result.output


def test_an_id_disagreeing_with_the_filename_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0002-widget.yaml", rfc_text("0001", "Widget")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "disagrees with the filename" in result.output


# ....................... #
# vocabularies: refused by the model, through the loader


def test_an_unknown_implementation_value_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", implementation="in_progress")
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "implementation: Input should be" in result.output and "abandoned" in result.output


def test_an_unknown_kind_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", kind="policy")
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "kind: Input should be" in result.output and "convention" in result.output


# ....................... #
# LOCKED paths globs (D-32): an implemented RFC cites real areas; a document
# not yet built names intended modules


def locked_doc(implementation: str) -> str:
    return rfc_text(
        "0001",
        "Widget",
        status="accepted",
        implementation=implementation,
        rows=[("D-T.1", "LOCKED", "Something is decided", "`src/ghost/**`")],
    )


def test_a_complete_rfc_citing_a_missing_area_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", locked_doc("complete")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "matches nothing" in result.output


def test_a_partial_rfc_warns_once_about_unbuilt_areas(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", locked_doc("partial")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "unbuilt areas" in result.output


def test_an_accepted_but_unbuilt_rfc_may_name_intended_modules(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", locked_doc("none")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# line-cite rot (0007 §3a): real paths redden, illustrations do not


def test_citing_a_real_path_with_a_line_number_reddens(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    doc = rfc_text("0001", "Widget", sections=prose("See `src/mod.py:12` for the loop.\n"))
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "line numbers rot" in result.output


def test_an_illustrative_location_passes(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001", "Widget", sections=prose("A model can cite a real `file.py:42` and lie.\n")
    )
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# numbering (D-A.17, D-A.19): derived, holes stay holes


def test_new_derives_max_plus_one_over_a_hole(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        ("0003-gamma.yaml", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "new", "Delta thing")
    assert result.exit_code == 0, result.output
    created = tmp_path / "rfcs" / "0004-delta-thing.yaml"
    assert created.is_file()  # the 0002 hole stays a hole
    text = created.read_text(encoding="utf-8")
    assert text.startswith("# yaml-language-server: $schema=schema/document.json\n")
    assert "id: '0004'" in text
    assert invoke(tmp_path, "check").exit_code == 0


def test_new_with_convention_kind_lands_in_the_documents_kind(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "new", "House style", "--kind", "convention")
    assert result.exit_code == 0, result.output
    created = tmp_path / "rfcs" / "0002-house-style.yaml"
    assert "kind: convention" in created.read_text(encoding="utf-8")
    assert loaded(tmp_path / "rfcs", "0002-house-style.yaml").kind == "convention"


# ....................... #
# section keys and identifier resolution


def test_two_sections_keyed_the_same_redden(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        sections=[
            {"key": "parts", "heading": "Parts", "md": "prose\n"},
            {"key": "parts", "heading": "Parts, again", "md": "more\n"},
        ],
    )
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "two sections keyed 'parts'" in result.output


def test_an_unresolvable_citation_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose("See D-9.9 for details.\n"))
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "cites D-9.9" in result.output


def test_a_retired_identifier_resolves(tmp_path: Path) -> None:
    # D-16.1: a citation of a retired identifier is history, not a typo.
    doc = rfc_text(
        "0001",
        "Widget",
        retired=["D-T.9"],
        sections=prose("D-T.9 was removed; the identifier is retired.\n"),
    )
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_redefining_a_retired_identifier_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1", retired=["D-T.9"])),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.9")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "never reused" in result.output


def test_a_citation_resolves_across_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        (
            "0002-beta.yaml",
            rfc_text("0002", "Beta", "D-T.2", sections=prose("Inherits D-T.1 from Alpha.\n")),
        ),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


def test_a_citation_inside_a_code_fence_is_illustration(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose("```yaml\ndecision: D-9.9\n```\n"))
    seed(tmp_path, ("0001-widget.yaml", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


# ....................... #
# machine surfaces


def test_check_json_is_one_parseable_document(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "check", "--format", "json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["schema_version"] == 1


def test_graph_lists_edges_with_statuses(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    # Content, not layout (D-18.1): both ends of the edge and their statuses.
    assert "0001" in result.output
    assert "0002" in result.output
    assert "draft" in result.output


def test_graph_shows_standalone_documents(tmp_path: Path) -> None:
    # A document with no edges never appeared in the per-edge table; the
    # tree renders it as a bare root.
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
        ("0003-gamma.yaml", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    assert "0003" in result.output


def test_graph_shows_implementation_state_and_omits_finished_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        (
            "0001-alpha.yaml",
            rfc_text("0001", "Alpha", "D-T.1", status="accepted", implementation="partial"),
        ),
        (
            "0002-beta.yaml",
            rfc_text(
                "0002",
                "Beta",
                "D-T.2",
                status="accepted",
                implementation="complete",
                depends_on=["0001"],
            ),
        ),
        ("0003-gamma.yaml", rfc_text("0003", "Gamma", "D-T.3", depends_on=["0002"])),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    assert "partial" in result.output
    # The finished document collapses to the count line; its dependent still
    # renders, attached where it stood.
    assert "omitted" in result.output and "0002" in result.output
    assert "0003" in result.output
    assert "complete" not in result.output.replace("accepted and complete", "")


def test_graph_renders_a_multi_parent_document_once(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.yaml", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.yaml", rfc_text("0002", "Beta", "D-T.2")),
        (
            "0003-gamma.yaml",
            rfc_text("0003", "Gamma", "D-T.3", depends_on=["0001", "0002"]),
        ),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    # Expanded under the first parent, back-referenced under the second —
    # asserted by content: the id appears exactly twice, once as a repeat.
    assert result.output.count("0003") == 2
    assert "↑" in result.output


def test_the_corpus_of_this_repository_is_clean() -> None:
    repo = Path(__file__).resolve().parent.parent
    result = invoke(repo, "check")
    assert result.exit_code == 0, result.output


# ----------------------- #
# What the model adds to `check`, `--fix-rot`, `archive` and `show` over
# the archive (RFC 0053 phase 2)


def _accepted(number: str, decision: str, paths: str, sections: Any = None) -> str:
    family = decision.rsplit(".", 1)[0]

    return document(
        number,
        [
            (decision, "ASSUMED", "Something is decided", paths),
            (f"{family}.9", "OPEN", "A second row, so a retirement leaves one", "—"),
        ],
        title=f"Doc {number}",
        status="accepted",
        implementation="complete",
        sections=sections,
    )


def _name(number: str) -> str:
    return f"{number}-doc-{number}.yaml"


def test_check_reports_a_hand_edited_grade_and_warns_on_a_hand_edited_text(
    tmp_path: Path,
) -> None:
    rfcs = seed(tmp_path)
    amended = invoke(
        tmp_path,
        "amend",
        "0001",
        "--title",
        "regrade",
        "--row",
        "D-T.1",
        "--grade",
        "LOCKED",
        "--path",
        "src/x/**",
    )

    assert amended.exit_code == 0, amended.output
    assert invoke(tmp_path, "check").exit_code == 0

    path = rfcs / "0001-widget.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("grade: LOCKED", "grade: OPEN"), encoding="utf-8")
    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "grade or paths changed by hand" in result.output

    path.write_text(text.replace("Something is decided", "Something is decided!"), encoding="utf-8")
    result = invoke(tmp_path, "check")

    assert result.exit_code == 0, result.output
    assert "editorial drift" in result.output

    fixed = invoke(tmp_path, "fix", "D-T.1", "Something is decided, once.")

    assert fixed.exit_code == 0, fixed.output
    assert "editorial drift" not in invoke(tmp_path, "check").output


def test_amend_with_retire_records_the_reason_and_the_diff(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, (_name("0001"), _accepted("0001", "D-T.1", "—")))
    result = invoke(
        tmp_path,
        "amend",
        "0001",
        "--title",
        "gone",
        "--row",
        "D-T.1",
        "--retire",
        "--reason",
        "path rot",
    )

    assert result.exit_code == 0, result.output
    doc = loaded(rfcs, _name("0001"))

    assert doc.retired == ["D-T.1"] and doc.decision("D-T.1") is None
    (change,) = [c for c in doc.amendments[-1].changes if c.field == "retired"]
    assert change.subject == "D-T.1" and change.after == "path rot"
    assert invoke(tmp_path, "check").exit_code == 0


def test_check_warns_on_path_rot_and_fix_rot_retires_it(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alive.py").write_text("", encoding="utf-8")
    rfcs = seed(
        tmp_path,
        (_name("0001"), _accepted("0001", "D-T.1", "`src/gone/**`")),
        (_name("0002"), _accepted("0002", "D-G.1", "`src/alive.py`")),
    )

    result = invoke(tmp_path, "check")

    assert result.exit_code == 0, result.output
    assert "D-T.1 (ASSUMED) declares src/gone/** and nothing in the tree matches" in result.output
    assert "D-G.1" not in result.output

    fixed = invoke(tmp_path, "check", "--fix-rot")

    assert fixed.exit_code == 0, fixed.output
    assert "retired D-T.1 (path rot)" in fixed.output

    doc = loaded(rfcs, _name("0001"))

    assert doc.retired == ["D-T.1"]
    assert "path-rotted row(s) retired by `torve rfc check --fix-rot`" in doc.amendments[-1].title
    assert "nothing in the tree matches" not in invoke(tmp_path, "check").output


def test_archive_moves_the_document_and_show_still_resolves_its_row(tmp_path: Path) -> None:
    seed(
        tmp_path,
        (_name("0001"), _accepted("0001", "D-T.1", "`src/x/**`")),
        (_name("0002"), _accepted("0002", "D-G.1", "`src/y/**`")),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "rfcs" / _name("0001")).exists()

    archived = tmp_path / "archive" / "rfcs" / _name("0001")

    assert archived.exists()
    assert "superseded_by: '0002'" in archived.read_text(encoding="utf-8")
    assert invoke(tmp_path, "check").exit_code == 0

    shown = invoke(tmp_path, "show", "D-T.1", "--format", "json")

    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)

    assert payload["archived"] is True and payload["defined_in"] == _name("0001")

    doc = invoke(tmp_path, "show", "0001", "--format", "json")

    assert doc.exit_code == 0, doc.output
    assert json.loads(doc.output)["superseded_by"] == "0002"


def test_archive_refuses_when_the_corpus_without_it_does_not_check(tmp_path: Path) -> None:
    seed(
        tmp_path,
        (_name("0001"), _accepted("0001", "D-T.1", "`src/x/**`")),
        (
            _name("0002"),
            _accepted(
                "0002", "D-G.1", "`src/y/**`", prose("Built on D-Z.9, which nothing defines.\n")
            ),
        ),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == EXIT_CONFIG
    assert (tmp_path / "rfcs" / _name("0001")).exists()
    assert not (tmp_path / "archive").exists()


def test_new_derives_its_number_over_the_archive(tmp_path: Path) -> None:
    seed(tmp_path)
    archive = tmp_path / "archive" / "rfcs"
    archive.mkdir(parents=True)
    (archive / "0007-old.yaml").write_text(
        rfc_text("0007", "Old", "D-O.1", status="superseded", superseded_by="0001"),
        encoding="utf-8",
    )

    result = invoke(tmp_path, "new", "Fresh")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "rfcs" / "0008-fresh.yaml").exists()


def test_show_enriches_a_row_with_its_details(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        details={
            "D-T.1": {
                "rationale": "because",
                "cites": ["D-T.1"],
                "check": "pytest tests/test_x.py",
                "fingerprint": "0123456789abcdef/fedcba9876543210",
            }
        },
    )
    seed(tmp_path, ("0001-widget.yaml", doc))

    shown = invoke(tmp_path, "show", "D-T.1", "--format", "json")

    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)

    assert payload["rationale"] == "because"
    assert payload["check"] == "pytest tests/test_x.py"
    assert payload["fingerprint"] == "0123456789abcdef/fedcba9876543210"


def test_archive_moves_a_document_that_others_cite_and_depend_on(tmp_path: Path) -> None:
    citing = document(
        "0002",
        [
            ("D-G.1", "ASSUMED", "Something is decided", "`src/y/**`"),
            ("D-G.9", "OPEN", "A second row", "—"),
        ],
        title="Doc 0002",
        status="accepted",
        implementation="complete",
        depends_on=["0001"],
        sections=prose("Built on D-T.1.\n"),
    )
    rfcs = seed(
        tmp_path,
        (_name("0001"), _accepted("0001", "D-T.1", "`src/x/**`")),
        (_name("0002"), citing),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "archive" / "rfcs" / _name("0001")).exists()

    checked = invoke(tmp_path, "check")

    assert checked.exit_code == 0, checked.output
    assert "depends_on names 0001, which is archived" in checked.output

    # a second document may leave while the first is already archived
    (rfcs / _name("0003")).write_text(_accepted("0003", "D-H.1", "`src/z/**`"), encoding="utf-8")

    second = invoke(tmp_path, "archive", "0003", "--superseded-by", "0002")

    assert second.exit_code == 0, second.output
    assert invoke(tmp_path, "check").exit_code == 0


# ....................... #
# RFC 0056 phase 2: the schema is the authoring contract (D-56.6)


def test_schema_is_written_beside_the_corpus_and_drift_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.yaml", document("0001", [("D-1.1", "OPEN", "x", "—")])))

    checked = invoke(tmp_path, "check")
    assert checked.exit_code == 0, checked.output
    assert "not written yet" in checked.output  # a warning, never a problem

    written = invoke(tmp_path, "schema")
    assert written.exit_code == 0, written.output
    schema = tmp_path / "rfcs" / "schema" / "document.json"
    assert schema.is_file()
    assert json.loads(schema.read_text(encoding="utf-8"))["title"] == "Document"
    assert invoke(tmp_path, "schema", "--check").exit_code == 0
    assert "not written yet" not in invoke(tmp_path, "check").output

    schema.write_text("{}\n", encoding="utf-8")

    assert invoke(tmp_path, "schema", "--check").exit_code == EXIT_CONFIG
    drifted = invoke(tmp_path, "check")
    assert drifted.exit_code == EXIT_CONFIG
    assert "lags the model" in drifted.output


def test_the_repository_schema_matches_the_model() -> None:
    repo = Path(__file__).resolve().parent.parent
    result = invoke(repo, "schema", "--check")
    assert result.exit_code == 0, result.output
