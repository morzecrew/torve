"""Skills as package data (A-3, S-0009/D-7): the runner materializes the role's
set into the sandbox and the specialisation is visible. The corpus-validator
breakage cases moved to tests/test_rfc_check.py when validation moved into
the package (S-0007/format-validation, S-0007/D-12). The corpus-bootstrap fixture (S-0031 phase
2) rides here too: the sample survey report in, the checkable output shape
out — the shape the skill teaches pinned against the package's own parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from torve.application.skills import available, materialize, skills_root
from torve.config.runconfig import ROLE_SKILLS
from torve.config.spec import DOCUMENT_DIRNAME, load_document
from torve.domain.vocabulary import GRADES, STATUSES


def test_the_four_specialised_skills_ship():
    assert {
        "corpus-bootstrap",
        "flag-dont-flip",
        "ratchet-what-you-build",
        "spec-writer",
    } <= set(available())


# Torve's own skills, written here rather than specialised from upstream, so
# the specialisation header and the gate below say nothing about them
# (S-0067/D-3: the working rules live once, and their source is this repository).
NATIVE = ("working-rules",)


def test_every_shipped_skill_carries_the_specialisation_header_and_a_gate():
    for name in available():
        if name in NATIVE:
            continue
        text = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
        assert "**Specialisation.**" in text, f"{name}: missing specialisation header"
        assert "\ngate: " in text, f"{name}: missing gate: frontmatter"
        assert "do not reconcile" in text, f"{name}: missing the no-reconcile rule"


def test_the_working_rules_ship_as_one_skill_carrying_the_three_new_rules():
    """S-0067/D-3: the working rules live once, at `skills/working-rules/`, so a
    sandbox takes them as equipment and a session reads them through its skill
    root. S-0067/D-6, S-0067/D-7 and S-0067/D-8 are the lines only this text
    carries — the scope's test file, the changelog, and a test that shares the
    machine."""

    text = (skills_root() / "working-rules" / "SKILL.md").read_text(encoding="utf-8")

    assert "working-rules" in available()
    assert "A scope naming a module names that module's test file" in text
    assert "A scope never names the changelog" in text
    assert "A test may not assume it is the only one on the machine" in text
    # The contract still governs, wherever the rules are read from.
    assert "Nothing here outranks the contract" in text


def test_every_role_that_runs_a_contract_declares_the_working_rules():
    """S-0067/D-3: declared equipment on the roles that need it — all three,
    because all three are dispatched against a contract by `build_prompt`."""

    from torve.config.agents import role_skills

    declared = role_skills(Path(__file__).resolve().parents[1])

    for role in ("implement", "review", "revert"):
        assert "working-rules" in declared.get(role, []), role


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
    # S-0061/D-11: the role default is `.torve/agents/implement.yaml`, minted from
    # this table — a bare RunnerConfig carries no sets because nobody writes them.
    sets = dict(ROLE_SKILLS)
    written = materialize("implement", tmp_path, sets)
    assert written == ["flag-dont-flip", "ratchet-what-you-build"]
    on_disk = sorted(p.name for p in tmp_path.iterdir())
    assert on_disk == sorted(written)  # exactly the role's set, nothing else
    assert (tmp_path / "flag-dont-flip" / "SKILL.md").is_file()


def test_materialize_refuses_an_unknown_skill(tmp_path):
    with pytest.raises(RuntimeError, match=r"neither shipped .* nor vendored"):
        materialize("implement", tmp_path, {"implement": ["definitely-not-a-skill"]})


# ....................... #
# corpus-bootstrap (S-0031 phase 2): the skill's fixture — a sample survey
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
    the shape the skill teaches: the loader accepts it, every row declares
    paths and a legal grade in the document's own family, and there is no
    phasing. The fixture stays a draft, because acceptance is the human's
    edit, never the skill's."""

    doc = load_document(skills_root() / "corpus-bootstrap" / "fixtures" / "S-0001")

    assert doc.status in STATUSES
    assert doc.status == "draft"
    assert doc.decisions

    family = f"{doc.id}/D-"  # the document's own family, global (S-0058/D-1)
    for row in doc.decisions:
        assert row.grade in GRADES, row.id
        assert row.paths, f"{row.id} declares no paths"
        assert row.id.startswith(family), row.id

    # The doctrine in checkable form: mostly ASSUMED, LOCKED only on the
    # boundary the sample history defended, never any phasing.
    grades = [row.grade for row in doc.decisions]
    assert grades.count("ASSUMED") > grades.count("LOCKED")
    assert "LOCKED" in grades
    assert doc.phasing == []


def test_the_bootstrap_fixture_ties_the_survey_to_the_draft():
    """The extraction the skill teaches maps the sample report to the sample
    draft: the report's corpus gaps and its fired gates both appear in the
    draft — the input fixture and the output fixture tell the same story."""

    report = json.loads(
        (skills_root() / "corpus-bootstrap" / "fixtures" / "survey-report.json").read_text(
            encoding="utf-8"
        )
    )
    draft = "".join(
        one.read_text(encoding="utf-8")
        for one in sorted((skills_root() / "corpus-bootstrap" / "fixtures" / "S-0001").iterdir())
    )

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
    document per adoption, the `S-NNNN/` directory of S-0057 — the skill
    names the convention, and the output fixture's directory is that shape
    concrete."""

    skill = (skills_root() / "corpus-bootstrap" / "SKILL.md").read_text(encoding="utf-8")
    assert "S-NNNN" in skill

    fixture = skills_root() / "corpus-bootstrap" / "fixtures" / "S-0001"
    assert fixture.is_dir()
    assert DOCUMENT_DIRNAME.match(fixture.name)


# ....................... #
# Vendored skills (S-0009/vendored-skills): committed repository content resolving
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
    and none collides with a shipped name (S-0009/D-12 held at rest)."""
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
