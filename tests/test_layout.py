"""RFC 0013 resolution: one path per lookup under `.torve/`, whether or not
the file exists (D-13.1, A-48). `--config` is the only override (D-13.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from torve.config import layout
from torve.config.runconfig import load_runner_config


def test_every_lookup_resolves_under_torve_dir(tmp_path: Path) -> None:
    assert layout.gates_file(tmp_path) == tmp_path / ".torve" / "gates.yaml"
    assert layout.config_file(tmp_path) == tmp_path / ".torve" / "config.yaml"
    assert layout.task_dir(tmp_path, "T-1") == tmp_path / ".torve" / "tasks" / "T-1"
    assert layout.task_file(tmp_path, "T-1") == (
        tmp_path / ".torve" / "tasks" / "T-1" / "contract.yaml"
    )
    assert layout.log_file(tmp_path, "T-1") == (tmp_path / ".torve" / "tasks" / "T-1" / "log.yaml")


def test_runner_config_reads_canonical_location(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceiling": 5})
    )
    assert load_runner_config(tmp_path).poison_ceiling == 5


def test_runner_config_explicit_override_wins(tmp_path: Path) -> None:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceiling": 5})
    )
    override = tmp_path / "elsewhere.yaml"
    override.write_text(yaml.safe_dump({"schema_version": 1, "poison_ceiling": 7}))
    assert load_runner_config(tmp_path, override).poison_ceiling == 7


def test_runner_config_missing_explicit_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no runner configuration"):
        load_runner_config(tmp_path, tmp_path / "absent.yaml")


def test_runner_config_rejects_unknown_keys(tmp_path: Path) -> None:
    # D-13.5: a typo must not silently remove a knob.
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "poison_ceilling": 5})
    )
    with pytest.raises(ValueError):
        load_runner_config(tmp_path)
