"""RFC 0024 phase 1: survey every root's escalation queue, decide the pause
once for the fleet, tick each root in deterministic order under its own
lock with that decision passed down, and record one fleet event. A
locked-out or failing root is recorded and the pass continues. `torve
fleet status` reads every root into one table ordered by escalation age."""

from __future__ import annotations

import json
from pathlib import Path

from torve.application.fleet import (
    decide_pause,
    fleet_escalations,
    fleet_tick,
    survey,
)
from torve.application.loop import LOCK, TickDeps, run_tick
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.fleet import FleetAttention, FleetManifest, FleetRepository
from torve.config.runconfig import RunnerConfig
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


def root(tmp_path: Path, name: str) -> Path:
    r = tmp_path / name
    (r / ".torve").mkdir(parents=True)
    (r / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    return r


def contract(root: Path, task_id: str) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: implement\nintent: work\ndecisions: []\n",
        encoding="utf-8",
    )


def escalate(root: Path, task_id: str) -> RunState:
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.state = TaskState.RUNNING
    state.save()
    state.escalate(EscalationReason.BLOCKER_FINDING, "unresolved")
    return state


def manifest(
    *repos: FleetRepository, pause_escalations: int = 1, order: str = "manifest"
) -> FleetManifest:
    return FleetManifest(
        repositories=list(repos),
        attention=FleetAttention(pause_escalations=pause_escalations),
        order=order,
    )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def leg(self, name: str, moved: bool = False):
        def call() -> tuple[str, bool]:
            self.calls.append(name)
            return (f"{name} ran", moved)

        return call

    def dispatch(self):
        def call(task_ids: list[str]) -> tuple[str, bool]:
            self.calls.extend(f"dispatch:{t}" for t in task_ids)
            return ("ran", True)

        return call


def deps_for(rec: Recorder) -> TickDeps:
    return TickDeps(
        reap=rec.leg("reap"),
        poll=None,
        dispatch=rec.dispatch(),
        lane=rec.leg("lane", moved=True),
        sync=None,
        landed=lambda _t: False,
    )


# ....................... #
# survey / decide_pause


def test_survey_reads_each_roots_escalation_queue(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    assert survey(m) == {str(a): 1, str(b): 0}


def test_decide_pause_is_the_fleet_total_not_a_per_root_check(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    m = manifest(
        FleetRepository(root=str(a), trust="own"),
        FleetRepository(root=str(b), trust="own"),
        pause_escalations=2,
    )
    assert decide_pause(m, survey(m)) == (1, False)

    escalate(b, "T-2")
    assert decide_pause(m, survey(m)) == (2, True)


# ....................... #
# fleet_tick


def test_an_escalation_in_one_root_suppresses_dispatch_in_both_while_lanes_still_land(
    tmp_path: Path,
):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    contract(b, "T-9001")  # a clean candidate elsewhere in the fleet
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    recs = {str(a): Recorder(), str(b): Recorder()}

    def tick(repo: FleetRepository, paused: bool):
        return run_tick(
            Path(repo.root), RunnerConfig(), deps_for(recs[repo.root]), fleet_pause=paused
        )

    report = fleet_tick(m, tick)

    assert report.paused is True
    assert report.escalated_total == 1

    for rec in recs.values():
        assert not any(c.startswith("dispatch:") for c in rec.calls)
        assert "lane" in rec.calls  # every other leg still runs, and still lands


def test_a_clean_fleet_dispatches_normally(tmp_path: Path):
    a = root(tmp_path, "a")
    contract(a, "T-9001")
    m = manifest(FleetRepository(root=str(a), trust="own"), pause_escalations=1)
    rec = Recorder()

    def tick(repo: FleetRepository, paused: bool):
        return run_tick(Path(repo.root), RunnerConfig(), deps_for(rec), fleet_pause=paused)

    report = fleet_tick(m, tick)
    assert report.paused is False
    assert "dispatch:T-9001" in rec.calls


def test_a_root_with_its_lock_held_is_a_recorded_noop_and_the_pass_continues(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    (a / ".torve" / LOCK).write_text(
        json.dumps({"pid": 1, "at": "2126-01-01T00:00:00Z"}), encoding="utf-8"
    )
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    recs = {str(a): Recorder(), str(b): Recorder()}

    def tick(repo: FleetRepository, paused: bool):
        return run_tick(
            Path(repo.root), RunnerConfig(), deps_for(recs[repo.root]), fleet_pause=paused
        )

    report = fleet_tick(m, tick)
    outcomes = {o.root: o.outcome for o in report.outcomes}
    assert outcomes[str(a)] == "locked out"
    assert outcomes[str(b)] == "ticked"
    assert recs[str(b)].calls  # the pass continued past the locked root


def test_a_root_that_raises_does_not_stop_the_pass(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    rec_b = Recorder()

    def tick(repo: FleetRepository, paused: bool):
        if repo.root == str(a):
            raise RuntimeError("sandbox exploded")

        return run_tick(Path(repo.root), RunnerConfig(), deps_for(rec_b), fleet_pause=paused)

    report = fleet_tick(m, tick)
    outcomes = {o.root: o.outcome for o in report.outcomes}
    assert outcomes[str(a)] == "error: sandbox exploded"
    assert outcomes[str(b)] == "ticked"
    assert rec_b.calls  # b still ran despite a's failure


def test_roots_tick_in_the_manifests_order(tmp_path: Path):
    a, b, c = root(tmp_path, "a"), root(tmp_path, "b"), root(tmp_path, "c")
    order: list[str] = []
    m = manifest(
        FleetRepository(root=str(b), trust="own"),
        FleetRepository(root=str(a), trust="own"),
        FleetRepository(root=str(c), trust="own"),
    )

    def tick(repo: FleetRepository, paused: bool):
        order.append(repo.root)
        return run_tick(Path(repo.root), RunnerConfig(), deps_for(Recorder()), fleet_pause=paused)

    fleet_tick(m, tick)
    assert order == [str(b), str(a), str(c)]


def test_a_fleet_event_is_recorded_to_every_ticked_roots_own_telemetry(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-1")
    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )

    def tick(repo: FleetRepository, paused: bool):
        return run_tick(Path(repo.root), RunnerConfig(), deps_for(Recorder()), fleet_pause=paused)

    fleet_tick(m, tick)

    for r in (a, b):
        lines = (r / ".torve" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
        events = [
            json.loads(line) for line in lines if json.loads(line).get("event") == "fleet_tick"
        ]
        assert len(events) == 1
        assert events[0]["escalated_total"] == 1
        assert events[0]["paused"] is True
        assert {row["root"] for row in events[0]["roots"]} == {str(a), str(b)}


# ....................... #
# run_tick's fleet_pause kwarg


def test_fleet_pause_overrides_a_roots_own_threshold_when_the_fleet_says_no(tmp_path: Path):
    # This root alone would pause on its own (1 escalation >= its default
    # threshold of 1), but the fleet's decision replaces that check (D-24.10).
    a = root(tmp_path, "a")
    contract(a, "T-9001")
    escalate(a, "T-1")
    rec = Recorder()
    report = run_tick(a, RunnerConfig(), deps_for(rec), fleet_pause=False)
    assert "dispatch:T-9001" in rec.calls
    assert not report.noop


def test_fleet_pause_true_pauses_even_a_root_with_an_empty_queue(tmp_path: Path):
    a = root(tmp_path, "a")
    contract(a, "T-9001")
    rec = Recorder()
    report = run_tick(a, RunnerConfig(), deps_for(rec), fleet_pause=True)
    assert not any(c.startswith("dispatch:") for c in rec.calls)
    assert any(
        "fleet-wide pause in force" in detail for name, detail in report.legs if name == "dispatch"
    )


def test_solo_ticks_are_unaffected_by_fleet_pause_being_none(tmp_path: Path):
    a = root(tmp_path, "a")
    contract(a, "T-9001")
    rec = Recorder()
    run_tick(a, RunnerConfig(), deps_for(rec))
    assert "dispatch:T-9001" in rec.calls


# ....................... #
# fleet_escalations — D-24.8


def test_fleet_status_orders_escalations_by_age_across_roots(tmp_path: Path):
    a, b = root(tmp_path, "a"), root(tmp_path, "b")
    escalate(a, "T-OLD")
    old = RunState.load(naming.state_file(a, "T-OLD"))
    old.heartbeat = "2001-01-01T00:00:00.000000Z"
    old.save()
    escalate(b, "T-NEW")

    m = manifest(
        FleetRepository(root=str(a), trust="own"), FleetRepository(root=str(b), trust="own")
    )
    rows = fleet_escalations(m)
    assert [r.task_id for r in rows] == ["T-OLD", "T-NEW"]
    assert [r.root for r in rows] == [str(a), str(b)]


def test_fleet_status_ignores_non_escalated_runs(tmp_path: Path):
    a = root(tmp_path, "a")
    state = RunState(task_id="T-1", path=naming.state_file(a, "T-1"))
    state.state = TaskState.RUNNING
    state.save()
    m = manifest(FleetRepository(root=str(a), trust="own"))
    assert fleet_escalations(m) == []
