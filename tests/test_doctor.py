"""S-0028/D-7, S-0061/D-10: `torve doctor` names, per seat, the two files it
was resolved from — informational only, so it can never turn doctor red.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app
from torve.cli.doctor import _equipment_checks, _profile_checks
from torve.config.agents import agents_dir, harnesses_dir

# ----------------------- #


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _doctor_repo(tmp_path: Path, config: dict) -> Path:
    import yaml

    root = tmp_path / "repo"
    write(root / ".torve" / "config.yaml", yaml.safe_dump({"schema_version": 1, **config}))
    return root


def _seat_repo(tmp_path: Path, config: dict, **profiles: str) -> Path:
    """A repository with the fake harness present and any named profiles
    written, since both files are the repository's now (S-0061/D-10)."""

    root = _doctor_repo(tmp_path, config)
    write(harnesses_dir(root) / "fake.yaml", "adapter: fake\n")

    for name, body in profiles.items():
        write(agents_dir(root) / f"{name}.yaml", body)

    return root


# ....................... #


def test_seat_check_names_the_harness_and_the_profile(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {"tiers": {"executor": {"harness": "fake", "profile": "careful"}}},
        careful="equipment: [{kind: skill, source: torve:flag-dont-flip}]\n",
    )

    checks = _profile_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "seat executor"
    assert ok is True
    assert "harness 'fake'" in detail and "profile 'careful'" in detail


def test_seat_check_says_when_the_role_s_own_profile_answers(tmp_path: Path):
    """S-0061/D-11: a seat naming no profile is not a seat with no equipment —
    it is one whose role's profile answers, and the line says so rather than
    going silent."""

    root = _seat_repo(tmp_path, {"tiers": {"executor": {"harness": "fake"}}})

    (_, _, detail) = _profile_checks(root, None)[0]

    assert "the role's own profile" in detail


def test_seat_check_is_silent_with_no_tiers_configured(tmp_path: Path):
    root = _doctor_repo(tmp_path, {"tiers": {}})

    assert _profile_checks(root, None) == []


def test_seat_check_ignores_an_unreferenced_profile_file(tmp_path: Path):
    # S-0028/D-7: unreferenced profiles are not warned about.
    root = _seat_repo(tmp_path, {"tiers": {}}, unused="skills: []\n")

    assert _profile_checks(root, None) == []


def test_equipment_check_names_an_override_tier(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {"tiers": {"executor.copywriter": {"harness": "fake", "profile": "copywriter"}}},
        copywriter=(
            "equipment: [{kind: skill, source: torve:prose-voice}]\n"
            "prompt_extras: [Docstrings follow the house voice.]\n"
        ),
    )

    checks = _equipment_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "equipment executor.copywriter"
    assert ok is True
    assert "skills [prose-voice] (override)" in detail
    assert "+1 prompt extra" in detail


def test_equipment_check_is_silent_with_no_override(tmp_path: Path):
    root = _seat_repo(tmp_path, {"tiers": {"executor": {"harness": "fake"}}})

    assert _equipment_checks(root, None) == []


def test_doctor_json_carries_the_seat_line_and_stays_green(tmp_path: Path):
    root = _seat_repo(
        tmp_path,
        {
            "runtime": {"adapter": "opensandbox"},
            "tiers": {"executor": {"harness": "fake", "profile": "careful"}},
        },
        careful="equipment: [{kind: skill, source: torve:flag-dont-flip}]\n",
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])

    document = json.loads(result.stdout)
    checks = {c["name"]: c for c in document["checks"]}
    assert checks["seat executor"]["ok"] is True
    assert result.exit_code == 0


# ----------------------- #
# S-0057 S-0057/D-5: doctor reddens when a schema lags its model or the ignore
# file lacks a minted pattern


def test_doctor_names_a_lagging_schema_and_a_missing_ignore_pattern(tmp_path: Path):
    from torve.cli.doctor import _init_checks

    root = _doctor_repo(tmp_path, {})
    write(root / ".torve" / "gates.yaml", "schema_version: 1\ngates: []\n")

    before = _init_checks(root, None)

    # never initialised: a hint, not a red — as `spec check` only warns
    assert [(name, ok) for name, ok, _ in before] == [
        ("schemas", True),
        ("ignore", True),
        ("standing", True),
        ("sources", True),
    ]
    assert "not written yet" in before[0][2] and "not written yet" in before[1][2]

    assert CliRunner().invoke(app, ["init", "--root", str(root)]).exit_code == 0
    after = _init_checks(root, None)

    assert [(name, ok) for name, ok, _ in after] == [
        ("schemas", True),
        ("ignore", True),
        ("standing", True),
        ("sources", True),
    ]

    write(root / ".torve" / "schemas" / "gates.json", "{}\n")
    ignore = root / ".torve" / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8").replace("traces/\n", ""), encoding="utf-8")

    tampered = _init_checks(root, None)

    assert tampered[0][1] is False and "gates.json" in tampered[0][2]
    assert tampered[1][1] is False and "traces/" in tampered[1][2]
    assert "tasks/" not in tampered[1][2]
