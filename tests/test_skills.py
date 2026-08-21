"""Skills as package data (A-3, D-9.7): the runner materializes the role's
set into the sandbox, the specialisation is visible, and the hardened
rfc_index reddens exactly where the conventions promise (charter D-A.3,
D-A.6; the four deliberate breakages of ops/skill-specialisation.md §7)."""

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
# hardened rfc_index (A-7 §7.6: verify the validator by breaking one of each)
# --------------------------------------------------------------------------- #

GOOD_RFC = """---
id: "0001"
title: Widget
status: accepted
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: Test Owner
description: The widget design.
schema_version: 1
---

# RFC 0001 — Widget

- **Scope:** A widget.

## 1. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-1 | `LOCKED` | Widgets are blue | `src/widget/**` | — |
| D-2 | `ASSUMED` | Blue is calming | — | — |
"""


def run_check(
    tmp_path: Path, rfc_text: str, tamper_index: str | None = None
) -> subprocess.CompletedProcess[str]:
    rfcs = tmp_path / "rfcs"
    rfcs.mkdir()
    (tmp_path / "src" / "widget").mkdir(parents=True)
    (tmp_path / "src" / "widget" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (rfcs / "0001-widget.md").write_text(rfc_text, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(RFC_INDEX), "--root", str(tmp_path), "generate"],
        capture_output=True, text=True, check=True,
    )
    if tamper_index is not None:
        index = rfcs / "INDEX.md"
        index.write_text(index.read_text(encoding="utf-8") + tamper_index, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RFC_INDEX), "--root", str(tmp_path), "check"],
        capture_output=True, text=True, check=False,
    )


def test_a_conforming_corpus_passes(tmp_path):
    result = run_check(tmp_path, GOOD_RFC)
    assert result.returncode == 0, result.stdout + result.stderr


# The four deliberate breakages (A-7 §7.6): a validator never observed to
# fail is not a validator.

def test_an_ungraded_row_reddens(tmp_path):
    result = run_check(tmp_path, GOOD_RFC.replace("`ASSUMED`", "`PROBABLY`"))
    assert result.returncode == 2
    assert "PROBABLY" in result.stdout


def test_a_locked_row_without_paths_reddens(tmp_path):
    result = run_check(tmp_path, GOOD_RFC.replace("| `src/widget/**` |", "| — |", 1))
    assert result.returncode == 2
    assert "declares no Paths" in result.stdout


def test_a_duplicate_identifier_reddens(tmp_path):
    result = run_check(tmp_path, GOOD_RFC + "| D-1 | `ASSUMED` | A twin | — | — |\n")
    assert result.returncode == 2
    assert "already used" in result.stdout


def test_a_hand_edited_index_reddens(tmp_path):
    result = run_check(tmp_path, GOOD_RFC, tamper_index="| hand-added row |\n")
    assert result.returncode == 2
    assert "generated output" in result.stdout


# And the newer checks past the four:

def test_an_accepted_glob_matching_nothing_reddens(tmp_path):
    result = run_check(tmp_path, GOOD_RFC.replace("`src/widget/**`", "`src/nothing/**`"))
    assert result.returncode == 2
    assert "matches nothing" in result.stdout


def test_a_draft_may_name_intended_modules(tmp_path):
    draft = GOOD_RFC.replace("status: accepted", "status: draft").replace(
        "`src/widget/**`", "`src/not-built-yet/**`"
    )
    result = run_check(tmp_path, draft)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_frontmatter_reddens(tmp_path):
    body = GOOD_RFC.split("---\n", 2)[2]
    result = run_check(tmp_path, body)
    assert result.returncode == 2
    assert "frontmatter" in result.stdout


def test_a_leftover_prose_status_line_reddens(tmp_path):
    broken = GOOD_RFC.replace("- **Scope:**", "- **Status:** 📝 Draft\n- **Scope:**")
    result = run_check(tmp_path, broken)
    assert result.returncode == 2
    assert "frontmatter (D-A.2)" in result.stdout or "prose" in result.stdout
