from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

from test_run_loop import MockRuntime

from torve.application.ports import SandboxInfo
from torve.application.reaper import reap
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig, TracesConfig
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
    return SandboxInfo(
        id=f"sbx-{run.task_id}",
        name=f"torve-{run.task_id}",
        labels={"torve.task": run.task_id, "torve.run": run.run_id},
    )


def put_trace(root, name, *, age_days=0.0, body="trace"):
    """One trace written into the durable store with its modification time
    back-dated — retention's clock is the filesystem's."""

    store = naming.traces_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    trace = store / name
    trace.write_text(body, encoding="utf-8")

    if age_days:
        moment = time.time() - age_days * 86_400
        os.utime(trace, (moment, moment))

    return trace


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

    # RFC 0038 §10's open question, decided by the reaper's actual knowledge
    # at sweep time: the sweep escalates through RunState.escalate, so the
    # durable engine event lands (D-38.5) — but the reaper holds no exec
    # result, no gate report, nothing from which a verdict could be
    # *derived*, and D-38.2 forbids inventing one. It stamps no verdict row
    # for the orphaned attempt; the event is the record.
    lines = [
        json.loads(line)
        for line in (tmp_path / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    events = [e for e in lines if e.get("kind") == "engine" and e.get("event") == "escalation"]
    assert [(e["task"], e["reason"], e["run_id"]) for e in events] == [
        ("T-9101", "lease_expired", stale.run_id)
    ]
    assert all("verdict" not in line for line in lines)


def test_orphaned_sandbox_with_no_state_at_all_is_destroyed(tmp_path):
    runtime = MockRuntime()
    runtime.registry = [
        SandboxInfo(
            id="sbx-x", name="torve-mystery", labels={"torve.task": "T-0000", "torve.run": "gone"}
        )
    ]
    report = reap(tmp_path, RunnerConfig(), runtime, ListingWorkspace([]))
    assert report.sandboxes_destroyed == ["torve-mystery"]


def test_the_reap_keeps_to_its_root(tmp_path):
    # D-3.25 (A-38): on a shared daemon, another engine's sandbox is not
    # ours to judge — found live when the lab's one-minute reap destroyed
    # the dev suite's test containers mid-test. Unlabelled strays predate
    # the amendment and stay reapable by anyone.
    from torve.base import naming

    runtime = MockRuntime()
    runtime.registry = [
        SandboxInfo(
            id="sbx-own",
            name="torve-own-orphan",
            labels={
                "torve.task": "T-0001",
                "torve.run": "gone",
                "torve.root": naming.root_key(tmp_path),
            },
        ),
        SandboxInfo(
            id="sbx-foreign",
            name="torve-foreign",
            labels={"torve.task": "T-0001", "torve.run": "gone", "torve.root": "b" * 12},
        ),
        SandboxInfo(
            id="sbx-legacy",
            name="torve-legacy",
            labels={"torve.task": "T-0002", "torve.run": "gone"},
        ),
    ]
    report = reap(tmp_path, RunnerConfig(), runtime, ListingWorkspace([]))
    assert report.sandboxes_destroyed == ["torve-own-orphan", "torve-legacy"]


def test_labels_carry_the_root_identity(tmp_path):
    from torve.base import naming

    worn = naming.labels("T-0003", "run-1", tmp_path)
    assert worn["torve.root"] == naming.root_key(tmp_path)
    assert naming.root_key(tmp_path) != naming.root_key(tmp_path / "other")


def test_intake_worktree_of_a_live_claim_survives_the_sweep(tmp_path):
    # T-0131: `.intake` is the drafting run's worktree suffix (RFC 0020
    # §5.4) over the same state file a bare task id names — unstripped, a
    # concurrent tick reads it as convention debris and destroys it mid-run.
    state_at(tmp_path, "T-9501", TaskState.CLAIMED)
    workspace = ListingWorkspace([("T-9501.intake", tmp_path / ".wt" / "T-9501.intake")])
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)
    assert report.worktrees_removed == []
    assert workspace.removed == []


def test_intake_worktree_with_no_state_is_swept_as_debris(tmp_path):
    workspace = ListingWorkspace([("T-9502.intake", tmp_path / ".wt" / "T-9502.intake")])
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)
    assert report.worktrees_removed == ["T-9502.intake"]
    assert workspace.removed == ["T-9502.intake"]


def test_worktrees_are_removed_only_for_terminal_or_stateless_tasks(tmp_path):
    state_at(tmp_path, "T-9201", TaskState.READY)
    escalated = state_at(tmp_path, "T-9202", TaskState.RUNNING, age_s=3600)
    workspace = ListingWorkspace(
        [
            ("T-9201", tmp_path / ".wt" / "T-9201"),
            ("T-9202", tmp_path / ".wt" / "T-9202"),  # becomes escalated: kept for triage
            ("T-9203", tmp_path / ".wt" / "T-9203"),  # no state file: convention debris
        ]
    )
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)
    assert sorted(report.worktrees_removed) == ["T-9201", "T-9203"]
    assert escalated.task_id not in report.worktrees_removed


def test_terminal_run_footprint_is_swept_whole(tmp_path):
    # RFC 0003 §4.2: the sweep destroys anything without a live lease — for
    # a terminal run that is the worktree and the state file. The trace is
    # not (D-39.1): it lives in the durable store, which the retention pass
    # alone empties — triage now really does outlive the workspace.
    ready = state_at(tmp_path, "T-9401", TaskState.READY)
    escalated = state_at(tmp_path, "T-9402", TaskState.ESCALATED)
    terminal_trace = put_trace(tmp_path, "T-9401.a1.trace.log")
    escalated_trace = put_trace(tmp_path, "T-9402.a1.trace.log")
    workspace = ListingWorkspace([("T-9401", tmp_path / ".wt" / "T-9401")])

    report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace)

    assert report.worktrees_removed == ["T-9401"]
    assert report.states_removed == ["T-9401"]
    assert not ready.path.exists()
    # The sweep leaves every trace alone, terminal or escalated — inside
    # both retention bounds it touches nothing.
    assert report.traces_removed == []
    assert terminal_trace.is_file()
    assert escalated.path.exists()
    assert escalated_trace.is_file()


# ....................... #
# The store's retention (D-39.3): the reaper's pass is the trace's only
# remover, and it sheds oldest-first past either bound.


def test_retention_sheds_past_keep_days_oldest_first(tmp_path):
    config = RunnerConfig(traces=TracesConfig(keep_days=30, max_mb=512))
    expired = put_trace(tmp_path, "T-9501.a1.trace.log", age_days=31)
    kept = put_trace(tmp_path, "T-9502.a1.trace.log", age_days=29)

    report = reap(tmp_path, config, MockRuntime(), ListingWorkspace([]))

    assert report.traces_removed == [expired.name]
    assert not expired.exists()
    assert kept.is_file()


def test_retention_sheds_past_max_mb_oldest_first(tmp_path):
    # Every trace is young: only the size bound bites. Three 400KB traces
    # against a 1MB cap breach it, and exactly the oldest goes — the two
    # remaining fit, so the pass sheds no further.
    config = RunnerConfig(traces=TracesConfig(keep_days=365, max_mb=1))
    bulk = "x" * 400_000
    oldest = put_trace(tmp_path, "T-9511.a1.trace.log", age_days=3, body=bulk)
    middle = put_trace(tmp_path, "T-9512.a1.trace.log", age_days=2, body=bulk)
    newest = put_trace(tmp_path, "T-9513.a1.trace.log", age_days=1, body=bulk)

    report = reap(tmp_path, config, MockRuntime(), ListingWorkspace([]))

    assert report.traces_removed == [oldest.name]
    assert not oldest.exists()
    assert middle.is_file()
    assert newest.is_file()


def test_retention_touches_nothing_within_both_bounds(tmp_path):
    recent = put_trace(tmp_path, "T-9521.a1.trace.log", age_days=1)

    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]))

    assert report.traces_removed == []
    assert recent.is_file()


def test_retention_reports_candidates_on_a_dry_run_without_deleting(tmp_path):
    config = RunnerConfig(traces=TracesConfig(keep_days=7, max_mb=512))
    expired = put_trace(tmp_path, "T-9531.a1.trace.log", age_days=8)

    report = reap(tmp_path, config, MockRuntime(), ListingWorkspace([]), dry_run=True)

    assert report.traces_removed == [expired.name]
    assert expired.is_file()


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
        + "decisions: []\n",
        encoding="utf-8",
    )


def test_an_unlanded_ready_implement_state_survives_the_sweep(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9110")
    state_at(tmp_path, "T-9110", TaskState.READY)
    report = reap(
        tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), landed=lambda _t: False
    )
    assert report.states_removed == []
    assert (tmp_path / ".wt" / "T-9110.state.json").exists()


def test_a_landed_ready_state_is_swept(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9111")
    state_at(tmp_path, "T-9111", TaskState.READY)
    report = reap(
        tmp_path,
        RunnerConfig(),
        MockRuntime(),
        ListingWorkspace([]),
        landed=lambda t: t == "T-9111",
    )
    assert report.states_removed == ["T-9111"]


def test_a_ready_review_state_stays_sweepable(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9112", role="review")
    state_at(tmp_path, "T-9112", TaskState.READY)
    report = reap(
        tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), landed=lambda _t: False
    )
    assert report.states_removed == ["T-9112"]


def test_without_a_landed_oracle_the_reaper_keeps_conservatively(tmp_path):
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9113")
    state_at(tmp_path, "T-9113", TaskState.READY)
    report = reap(tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]))
    assert report.states_removed == []


def test_a_ready_draft_state_survives_unconditionally(tmp_path):
    # RFC 0020 D-20.10: a draft's landing is adoption — the lab's first
    # live drafting run was swept one tick after green, orphaning it.
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9114", role="draft")
    state_at(tmp_path, "T-9114", TaskState.READY)
    report = reap(
        tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), landed=lambda _t: True
    )
    assert report.states_removed == []
    assert (tmp_path / ".wt" / "T-9114.state.json").exists()


def test_durable_reap_keeps_a_fresh_shadow_sandbox(tmp_path, monkeypatch):
    """D-4.18 (A-57): a shadow run registers no durable record by design, so
    its liveness is the host state file — the durable-regime sweep must not
    destroy a sandbox whose run a fresh-heartbeat state names, and must
    still destroy one no state file knows."""
    import asyncio

    import torve.application.taskstore as taskstore_module
    from torve.application.reaper import _durable_reap

    shadow = state_at(tmp_path, "shadow-T-0001", TaskState.RUNNING)
    stale = state_at(tmp_path, "shadow-T-0002", TaskState.RUNNING, age_s=3600)
    orphan = SandboxInfo(
        id="sbx-orphan", name="torve-orphan", labels={"torve.run": "nobody-knows-this-run"}
    )
    runtime = MockRuntime()
    runtime.registry = [sandbox_for(shadow), sandbox_for(stale), orphan]

    class StubTaskStore:
        def __init__(self, store, config):
            pass

        async def expire_abandoned(self):
            return []

        async def live_records(self):
            return []

    monkeypatch.setattr(taskstore_module, "TaskStore", StubTaskStore)

    async def factory(config):
        return object()

    report = asyncio.run(
        _durable_reap(
            tmp_path, RunnerConfig(), runtime, ListingWorkspace([]), False, False, factory
        )
    )

    destroyed = set(report.sandboxes_destroyed)
    assert f"torve-{shadow.task_id}" not in destroyed
    assert f"torve-{stale.task_id}" in destroyed
    assert "torve-orphan" in destroyed


def test_durable_reap_escalates_active_states_with_no_live_engine_run(tmp_path, monkeypatch):
    """T-0131: the durable-store escalation path only escalated states tied
    to an expired taskstore record, so a state with no durable record at
    all — a stale shadow replay, or a task minted and claimed at
    intake/decompose whose drafting run then died before writing anything
    further — sat in .wt forever, blocking dispatch through the overlap
    gate. One liveness check now covers both: the same set that decides
    which sandboxes survive also decides which states escalate."""
    import asyncio

    import torve.application.taskstore as taskstore_module
    from torve.application.reaper import _durable_reap

    state_at(tmp_path, "shadow-T-0001", TaskState.RUNNING, age_s=3600)
    # A drafting run the engine minted and claimed at intake, whose run then
    # died before it wrote anything further: a state file, no durable
    # record, a stale heartbeat.
    state_at(tmp_path, "T-9601", TaskState.CLAIMED, age_s=3600)
    state_at(tmp_path, "T-9602", TaskState.CLAIMED)

    class StubTaskStore:
        def __init__(self, store, config):
            pass

        async def expire_abandoned(self):
            return []

        async def live_records(self):
            return []

    monkeypatch.setattr(taskstore_module, "TaskStore", StubTaskStore)

    async def factory(config):
        return object()

    report = asyncio.run(
        _durable_reap(
            tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), False, False, factory
        )
    )

    assert sorted(report.runs_expired) == ["T-9601", "shadow-T-0001"]

    reloaded_shadow = RunState.load(tmp_path / ".wt" / "shadow-T-0001.state.json")
    assert reloaded_shadow.state is TaskState.ESCALATED
    assert reloaded_shadow.escalation.reason == "lease_expired"

    reloaded_claim = RunState.load(tmp_path / ".wt" / "T-9601.state.json")
    assert reloaded_claim.state is TaskState.ESCALATED
    assert reloaded_claim.escalation.reason == "lease_expired"

    assert RunState.load(tmp_path / ".wt" / "T-9602.state.json").state is TaskState.CLAIMED


def test_durable_reap_dry_run_predicts_no_live_run_escalation_without_mutating(
    tmp_path, monkeypatch
):
    import asyncio

    import torve.application.taskstore as taskstore_module
    from torve.application.reaper import _durable_reap

    state_at(tmp_path, "T-9603", TaskState.RUNNING, age_s=3600)

    class StubTaskStore:
        def __init__(self, store, config):
            pass

        async def expire_abandoned(self):
            raise AssertionError("a dry run never claims a lease")

        async def live_records(self):
            return []

    monkeypatch.setattr(taskstore_module, "TaskStore", StubTaskStore)

    async def factory(config):
        return object()

    report = asyncio.run(
        _durable_reap(
            tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), False, True, factory
        )
    )

    assert report.runs_expired == ["T-9603"]
    assert RunState.load(tmp_path / ".wt" / "T-9603.state.json").state is TaskState.RUNNING


def test_escalated_states_sweep_only_on_the_flag(tmp_path):
    """A-70: an escalation exists to be looked at — the default sweep keeps
    it; --escalated is the operator's explicit triage-discard."""
    from torve.application.reaper import ReapReport, _sweep_states
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import EscalationReason, TaskState

    (tmp_path / naming.WORKTREE_DIR).mkdir(parents=True, exist_ok=True)
    state = RunState(task_id="T-9100", path=naming.state_file(tmp_path, "T-9100"))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")
    state.save()

    report = ReapReport()
    _sweep_states(tmp_path, [state], report, dry_run=False, landed=None)
    assert report.states_removed == [] and state.path.exists()

    _sweep_states(tmp_path, [state], report, dry_run=False, landed=None, escalated=True)
    assert report.states_removed == ["T-9100"] and not state.path.exists()
