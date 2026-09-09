"""`torve rfc fmt` (RFC 0025 §5.2; RFC 0056 D-56.4): `--check` is the
only mode. It reports every document whose text differs from what the
serializer would write for it (DRIFT), names the one the loader refuses
(REFUSE, exit 3), and writes nothing either way — a hand-authored
document is legal as it stands, and every verb that changes one writes
the canonical form itself.
"""

from __future__ import annotations

from pathlib import Path

from test_decisions import document
from typer.testing import CliRunner

from torve.cli import app
from torve.config.rfc_emit import canonical

runner = CliRunner()

EXIT_CONFIG = 3

ROWS = [("D-1.1", "ASSUMED", "Something is decided.", "`src/torve/cli/**`")]
SECTIONS = [{"key": "summary", "heading": "1. Summary", "md": "Prose.\n\nMore prose."}]

# What a person types: the model's keys, PyYAML's own indentation — which
# is not what the serializer writes.
HAND_WRITTEN = document("0001", ROWS, sections=SECTIONS)


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, text: str, name: str = "0001-document-0001.yaml") -> Path:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)
    path = rfcs / name
    path.write_text(text, encoding="utf-8")

    return path


# ....................... #


def test_fmt_check_reports_drift_and_writes_nothing(tmp_path: Path) -> None:
    path = seed(tmp_path, HAND_WRITTEN)

    result = invoke(tmp_path, "fmt", "--check")

    assert "DRIFT" in result.output
    assert path.name in result.output
    assert path.read_text(encoding="utf-8") == HAND_WRITTEN  # byte-for-byte


def test_fmt_check_on_a_canonical_document_is_ok(tmp_path: Path) -> None:
    text = canonical(HAND_WRITTEN, Path("0001-document-0001.yaml"))
    path = seed(tmp_path, text)

    result = invoke(tmp_path, "fmt", "--check")

    assert result.exit_code == 0, result.output
    assert "DRIFT" not in result.output
    assert path.read_text(encoding="utf-8") == text


def test_fmt_check_refuses_a_document_the_loader_refuses(tmp_path: Path) -> None:
    unconverted = document("0001", ROWS, sections=SECTIONS, schema_version=1)
    path = seed(tmp_path, unconverted)

    result = invoke(tmp_path, "fmt", "--check")

    assert result.exit_code == EXIT_CONFIG
    assert "REFUSE" in result.output and "schema_version" in result.output
    assert path.read_text(encoding="utf-8") == unconverted  # the tree is left untouched


def test_fmt_targets_a_single_document_by_number(tmp_path: Path) -> None:
    first = seed(tmp_path, HAND_WRITTEN)
    other = document("0002", [("D-2.1", "ASSUMED", "Another decision.", "—")])
    second = seed(tmp_path, other, "0002-document-0002.yaml")

    result = invoke(tmp_path, "fmt", "0001")

    assert result.exit_code == 0, result.output
    assert first.name in result.output
    assert second.name not in result.output
    assert first.read_text(encoding="utf-8") == HAND_WRITTEN
    assert second.read_text(encoding="utf-8") == other


def test_fmt_with_an_unknown_number_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, HAND_WRITTEN)

    result = invoke(tmp_path, "fmt", "9999")

    assert result.exit_code == EXIT_CONFIG
    assert "9999" in result.output
