"""Skills as package data (A-3, D-9.7): the runner materializes the role's
set into the sandbox and the specialisation is visible. The corpus-validator
breakage cases moved to tests/test_rfc_check.py when validation moved into
the package (0007 §3a, D-7.12). The corpus-bootstrap fixture (RFC 0031 phase
2) rides here too: the sample survey report in, the checkable output shape
out — the shape the skill teaches pinned against the package's own parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from torve.application.skills import available, materialize, skills_root
from torve.config.rfc_parse import (
    PHASING_HEADING,
    REQUIRED_FIELDS,
    RFC_FILENAME,
    check_contract_example,
    decision_table,
    parse_contract_example,
    parse_frontmatter,
)
from torve.config.runconfig import RunnerConfig
from torve.domain.rfc import GRADES, STATUSES


def test_the_four_specialised_skills_ship():
    assert {
        "corpus-bootstrap",
        "flag-dont-flip",
        "ratchet-what-you-build",
        "rfc-writer",
    } <= set(available())


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


def test_the_rfc_templates_contract_example_validates_against_the_task_schema():
    """The template's own demonstration (D-25.10) must track the schema it
    demonstrates — this catches a schema change breaking it in CI, not only
    the moment an author copies it into a real RFC."""
    text = (skills_root() / "rfc-writer" / "references" / "rfc-template.md").read_text(
        encoding="utf-8"
    )
    assert check_contract_example(Path("rfc-template.md"), text) == []
    assert parse_contract_example(text) is not None


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
# corpus-bootstrap (RFC 0031 phase 2): the skill's fixture — a sample survey
# report in, the checkable output shape out. The extraction doctrine's
# properties (paths on every row, no phasing, mostly ASSUMED, the recorded
# shape) are pinned against the fixture with the package's own parsers.


def test_the_bootstrap_fixture_survey_report_is_a_wellformed_survey():
    """The input fixture is a genuine survey report in the exact shape the
    survey emits — the extraction's evidence base cannot be a lookalike, or
    the doctrine teaches reading a shape the engine never writes."""

    doc = json.loads(
        (skills_root() / "corpus-bootstrap" / "fixtures" / "survey-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(doc) == {
        "schema_version",
        "kind",
        "branch",
        "last",
        "manifest",
        "landings",
        "summary",
    }
    assert doc["kind"] == "survey"
    assert doc["schema_version"] == 1

    for landing in doc["landings"]:
        assert set(landing) == {"sha", "short", "subject", "parent", "gates"}
        assert landing["short"] == landing["sha"][:7]
        assert landing["parent"] is None or len(landing["parent"]) == 40

        for gate in landing["gates"]:
            assert set(gate) == {
                "name",
                "outcome",
                "state",
                "duration_s",
                "exit_code",
                "output",
                "no_corpus",
            }

    summary = doc["summary"]
    assert set(summary) == {"landings", "by_gate", "corpus_adds"}
    assert summary["landings"] == doc["last"]

    for counts in summary["by_gate"].values():
        assert set(counts) == {"fired", "clean", "skipped"}

    # The pitch: the report names the gates whose silence is the corpus's
    # absence — every one becomes a candidate row in the draft.
    assert summary["corpus_adds"]


def test_the_bootstrap_fixture_draft_is_a_checkable_corpus_document():
    """The output fixture is one draft document in this corpus's format —
    the shape the skill teaches: frontmatter parses, a decision table where
    every row declares paths and a legal grade, no Phasing section. The
    fixture stays a draft, because acceptance is the human's edit, never the
    skill's."""

    text = (
        skills_root() / "corpus-bootstrap" / "fixtures" / "0001-standing-decisions.md"
    ).read_text(encoding="utf-8")

    fm = parse_frontmatter(text)
    assert fm is not None
    for fname in REQUIRED_FIELDS:
        assert fname in fm, fname
    assert fm["status"] in STATUSES

    rows = decision_table(text)
    assert rows

    family = f"D-{int(fm['id'])}."
    for row in rows:
        assert row.grade in GRADES, row.identifier
        assert row.paths, f"{row.identifier} declares no paths"
        assert row.identifier.startswith(family), row.identifier

    # The doctrine in checkable form: mostly ASSUMED, LOCKED only on the
    # boundary the sample history defended, never any phasing.
    assert sum(r.grade == "ASSUMED" for r in rows) > sum(r.grade == "LOCKED" for r in rows)
    assert any(r.grade == "LOCKED" for r in rows)
    assert PHASING_HEADING.search(text) is None


def test_the_bootstrap_fixture_ties_the_survey_to_the_draft():
    """The extraction the skill teaches maps the sample report to the sample
    draft: the report's corpus gaps and its fired gates both appear in the
    draft — the input fixture and the output fixture tell the same story."""

    report = json.loads(
        (skills_root() / "corpus-bootstrap" / "fixtures" / "survey-report.json").read_text(
            encoding="utf-8"
        )
    )
    draft = (
        skills_root() / "corpus-bootstrap" / "fixtures" / "0001-standing-decisions.md"
    ).read_text(encoding="utf-8")

    for gate in report["summary"]["corpus_adds"]:
        assert gate in draft, f"draft does not address corpus gap {gate}"

    fired = {
        gate["name"]
        for landing in report["landings"]
        for gate in landing["gates"]
        if gate["outcome"] in {"fail", "error", "bypassed"}
    }
    assert fired
    for gate in fired:
        assert gate in draft, f"draft does not address fired gate {gate}"


def test_the_bootstrap_skill_records_the_shape_it_chose():
    """The recorded shape (the open question the skill is charged with): one
    document per adoption, NNNN-standing-decisions.md — the skill names the
    convention, and the output fixture's filename is that shape concrete."""

    skill = (skills_root() / "corpus-bootstrap" / "SKILL.md").read_text(encoding="utf-8")
    assert "NNNN-standing-decisions.md" in skill

    fixture = skills_root() / "corpus-bootstrap" / "fixtures" / "0001-standing-decisions.md"
    assert RFC_FILENAME.match(fixture.name)


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
    written = materialize(
        "review", dest, {"review": ["flag-dont-flip", "team-checklist"]}, vendor_root
    )
    assert written == ["flag-dont-flip", "team-checklist"]
    assert (dest / "team-checklist" / "SKILL.md").read_text() == "vendored\n"


def test_a_collision_with_a_shipped_skill_is_refused_both_directions(tmp_path):
    vendor_root = vendor(tmp_path, "flag-dont-flip")
    with pytest.raises(RuntimeError, match="both shipped and vendored"):
        materialize("implement", tmp_path / "out", {"implement": ["flag-dont-flip"]}, vendor_root)


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
