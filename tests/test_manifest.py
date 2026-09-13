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
    # S-0002/D-19: every manifest entry carries origin and state — a boolean (or an
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


def test_config_hash_tracks_the_manifest(tmp_path):
    path = write_manifest(tmp_path, BASE_MANIFEST)
    first = config_hash(path, tmp_path)
    assert first == config_hash(path, tmp_path)  # stable

    changed = dict(BASE_MANIFEST, quarantine=["flaky-command"])
    assert config_hash(write_manifest(tmp_path, changed), tmp_path) != first


def test_the_hash_reads_what_was_resolved_and_not_another_tool_s_lockfile(tmp_path):
    """S-0061/D-7: `skills-lock.json` is the `skills` CLI's file, which this
    engine installs nothing from and reads for nothing else. Hashing it meant a
    reformat by another tool changed the regime while a change to the equipment
    this engine actually resolves did not."""

    from torve.config.runconfig import RunnerConfig, SkillsConfig

    path = write_manifest(tmp_path, BASE_MANIFEST)
    config = RunnerConfig(skills=SkillsConfig(sets={"implement": ["flag-dont-flip"]}))
    first = config_hash(path, tmp_path, config)

    (tmp_path / "skills-lock.json").write_text('{"skills": {}}', encoding="utf-8")
    assert config_hash(path, tmp_path, config) == first  # not this engine's file

    equipped = RunnerConfig(skills=SkillsConfig(sets={"implement": ["ratchet-what-you-build"]}))
    assert config_hash(path, tmp_path, equipped) != first  # what a role loads is the regime


# S-0034/D-4: the axis vocabulary classifies what a conviction from a gate means.
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


# The sabotage-pair lint at refusal stage (S-0036/D-3): every gate entry names the
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
    # The refusal (S-0036/D-3): adopting the field is adopting it wholly — one
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


def test_this_repositorys_manifest_names_the_projection_gate_and_its_twin():
    """S-0054 S-0054/D-7: the rendered AGENTS.md sections are drift-checked by a
    manifest gate; it enters at shadow (S-0002/D-18) and names the test file
    whose drift case reddens it (S-0036/D-3)."""

    from pathlib import Path

    from torve.config.manifest import load_manifest

    manifest = load_manifest(Path(__file__).resolve().parent.parent / ".torve" / "gates.yaml")
    gate = next(g for g in manifest.resolved_gates() if g.name == "spec-projection")

    assert gate.run == "uv run torve spec project --check"
    assert gate.state == "shadow" and gate.axis == "form"
    assert gate.sabotage == "tests/test_colocation.py"


def test_the_computed_order_puts_no_gate_before_one_declaring_less(repo):
    """S-0071/D-3: the runner orders the battery cheapest-first on the declared
    timeout, so an entry declaring nothing sorts as whatever default gets filled
    in — which is how the dearest gate in this battery came to run ahead of the
    cheapest. Every entry declares a value, and the order the runner computes
    over this repository's own manifest never puts a gate before one that
    declares less.

    The order is read from the runner rather than restated here: the live
    manifest runs in a scratch repository with its shell commands stubbed, so
    the sort under test is the one that runs the battery, and no gate's command
    runs. The manifest's own order is not sorted by timeout, so a runner that
    stopped sorting on the field reddens this too.
    """

    from conftest import context_for

    from torve.gates.runner import run_gates

    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / ".torve" / "gates.yaml").read_text(encoding="utf-8"))
    declared = {gate["name"]: gate.get("timeout") for gate in data["gates"]}

    undeclared = [name for name, value in declared.items() if value is None]
    assert undeclared == [], f"gate entries declaring no timeout: {', '.join(undeclared)}"

    for gate in data["gates"]:
        if not gate["run"].startswith("@"):
            gate["run"] = f"echo {gate['name']}"

    repo.seed(data)
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))

    ran = [(result.name, declared[result.name]) for result in report.results]
    assert [cost for _, cost in ran] == sorted(cost for _, cost in ran), ran


def test_the_coverage_entry_says_which_of_its_two_halves_went_red():
    """The entry runs the suite before it can measure anything, so a failing
    test and a coverage shortfall both redden one gate. It says which — the
    shipped command's own shell logic, exercised with the two commands
    stubbed, because the distinction is the point of the entry and reverting
    it to a bare `&&` restores exactly the misreading that cost T-0319."""

    import subprocess

    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root / ".torve" / "gates.yaml")
    (entry,) = [g for g in manifest.gates if g.name == "coverage-delta"]

    suite = "uv run pytest --cov=src --cov-report=xml -q"
    measure = entry.run.rsplit("; ", 1)[1]
    assert suite in entry.run and measure.startswith("uv run diff-cover")

    def behaves(suite_exit: str) -> subprocess.CompletedProcess[str]:
        line = entry.run.replace(suite, suite_exit).replace(measure, "echo MEASURED")
        return subprocess.run(["sh", "-c", line], capture_output=True, text=True)

    red = behaves("false")
    assert red.returncode == 1
    assert "the coverage delta was never measured" in red.stdout
    assert "MEASURED" not in red.stdout  # nothing was measured, and it says so

    green = behaves("true")
    assert green.returncode == 0
    assert "MEASURED" in green.stdout


def test_no_shape_borrows_another_shape_s_version():
    """T-0321: one shared constant meant bumping any shape bumped every shape —
    the contract going to 2 reddened the telemetry and survey suites, which
    declare nothing about a contract. `domain.task` now exports the contract's
    version alone."""

    from torve.domain import task

    assert task.CONTRACT_SCHEMA_VERSION == 2
    assert not hasattr(task, "SCHEMA_VERSION")

    # Each shape says its own, and they are free to disagree.
    from torve.application.evals import SCHEMA_VERSION as evals_version
    from torve.application.runstate import SCHEMA_VERSION as runstate_version
    from torve.application.telemetry import RECORD_SCHEMA_VERSION
    from torve.config.fleet import FleetManifest
    from torve.config.manifest import SCHEMA_VERSION as manifest_version
    from torve.domain.attempt import SCHEMA_VERSION as attempt_version

    declared = (
        RECORD_SCHEMA_VERSION,
        evals_version,
        runstate_version,
        manifest_version,
        attempt_version,
        FleetManifest().schema_version,
    )
    assert all(isinstance(v, int) and v >= 1 for v in declared)
