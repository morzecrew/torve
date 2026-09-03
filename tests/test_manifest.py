from __future__ import annotations

import warnings

import pytest
import yaml

from torve.application.telemetry import config_hash
from torve.config.manifest import TwinlessGateWarning, load_manifest
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
    bad = dict(
        BASE_MANIFEST,
        gates=[{"name": "x", "run": "@nonsense", "state": "blocking", "origin": "structural"}],
    )
    with pytest.raises(ValueError, match="unknown builtin"):
        load_manifest(write_manifest(tmp_path, bad))


def test_duplicate_gate_names_refused(tmp_path):
    bad = dict(
        BASE_MANIFEST,
        gates=[
            {"name": "x", "run": "@scope", "state": "blocking", "origin": "structural"},
            {"name": "x", "run": "@secrets", "state": "blocking", "origin": "structural"},
        ],
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
    bad = dict(
        BASE_MANIFEST,
        gates=[{"name": "x", "run": "@scope", "state": "blocking", "origin": "because"}],
    )
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


# D-34.4: the axis vocabulary classifies what a conviction from a gate means.
# The four words load; anything else is refused at load; an unlabeled entry
# reads as functional once `resolved_gates()` fills the default.


def _one_gate(axis=None):
    gate = {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"}
    if axis is not None:
        gate["axis"] = axis
    return dict(BASE_MANIFEST, gates=[gate])


@pytest.mark.parametrize("axis", ["functional", "boundary", "compliance", "form"])
def test_a_gate_declaration_may_carry_an_axis(tmp_path, axis):
    manifest = load_manifest(write_manifest(tmp_path, _one_gate(axis)))
    assert manifest.resolved_gates()[0].axis == axis  # verbatim through resolution


def test_an_unlabeled_gate_reads_as_functional(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, _one_gate()))
    gate = next(g for g in manifest.gates if g.name == "scope")
    assert gate.axis is None  # the declaration stays absent...
    assert manifest.resolved_gates()[0].axis == "functional"  # ...the reading is functional


def test_an_axis_outside_the_vocabulary_is_a_load_error(tmp_path):
    with pytest.raises(ValueError, match="axis"):
        load_manifest(write_manifest(tmp_path, _one_gate("philosophical")))


# The sabotage-pair lint: every gate entry names the twin that proves the gate
# convicts — a CASES family or a test path. The refusal is not loadable yet
# (the shipped battery predates the field, and a refusal against it would
# brick the engine's own manifest load), so a twinless entry is voiced at
# load; the backfill landing flips the warn cases to raises.


def _gate(**fields):
    gate = {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"}
    gate.update(fields)
    return dict(BASE_MANIFEST, gates=[gate])


def test_a_gate_declaration_carries_its_sabotage_twin(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, _gate(sabotage="scope")))
    assert manifest.resolved_gates()[0].sabotage == "scope"  # verbatim through resolution


def test_a_test_path_is_a_valid_twin(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, _gate(sabotage="tests/test_gates.py")))
    assert manifest.twinless_gates() == []


def test_a_twinned_load_is_quiet(tmp_path):
    path = write_manifest(tmp_path, _gate(sabotage="scope"))

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning at all fails the load
        load_manifest(path)


def test_a_twinless_gate_warns_at_load(tmp_path):
    with pytest.warns(TwinlessGateWarning, match="scope"):
        manifest = load_manifest(write_manifest(tmp_path, _gate()))

    assert manifest.gates[0].name == "scope"  # voiced, not refused
    assert manifest.twinless_gates() == ["scope"]


def test_the_warning_names_every_twinless_gate(tmp_path):
    manifest = dict(
        BASE_MANIFEST,
        gates=[
            {"name": "a", "run": "@scope", "state": "blocking", "origin": "structural"},
            {
                "name": "b",
                "run": "@secrets",
                "state": "blocking",
                "origin": "structural",
                "sabotage": "b",
            },
            {
                "name": "c",
                "run": "@no-test-tampering",
                "state": "shadow",
                "origin": "structural",
            },
        ],
    )

    with pytest.warns(TwinlessGateWarning, match="a, c") as caught:
        loaded = load_manifest(write_manifest(tmp_path, manifest))

    assert len(caught) == 1  # one aggregated warning, not a spam per entry
    assert loaded.twinless_gates() == ["a", "c"]  # the twinned entry is not named


def test_the_scratch_battery_is_caught_and_still_loads(tmp_path):
    # The self-hosting boundary: a manifest where no entry carries the field
    # must warn for all of them and remain loadable — refusal arrives with
    # the battery's backfill, in the same landing that makes it zero-exception.
    with pytest.warns(TwinlessGateWarning):
        manifest = load_manifest(write_manifest(tmp_path, BASE_MANIFEST))

    assert manifest.twinless_gates() == [g["name"] for g in BASE_MANIFEST["gates"]]


def test_a_blank_twin_is_refused(tmp_path):
    # A present-but-empty sabotage: is a malformed declaration, not a twinless
    # one — there is nothing for the hardening to resolve against.
    with pytest.raises(ValueError, match="non-blank"):
        load_manifest(write_manifest(tmp_path, _gate(sabotage="   ")))
