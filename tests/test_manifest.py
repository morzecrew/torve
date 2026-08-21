from __future__ import annotations

import pytest
import yaml

from torve.application.telemetry import config_hash
from torve.config.manifest import load_manifest
from torve.gates.sabotage import BASE_MANIFEST


def write_manifest(tmp_path, data):
    path = tmp_path / "gates.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_load_and_resolve_defaults(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, BASE_MANIFEST))
    gates = {g.name: g for g in manifest.resolved_gates()}
    assert gates["scope"].input == "diff"
    assert gates["decisions-reported"].input == "log"
    assert gates["acceptance"].timeout == 600
    assert gates["scope"].timeout == 30


def test_unknown_builtin_is_a_load_error(tmp_path):
    bad = dict(BASE_MANIFEST,
               gates=[{"name": "x", "run": "@nonsense",
                       "state": "blocking", "origin": "structural"}])
    with pytest.raises(ValueError, match="unknown builtin"):
        load_manifest(write_manifest(tmp_path, bad))


def test_duplicate_gate_names_refused(tmp_path):
    bad = dict(
        BASE_MANIFEST,
        gates=[{"name": "x", "run": "@scope", "state": "blocking", "origin": "structural"},
               {"name": "x", "run": "@secrets", "state": "blocking", "origin": "structural"}],
    )
    with pytest.raises(ValueError, match="unique"):
        load_manifest(write_manifest(tmp_path, bad))


def test_an_entry_without_state_or_origin_is_refused(tmp_path):
    # D-2.19: every manifest entry carries origin and state — a boolean (or an
    # omission) cannot express shadow or quarantine, and provenance is
    # unrecoverable later.
    bad = dict(BASE_MANIFEST, gates=[{"name": "x", "run": "@scope"}])
    with pytest.raises(ValueError):
        load_manifest(write_manifest(tmp_path, bad))


def test_a_shapeless_origin_is_refused(tmp_path):
    bad = dict(BASE_MANIFEST,
               gates=[{"name": "x", "run": "@scope",
                       "state": "blocking", "origin": "because"}])
    with pytest.raises(ValueError, match="origin"):
        load_manifest(write_manifest(tmp_path, bad))


def test_config_hash_tracks_manifest_and_skill_lock(tmp_path):
    path = write_manifest(tmp_path, BASE_MANIFEST)
    first = config_hash(path, tmp_path)
    assert first == config_hash(path, tmp_path)  # stable

    changed = dict(BASE_MANIFEST, quarantine=["flaky-command"])
    assert config_hash(write_manifest(tmp_path, changed), tmp_path) != first

    path = write_manifest(tmp_path, BASE_MANIFEST)
    (tmp_path / "skills-lock.json").write_text("{}", encoding="utf-8")
    assert config_hash(path, tmp_path) != first  # the skill set is part of the regime
