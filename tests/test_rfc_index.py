"""`rfc_index.py check` staleness cases (A-14): a document the index does not
know, and an `implementation` value that changed after generation — both must
redden. The check is a byte-compare against regeneration, like a lockfile.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / "skills" / "rfc-writer" / "scripts" / "rfc_index.py")


def rfc_text(number: str, title: str, decision: str, implementation: str = "none") -> str:
    return textwrap.dedent(f"""\
        ---
        id: "{number}"
        title: {title}
        status: draft
        implementation: {implementation}
        depends_on: []
        informed_by: []
        supersedes: []
        superseded_by: null
        amended_by: []
        owner: Test Owner
        description: >-
          Scratch document {title}.
        schema_version: 1
        ---

        # RFC {number} — {title}

        ## Decisions

        | # | Grade | Decision | Paths | Consequence |
        | --- | --- | --- | --- | --- |
        | {decision} | `ASSUMED` | Something is decided | — | — |
        """)


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True)


def seed_corpus(tmp_path: Path) -> Path:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-alpha.md").write_text(rfc_text("0001", "Alpha", "D-T.1"), encoding="utf-8")
    assert run(tmp_path, "generate").returncode == 0
    return rfcs


def test_clean_corpus_passes(tmp_path: Path) -> None:
    seed_corpus(tmp_path)
    result = run(tmp_path, "check")
    assert result.returncode == 0, result.stdout


def test_document_missing_from_the_index_reddens(tmp_path: Path) -> None:
    rfcs = seed_corpus(tmp_path)
    (rfcs / "0002-beta.md").write_text(rfc_text("0002", "Beta", "D-T.2"), encoding="utf-8")
    result = run(tmp_path, "check")
    assert result.returncode == 2
    assert "INDEX.md differs" in result.stdout


def test_stale_implementation_value_reddens(tmp_path: Path) -> None:
    rfcs = seed_corpus(tmp_path)
    doc = rfcs / "0001-alpha.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "implementation: none", "implementation: partial"), encoding="utf-8")
    result = run(tmp_path, "check")
    assert result.returncode == 2
    assert "INDEX.md differs" in result.stdout


def test_unknown_implementation_value_reddens(tmp_path: Path) -> None:
    rfcs = seed_corpus(tmp_path)
    doc = rfcs / "0001-alpha.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "implementation: none", "implementation: in_progress"), encoding="utf-8")
    result = run(tmp_path, "check")
    assert result.returncode == 2
    assert "not one of none, partial, complete, abandoned" in result.stdout
