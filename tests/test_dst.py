"""The deterministic simulation sweep (RFC 0003 §6 layer 3, D-3.5): one master
seed set drives concurrent interleavings of the real loop over the real store;
the invariants must always hold, the reachability targets must sometimes fire,
and every deliberately broken twin must be caught — a simulation that cannot
fail proves nothing."""

from __future__ import annotations

from dst_world import CEILING, World
from forze_dst import Simulation, SimulationConfig, Strategy
from forze_dst.invariants import expect, no_duplicate_effect
from forze_mock import MockDepsModule

INVARIANT_MESSAGES = {
    "double_hold": "two workers hold one task",
    "ceiling": "attempts exceeded the poison ceiling",
    "red_ready": "ready with red gates",
}


def invariants():
    return [
        expect("double_hold", lambda _event: False, message=INVARIANT_MESSAGES["double_hold"]),
        expect(
            "attempt",
            lambda event: event.fields["n"] <= CEILING,
            message=INVARIANT_MESSAGES["ceiling"],
        ),
        expect(
            "ready",
            lambda event: event.fields["gates"] == "green",
            message=INVARIANT_MESSAGES["red_ready"],
        ),
        no_duplicate_effect("landed", by="run"),
    ]


def simulation_for(world: World) -> Simulation:
    async def setup(_ctx) -> None:
        world.reset()

    return Simulation(
        operations=world.registry(),
        deps=lambda: MockDepsModule(),
        setup=setup,
        invariants=invariants(),
    )


def config(seeds: int = 6, count: int = 14) -> SimulationConfig:
    return SimulationConfig(
        strategy=Strategy.OP_CASE, seeds=range(seeds), count=count, concurrency=4
    )


def run_world(tmp_path, twin: str | None, seeds: int = 6):
    world = World(tmp_path, twin=twin)
    report = simulation_for(world).run(config(seeds=seeds), cases=world.cases())
    return world, report


def test_invariants_hold_and_targets_fire(tmp_path):
    world, report = run_world(tmp_path, twin=None, seeds=8)
    assert report is None, f"violation in the honest engine:\n{report}"
    # Reachability: an invariant sweep that never visited the hard states
    # proves nothing (D-3.5).
    required = {"lease_reclaimed", "zombie_abandoned", "gate_red_then_green", "cancel_observed"}
    assert required <= world.reach, f"targets never fired: {required - world.reach}"


def _messages(report) -> str:
    return " | ".join(v.message for v in report.violations)


def test_broken_twin_no_ceiling_is_caught(tmp_path):
    _, report = run_world(tmp_path, twin="no_ceiling")
    assert report is not None
    assert INVARIANT_MESSAGES["ceiling"] in _messages(report)


def test_broken_twin_lying_gates_is_caught(tmp_path):
    _, report = run_world(tmp_path, twin="lie_gates")
    assert report is not None
    assert INVARIANT_MESSAGES["red_ready"] in _messages(report)


def test_broken_twin_duplicate_landing_is_caught(tmp_path):
    _, report = run_world(tmp_path, twin="dup_land")
    assert report is not None
    assert "landed" in _messages(report) or "duplicate" in _messages(report).lower()


def test_broken_twin_rogue_dispatch_is_caught(tmp_path):
    _, report = run_world(tmp_path, twin="rogue")
    assert report is not None
    assert INVARIANT_MESSAGES["double_hold"] in _messages(report)
