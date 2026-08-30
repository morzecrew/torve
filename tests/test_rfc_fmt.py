"""`torve rfc fmt` (RFC 0025 §5.2, D-25.1/D-25.2): writes a document's
canonical rendering when it differs, refuses a document whose own check
already reports a problem, and `--check` reports drift without writing.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app
from torve.config.rfc_emit import emit

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
  Scratch document for fmt tests.
schema_version: 1
---

# RFC 0001 — Widget

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-T.1 | `ASSUMED` | Something is decided | `src/thing/**` | Nothing yet |

## Phasing

```yaml
- phase: 1
  title: the-only-phase
  intent: >-
    Build the thing.
  scope: ["src/thing/**"]
  acceptance: ["make test"]
  depends_on: []
```

## Amendments

### A-1 — 2026-01-01 — first amendment

Prose.
"""


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["rfc", *args, "--root", str(root)])


def seed(tmp_path: Path, text: str, name: str = "0001-widget.md") -> Path:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir(exist_ok=True)
    (rfcs / name).write_text(text, encoding="utf-8")
    generated = invoke(tmp_path, "index")
    assert generated.exit_code == 0, generated.output
    return rfcs


# ....................... #
# writes only when the canonical rendering differs


def test_fmt_writes_a_drifted_document(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, DOC)
    result = invoke(tmp_path, "fmt")
    assert result.exit_code == 0, result.output
    assert "WROTE" in result.output

    written = (rfcs / "0001-widget.md").read_text(encoding="utf-8")
    assert written == emit(DOC)


def test_fmt_on_an_already_canonical_document_writes_nothing(tmp_path: Path) -> None:
    canonical = emit(DOC)
    rfcs = seed(tmp_path, canonical)
    path = rfcs / "0001-widget.md"
    before = path.read_text(encoding="utf-8")

    result = invoke(tmp_path, "fmt")
    assert result.exit_code == 0, result.output
    assert "WROTE" not in result.output

    assert path.read_text(encoding="utf-8") == before  # byte-for-byte: nothing written


# ....................... #
# --check reports without writing


def test_fmt_check_reports_drift_and_writes_nothing(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, DOC)
    path = rfcs / "0001-widget.md"
    before = path.read_text(encoding="utf-8")

    result = invoke(tmp_path, "fmt", "--check")
    assert result.exit_code == EXIT_CONFIG
    assert "DRIFT" in result.output
    assert path.read_text(encoding="utf-8") == before


def test_fmt_check_on_a_canonical_document_passes(tmp_path: Path) -> None:
    seed(tmp_path, emit(DOC))
    result = invoke(tmp_path, "fmt", "--check")
    assert result.exit_code == 0, result.output


# ....................... #
# refuses a document whose own check already reports a problem


def test_fmt_refuses_a_document_with_an_ungraded_row(tmp_path: Path) -> None:
    broken = DOC.replace("`ASSUMED`", "`MAYBE`")
    rfcs = seed(tmp_path, broken)
    path = rfcs / "0001-widget.md"
    before = path.read_text(encoding="utf-8")

    result = invoke(tmp_path, "fmt")
    assert result.exit_code == EXIT_CONFIG
    assert "REFUSE" in result.output
    assert path.read_text(encoding="utf-8") == before  # the tree is left untouched


def test_fmt_check_also_refuses_a_broken_document(tmp_path: Path) -> None:
    broken = DOC.replace("`ASSUMED`", "`MAYBE`")
    seed(tmp_path, broken)
    result = invoke(tmp_path, "fmt", "--check")
    assert result.exit_code == EXIT_CONFIG
    assert "REFUSE" in result.output


# ....................... #
# one document, by number


def test_fmt_targets_a_single_document_by_number(tmp_path: Path) -> None:
    rfcs = seed(tmp_path, DOC, "0001-widget.md")
    doc2 = (
        DOC.replace('id: "0001"', 'id: "0002"')
        .replace("RFC 0001", "RFC 0002")
        .replace("D-T.1", "D-T.2")
        .replace("A-1", "A-2")
        .replace("2026-01-01", "2026-01-02")
    )
    (rfcs / "0002-other.md").write_text(doc2, encoding="utf-8")
    generated = invoke(tmp_path, "index")
    assert generated.exit_code == 0, generated.output

    result = invoke(tmp_path, "fmt", "0001")
    assert result.exit_code == 0, result.output
    assert "0001-widget.md" in result.output
    assert "0002-other.md" not in result.output

    assert (rfcs / "0001-widget.md").read_text(encoding="utf-8") == emit(DOC)
    assert (rfcs / "0002-other.md").read_text(encoding="utf-8") == doc2  # untouched


def test_fmt_with_an_unknown_number_is_a_configuration_error(tmp_path: Path) -> None:
    seed(tmp_path, DOC)
    result = invoke(tmp_path, "fmt", "9999")
    assert result.exit_code == EXIT_CONFIG
    assert "9999" in result.output
