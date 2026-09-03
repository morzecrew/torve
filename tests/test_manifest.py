from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import yaml

from torve.application.telemetry import config_hash
from torve.config.manifest import TwinlessGateWarning, load_manifest
from torve.gates.sabotage import BASE_MANIFEST, CASES


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


# The sabotage-pair lint at refusal stage (D-36.3): every gate entry names the
# twin that proves it convicts — a CASES family or a test path. A manifest
# that names a twin anywhere refuses its twinless entries at load; a manifest
# naming none at all predates the field — the shipped scenario data builds
# one per case — and is voiced, not bricked. The shipped battery is fully
# twinned: the red cases pin the refusal, this repository's own manifest pins
# green.


def _gate(**fields):
    gate = {"name": "scope", "run": "@scope", "state": "blocking", "origin": "structural"}
    gate.update(fields)
    return dict(BASE_MANIFEST, gates=[gate])


def _partial_battery():
    # Adopted (b names its twin) but incomplete: a and c must not load.
    return dict(
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


def test_a_second_twin_refuses_the_twinless_gate(tmp_path):
    # The refusal (D-36.3): adopting the field is adopting it wholly — one
    # declared twin turns every twinless sibling from a warning into a load
    # error, before any gate runs.
    with pytest.raises(ValueError, match="without a declared sabotage twin"):
        load_manifest(write_manifest(tmp_path, _partial_battery()))


def test_the_refusal_names_every_twinless_gate(tmp_path):
    # One aggregated error naming exactly the offenders, in order: the
    # twinned entry must not read as guilty.
    with pytest.raises(ValueError, match="twin: a, c —"):
        load_manifest(write_manifest(tmp_path, _partial_battery()))


def test_a_pre_field_manifest_warns_and_loads(tmp_path):
    # The self-hosting boundary, held: a manifest naming no twin at all is
    # pre-field data — scenario batteries the shipped suite seeds per case —
    # and the refusal must not reach through it. Voiced, never bricked.
    with pytest.warns(TwinlessGateWarning, match="scope"):
        manifest = load_manifest(write_manifest(tmp_path, _gate()))

    assert manifest.gates[0].name == "scope"
    assert manifest.twinless_gates() == ["scope"]


def test_the_scratch_battery_is_caught_and_still_loads(tmp_path):
    # Same rule at battery scale: no entry carries the field, so all are
    # named in one warning and the load stands — the scenario suite the
    # engine runs against these manifests cannot refuse itself into nothing.
    with pytest.warns(TwinlessGateWarning, match="acceptance"):
        manifest = load_manifest(write_manifest(tmp_path, BASE_MANIFEST))

    assert manifest.twinless_gates() == [g["name"] for g in BASE_MANIFEST["gates"]]


def test_the_shipped_battery_loads_quiet_with_zero_exceptions():
    # The green pin, hardened with the backfill (T-0255's proposal): this
    # repository's own manifest loads under warnings-as-errors — no entry is
    # voiced — and no entry is twinless. Grandfathering has nowhere to hide.
    shipped = Path(__file__).resolve().parents[1] / ".torve" / "gates.yaml"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        manifest = load_manifest(shipped)

    assert manifest.gates
    assert manifest.twinless_gates() == []


def test_every_shipped_twin_resolves():
    # A gate cannot satisfy the lint with a twin that does not exist
    # (T-0255's proposal, landed at the refusal): a family must ship in the
    # sabotage suite with a red case; a test path must name a file in the
    # tree. The cross-check lives here, not in the model — the suite imports
    # the model, so the load cannot import the suite back.
    root = Path(__file__).resolve().parents[1]

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        manifest = load_manifest(root / ".torve" / "gates.yaml")

    families = {case.gate for case in CASES}
    reddened = {case.gate for case in CASES if case.expected == "fail"}

    for gate in manifest.gates:
        twin = gate.sabotage
        assert twin is not None, f"gate {gate.name} declares no twin"

        if twin in families:
            assert twin in reddened, f"gate {gate.name}: family {twin} ships no red case"
        else:
            assert (root / twin).is_file(), f"gate {gate.name} names a missing twin {twin}"


def test_a_blank_twin_is_refused(tmp_path):
    # A present-but-empty sabotage: is a malformed declaration, not a twinless
    # one — there is nothing for the resolution to check against.
    with pytest.raises(ValueError, match="non-blank"):
        load_manifest(write_manifest(tmp_path, _gate(sabotage="   ")))
