"""RFC 0028 D-28.7: `torve doctor` names, per tier, the profile it resolved
through — informational only, so it can never turn doctor red, and a tier
or profile file nobody referenced gets no line.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from torve.cli import app
from torve.cli.doctor import _equipment_checks, _profile_checks
from torve.config.runconfig import profiles_dir

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


def _write_profile(monkeypatch, tmp_path: Path, name: str, body: str) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write(profiles_dir() / f"{name}.yaml", body)


# ....................... #


def test_profile_check_names_the_resolved_tier(monkeypatch, tmp_path: Path):
    _write_profile(
        monkeypatch, tmp_path, "claude-sonnet", "adapter: harness\nprovider: p\ncommand: c\n"
    )
    root = _doctor_repo(tmp_path, {"tiers": {"executor": {"profile": "claude-sonnet"}}})

    checks = _profile_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "profile executor"
    assert ok is True
    assert "executor" in detail and "claude-sonnet" in detail


def test_profile_check_is_silent_with_no_profile_referenced(tmp_path: Path):
    root = _doctor_repo(tmp_path, {})

    assert _profile_checks(root, None) == []


def test_profile_check_ignores_an_unreferenced_profile_file(monkeypatch, tmp_path: Path):
    # D-28.7: unreferenced profiles are not warned about.
    _write_profile(monkeypatch, tmp_path, "unused", "adapter: harness\nprovider: p\ncommand: c\n")
    root = _doctor_repo(tmp_path, {})

    assert _profile_checks(root, None) == []


def test_equipment_check_names_an_override_tier(tmp_path: Path):
    root = _doctor_repo(
        tmp_path,
        {
            "tiers": {
                "executor.copywriter": {
                    "skills": ["prose-voice"],
                    "prompt_extras": ["Docstrings follow the house voice."],
                }
            }
        },
    )

    checks = _equipment_checks(root, None)

    assert len(checks) == 1
    name, ok, detail = checks[0]
    assert name == "equipment executor.copywriter"
    assert ok is True
    assert "skills [prose-voice] (override)" in detail
    assert "+1 prompt extra" in detail


def test_equipment_check_is_silent_with_no_override(tmp_path: Path):
    root = _doctor_repo(tmp_path, {"tiers": {"executor": {}}})

    assert _equipment_checks(root, None) == []


def test_doctor_json_carries_the_profile_line_and_stays_green(monkeypatch, tmp_path: Path):
    _write_profile(
        monkeypatch, tmp_path, "claude-sonnet", "adapter: harness\nprovider: p\ncommand: c\n"
    )
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "opensandbox"}, "tiers": {"executor": {"profile": "claude-sonnet"}}},
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])

    document = json.loads(result.stdout)
    checks = {c["name"]: c for c in document["checks"]}
    assert checks["profile executor"]["ok"] is True
    assert result.exit_code == 0
