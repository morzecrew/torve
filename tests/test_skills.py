"""Skills as package data (A-3, S-0009/D-7): the runner materializes the role's
set into the sandbox and the specialisation is visible. The corpus-validator
breakage cases moved to tests/test_rfc_check.py when validation moved into
the package (S-0007/format-validation, S-0007/D-12). The corpus-bootstrap fixture (S-0031 phase
2) rides here too: the sample survey report in, the checkable output shape
out — the shape the operator skill's adopting reference teaches (S-0100/D-9),
pinned against the package's own parsers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from torve.application.skills import available, materialize, skills_root
from torve.config.spec import DOCUMENT_DIRNAME, load_document
from torve.domain.vocabulary import GRADES, STATUSES

BOOTSTRAP = Path(__file__).resolve().parent / "fixtures" / "corpus-bootstrap"


def test_torve_ships_five_skills_each_for_one_audience():
    """S-0100/D-1, extended by S-0116/D-6: the operator's, the author's, the two
    an executing agent reads, and the verifier's — nothing else ships."""

    assert set(available()) == {
        "torve",
        "spec-writer",
        "working-rules",
        "flag-dont-flip",
        "verifier",
    }


# Torve's own skills, written here rather than specialised from upstream, so
# the specialisation header and the gate below say nothing about them
# (S-0067/D-3: the working rules live once, and their source is this repository;
# the verifier's brief is authored here too, S-0116/D-6).
NATIVE = ("working-rules", "torve", "verifier")


def test_every_shipped_skill_carries_the_specialisation_header_and_a_gate():
    for name in available():
        if name in NATIVE:
            continue
        text = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
        assert "**Specialisation.**" in text, f"{name}: missing specialisation header"
        assert "\ngate: " in text, f"{name}: missing gate: frontmatter"
        assert "do not reconcile" in text, f"{name}: missing the no-reconcile rule"


def test_the_spec_writer_teaches_when_a_row_gets_a_property():
    """S-0106/D-4: the skill says which check a row gets — a property when the
    rule is quantified over inputs, an example when it is a statement about one
    case — and that a blocking row's twin proves the check can fail. The three
    sentences are the ones only this text carries."""

    raw = (skills_root() / "spec-writer" / "SKILL.md").read_text(encoding="utf-8")
    text = " ".join(raw.split())

    assert "A rule over inputs gets a property" in text
    assert "Where no input rule exists, an example test is the honest check" in text
    assert "The phase that settles the row writes its check" in text
    assert "check_twin" in text
    assert "proves the check can fail" in text


def test_the_spec_writer_reads_a_survivor_as_a_weak_check():
    """S-0107/D-4 (documentation): a row's check is judged by the mutants it
    leaves alive on its own lines, so the skill says a check that leaves
    survivors there is a weak one — and that a survivor is evidence, graded,
    never a verdict on its own."""

    raw = (skills_root() / "spec-writer" / "SKILL.md").read_text(encoding="utf-8")
    text = " ".join(raw.split())

    assert "a check that leaves survivors on its row's lines is a weak check" in text
    assert "A survivor is evidence, not a verdict" in text


def test_hypothesis_is_a_direct_dev_dependency():
    """S-0106/D-4: a row's property imports Hypothesis without a fixture, so the
    dependency is named in the dev group rather than reached transitively
    through another package's extra."""

    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert any(spec.startswith("hypothesis") for spec in data["dependency-groups"]["dev"])


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


def test_the_working_rules_carry_the_three_rules_the_burn_profile_counts():
    """S-0073/D-5, S-0073/D-6, S-0073/D-7: the three sentences, each judged by
    a class the burn profile counts — calls per message, acceptance runs per
    attempt, bookkeeping calls per attempt. Asserted as the lines only this
    text carries, the way the rules above it are."""

    text = (skills_root() / "working-rules" / "SKILL.md").read_text(encoding="utf-8")

    assert "Issue independent reads and greps in one message" in text
    assert "Run the contract's acceptance commands as written, once, after your edits" in text
    assert "Record every divergence in one call" in text
    assert "`torve log owed` is answered by the finishing check before you stop" in text


def test_the_working_rules_teach_the_batch_form_the_verb_actually_takes():
    """The rules and the verb are one claim: the skill says the options
    repeat and pair by position, and `log divergence` is what has to accept
    that. A signature that stopped taking several rows fails here rather
    than teaching a call that is refused."""

    from typer.main import get_command

    from torve.cli.main import app

    text = (skills_root() / "working-rules" / "SKILL.md").read_text(encoding="utf-8")
    command = get_command(app).commands["log"].commands["divergence"]
    multiple = {param.opts[0] for param in command.params if getattr(param, "multiple", False)}

    assert {"--decision", "--grade", "--claim", "--evidence", "--action"} <= multiple
    assert {"--kind", "--class", "--proposal", "--notes"} <= multiple
    # The attempt belongs to the call, not the row, and the skill says so.
    assert "--attempt" not in multiple
    assert "The verb takes several rows" in text


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
    sets = {"implement": ["flag-dont-flip", "working-rules"]}
    written = materialize("implement", tmp_path, sets)
    assert written == ["flag-dont-flip", "working-rules"]
    on_disk = sorted(p.name for p in tmp_path.iterdir())
    assert on_disk == sorted(written)  # exactly the role's set, nothing else
    assert (tmp_path / "flag-dont-flip" / "SKILL.md").is_file()


def test_a_materialized_skill_set_carries_no_projection(tmp_path):
    """S-0100/D-7: the `AGENTS.md` beside a skill is torve's corpus projection,
    and a sandbox copy takes the skill's text and nothing of the rows."""

    assert (skills_root() / "flag-dont-flip" / "AGENTS.md").is_file(), "the source has one"

    materialize("implement", tmp_path, {"implement": ["flag-dont-flip"]})

    assert (tmp_path / "flag-dont-flip" / "SKILL.md").is_file()
    assert not list(tmp_path.rglob("AGENTS.md"))


def test_materialize_refuses_an_unknown_skill(tmp_path):
    with pytest.raises(RuntimeError, match=r"neither shipped .* nor vendored"):
        materialize("implement", tmp_path, {"implement": ["definitely-not-a-skill"]})


# ....................... #
# corpus-bootstrap (S-0031 phase 2), now the operator skill's adopting reference
# (S-0100/D-9): the fixture — a sample survey
# report in, the checkable output shape out. The extraction doctrine's
# properties (paths on every row, no phasing, mostly ASSUMED, the recorded
# shape) are pinned against the fixture with the package's own parsers.


def test_the_bootstrap_fixture_survey_report_is_a_wellformed_survey():
    """The input fixture is a genuine survey report in the exact shape the
    survey emits — the extraction's evidence base cannot be a lookalike, or
    the doctrine teaches reading a shape the engine never writes."""

    doc = json.loads((BOOTSTRAP / "survey-report.json").read_text(encoding="utf-8"))
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

    doc = load_document(BOOTSTRAP / "S-0001")

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

    report = json.loads((BOOTSTRAP / "survey-report.json").read_text(encoding="utf-8"))
    draft = "".join(
        one.read_text(encoding="utf-8") for one in sorted((BOOTSTRAP / "S-0001").iterdir())
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

    skill = (skills_root() / "torve" / "references" / "adopting.md").read_text(encoding="utf-8")
    assert "S-NNNN" in skill

    fixture = BOOTSTRAP / "S-0001"
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


# ....................... #


def test_the_built_wheel_carries_no_projection(tmp_path):
    """S-0100/D-7: the projections stay out of the distribution. The wheel is
    built from the sdist, and the sdist prunes `skills/**/AGENTS.md`, so the
    built wheel holds the skills and none of the corpus rows beside them."""

    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "-o", str(tmp_path)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("torve-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()

    assert any(name.endswith("_skills/flag-dont-flip/SKILL.md") for name in names)
    assert not [
        name for name in names if name.startswith("torve/_skills/") and name.endswith("AGENTS.md")
    ]


def test_the_night_reference_covers_the_verify_pilot():
    """S-0116/D-13: the reference an operator reads during a night says what
    changes when the pilot is on, how a held-out failure is read, and how a
    disputed verifier test arrives."""

    raw = (skills_root() / "torve" / "references" / "night.md").read_text(encoding="utf-8")
    text = " ".join(raw.split())

    assert "verify.enabled" in text
    assert "held-out" in text
    assert "the failing test's identifier, its message and the rows it cites" in text
    assert "naming the test and the row it claims to decide" in text
    assert "underspecified" in text


def test_the_spec_writer_asks_a_phase_intent_to_name_the_public_surface():
    """S-0116/D-13: a phase written for a verifier names what to test, so the
    verifier does not invent an API — the skill and the template it points at
    both say the intent carries the surface the phase's tests drive."""

    raw = (skills_root() / "spec-writer" / "SKILL.md").read_text(encoding="utf-8")
    skill = " ".join(raw.split())
    template = (skills_root() / "spec-writer" / "references" / "spec-template.md").read_text(
        encoding="utf-8"
    )

    assert "The intent names the public surface the phase's tests drive" in skill
    assert "the public surface its" in template


def test_the_verifier_skill_briefs_an_independent_test():
    """S-0116/D-6: the verify role learns in one place what makes its tests
    independent — the rows and the intent alone, a test per settled row citing
    it, the import inside the test body, a property where a row is quantified
    over inputs, held-out properties under `.torve/tmp/holdout/`, no
    implementation, and a halt when the intent names no public surface. Each
    line here is one of the rules only this text carries."""

    raw = (skills_root() / "verifier" / "SKILL.md").read_text(encoding="utf-8")
    text = " ".join(raw.split())

    assert "verifier" in available()
    # From the rows and the intent alone.
    assert "the task's inherited rows and its intent" in text
    assert "There is no implementation to read yet" in text
    # A test per settled row, citing it.
    assert "Every row the entry settles gets at least one test" in text
    # The import lives inside the test body.
    assert "The import lives inside the test body" in text
    # A property where a row is quantified over inputs.
    assert "A row over inputs gets a property" in text
    # Held-out properties, and where they go.
    assert "Held-out properties live under" in text
    assert "holdout/" in text
    # No implementation of the verifier's own.
    assert "Never write, stub or scaffold the code the tests drive" in text
    # A halt when the intent names no public surface.
    assert "When the intent names no public surface" in text
    assert "halted" in text and "spec-gap" in text


# ....................... #
# The operator skill (S-0100/D-1, S-0100/D-4)


def test_every_shipped_skill_opens_its_description_with_its_audience():
    """S-0100/D-1: a reader knows from the first words whom a skill is for."""
    from torve.application.skills import description

    assert description("torve").startswith("When operating torve from a session")
    assert description("spec-writer").startswith("When writing a torve specification")
    for name in ("working-rules", "flag-dont-flip"):
        assert description(name).startswith("When executing a torve task contract"), name
    assert description("verifier").startswith("When verifying a torve task change")


def test_no_shipped_skill_cites_torve_s_own_corpus():
    """S-0100/D-5: inside an adopter, `torve spec show` resolves an identifier
    against the adopter's corpus, so a provenance citation into torve's would
    answer with an unrelated row. Example identifiers in a grammar lesson are
    not citations; a parenthesised one and a path into torve's source are."""
    import re

    for path in sorted(skills_root().rglob("*.md")):
        if path.name == "AGENTS.md":
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\(S-\d{4}|S-\d{4}/[A-Z]-\d+\)", text), path
        assert "src/torve/" not in text, path


def test_the_divergence_mechanics_live_in_working_rules_alone():
    """S-0100/D-6: flag-dont-flip says what an entry must say; how to record
    one is working-rules', so the two cannot contradict each other."""

    text = (skills_root() / "flag-dont-flip" / "SKILL.md").read_text(encoding="utf-8")

    assert "torve log divergence" not in text
    assert "torve log owed" not in text


def test_flag_dont_flip_sends_a_disputed_verifier_test_to_a_halt():
    """S-0116/D-8: a verifier's test an implementer disputes is never edited —
    the implementer halts with an entry naming the test and the row it claims
    to decide, and a person decides whether the test or the row is wrong."""

    raw = (skills_root() / "flag-dont-flip" / "SKILL.md").read_text(encoding="utf-8")
    text = " ".join(raw.split())

    assert "A verifier test you dispute" in text
    assert "naming the test and the row it claims to decide" in text
    assert "Never edit the file" in text
    assert "underspecified" in text


def test_the_operator_skill_states_the_rails_and_ships_its_references():
    text = (skills_root() / "torve" / "SKILL.md").read_text(encoding="utf-8")

    for rail in (
        "Accepting a document",
        "Merging a pull request",
        "A review thread a person opened is never resolved by you",
        "Credentials stay with the host's `gh` login",
        "are claims",
    ):
        assert rail in text, rail

    references = skills_root() / "torve" / "references"
    for name in ("night", "escalations", "by-hand", "review", "adopting"):
        assert (references / f"{name}.md").is_file(), name
        assert f"torve guide torve {name}" in text, name


def test_the_adopting_reference_says_when_to_nest_a_docker_daemon():
    """An operator choosing how a sandbox gets Docker reads from `adopting.md`
    when `nested` beats the host socket, that a nested attempt's proxy must
    answer on the side container's network gateway, and that the first task in
    a worker slot pays a cold-cache pull."""

    text = (skills_root() / "torve" / "references" / "adopting.md").read_text(encoding="utf-8")

    assert "nested" in text
    assert "socket" in text
    assert "gateway" in text, "the host proxy prerequisite for a nested attempt is not stated"
    assert "cold cache" in text, "the cold-cache cost of a slot's first task is not stated"


def test_the_adopting_and_upgrading_references_name_the_published_images():
    """S-0112/D-8: the adoption reference derives from the published base and
    the upgrading reference takes the release's seat images, both under
    `ghcr.io/morzecrew/`, and both name an image with the `{torve}`
    placeholder the configuration resolves."""

    refs = skills_root() / "torve" / "references"
    adopting = (refs / "adopting.md").read_text(encoding="utf-8")
    upgrading = (refs / "upgrading.md").read_text(encoding="utf-8")

    assert "ghcr.io/morzecrew/sandbox-base" in adopting
    assert "ghcr.io/morzecrew/" in upgrading
    for text in (adopting, upgrading):
        assert "{torve}" in text


def test_every_verb_the_operator_skill_names_exists():
    """The skill defers to `--help` for flags, so what it can drift on is a verb:
    every `torve <group> <verb>` it names resolves in the CLI."""

    import re

    from typer.main import get_command

    from torve.cli.main import app

    root = get_command(app)
    texts = [(skills_root() / "torve" / "SKILL.md").read_text(encoding="utf-8")]
    texts += [
        one.read_text(encoding="utf-8")
        for one in sorted((skills_root() / "torve" / "references").glob("*.md"))
    ]

    for text in texts:
        for first, second in re.findall(r"`torve ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", text):
            assert first in root.commands, first
            group = root.commands[first]
            if second and hasattr(group, "commands") and first != "guide":
                assert second in group.commands, f"{first} {second}"
