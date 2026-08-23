"""RFC 0019 phase 1: the tick — fixed order, the lock with its loud stale
break, the file-system selection rule (no run *record*, not merely no run
state — telemetry survives the reaper), the escalation pause that lets the
queue drain but not grow, the approval-gated lane leg, and honest noops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from torve.application.loop import LOCK, TickDeps, next_queued, run_tick
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8")
    return tmp_path


def contract(root: Path, task_id: str, role: str = "implement",
             depends_on: list[str] | None = None) -> None:
    task_dir = root / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    deps = f"depends_on: {depends_on}\n" if depends_on else ""
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: {role}\n"
        f"intent: work\n{deps}"
        + ("targets: ['T-0001']\n" if role == "review" else "")
        + "decisions: []\n",
        encoding="utf-8")


def run_state(root: Path, task_id: str, state: TaskState) -> RunState:
    record = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    record.state = state
    record.save()
    return record


class Recorder:
    """Every leg records its call; dispatch records its task."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def leg(self, name: str, moved: bool = False):
        def call() -> tuple[str, bool]:
            self.calls.append(name)
            return (f"{name} ran", moved)
        return call

    def dispatch(self, detail: str = "ran", moved: bool = True):
        def call(task_id: str) -> tuple[str, bool]:
            self.calls.append(f"dispatch:{task_id}")
            return (detail, moved)
        return call


def deps_for(rec: Recorder, lane: bool = True) -> TickDeps:
    return TickDeps(
        reap=rec.leg("reap"), poll=rec.leg("poll"),
        dispatch=rec.dispatch(),
        lane=rec.leg("lane") if lane else None,
        sync=rec.leg("sync"), landed=lambda _task: False)


def config() -> RunnerConfig:
    return RunnerConfig()


def tick_events(root: Path) -> list[dict[str, object]]:
    path = root / ".torve" / "telemetry.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip() and json.loads(line).get("event") == "tick"]


def test_legs_run_in_the_fixed_order(root):
    # A-27's order: the lane before the reaper — READY is sweepable, and
    # the second live tick proved reap-first destroys the lane's input.
    contract(root, "T-9001")
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec))
    assert rec.calls == ["poll", "lane", "reap", "dispatch:T-9001", "sync"]
    assert rec.calls.index("lane") < rec.calls.index("reap")
    assert report.noop is False  # dispatch moved


def test_a_held_lock_makes_the_tick_a_recorded_noop(root):
    lock = root / ".torve" / LOCK
    lock.write_text(json.dumps({"pid": 1, "at": "2126-01-01T00:00:00Z"}))
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec))
    assert report.locked_out and report.noop
    assert rec.calls == []  # nothing ran
    assert lock.exists()  # the holder's lock is not touched


def test_a_stale_lock_is_broken_loudly(root):
    (root / ".torve" / LOCK).write_text(
        json.dumps({"pid": 1, "at": "2001-01-01T00:00:00Z"}))
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec))
    assert not report.locked_out
    assert rec.calls[0] == "poll"  # the tick ran
    events = [r for r in (json.loads(line) for line in
              (root / ".torve" / "telemetry.jsonl").read_text().splitlines())
              if r.get("event") == "tick_lock_broken"]
    assert len(events) == 1 and events[0]["stale_holder"] == 1
    assert not (root / ".torve" / LOCK).exists()  # released after the pass


def test_selection_is_ascending_and_one_per_tick(root):
    contract(root, "T-9001")
    contract(root, "T-9002")
    rec = Recorder()
    run_tick(root, config(), deps_for(rec))
    dispatched = [c for c in rec.calls if c.startswith("dispatch:")]
    assert dispatched == ["dispatch:T-9001"]


def test_a_reaped_task_with_telemetry_is_not_redispatched(root):
    # The execution refinement of D-19.4: the reaper removes state files,
    # so "no run state" alone would re-dispatch the whole reaped history.
    contract(root, "T-9001")
    contract(root, "T-9002")
    (root / ".torve" / "telemetry.jsonl").write_text(
        json.dumps({"schema_version": 1, "kind": "attempt",
                    "task_id": "T-9001"}) + "\n", encoding="utf-8")
    assert next_queued(root, lambda _t: False) == "T-9002"


def test_a_task_with_run_state_is_not_redispatched(root):
    contract(root, "T-9001")
    run_state(root, "T-9001", TaskState.RUNNING)
    assert next_queued(root, lambda _t: False) is None


def test_review_role_contracts_are_never_selected(root):
    contract(root, "T-9001", role="review")
    assert next_queued(root, lambda _t: False) is None


def test_dependencies_hold_until_ready_or_landed(root):
    contract(root, "T-9002", depends_on=["T-9001"])
    assert next_queued(root, lambda _t: False) is None
    # A landed dependency satisfies even after its state was reaped.
    assert next_queued(root, lambda t: t == "T-9001") == "T-9002"
    # A READY dependency satisfies too.
    run_state(root, "T-9001", TaskState.READY)
    assert next_queued(root, lambda _t: False) == "T-9002"


def test_an_escalation_pauses_intake_but_drains_everything_else(root):
    contract(root, "T-9002")
    state = run_state(root, "T-9001", TaskState.RUNNING)
    state.escalate(EscalationReason.BLOCKER_FINDING, "unresolved")
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec))
    assert not any(c.startswith("dispatch:") for c in rec.calls)
    assert "poll" in rec.calls and "lane" in rec.calls and "sync" in rec.calls
    assert any("paused: escalation queue at 1" in detail
               for name, detail in report.legs if name == "dispatch")


def test_auto_merge_off_skips_the_lane_with_its_reason(root):
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec, lane=False))
    assert ("lane", "skipped: auto_merge off") in report.legs


def test_a_tick_that_moved_nothing_is_an_honest_noop(root):
    rec = Recorder()
    report = run_tick(root, config(), deps_for(rec))  # nothing queued
    assert report.noop is True
    events = tick_events(root)
    assert len(events) == 1 and events[0]["noop"] is True


def test_a_leg_error_is_recorded_and_the_tick_reaches_sync(root):
    contract(root, "T-9001")
    rec = Recorder()

    def broken(task_id: str) -> tuple[str, bool]:
        raise RuntimeError("sandbox exploded")

    deps = TickDeps(reap=rec.leg("reap"), poll=rec.leg("poll"),
                    dispatch=broken, lane=rec.leg("lane"),
                    sync=rec.leg("sync"), landed=lambda _t: False)
    report = run_tick(root, config(), deps)
    assert ("dispatch", "error: sandbox exploded") in report.legs
    assert "sync" in rec.calls
    assert not (root / ".torve" / LOCK).exists()


def test_loop_config_defaults():
    cfg = RunnerConfig()
    assert cfg.loop.pause_escalations == 1
    assert cfg.loop.tick_budget == 3600


def test_the_tick_pushes_what_the_lane_lands(tmp_path: Path) -> None:
    # D-19.9 (A-28): the loop publishes what it lands — after the lane leg
    # fast-forwards the base, the tick pushes it to origin.
    import subprocess

    from typer.testing import CliRunner

    from torve.cli.main import app

    def git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                   check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    git(repo, "config", "user.name", "Loop Operator")
    git(repo, "config", "user.email", "loop@example.invalid")
    (repo / ".torve").mkdir()
    (repo / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8")
    (repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  auto_merge: true\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".wt/\n.torve/telemetry.jsonl\n",
                                     encoding="utf-8")
    (repo / "app.py").write_text("base = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "init")
    git(repo, "push", "-q", "origin", "main")

    git(repo, "checkout", "-q", "-b", naming.branch("T-9201"), "main")
    (repo / "landed.py").write_text("landed = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "work (T-9201)")
    git(repo, "checkout", "-q", "main")
    state = RunState(task_id="T-9201", path=naming.state_file(repo, "T-9201"))
    state.state = TaskState.READY
    state.save()

    result = CliRunner().invoke(app, ["tick", "--root", str(repo)])
    assert result.exit_code == 0, result.output
    assert "landed 1 of 1" in result.output and "base pushed" in result.output
    # Origin holds the landing: the next dispatch bases on the truth.
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "main")
    assert "landed.py" in git(origin, "ls-tree", "--name-only", "main")


def test_a_board_requeued_task_is_selected_again(root):
    # T-0059: re-entry into the queue is the human act §4 names — a QUEUED
    # state is queued, telemetry record notwithstanding.
    contract(root, "T-9001")
    (root / ".torve" / "telemetry.jsonl").write_text(
        json.dumps({"schema_version": 1, "kind": "attempt",
                    "task_id": "T-9001"}) + "\n", encoding="utf-8")
    assert next_queued(root, lambda _t: False) is None  # ran, not re-queued
    run_state(root, "T-9001", TaskState.QUEUED)
    assert next_queued(root, lambda _t: False) == "T-9001"


def test_a_requeued_task_still_waits_for_its_dependencies(root):
    contract(root, "T-9002", depends_on=["T-9001"])
    run_state(root, "T-9002", TaskState.QUEUED)
    assert next_queued(root, lambda _t: False) is None
    assert next_queued(root, lambda t: t == "T-9001") == "T-9002"
