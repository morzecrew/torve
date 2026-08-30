"""`torve rfc amend`, `add-decision`, `retire` and `relocate-paths` (RFC 0025
§5.3): each is a parse-mutate-emit-check transaction that aborts whole on a
red check, leaving the tree untouched.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app

runner = CliRunner()

EXIT_CONFIG = 3

DOC = """---
id: "0001"
title: Widget
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: ["A-1"]
owner: Test Owner
description: >-
  Scratch document for verb tests.
schema_version: 1
---

# RFC 0001 — Widget

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-1.1 | `ASSUMED` | Something is decided | `src/thing/**` | Nothing yet |
| D-1.2 | `ASSUMED` | Something else is decided | `src/other/**` | Nothing yet |

## Amendments

### A-1 — 2026-01-01 — first amendment

Prose.
"""

BROKEN_SECOND_DOC = """---
id: "0002"
title: Broken
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: Test Owner
description: >-
  A second document that never checks clean, for abort-whole tests.
schema_version: 1
---

# RFC 0002 — Broken

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-2.1 | `MAYBE` | An ungraded row | — | — |

## Amendments
"""


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, *docs: tuple[str, str]) -> Path:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)

    for name, text in docs:
        (rfcs / name).write_text(text, encoding="utf-8")

    generated = invoke(tmp_path, "index")
    assert generated.exit_code == 0, generated.output
    return rfcs


# ....................... #
# rfc amend


def test_amend_appends_derived_heading_and_records_amended_by(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "amend", "0001", "--title", "second amendment")
    assert result.exit_code == 0, result.output
    assert "A-2" in result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert 'amended_by: ["A-1", "A-2"]' in written
    assert "### A-2 — " in written and "— second amendment" in written


def test_amend_derives_the_next_number_corpus_wide(tmp_path: Path) -> None:
    second = DOC.replace('id: "0001"', 'id: "0002"').replace("RFC 0001", "RFC 0002")
    second = second.replace("A-1", "A-9").replace("D-1.", "D-2.")
    rfcs = seed(tmp_path, ("0001-widget.md", DOC), ("0002-other.md", second))

    result = invoke(tmp_path, "amend", "0001", "--title", "third amendment")
    assert result.exit_code == 0, result.output
    assert "A-10" in result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert "### A-10 — " in written


def test_amend_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC), ("0002-broken.md", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.md").read_text(encoding="utf-8")

    result = invoke(tmp_path, "amend", "0001", "--title", "never lands")
    assert result.exit_code == EXIT_CONFIG
    assert "PROBLEM" in result.output
    assert (rfcs / "0001-widget.md").read_text(encoding="utf-8") == before  # tree untouched


# ....................... #
# rfc add-decision


def test_add_decision_appends_a_row_with_open_grade(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "D-1.3" in result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert "| D-1.3 | `OPEN` | <decision> | — | — |" in written


def test_add_decision_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC), ("0002-broken.md", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.md").read_text(encoding="utf-8")

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.md").read_text(encoding="utf-8") == before


# ....................... #
# rfc retire


def test_retire_removes_the_row_and_records_it_retired(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "retire", "D-1.1")
    assert result.exit_code == 0, result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert "| D-1.1 | `ASSUMED` | Something is decided |" not in written
    assert 'retired: ["D-1.1"]' in written
    assert "D-1.1 was retired " in written
    assert "| D-1.2 | `ASSUMED` |" in written  # the other row is untouched

    check = invoke(tmp_path, "check")
    assert check.exit_code == 0, check.output  # the tombstone citation still resolves


def test_add_decision_skips_a_retired_identifier_at_the_top_of_the_family(
    tmp_path: Path,
) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC))
    retired = invoke(tmp_path, "retire", "D-1.2")  # D-1.2 is the family's highest number
    assert retired.exit_code == 0, retired.output

    result = invoke(tmp_path, "add-decision", "0001")
    assert result.exit_code == 0, result.output
    assert "D-1.3" in result.output  # not D-1.2 again — retired ids are never reused

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert "| D-1.3 | `OPEN` | <decision> | — | — |" in written


def test_retire_refuses_an_unknown_identifier(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "retire", "D-9.9")
    assert result.exit_code == EXIT_CONFIG
    assert "D-9.9" in result.output


def test_retire_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC), ("0002-broken.md", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.md").read_text(encoding="utf-8")

    result = invoke(tmp_path, "retire", "D-1.1")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.md").read_text(encoding="utf-8") == before


# ....................... #
# rfc relocate-paths


def test_relocate_paths_sweeps_only_matching_cells(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == 0, result.output
    assert "D-1.1" in result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert "`src/moved/**`" in written
    assert "`src/other/**`" in written  # D-1.2's cell is untouched
    assert "src/thing/**" not in written


def test_relocate_paths_with_no_matching_cell_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, ("0001-widget.md", DOC))
    result = invoke(tmp_path, "relocate-paths", "src/nowhere/**", "src/elsewhere/**")
    assert result.exit_code == EXIT_CONFIG


def test_relocate_paths_aborts_whole_when_the_corpus_does_not_check_clean(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, ("0001-widget.md", DOC), ("0002-broken.md", BROKEN_SECOND_DOC))
    before = (rfcs / "0001-widget.md").read_text(encoding="utf-8")

    result = invoke(tmp_path, "relocate-paths", "src/thing/**", "src/moved/**")
    assert result.exit_code == EXIT_CONFIG
    assert (rfcs / "0001-widget.md").read_text(encoding="utf-8") == before
