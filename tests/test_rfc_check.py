"""`torve rfc` — the corpus checks of RFC 0007 §3a and charter A-15, each
observed to fail (D-2.2 discipline): format sabotage (ungraded row, LOCKED
without paths, duplicate identifier, cycle, hand-edited index), directory
contents with routing messages (D-A.18), derived numbering over a hole
(D-A.17/D-A.19), staleness against the generated index, and line-cite rot.

Inheriting from a non-accepted document is a problem (D-A.10, hardened once
the corpus's one violation was resolved). Citation resolution is a problem
too: retired identifiers resolve through `retired:` frontmatter (D-16.1), so
an unresolvable citation is a typo, and a retired identifier can never be
redefined.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app

runner = CliRunner()

EXIT_CONFIG = 3


def rfc_text(
    number: str,
    title: str,
    decision: str,
    status: str = "draft",
    implementation: str = "none",
    depends: str = "[]",
    extra_front: str = "",
    body_extra: str = "",
) -> str:
    return (
        "---\n"
        f'id: "{number}"\n'
        f"title: {title}\n"
        f"status: {status}\n"
        f"implementation: {implementation}\n"
        f"{extra_front}"
        f"depends_on: {depends}\n"
        "informed_by: []\n"
        "supersedes: []\n"
        "superseded_by: null\n"
        "amended_by: []\n"
        "owner: Test Owner\n"
        "description: >-\n"
        f"  Scratch document {title}.\n"
        "schema_version: 1\n"
        "---\n"
        "\n"
        f"# RFC {number} — {title}\n"
        f"{body_extra}"
        "\n"
        "## Decisions\n"
        "\n"
        "| # | Grade | Decision | Paths | Consequence |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| {decision} | `ASSUMED` | Something is decided | — | — |\n"
    )


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, str]) -> Path:
    """Write (filename, text) docs, generate the index, return the corpus dir."""
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)
    for name, text in docs or (("0001-widget.md", rfc_text("0001", "Widget", "D-T.1")),):
        (rfcs / name).write_text(text, encoding="utf-8")
    generated = invoke(tmp_path, "index")
    assert generated.exit_code == 0, generated.output
    return rfcs


# ....................... #
# format sabotage (0007 §3a): each check observed to fail


def test_a_conforming_corpus_passes(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_an_ungraded_row_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1").replace("`ASSUMED`", "`PROBABLY`")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "PROBABLY" in result.output


def test_a_locked_row_without_paths_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1").replace("`ASSUMED`", "`LOCKED`")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "declares no Paths" in result.output


def test_a_duplicate_identifier_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1") + "| D-T.1 | `ASSUMED` | A twin | — | — |\n"
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "already used" in result.output


def test_a_two_document_cycle_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1", depends='["0002"]')),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2", depends='["0001"]')),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "cycle" in result.output


def test_a_hand_edited_index_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    index = rfcs / "INDEX.md"
    index.write_text(index.read_text(encoding="utf-8") + "| a row |\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "generated" in result.output


def test_inheriting_from_a_draft_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1",
                                   status="accepted", depends='["0002"]')),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "D-A.10" in result.output


# ....................... #
# directory contents (charter A-15, D-A.18): refusals that route


def test_a_stray_document_is_routed(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "notes.md").write_text("scratch\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "pages/" in result.output and "ops/" in result.output


def test_a_slugless_filename_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "0016.md").write_text(rfc_text("0016", "Widget", "D-T.9"), encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "NNNN-slug.md" in result.output


def test_a_subdirectory_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "draft").mkdir()
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "no subdirectories" in result.output


def test_a_backup_file_is_told_to_die(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "old-0004.md.bak").write_text("x\n", encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "git is the archive" in result.output


def test_an_id_disagreeing_with_the_filename_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0002-widget.md", rfc_text("0001", "Widget", "D-T.1")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "frontmatter id" in result.output


# ....................... #
# staleness against the generated index, and vocabularies


def test_a_document_missing_from_the_index_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    (rfcs / "0002-beta.md").write_text(rfc_text("0002", "Beta", "D-T.2"), encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "INDEX.md differs" in result.output


def test_a_stale_implementation_value_reddens(tmp_path: Path) -> None:
    rfcs = seed(tmp_path)
    doc = rfcs / "0001-widget.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "implementation: none", "implementation: partial"), encoding="utf-8")
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "INDEX.md differs" in result.output


def test_an_unknown_implementation_value_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1", implementation="in_progress")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "not one of none, partial, complete, abandoned" in result.output


def test_an_unknown_kind_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1", extra_front="kind: policy\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "not one of design, convention" in result.output


# ....................... #
# LOCKED paths globs (D-32): an implemented RFC cites real areas; a document
# not yet built names intended modules


def locked_doc(implementation: str) -> str:
    doc = rfc_text("0001", "Widget", "D-T.1", status="accepted",
                   implementation=implementation)
    return doc.replace("| D-T.1 | `ASSUMED` | Something is decided | — | — |",
                       "| D-T.1 | `LOCKED` | Something is decided | `src/ghost/**` | — |")


def test_a_complete_rfc_citing_a_missing_area_reddens(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.md", locked_doc("complete")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "matches nothing" in result.output


def test_a_partial_rfc_warns_once_about_unbuilt_areas(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.md", locked_doc("partial")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "unbuilt areas" in result.output


def test_an_accepted_but_unbuilt_rfc_may_name_intended_modules(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.md", locked_doc("none")))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# line-cite rot (0007 §3a): real paths redden, illustrations do not


def test_citing_a_real_path_with_a_line_number_reddens(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    doc = rfc_text("0001", "Widget", "D-T.1",
                   body_extra="\nSee `src/mod.py:12` for the loop.\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "line numbers rot" in result.output


def test_an_illustrative_location_passes(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1",
                   body_extra="\nA model can cite a real `file.py:42` and lie.\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


# ....................... #
# numbering (D-A.17, D-A.19): derived, holes stay holes


def test_new_derives_max_plus_one_over_a_hole(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1")),
        ("0003-gamma.md", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "new", "Delta thing")
    assert result.exit_code == 0, result.output
    created = tmp_path / "rfcs" / "0004-delta-thing.md"
    assert created.is_file()  # the 0002 hole stays a hole
    assert 'id: "0004"' in created.read_text(encoding="utf-8")
    assert "0004" in (tmp_path / "rfcs" / "INDEX.md").read_text(encoding="utf-8")


def test_new_with_convention_kind_lands_in_the_conventions_group(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "new", "House style", "--kind", "convention")
    assert result.exit_code == 0, result.output
    created = tmp_path / "rfcs" / "0002-house-style.md"
    assert "kind: convention" in created.read_text(encoding="utf-8")
    assert "## Conventions" in (tmp_path / "rfcs" / "INDEX.md").read_text(encoding="utf-8")


# ....................... #
# duplicate headings and identifier resolution (charter-decomposition patch §6)


def test_two_identically_named_sections_redden(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1",
                   body_extra="\n## Parts\n\nprose\n\n## 2. Parts\n\nmore\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "name the same section twice" in result.output


def test_an_unresolvable_citation_reddens(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1", body_extra="\nSee D-9.9 for details.\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "cites D-9.9" in result.output


def test_a_retired_identifier_resolves(tmp_path: Path) -> None:
    # D-16.1: the tombstone's citation is history, not a typo.
    doc = rfc_text("0001", "Widget", "D-T.1", extra_front='retired: ["D-T.9"]\n',
                   body_extra="\nD-T.9 was removed 2026-08-22; the identifier is retired.\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output


def test_redefining_a_retired_identifier_reddens(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1",
                                   extra_front='retired: ["D-T.9"]\n')),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.9")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == EXIT_CONFIG
    assert "never reused" in result.output


def test_a_citation_resolves_across_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2",
                                  body_extra="\nInherits D-T.1 from Alpha.\n")),
    )
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


def test_a_citation_inside_a_code_fence_is_illustration(tmp_path: Path) -> None:
    doc = rfc_text("0001", "Widget", "D-T.1",
                   body_extra="\n```yaml\ndecision: D-9.9\n```\n")
    seed(tmp_path, ("0001-widget.md", doc))
    result = invoke(tmp_path, "check")
    assert result.exit_code == 0, result.output
    assert "cites" not in result.output


# ....................... #
# machine surfaces


def test_check_json_is_one_parseable_document(tmp_path: Path) -> None:
    seed(tmp_path)
    result = invoke(tmp_path, "check", "--format", "json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["ok"] is True
    assert document["schema_version"] == 1


def test_graph_lists_edges_with_statuses(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2", depends='["0001"]')),
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
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2", depends='["0001"]')),
        ("0003-gamma.md", rfc_text("0003", "Gamma", "D-T.3")),
    )
    result = invoke(tmp_path, "graph")
    assert result.exit_code == 0, result.output
    assert "0003" in result.output


def test_graph_shows_implementation_state_and_omits_finished_documents(tmp_path: Path) -> None:
    seed(
        tmp_path,
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1",
                                   status="accepted", implementation="partial")),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2", depends='["0001"]',
                                  status="accepted", implementation="complete")),
        ("0003-gamma.md", rfc_text("0003", "Gamma", "D-T.3", depends='["0002"]')),
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
        ("0001-alpha.md", rfc_text("0001", "Alpha", "D-T.1")),
        ("0002-beta.md", rfc_text("0002", "Beta", "D-T.2")),
        ("0003-gamma.md", rfc_text("0003", "Gamma", "D-T.3", depends='["0001", "0002"]')),
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
