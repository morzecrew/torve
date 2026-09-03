"""RFC 0013 resolution: one path per lookup under `.torve/`, whether or not
the file exists (D-13.1, A-48). `--config` is the only override (D-13.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from torve.base import naming
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


# ....................... #
# The trace store's home: one directory of the engine root, referenced
# root-relative, and one path helper every writer reaches.


def test_trace_store_home_is_under_the_root(tmp_path: Path) -> None:
    assert naming.traces_dir(tmp_path) == tmp_path / ".torve" / "traces"
    assert naming.trace_file(naming.worktree(tmp_path, "T-1"), 4) == (
        tmp_path / ".torve" / "traces" / "T-1.a4.trace.log"
    )


def test_trace_ref_is_root_relative(tmp_path: Path) -> None:
    expected = ".torve/traces/T-1.a4.trace.log"
    assert naming.trace_ref(naming.worktree(tmp_path, "T-1"), 4) == expected


def test_the_trace_path_helper_ensures_the_store_directory(tmp_path: Path) -> None:
    # The one path helper creates the home, so no writer of the store —
    # the harness adapter or the review lane's relocation — can depend on
    # another having run first.

    assert not (tmp_path / ".torve" / "traces").exists()

    trace = naming.trace_file(naming.worktree(tmp_path, "T-1"), 1)

    assert trace.parent.is_dir()

    # And it is idempotent: the helper serves a second writer into a home
    # the first one already made.
    assert naming.trace_file(naming.worktree(tmp_path, "T-2"), 1) == (
        tmp_path / ".torve" / "traces" / "T-2.a1.trace.log"
    )
