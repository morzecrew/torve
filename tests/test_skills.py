"""Skills as package data (A-3, D-9.7): the runner materializes the role's
set into the sandbox, the specialisation is visible, and the hardened
rfc_index reddens exactly where the guide promises (SPECIALISATION §7)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from torve.runconfig import RunnerConfig
from torve.skills import available, materialize, skills_root

REPO = Path(__file__).resolve().parent.parent
RFC_INDEX = REPO / "skills" / "rfc-writer" / "scripts" / "rfc_index.py"


def test_the_three_specialised_skills_ship():
    assert {"flag-dont-flip", "ratchet-what-you-build", "rfc-writer"} <= set(available())


def test_every_shipped_skill_carries_the_specialisation_header_and_a_gate():
    for name in available():
        text = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
        assert "**Specialisation.**" in text, f"{name}: missing specialisation header"
        assert "\ngate: " in text, f"{name}: missing gate: frontmatter"
        assert "do not reconcile" in text, f"{name}: missing the no-reconcile rule"


def test_no_shipped_skill_is_byte_identical_to_upstream():
    upstream = Path("/home/misery7100/GitLibrary/Morze/agent-skills/skills")
    if not upstream.is_dir():
        pytest.skip("upstream agent-skills checkout not present")
    for name in available():
        ours = (skills_root() / name / "SKILL.md").read_bytes()
        theirs_path = upstream / name / "SKILL.md"
        if theirs_path.is_file():
            assert ours != theirs_path.read_bytes(), f"{name}: unspecialised copy"


def test_materialize_writes_the_role_set_and_nothing_else(tmp_path):
    sets = RunnerConfig().skills.sets
    written = materialize("implement", tmp_path, sets)
    assert written == ["flag-dont-flip", "ratchet-what-you-build"]
    on_disk = sorted(p.name for p in tmp_path.iterdir())
    assert on_disk == sorted(written)  # exactly the role's set, nothing else
    assert (tmp_path / "flag-dont-flip" / "SKILL.md").is_file()


def test_materialize_refuses_an_unknown_skill(tmp_path):
    with pytest.raises(RuntimeError, match="does not ship"):
        materialize("implement", tmp_path, {"implement": ["definitely-not-a-skill"]})


# --------------------------------------------------------------------------- #
# hardened rfc_index (SPECIALISATION §7: verify by breaking one of each)
# --------------------------------------------------------------------------- #

GOOD_RFC = """# RFC 0001 — Widget

- **Status:** 📝 Draft
- **Scope:** A widget.

## 1. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-1 | `LOCKED` | Widgets are blue | `src/widget/**` | — |
| D-2 | `ASSUMED` | Blue is calming | — | — |
"""

INDEX = """# RFCs

The next free number is **0002**.

## Index

| # | Title | Status | One-line routing description |
|---|---|---|---|
| [0001](0001-widget.md) | Widget | 📝 Draft | The widget design. |
"""


def run_check(tmp_path: Path, rfc_text: str) -> subprocess.CompletedProcess:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (rfcs / "0001-widget.md").write_text(rfc_text, encoding="utf-8")
    (rfcs / "INDEX.md").write_text(INDEX, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RFC_INDEX), "--root", str(tmp_path), "check"],
        capture_output=True, text=True, check=False,
    )


def test_a_pathed_graded_table_passes(tmp_path):
    result = run_check(tmp_path, GOOD_RFC)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_locked_row_without_paths_reddens(tmp_path):
    broken = GOOD_RFC.replace("| `src/widget/**` |", "| — |", 1)
    result = run_check(tmp_path, broken)
    assert result.returncode == 2
    assert "declares no Paths" in result.stdout


def test_a_missing_paths_column_reddens(tmp_path):
    broken = GOOD_RFC.replace(" Paths |", "", 1).replace("| `src/widget/**` ", "", 1)\
                     .replace("| — | — |\n", "| — |\n")
    result = run_check(tmp_path, broken)
    assert result.returncode == 2
    assert "no Paths column" in result.stdout


def test_an_ungraded_row_reddens(tmp_path):
    broken = GOOD_RFC.replace("`ASSUMED`", "`PROBABLY`")
    result = run_check(tmp_path, broken)
    assert result.returncode == 2
    assert "PROBABLY" in result.stdout


def test_a_duplicate_identifier_reddens(tmp_path):
    broken = GOOD_RFC + "| D-1 | `ASSUMED` | A twin | — | — |\n"
    result = run_check(tmp_path, broken)
    assert result.returncode == 2
    assert "appears twice" in result.stdout
