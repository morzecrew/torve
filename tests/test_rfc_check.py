"""`torve spec` — the corpus checks of RFC 0007 §3a and charter A-15, each
observed to fail (D-2.2 discipline): sabotage the model refuses (ungraded
row, unknown vocabulary, a key outside the schema), sabotage only the
corpus can see (LOCKED without paths, duplicate identifier, cycle, a
comment), directory contents with routing messages (D-A.18, I-57.1),
derived numbering over a hole and over the archive (D-A.17/D-A.19), and
line-cite rot.

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

from test_decisions import Doc, corpus, document, place
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
) -> Doc:
    """One scratch document as its files: the shared builder with this
    suite's defaults — a draft nobody has built, carrying one ungoverned
    row."""

    return document(
        number,
        rows or [(decision, "ASSUMED", "Something is decided", "—")],
        title=title,
        status=status,
        implementation=implementation,
        **kw,
    )


def prose(md: str) -> list[dict[str, str]]:
    return [{"key": "design", "md": md}]


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["spec", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, Doc]) -> Path:
    """Write (number, document) documents and return the corpus dir. There
    is no index to generate any more (D-56.7)."""

    specs = corpus(tmp_path)

    for number, doc in docs or (("0001", rfc_text("0001", "Widget")),):
        place(specs, number, doc)

    return specs


def loaded(specs: Path, name: str):
    return load_document(specs / name)


# ....................... #
# format sabotage (0007 §3a): each check observed to fail


def test_a_conforming_corpus_passes(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_an_ungraded_row_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", rows=[("D-T.1", "PROBABLY", "Something is decided", "—")])
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "decisions.0.grade" in result.output


def test_a_locked_row_without_paths_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", rows=[("D-T.1", "LOCKED", "Something is decided", "—")])
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "declares no paths" in result.output


def test_a_duplicate_identifier_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002", rfc_text("0002", "Beta", "D-T.1")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "already used" in result.output


def test_a_two_document_cycle_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1", depends_on=["0002"])),
        ("0002", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "cycle" in result.output


def test_a_comment_reddens_naming_the_line(tmp_path: Path) -> None:
    # D-56.4: the schema header is the one legal comment; anything else is
    # meaning outside the model — per file (D-57.1).
    doc = rfc_text("0001", "Widget")
    head, rest = doc["document.yaml"].split("\n", 1)
    doc["document.yaml"] = f"{head}\n# a note nobody can read\n{rest}"
    seed(tmp_path, ("0001", doc))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "S-0001/document.yaml:2: a comment" in result.output


def test_schema_version_one_is_refused_naming_the_conversion(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", rfc_text("0001", "Widget", schema_version=1)))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "RFC 0057 phase 1" in result.output


def test_inheriting_from_a_draft_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1", status="accepted", depends_on=["0002"])),
        ("0002", rfc_text("0002", "Beta", "D-T.2")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "D-A.10" in result.output


def test_check_reports_a_key_the_model_refuses(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", questions=[{"id": "Q-1.1", "text": "x", "state": "open"}])
    seed(tmp_path, ("0001", doc))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "questions.0.state" in result.output


def test_a_key_in_the_wrong_file_reddens_naming_the_file_that_owns_it(tmp_path: Path) -> None:
    # D-57.1: each file carries the slice of the model one hand writes.
    doc = rfc_text("0001", "Widget")
    doc["decisions.yaml"] += "sections: []\n"
    seed(tmp_path, ("0001", doc))

    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "S-0001/decisions.yaml: sections belongs in document.yaml" in result.output


# ....................... #
# the contract example (RFC 0025 §5.4, D-25.10): typed, so a schema change
# reddens the example rather than letting it rot


def test_a_valid_contract_example_passes(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", contract_example={"id": "T-9999", "decisions": []})
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_an_invalid_contract_example_reddens(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        contract_example={"id": "T-9999", "decisions": [], "extra_unknown_field": True},
    )
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "contract_example.extra_unknown_field" in result.output


def test_a_document_without_a_contract_example_passes(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose("The contract example lives in 0001.\n"))
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# directory contents (charter A-15, D-A.18, I-57.1): refusals that route


def test_a_stray_file_reddens(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    (specs / "notes.txt").write_text("scratch\n", encoding="utf-8")
    (specs / "0016.yaml").write_text("id: '0016'\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "notes.txt: not S-NNNN/" in result.output
    assert "0016.yaml: a one-file document" in result.output


def test_a_subdirectory_reddens(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    (specs / "draft").mkdir()
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "draft: not S-NNNN/ — a stray entry in the corpus path" in result.output


def test_a_file_inside_a_document_directory_reddens(tmp_path: Path) -> None:
    # I-57.1: a document directory holds the four files and nothing else.
    specs = seed(tmp_path)
    (specs / "S-0001" / "notes.md").write_text("scratch\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "S-0001/notes.md: not one of" in result.output


def test_a_backup_file_is_told_to_die(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    (specs / "old-0004.yaml.bak").write_text("x\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "a stray entry in the corpus path" in result.output


def test_a_markdown_document_is_told_to_convert(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    (specs / "0002-legacy.md").write_text("---\nid: '0002'\n---\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "a one-file document" in result.output and "convert it" in result.output


def test_an_id_disagreeing_with_the_filename_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0002", rfc_text("0001", "Widget")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "disagrees with the directory name" in result.output


# ....................... #
# vocabularies: refused by the model, through the loader


def test_an_unknown_implementation_value_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", implementation="in_progress")
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "implementation: Input should be" in result.output and "abandoned" in result.output


def test_an_unknown_kind_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", kind="policy")
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "kind: Input should be" in result.output and "convention" in result.output


# ....................... #
# LOCKED paths globs (D-32): an implemented document cites real areas; a
# document not yet built names intended modules


def locked_doc(implementation: str) -> Doc:
    return rfc_text(
        "0001",
        "Widget",
        status="accepted",
        implementation=implementation,
        rows=[("D-T.1", "LOCKED", "Something is decided", "`src/ghost/**`")],
    )


def test_a_complete_rfc_citing_a_missing_area_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", locked_doc("complete")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "matches nothing" in result.output


def test_a_partial_rfc_warns_once_about_unbuilt_areas(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", locked_doc("partial")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "unbuilt areas" in result.output


def test_an_accepted_but_unbuilt_rfc_may_name_intended_modules(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", locked_doc("none")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# line-cite rot (0007 §3a): real paths redden, illustrations do not


def test_citing_a_real_path_with_a_line_number_reddens(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    doc = rfc_text("0001", "Widget", sections=prose("See `src/mod.py:12` for the loop.\n"))
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "line numbers rot" in result.output


def test_an_illustrative_location_passes(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001", "Widget", sections=prose("A model can cite a real `file.py:42` and lie.\n")
    )
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# numbering (D-A.17, D-A.19): derived, holes stay holes


def test_new_derives_max_plus_one_over_a_hole(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0003", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "new", "Delta thing")
    assert result.exit_code == 0, result.output
    created = tmp_path / ".torve" / "specs" / "S-0004"
    assert created.is_dir()  # the 0002 hole stays a hole, and there is no slug
    text = (created / "document.yaml").read_text(encoding="utf-8")
    assert text.startswith("# yaml-language-server: $schema=../../schemas/document.json\n")
    assert "id: '0004'" in text
    assert invoke(tmp_path, "check").exit_code == 0


def test_new_with_convention_kind_lands_in_the_documents_kind(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    result = invoke(tmp_path, "new", "House style", "--kind", "convention")
    assert result.exit_code == 0, result.output
    created = specs / "S-0002"
    assert "kind: convention" in (created / "document.yaml").read_text(encoding="utf-8")
    assert loaded(specs, "S-0002").kind == "convention"


# ....................... #
# section keys and identifier resolution


def test_two_sections_keyed_the_same_redden(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        sections=[
            {"key": "parts", "md": "prose\n"},
            {"key": "parts", "md": "more\n"},
        ],
    )
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "two sections keyed 'parts'" in result.output


def test_an_unresolvable_citation_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose("See D-9.9 for details.\n"))
    seed(tmp_path, ("0001", doc))
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
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_redefining_a_retired_identifier_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1", retired=["D-T.9"])),
        ("0002", rfc_text("0002", "Beta", "D-T.9")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "never reused" in result.output


def test_a_citation_resolves_across_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002", rfc_text("0002", "Beta", "D-T.2", sections=prose("Inherits D-T.1 from Alpha.\n"))),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


def test_a_citation_inside_a_code_fence_is_illustration(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", sections=prose('```json\n{"decision": "D-9.9"}\n```\n'))
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


# ....................... #
# what a section may not carry (D-57.2): prose, and nothing a typed list
# holds — each refusal observed


def test_a_section_that_carries_what_a_typed_list_holds_reddens(tmp_path: Path) -> None:
    doc = rfc_text(
        "0001",
        "Widget",
        sections=[
            {"key": "summary", "md": "  \n"},
            {"key": "a-161-the-words", "md": "words\n"},
            {"key": "options", "md": "```yaml alternatives\n- option: x\n```\n"},
            {"key": "rows", "md": "| # | Grade | Decision |\n"},
        ],
    )
    seed(tmp_path, ("0001", doc))
    result = invoke(tmp_path, "check")

    assert result.exit_code == EXIT_CONFIG
    assert "section 'summary' has no body" in result.output
    assert "section 'a-161-the-words' is an amendment" in result.output
    assert "carries a `alternatives` fence" in result.output
    assert "carries the decisions table" in result.output


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
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
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
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002", rfc_text("0002", "Beta", "D-T.2", depends_on=["0001"])),
        ("0003", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    assert "0003" in result.output


def test_graph_shows_implementation_state_and_omits_finished_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001", rfc_text("0001", "Alpha", "D-T.1", status="accepted", implementation="partial")),
        (
            "0002",
            rfc_text(
                "0002",
                "Beta",
                "D-T.2",
                status="accepted",
                implementation="complete",
                depends_on=["0001"],
            ),
        ),
        ("0003", rfc_text("0003", "Gamma", "D-T.3", depends_on=["0002"])),
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
        ("0001", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002", rfc_text("0002", "Beta", "D-T.2")),
        ("0003", rfc_text("0003", "Gamma", "D-T.3", depends_on=["0001", "0002"])),
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


def _accepted(number: str, decision: str, paths: str, sections: Any = None) -> Doc:
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
    return f"S-{number}"


def test_check_reports_a_hand_edited_grade_and_warns_on_a_hand_edited_text(
    tmp_path: Path,
) -> None:
    specs = seed(tmp_path)
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

    path = specs / _name("0001") / "decisions.yaml"
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
    specs = seed(tmp_path, ("0001", _accepted("0001", "D-T.1", "—")))
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
    doc = loaded(specs, _name("0001"))

    assert doc.retired == ["D-T.1"] and doc.decision("D-T.1") is None
    (change,) = [c for c in doc.amendments[-1].changes if c.field == "retired"]
    assert change.subject == "D-T.1" and change.after == "path rot"
    assert invoke(tmp_path, "check").exit_code == 0


def test_check_warns_on_path_rot_and_fix_rot_retires_it(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alive.py").write_text("", encoding="utf-8")
    specs = seed(
        tmp_path,
        ("0001", _accepted("0001", "D-T.1", "`src/gone/**`")),
        ("0002", _accepted("0002", "D-G.1", "`src/alive.py`")),
    )

    result = invoke(tmp_path, "check")

    assert result.exit_code == 0, result.output
    assert "D-T.1 (ASSUMED) declares src/gone/** and nothing in the tree matches" in result.output
    assert "D-G.1" not in result.output

    fixed = invoke(tmp_path, "check", "--fix-rot")

    assert fixed.exit_code == 0, fixed.output
    assert "retired D-T.1 (path rot)" in fixed.output

    doc = loaded(specs, _name("0001"))

    assert doc.retired == ["D-T.1"]
    assert "path-rotted row(s) retired by `torve spec check --fix-rot`" in doc.amendments[-1].title
    assert "nothing in the tree matches" not in invoke(tmp_path, "check").output


def test_archive_moves_the_document_and_show_still_resolves_its_row(tmp_path: Path) -> None:
    specs = seed(
        tmp_path,
        ("0001", _accepted("0001", "D-T.1", "`src/x/**`")),
        ("0002", _accepted("0002", "D-G.1", "`src/y/**`")),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == 0, result.output
    assert not (specs / _name("0001")).exists()

    archived = tmp_path / ".torve" / "archive" / _name("0001")

    assert archived.is_dir()
    assert "superseded_by: '0002'" in (archived / "document.yaml").read_text(encoding="utf-8")
    assert invoke(tmp_path, "check").exit_code == 0

    shown = invoke(tmp_path, "show", "D-T.1", "--format", "json")

    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)

    assert payload["archived"] is True and payload["defined_in"] == _name("0001")

    doc = invoke(tmp_path, "show", "0001", "--format", "json")

    assert doc.exit_code == 0, doc.output
    assert json.loads(doc.output)["superseded_by"] == "0002"


def test_archive_refuses_when_the_corpus_without_it_does_not_check(tmp_path: Path) -> None:
    specs = seed(
        tmp_path,
        ("0001", _accepted("0001", "D-T.1", "`src/x/**`")),
        (
            "0002",
            _accepted(
                "0002", "D-G.1", "`src/y/**`", prose("Built on D-Z.9, which nothing defines.\n")
            ),
        ),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == EXIT_CONFIG
    assert (specs / _name("0001")).exists()
    assert not (tmp_path / ".torve" / "archive").exists()


def test_new_derives_its_number_over_the_archive(tmp_path: Path) -> None:
    specs = seed(tmp_path)
    place(
        tmp_path / ".torve" / "archive",
        "0007",
        rfc_text("0007", "Old", "D-O.1", status="superseded", superseded_by="0001"),
    )

    result = invoke(tmp_path, "new", "Fresh")

    assert result.exit_code == 0, result.output
    assert (specs / "S-0008").is_dir()


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
    seed(tmp_path, ("0001", doc))

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
    specs = seed(
        tmp_path,
        ("0001", _accepted("0001", "D-T.1", "`src/x/**`")),
        ("0002", citing),
    )

    result = invoke(tmp_path, "archive", "0001", "--superseded-by", "0002")

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".torve" / "archive" / _name("0001")).is_dir()

    checked = invoke(tmp_path, "check")

    assert checked.exit_code == 0, checked.output
    assert "depends_on names 0001, which is archived" in checked.output

    # a second document may leave while the first is already archived
    place(specs, "0003", _accepted("0003", "D-H.1", "`src/z/**`"))

    second = invoke(tmp_path, "archive", "0003", "--superseded-by", "0002")

    assert second.exit_code == 0, second.output
    assert invoke(tmp_path, "check").exit_code == 0


# ....................... #
# RFC 0056 phase 2 / RFC 0057 D-57.5: the schema is the authoring contract
# (D-56.6), one per file, written by `torve init`


def test_schema_is_written_beside_the_corpus_and_drift_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0001", document("0001", [("D-1.1", "OPEN", "x", "—")])))

    checked = invoke(tmp_path, "check")
    assert checked.exit_code == 0, checked.output
    assert "not written yet" in checked.output  # a warning, never a problem

    written = runner.invoke(app, ["init", "--root", str(tmp_path)])
    assert written.exit_code == 0, written.output
    schemas = tmp_path / ".torve" / "schemas"
    assert sorted(p.name for p in schemas.iterdir()) == [
        "amendments.json",
        "decisions.json",
        "document.json",
        "execution.json",
    ]
    assert (
        json.loads((schemas / "document.json").read_text(encoding="utf-8"))["title"] == "document"
    )
    assert "not written yet" not in invoke(tmp_path, "check").output

    (schemas / "document.json").write_text("{}\n", encoding="utf-8")

    drifted = invoke(tmp_path, "check")
    assert drifted.exit_code == EXIT_CONFIG
    assert "lags the model" in drifted.output


def test_the_repository_schema_matches_the_model() -> None:
    from torve.config.spec import schema_file, schema_text
    from torve.domain.spec import FILES

    specs = Path(__file__).resolve().parent.parent / ".torve" / "specs"

    for file_name in FILES:
        path = schema_file(specs, file_name)
        assert path.is_file(), f"{path} — `torve init` writes it"
        assert path.read_text(encoding="utf-8") == schema_text(file_name), path
