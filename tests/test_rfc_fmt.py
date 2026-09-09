"""`torve spec fmt` (S-0025/torve-rfc-fmt; S-0056 S-0056/D-4; S-0057 S-0057/D-1):
`--check` is the only mode. It reports every file of every document whose
text differs from what the serializer would write for it (DRIFT), names
the document the loader refuses (REFUSE, exit 3), and writes nothing
either way — a hand-authored document is legal as it stands, and every
verb that changes one writes the canonical form itself.
"""

from __future__ import annotations

from pathlib import Path

from test_decisions import Doc, corpus, document, place
from typer.testing import CliRunner

from torve.cli import app
from torve.config.spec_emit import canonical

runner = CliRunner()

EXIT_CONFIG = 3

ROWS = [("S-0001/D-1", "ASSUMED", "Something is decided.", "`src/torve/cli/**`")]
SECTIONS = [{"key": "summary", "md": "Prose.\n\nMore prose."}]

# What a person types: the model's keys, PyYAML's own indentation — which
# is not what the serializer writes.
HAND_WRITTEN = document("0001", ROWS, sections=SECTIONS)


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["spec", *args, "--root", str(root)])


def seed(tmp_path: Path, doc: Doc, number: str = "0001") -> Path:
    return place(corpus(tmp_path), number, doc)


def texts(directory: Path) -> Doc:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(directory.iterdir())}


# ....................... #


def test_fmt_check_reports_drift_and_writes_nothing(tmp_path: Path) -> None:
    directory = seed(tmp_path, HAND_WRITTEN)

    result = invoke(tmp_path, "fmt", "--check")

    assert "DRIFT" in result.output
    assert f"{directory.name}/document.yaml" in result.output
    assert texts(directory) == HAND_WRITTEN  # byte-for-byte


def test_fmt_check_on_a_canonical_document_is_ok(tmp_path: Path) -> None:
    directory = seed(tmp_path, HAND_WRITTEN)
    written = canonical(directory)
    place(directory.parent, "0001", written)

    result = invoke(tmp_path, "fmt", "--check")

    assert result.exit_code == 0, result.output
    assert "DRIFT" not in result.output
    assert texts(directory) == written


def test_fmt_check_refuses_a_document_the_loader_refuses(tmp_path: Path) -> None:
    unconverted = document("0001", ROWS, sections=SECTIONS, schema_version=1)
    directory = seed(tmp_path, unconverted)

    result = invoke(tmp_path, "fmt", "--check")

    assert result.exit_code == EXIT_CONFIG
    assert "REFUSE" in result.output and "schema_version" in result.output
    assert texts(directory) == unconverted  # the tree is left untouched


def test_fmt_targets_a_single_document_by_number(tmp_path: Path) -> None:
    first = seed(tmp_path, HAND_WRITTEN)
    other = document("0002", [("S-0002/D-1", "ASSUMED", "Another decision.", "—")])
    second = seed(tmp_path, other, "0002")

    result = invoke(tmp_path, "fmt", "0001")

    assert result.exit_code == 0, result.output
    assert first.name in result.output
    assert second.name not in result.output
    assert texts(first) == HAND_WRITTEN
    assert texts(second) == other


def test_fmt_with_an_unknown_number_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, HAND_WRITTEN)

    result = invoke(tmp_path, "fmt", "9999")

    assert result.exit_code == EXIT_CONFIG
    assert "9999" in result.output
