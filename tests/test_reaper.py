from __future__ import annotations

from datetime import UTC, datetime, timedelta

from test_run_loop import MockRuntime

from torve.application.ports import SandboxInfo
from torve.application.reaper import reap
from torve.application.runstate import RunState
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState


class ListingWorkspace:
    def __init__(self, entries):
        self.entries = entries
        self.removed: list[str] = []

    def create(self, task_id, base_ref):
        raise AssertionError("reap never creates")

    def remove(self, task_id):
        self.removed.append(task_id)

    def list_worktrees(self):
        return self.entries


def state_at(tmp_path, task_id, state, age_s=0.0):
    run = RunState(task_id=task_id, path=tmp_path / ".wt" / f"{task_id}.state.json")
    run.state = state
    if age_s:
        stamp = datetime.now(UTC) - timedelta(seconds=age_s)
        run.heartbeat = stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    run.save()
    return run


def sandbox_for(run):
    return SandboxInfo(id=f"sbx-{run.task_id}", name=f"torve-{run.task_id}",
                       labels={"torve.task": run.task_id, "torve.run": run.run_id})


def test_stale_run_is_expired_and_its_sandbox_destroyed(tmp_path):
    stale = state_at(tmp_path, "T-9101", TaskState.RUNNING, age_s=3600)
    fresh = state_at(tmp_path, "T-9102", TaskState.RUNNING)
    runtime = MockRuntime()
    runtime.registry = [sandbox_for(stale), sandbox_for(fresh)]

    report = reap(tmp_path, RunnerConfig(), runtime, ListingWorkspace([]))

    assert report.runs_expired == ["T-9101"]
    assert report.sandboxes_destroyed == ["torve-T-9101"]
    reloaded = RunState.load(tmp_path / ".wt" / "T-9101.state.json")
    assert reloaded.state is TaskState.ESCALATED
    assert reloaded.escalation.reason == "lease_expired"
    assert RunState.load(tmp_path / ".wt" / "T-9102.state.json").state is TaskState.RUNNING


def test_orphaned_sandbox_with_no_state_at_all_is_destroyed(tmp_path):
    runtime = MockRuntime()
    runtime.registry = [SandboxInfo(id="sbx-x", name="torve-mystery",
                                    labels={"torve.task": "T-0000", "torve.run": "gone"})]
    report = reap(tmp_path, RunnerConfig(), runtime, ListingWorkspace([]))
    assert report.sandboxes_destroyed == ["torve-mystery"]


def test_worktrees_are_removed_only_for_terminal_or_stateless_tasks(tmp_path):
    state_at(tmp_path, "T-9201", TaskState.READY)
    escalated = state_at(tmp_path, "T-9202", TaskState.RUNNING, age_s=3600)
    workspace = ListingWorkspace([
        ("T-9201", tmp_path / ".wt" / "T-9201"),
        ("T-9202", tmp_path / ".wt" / "T-9202"),  # becomes escalated: kept for triage
        ("T-9203", tmp_path / ".wt" / "T-9203"),  # no state file: convention debris
    ])
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)
    assert sorted(report.worktrees_removed) == ["T-9201", "T-9203"]
    assert escalated.task_id not in report.worktrees_removed


def test_terminal_run_footprint_is_swept_whole(tmp_path):
    # RFC 0003 §4.2: the sweep destroys anything without a live lease — for a
    # terminal run that is the worktree, the state file AND the trace logs.
    ready = state_at(tmp_path, "T-9401", TaskState.READY)
    escalated = state_at(tmp_path, "T-9402", TaskState.ESCALATED)
    (tmp_path / ".wt" / "T-9401.a1.trace.log").write_text("trace")
    (tmp_path / ".wt" / "T-9402.a1.trace.log").write_text("trace")
    workspace = ListingWorkspace([("T-9401", tmp_path / ".wt" / "T-9401")])

    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)

    assert report.worktrees_removed == ["T-9401"]
    assert report.states_removed == ["T-9401"]
    assert not ready.path.exists()
    assert not (tmp_path / ".wt" / "T-9401.a1.trace.log").exists()
    # An escalated run is triage evidence: state file and trace stay.
    assert escalated.path.exists()
    assert (tmp_path / ".wt" / "T-9402.a1.trace.log").exists()


def test_terminal_state_whose_worktree_is_already_gone_is_still_swept(tmp_path):
    # The state sweep is driven by the state files, not the worktree listing —
    # otherwise a once-reaped run haunts `torve status` forever.
    ready = state_at(tmp_path, "T-9403", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]))
    assert report.worktrees_removed == []
    assert report.states_removed == ["T-9403"]
    assert not ready.path.exists()


def test_force_expires_even_fresh_runs(tmp_path):
    fresh = state_at(tmp_path, "T-9301", TaskState.CLAIMED)
    runtime = MockRuntime()
    runtime.registry = [sandbox_for(fresh)]
    report = reap(tmp_path, RunnerConfig(), runtime, ListingWorkspace([]), force=True)
    assert report.runs_expired == ["T-9301"]
    assert report.sandboxes_destroyed == [f"torve-{fresh.task_id}"]


def test_dry_run_reports_without_touching_anything(tmp_path):
    # RFC 0011 §6: --dry-run on anything that mutates.
    stale = state_at(tmp_path, "T-9105", TaskState.RUNNING, age_s=3600)
    done = state_at(tmp_path, "T-9106", TaskState.READY)
    runtime = MockRuntime()
    runtime.registry = [sandbox_for(stale)]
    # An orphan worktree with no state file would be removed by a wet run.
    workspace = ListingWorkspace([("T-9999", tmp_path / ".wt" / "T-9999")])

    report = reap(tmp_path, RunnerConfig(), runtime, workspace, dry_run=True)

    assert report.runs_expired == ["T-9105"]
    assert report.sandboxes_destroyed == ["torve-T-9105"]
    assert report.worktrees_removed == ["T-9999"]
    assert report.states_removed == ["T-9106"]
    assert runtime.destroyed == []
    assert workspace.removed == []
    assert done.path.exists()
    reloaded = RunState.load(tmp_path / ".wt" / "T-9105.state.json")
    assert reloaded.state is TaskState.RUNNING  # nothing escalated, nothing saved


# D-19.10 (A-28, narrowing D-3.23): a READY implement run whose task has
# not landed is the lane's input, not debris.


def implement_contract(tmp_path, task_id, role="implement"):
    task_dir = tmp_path / ".torve" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "contract.yaml").write_text(
        f"schema_version: 1\nid: {task_id}\nrole: {role}\nintent: work\n"
        + ("targets: ['T-0001']\n" if role == "review" else "")
        + "decisions: []\n", encoding="utf-8")


def test_an_unlanded_ready_implement_state_survives_the_sweep(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9110")
    state_at(tmp_path, "T-9110", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]),
                  landed=lambda _t: False)
    assert report.states_removed == []
    assert (tmp_path / ".wt" / "T-9110.state.json").exists()


def test_a_landed_ready_state_is_swept(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9111")
    state_at(tmp_path, "T-9111", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]),
                  landed=lambda t: t == "T-9111")
    assert report.states_removed == ["T-9111"]


def test_a_ready_review_state_stays_sweepable(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9112", role="review")
    state_at(tmp_path, "T-9112", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]),
                  landed=lambda _t: False)
    assert report.states_removed == ["T-9112"]


def test_without_a_landed_oracle_the_reaper_keeps_conservatively(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9113")
    state_at(tmp_path, "T-9113", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]))
    assert report.states_removed == []
