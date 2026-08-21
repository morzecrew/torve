"""Skills as package data (A-3, D-9.7): the runner materializes the role's
set into the sandbox and the specialisation is visible. The corpus-validator
breakage cases moved to tests/test_rfc_check.py when validation moved into
the package (0007 §3a, D-7.12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.application.skills import available, materialize, skills_root
from torve.config.runconfig import RunnerConfig


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
