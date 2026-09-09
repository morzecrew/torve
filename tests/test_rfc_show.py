"""`torve spec show` — one corpus identifier resolved from the load `check`
runs (S-0007 A-55, S-0007/D-28): the decision row as it stands with defining
and citing documents, the amendment with its heading and the rows its diff
names, the document with its header fields and phases. No cache — every
answer is the committed corpus. An undefined identifier is a configuration
error naming the nearest family."""

from __future__ import annotations

import json
from pathlib import Path

from test_decisions import corpus as make_corpus
from test_decisions import document
from typer.testing import CliRunner

from torve.cli import app
from torve.config.spec import load_document, lookup, next_amendment

runner = CliRunner()

DOC = document(
    "0090",
    [
        (
            "S-0090/D-1",
            "LOCKED",
            "Widgets are round",
            "`src/widget/**`",
            "Square widgets break the frob",
        ),
        ("S-0090/D-2", "ASSUMED", "Amended once.", "—"),
    ],
    title="Widgets",
    status="accepted",
    implementation="partial",
    retired=["S-0090/D-9"],
    sections=[{"key": "design", "md": "The frob honours S-0090/D-1 throughout.\n"}],
    phasing=[
        {
            "phase": 1,
            "title": "the-core",
            "intent": "Build the core.",
            "scope": ["src/widget/**"],
            "acceptance": ["make test"],
        }
    ],
    amendments=[
        {
            "id": "A-1",
            "at": "2026-01-01",
            "title": "rounder widgets (amends S-0090/D-2)",
            "changes": [
                {
                    "subject": "S-0090/D-2",
                    "field": "text",
                    "before": "Amended once.",
                    "after": "Amended once.",
                }
            ],
            "md": "Rounder.\n",
        }
    ],
)

CITER = document(
    "0091",
    [("S-0091/D-1", "ASSUMED", "Frobs exist", "—")],
    title="Frob",
    status="draft",
    implementation="none",
    sections=[
        {
            "key": "frob",
            "md": (
                "S-0090/D-1 governs the frob too; the second row appears only fenced here:\n\n"
                "```text\nD-90.2 inside a fence is illustration, not a citation.\n```\n"
            ),
        }
    ],
)


def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path, **{"0090": DOC, "0091": CITER})


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["spec", "show", *args, "--root", str(root)])


# ----------------------- #


def test_decision_row_with_citers_and_boundaries(tmp_path):
    rfcs = corpus(tmp_path)
    found = lookup(rfcs, "S-0090/D-1")

    assert found is not None
    assert found["grade"] == "LOCKED"
    assert found["paths"] == ["src/widget/**"]
    assert found["consequence"] == "Square widgets break the frob"
    assert found["defined_in"] == "S-0090"
    # 0091 cites it in prose; the defining document is not its own citer.
    assert found["cited_by"] == ["S-0091"]

    # S-0090/D-2's only appearance in 0091 is fenced — illustration, not citation.
    fenced = lookup(rfcs, "S-0090/D-2")
    assert fenced is not None and fenced["cited_by"] == []


def test_retired_identifier_resolves_as_tombstone(tmp_path):
    found = lookup(corpus(tmp_path), "S-0090/D-9")

    assert found is not None
    assert found["retired_in"] == "S-0090"
    assert found["defined_in"] is None


def test_amendment_heading_and_the_rows_its_diff_names(tmp_path):
    rfcs = corpus(tmp_path)
    found = lookup(rfcs, "S-0090/A-1")

    assert found is not None
    assert found["defined_in"] == "S-0090"
    assert found["heading"].startswith("S-0090/A-1 — 2026-01-01")
    assert found["title"] == "rounder widgets (amends S-0090/D-2)"
    assert found["rows"] == ["S-0090/D-2"]
    # derived within the document, local, and never corpus-wide (S-0058/D-1)
    assert next_amendment(load_document(rfcs / "S-0090")) == "A-2"


def test_document_answers_its_header_fields_and_phases(tmp_path):
    found = lookup(corpus(tmp_path), "0090")

    assert found is not None
    assert found["status"] == "accepted"
    assert found["implementation"] == "partial"
    assert found["sections"][:2] == ["summary", "motivation"] and "design" in found["sections"]
    assert found["phases"] == [{"phase": 1, "title": "the-core", "depends_on": []}]
    assert found["file"] == "S-0090"


def test_cli_json_and_undefined_exits_config(tmp_path):
    corpus(tmp_path)

    shown = invoke(tmp_path, "S-0090/D-1", "--format", "json")
    assert shown.exit_code == 0
    assert json.loads(shown.output)["kind"] == "decision"

    missing = invoke(tmp_path, "A-99")
    assert missing.exit_code == 3
    assert "an item is `S-NNNN/<local>`" in missing.output
