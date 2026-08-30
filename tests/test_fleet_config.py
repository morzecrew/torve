"""RFC 0024 §5.1: the operator-side fleet manifest — never resolved under a
repository's own `.torve/`, deterministic order, and a `trust` class that is
never defaulted."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from torve.config.fleet import (
    FleetManifest,
    FleetRepository,
    default_manifest_path,
    load_fleet_manifest,
)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_repositories_trust_is_never_defaulted():
    with pytest.raises(ValidationError):
        FleetRepository.model_validate({"root": "~/work/torve"})


def test_repository_path_expands_the_home_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = FleetRepository(root="~/work/torve", trust="own")
    assert repo.path == (tmp_path / "work" / "torve").resolve()


def test_manifest_order_is_manifest_by_default_never_a_priority_field():
    manifest = FleetManifest(
        repositories=[
            FleetRepository(root="/b", trust="own"),
            FleetRepository(root="/a", trust="own"),
        ]
    )
    assert [r.root for r in manifest.ticking_order()] == ["/b", "/a"]


def test_alphabetical_order_is_deterministic_by_root():
    manifest = FleetManifest(
        order="alphabetical",
        repositories=[
            FleetRepository(root="/b", trust="own"),
            FleetRepository(root="/a", trust="own"),
        ],
    )
    assert [r.root for r in manifest.ticking_order()] == ["/a", "/b"]


def test_load_fleet_manifest_parses_the_documented_shape(tmp_path: Path):
    path = write(
        tmp_path / "fleet.yaml",
        "repositories:\n"
        "  - root: ~/work/torve\n"
        "    trust: own\n"
        "  - root: ~/work/lab\n"
        "    trust: reviewed\n"
        "attention:\n"
        "  pause_escalations: 2\n"
        "order: manifest\n",
    )
    manifest = load_fleet_manifest(path)
    assert [r.trust for r in manifest.repositories] == ["own", "reviewed"]
    assert manifest.attention.pause_escalations == 2


def test_load_fleet_manifest_rejects_unknown_trust_classes(tmp_path: Path):
    path = write(tmp_path / "fleet.yaml", "repositories:\n  - root: /x\n    trust: superuser\n")
    with pytest.raises(ValidationError):
        load_fleet_manifest(path)


def test_load_fleet_manifest_rejects_a_non_mapping_document(tmp_path: Path):
    path = write(tmp_path / "fleet.yaml", "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_fleet_manifest(path)


def test_an_empty_manifest_file_is_an_empty_fleet(tmp_path: Path):
    path = write(tmp_path / "fleet.yaml", "")
    assert load_fleet_manifest(path) == FleetManifest()


def test_default_manifest_path_honours_xdg_config_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_manifest_path() == tmp_path / "torve" / "fleet.yaml"


def test_default_manifest_path_falls_back_to_the_home_config_dir(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_manifest_path() == tmp_path / ".config" / "torve" / "fleet.yaml"
