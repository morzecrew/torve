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
    with pytest.raises(RuntimeError, match=r"neither shipped .* nor vendored"):
        materialize("implement", tmp_path, {"implement": ["definitely-not-a-skill"]})


# ....................... #
# Vendored skills (RFC 0009 §4a): committed repository content resolving
# beside package data — collisions refused, digest in the regime hash.


def vendor(tmp_path: Path, name: str, body: str = "vendored\n") -> Path:
    root = tmp_path / "skills-vendor"
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(body, encoding="utf-8")
    return root

def test_a_vendored_skill_resolves_beside_shipped_ones(tmp_path):
    vendor_root = vendor(tmp_path, "team-checklist")
    dest = tmp_path / "out"
    written = materialize("review", dest,
                          {"review": ["flag-dont-flip", "team-checklist"]},
                          vendor_root)
    assert written == ["flag-dont-flip", "team-checklist"]
    assert (dest / "team-checklist" / "SKILL.md").read_text() == "vendored\n"


def test_a_collision_with_a_shipped_skill_is_refused_both_directions(tmp_path):
    vendor_root = vendor(tmp_path, "flag-dont-flip")
    with pytest.raises(RuntimeError, match="both shipped and vendored"):
        materialize("implement", tmp_path / "out",
                    {"implement": ["flag-dont-flip"]}, vendor_root)


def test_the_committed_vendor_directory_is_well_formed():
    """The repository's own vendored skills: every entry carries a SKILL.md
    and none collides with a shipped name (D-9.12 held at rest)."""
    committed = Path(__file__).resolve().parents[1] / ".torve" / "skills-vendor"
    assert committed.is_dir(), "torve vendors at least one skill"
    names = sorted(p.name for p in committed.iterdir() if p.is_dir())
    assert names, "the vendor directory is not empty"
    for name in names:
        assert (committed / name / "SKILL.md").is_file(), name
        assert name not in available(), f"{name} collides with a shipped skill"


def test_an_edited_vendored_skill_is_a_regime_change(tmp_path):
    from torve.application.telemetry import config_hash

    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    manifest = root / ".torve" / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    vendor_dir = root / ".torve" / "skills-vendor" / "team-checklist"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "SKILL.md").write_text("v1\n", encoding="utf-8")
    before = config_hash(manifest, root)
    (vendor_dir / "SKILL.md").write_text("v2\n", encoding="utf-8")
    assert config_hash(manifest, root) != before
