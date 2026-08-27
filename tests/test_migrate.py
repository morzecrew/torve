"""The migrate surface without a database: package-data resolution, target
vocabulary, the lazy yoyo import's instruction (exit code 3 semantics), the
forze pin, and its place inside config_hash."""

from __future__ import annotations

import builtins

import pytest
import yaml

from torve.application.migrate import (
    MISSING_EXTRA_EXIT,
    MigrateError,
    check_forze_pin,
    forze_pin,
    status,
    steps_for,
)
from torve.application.telemetry import config_hash
from torve.gates.sabotage import BASE_MANIFEST


def test_owner_grouped_layout():
    assert steps_for("substrate") and steps_for("substrate")[0].name == "0001_durable.sql"
    assert steps_for("torve") == []  # history starts at the first document table
    assert steps_for("telemetry") == []  # stage 1: a file has no schema
    with pytest.raises(MigrateError, match="unknown target"):
        steps_for("everything")


def test_the_pin_matches_the_installed_forze():
    import importlib.metadata

    assert forze_pin() == importlib.metadata.version("forze")
    ok, message = check_forze_pin()
    assert ok, message


def test_a_pin_mismatch_is_a_migration_task_not_a_warning(monkeypatch):
    monkeypatch.setattr("torve.application.migrate.forze_pin", lambda: "0.0.1")
    ok, message = check_forze_pin()
    assert not ok
    assert "migration task" in message


def test_missing_extra_names_the_install_and_exit_code(monkeypatch):
    import sys

    real_import = builtins.__import__

    def no_yoyo(name, *args, **kwargs):
        if name == "yoyo":
            raise ImportError("no module named yoyo")
        return real_import(name, *args, **kwargs)

    # An earlier test may have imported yoyo; cached modules bypass
    # __import__, so the absence being simulated must evict the family.
    for cached in [m for m in sys.modules if m == "yoyo" or m.startswith("yoyo.")]:
        monkeypatch.delitem(sys.modules, cached)
    monkeypatch.setattr(builtins, "__import__", no_yoyo)
    from torve.application.migrate import apply

    with pytest.raises(MigrateError, match=r"torve\[migrate\]") as caught:
        apply("substrate", "postgresql://nowhere/db")
    assert caught.value.exit_code == MISSING_EXTRA_EXIT


def test_status_reports_three_targets_and_the_pin():
    lines = status(dsn=None)
    assert len(lines) == 4
    assert lines[0].startswith("torve") and "no migrations yet" in lines[0]
    assert lines[1].startswith("substrate") and "1 step(s)" in lines[1]
    assert lines[2].startswith("telemetry")
    assert "forze" in lines[3]


def test_config_hash_moves_with_the_forze_pin(tmp_path, monkeypatch):
    path = tmp_path / "gates.yaml"
    path.write_text(yaml.safe_dump(BASE_MANIFEST, sort_keys=False), encoding="utf-8")
    before = config_hash(path, tmp_path)
    monkeypatch.setattr("torve.application.migrate.forze_pin", lambda: "9.9.9")
    assert config_hash(path, tmp_path) != before  # the pin is part of the regime
