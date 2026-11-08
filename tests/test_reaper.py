from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta

import pytest
from test_run_loop import MockRuntime

from torve.adapters.workspace.git import GitWorkspace
from torve.application.ports import SandboxInfo, judge_ref
from torve.application.reaper import reap
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig, TracesConfig
from torve.domain.states import TaskState


class ListingWorkspace:
    def __init__(self, entries):
        self.entries = entries
        self.removed: list[str] = []
        self.removed_judge: list[str] = []

    def create(self, task_id, base_ref):
        raise AssertionError("reap never creates")

    def remove(self, task_id):
        self.removed.append(task_id)

    def remove_judge(self, task_id):
        self.removed_judge.append(task_id)

    def list_worktrees(self):
        return self.entries


def state_at(tmp_path, task_id, state, age_s=0.0):
    run = RunState(task_id=task_id, path=tmp_path / ".wt" / f"{task_id}.state.json")
    run.state = state
    if age_s:
        stamp = datetime.now(UTC) - timedelta(seconds=age_s)
        run.heartbeat = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
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

    # S-0038/unresolved-questions's open question, decided by the reaper's actual knowledge
    # at sweep time: the sweep escalates through RunState.escalate, so the
    # durable engine event lands (S-0038/D-5) — but the reaper holds no exec
    # result, no gate report, nothing from which a verdict could be
    # *derived*, and S-0038/D-2 forbids inventing one. It stamps no verdict row
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
    # S-0003/D-25 (A-38): on a shared daemon, another engine's sandbox is not
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
    # T-0131: `.intake` is the drafting run's worktree suffix (S-0020
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


def test_the_lane_s_checkouts_survive_every_sweep(tmp_path):
    # A `lane-*` checkout has no state file; a reap during a served night took
    # the one a completion battery was running in, and every later gate errored.
    workspace = ListingWorkspace(
        [
            ("lane-S-0901", tmp_path / ".wt" / "lane-S-0901"),
            ("lane-T-9503", tmp_path / ".wt" / "lane-T-9503"),
        ]
    )

    for escalated in (False, True):
        report = reap(tmp_path, RunnerConfig(), MockRuntime(), workspace, escalated=escalated)
        assert report.worktrees_removed == []

    assert workspace.removed == []


def test_a_left_judge_checkout_and_its_ref_are_swept_a_live_battery_s_kept(tmp_path):
    # S-0114/D-5: a judge checkout has no state file of its own, so its
    # liveness is its task's run state — a live battery's survives, and a
    # killed pass's goes with the rest of the task's footprint.
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")

    workspace = GitWorkspace(root)
    # A killed pass left its checkout behind and no state file: pure debris.
    workspace.create("T-9701", "HEAD")
    dead = workspace.judge_checkout("T-9701")
    # A live battery: the task's run is active with a fresh heartbeat.
    state_at(root, "T-9702", TaskState.RUNNING)
    workspace.create("T-9702", "HEAD")
    live = workspace.judge_checkout("T-9702")

    report = reap(root, RunnerConfig(), MockRuntime(), workspace)

    assert "T-9701.judge" in report.worktrees_removed
    assert not dead.exists()
    assert ref_missing(root, "T-9701")
    assert "T-9702.judge" not in report.worktrees_removed
    assert live.exists()
    assert not ref_missing(root, "T-9702")


def test_a_live_unit_s_candidates_are_kept_and_a_dead_one_s_swept(tmp_path):
    """S-0122/D-7: a unit's later candidate works in `.wt/<task>~<k>`, judged
    in `.wt/<task>~<k>.judge` under a ref git can hold. Both live as long as
    their task's run does."""

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")

    workspace = GitWorkspace(root)
    state_at(root, "T-9703", TaskState.RUNNING)

    for task_id in ("T-9703", "T-9704"):
        workspace.create(task_id, "HEAD")
        workspace.create_candidate(task_id, 2, "")
        workspace.judge_checkout(f"{task_id}~2")

    report = reap(root, RunnerConfig(), MockRuntime(), workspace)

    assert {"T-9704~2", "T-9704~2.judge"} <= set(report.worktrees_removed)
    assert ref_missing(root, "T-9704~2")
    assert not {"T-9703~2", "T-9703~2.judge"} & set(report.worktrees_removed)
    assert (root / ".wt" / "T-9703~2").is_dir()
    assert not ref_missing(root, "T-9703~2")


def ref_missing(root, task_id):
    return (
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", judge_ref(task_id)],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )


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
    # S-0003/reaper: the sweep destroys anything without a live lease — for
    # a terminal run that is the worktree and the state file. The trace is
    # not (S-0039/D-1): it lives in the durable store, which the retention pass
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
# The store's retention (S-0039/D-3): the reaper's pass is the trace's only
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
    # S-0011/command-surface-for-now: --dry-run on anything that mutates.
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


# S-0019/D-10 (A-28, narrowing S-0003/D-23): a READY implement run whose task has
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
    # S-0020 S-0020/D-10: a draft's landing is adoption — the lab's first
    # live drafting run was swept one tick after green, orphaning it.
    (tmp_path / ".torve").mkdir()
    implement_contract(tmp_path, "T-9114", role="draft")
    state_at(tmp_path, "T-9114", TaskState.READY)
    report = reap(
        tmp_path, RunnerConfig(), MockRuntime(), ListingWorkspace([]), landed=lambda _t: True
    )
    assert report.states_removed == []
    assert (tmp_path / ".wt" / "T-9114.state.json").exists()


def test_a_ready_state_whose_contract_lives_in_its_worktree_survives_the_escalated_sweep(
    tmp_path,
):
    # S-0056/D-9: with a store the root has no contract file and the lane reads
    # the projection in the run's worktree. `reap --escalated` swept T-0885's
    # READY state and worktree on 2026-11-07, stranding a landing the lane owed.
    (tmp_path / ".torve").mkdir()
    implement_contract(naming.worktree(tmp_path, "T-9115"), "T-9115")
    state_at(tmp_path, "T-9115", TaskState.READY)
    state_at(tmp_path, "T-9116", TaskState.ESCALATED)
    workspace = ListingWorkspace(
        [
            ("T-9115", naming.worktree(tmp_path, "T-9115")),
            ("T-9116", naming.worktree(tmp_path, "T-9116")),
        ]
    )
    report = reap(
        tmp_path,
        RunnerConfig(),
        MockRuntime(),
        workspace,
        landed=lambda _t: False,
        escalated=True,
    )
    assert report.states_removed == ["T-9116"]
    assert workspace.removed == ["T-9116"]
    assert (tmp_path / ".wt" / "T-9115.state.json").exists()


def test_durable_reap_keeps_a_fresh_shadow_sandbox(tmp_path, monkeypatch):
    """S-0004/D-18 (A-57): a shadow run registers no durable record by design, so
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


def test_recover_reclaims_an_abandoned_durable_run_before_reap_would(tmp_path):
    """S-0042/D-3: the substrate's recovery step is now standalone, callable
    ahead of and independent from the reap sweep — the same claim_abandoned
    + escalate verdict _durable_reap's embedded call always gave."""
    import asyncio
    from datetime import timedelta

    from torve.adapters.store.durable import open_mock_store
    from torve.application.reaper import recover
    from torve.application.taskstore import TaskStore
    from torve.config.runconfig import StoreConfig

    store = open_mock_store()
    store_config = StoreConfig(
        adapter="postgres", lease_for=0.05, heartbeat_divisor=2, max_run_duration=10
    )
    config = RunnerConfig(store=store_config)
    abandoned = state_at(tmp_path, "T-9701", TaskState.RUNNING)

    async def seed():
        async def body(_ctx, _input_json):
            return {}

        taskstore = TaskStore(store, store_config)
        taskstore.register(body)
        record = await taskstore.enqueue({"engine_run_id": abandoned.run_id})
        await taskstore.store.begin(record.run_id, lease_for=timedelta(milliseconds=20))
        await asyncio.sleep(0.05)  # let the lease go stale

    asyncio.run(seed())

    async def factory(_store_config):
        return store

    report = recover(tmp_path, config, factory)

    assert report.runs_expired == ["T-9701"]
    reloaded = RunState.load(tmp_path / ".wt" / "T-9701.state.json")
    assert reloaded.state is TaskState.ESCALATED
    assert reloaded.escalation.reason == "lease_expired"


def test_recover_is_a_true_noop_without_a_postgres_store(tmp_path):
    """S-0003/D-6: the mock regime has no durable lease authority to reclaim —
    recover() must not even call the store factory."""
    from torve.application.reaper import recover

    def factory(_store_config):
        raise AssertionError("no durable store: the factory must not be called")

    report = recover(tmp_path, RunnerConfig(), factory)
    assert report.runs_expired == []


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


def test_the_escalated_sweep_takes_the_worktree_in_the_same_pass(tmp_path):
    """The two halves of a footprint answered differently: --escalated
    dropped the state file and left the worktree, which the *next* reap
    then collected as debris. One pass, or the operator runs it twice and
    the disk keeps 8.8 MB in the meantime (A-112)."""

    from torve.application.reaper import ReapReport, _sweep_worktrees
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import EscalationReason, TaskState

    class Workspace:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def list_worktrees(self):
            return [("T-9101", tmp_path / ".wt" / "T-9101")]

        def remove(self, name: str) -> None:
            self.removed.append(name)

    (tmp_path / naming.WORKTREE_DIR).mkdir(parents=True, exist_ok=True)
    state = RunState(task_id="T-9101", path=naming.state_file(tmp_path, "T-9101"))
    state.transition(TaskState.CLAIMED, "t")
    state.transition(TaskState.RUNNING, "t")
    state.escalate(EscalationReason.KILLED, "operator kill")

    kept = Workspace()
    _sweep_worktrees(kept, {"T-9101": state}, ReapReport(), dry_run=False)
    assert kept.removed == []  # the default sweep keeps it for triage

    swept = Workspace()
    report = ReapReport()
    _sweep_worktrees(swept, {"T-9101": state}, report, dry_run=False, escalated=True)
    assert swept.removed == ["T-9101"] and report.worktrees_removed == ["T-9101"]


def test_the_escalated_flag_reaches_the_durable_path(tmp_path, monkeypatch):
    """`reap` took the flag, matched on the store adapter and forwarded
    every other argument. On postgres — which is what a real run uses —
    `--escalated` was accepted and dropped, so the verb reported a sweep it
    had not been asked for (A-112)."""

    import torve.application.taskstore as taskstore_module
    from torve.application.reaper import reap
    from torve.domain.states import EscalationReason

    class StubTaskStore:
        def __init__(self, store, config):
            pass

        async def expire_abandoned(self):
            return []

        async def live_records(self):
            return []

    monkeypatch.setattr(taskstore_module, "TaskStore", StubTaskStore)
    monkeypatch.setenv("TORVE_PG_DSN", "postgresql://stub/db")

    state = state_at(tmp_path, "T-9701", TaskState.RUNNING)
    state.escalate(EscalationReason.KILLED, "operator kill")

    config = RunnerConfig()
    config.store.adapter = "postgres"

    async def factory(config):
        return object()

    workspace = ListingWorkspace([("T-9701", tmp_path / ".wt" / "T-9701")])
    report = reap(
        tmp_path,
        config,
        MockRuntime(),
        workspace,
        dry_run=True,
        store=factory,
        escalated=True,
    )

    assert report.states_removed == ["T-9701"]
    assert report.worktrees_removed == ["T-9701"]


# S-0094/D-3: `manager resolve --resolution requeued` clears one escalated
# task's run-state file, worktree and sandbox so the requeue it writes next
# never refuses or fails the overlap gate on what its own escalation left
# behind.


def test_clear_escalated_takes_the_state_worktree_and_sandbox(tmp_path):
    from torve.application.reaper import clear_escalated
    from torve.domain.states import EscalationReason

    state = state_at(tmp_path, "T-9801", TaskState.RUNNING)
    state.sandbox_id = "sbx-T-9801"
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")

    runtime = MockRuntime()
    runtime.registry = [sandbox_for(state)]
    workspace = ListingWorkspace([("T-9801", tmp_path / ".wt" / "T-9801")])

    assert clear_escalated(tmp_path, runtime, workspace, "T-9801") is True

    assert runtime.destroyed == ["sbx-T-9801"]
    assert workspace.removed == ["T-9801"]
    assert not state.path.exists()


def test_clear_escalated_takes_a_nested_sandbox_s_side_container(tmp_path):
    """T-0877 (S-0111/D-2): the run state names the nested sandbox's own id,
    never the `-dockerd` side container, though the side carries the same
    labels. Clearing only the recorded ids left the daemon running after the
    state and worktree were deleted; the side is found by its run label."""
    from torve.application.reaper import clear_escalated
    from torve.domain.states import EscalationReason

    state = state_at(tmp_path, "T-9807", TaskState.RUNNING)
    state.sandboxes = ["sbx-main"]
    state.sandbox_id = "sbx-main"
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")

    labels = naming.labels("T-9807", state.run_id, tmp_path)
    runtime = MockRuntime()
    runtime.registry = [
        SandboxInfo(id="sbx-main", name="torve-T-9807", labels=labels),
        SandboxInfo(id="sbx-side", name="torve-T-9807-dockerd", labels=labels),
        # Another engine's nested side container is not ours to judge.
        SandboxInfo(
            id="sbx-foreign",
            name="torve-T-9807-dockerd",
            labels=naming.labels("T-9807", state.run_id, tmp_path / "other"),
        ),
    ]
    workspace = ListingWorkspace([("T-9807", tmp_path / ".wt" / "T-9807")])

    assert clear_escalated(tmp_path, runtime, workspace, "T-9807") is True

    assert runtime.destroyed == ["sbx-main", "sbx-side"]
    assert workspace.removed == ["T-9807"]
    assert not state.path.exists()


def test_a_stopped_run_s_work_is_kept_on_its_branch_for_the_next_dispatch(tmp_path):
    # A night stopped mid-attempt left T-0811's gated tree only in its
    # worktree, and the requeue deleted it; the next dispatch started over.
    from types import SimpleNamespace

    from torve.application.reaper import clear_escalated
    from torve.application.runner import _checkpointed_for_resume
    from torve.domain.states import EscalationReason

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q", "-b", "main")
    (tmp_path / "seed").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "seed")
    workspace = GitWorkspace(tmp_path)

    for task_id, reason, detail in (
        ("T-9810", EscalationReason.KILLED, "stopped"),
        ("T-9811", EscalationReason.POISON_CEILING, "stopped"),
        # T-0904: an engine before the cap's own checkpoint left the spent
        # unit's green tree uncommitted.
        (
            "T-9813",
            EscalationReason.BUDGET_EXHAUSTED,
            "unit budget exhausted: $3.57 spent of $3.00",
        ),
        ("T-9814", EscalationReason.BUDGET_EXHAUSTED, "3 attempts, budget 3"),
    ):
        worktree = workspace.create(task_id, "main")
        (worktree / "work.py").write_text("done = True\n", encoding="utf-8")
        state = state_at(tmp_path, task_id, TaskState.RUNNING)
        state.escalate(reason, detail)

        assert clear_escalated(tmp_path, MockRuntime(), workspace, task_id) is True
        assert not worktree.exists()

    for kept in ("T-9810", "T-9813"):
        assert git("show", f"torve/{kept}:work.py") == "done = True\n"
        assert _checkpointed_for_resume(tmp_path, SimpleNamespace(id=kept))

    # A judged escalation keeps its old rule: its tree is not continued.
    for judged in ("T-9811", "T-9814"):
        assert git("rev-parse", f"torve/{judged}") == git("rev-parse", "main")
        assert not _checkpointed_for_resume(tmp_path, SimpleNamespace(id=judged))


def test_a_stopped_run_with_nothing_new_adds_no_checkpoint(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q", "-b", "main")
    (tmp_path / "seed").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "seed")
    workspace = GitWorkspace(tmp_path)
    workspace.create("T-9812", "main")

    assert workspace.checkpoint("T-9812", "torve(T-9812): nothing") is False
    assert workspace.checkpoint("T-9899", "torve(T-9899): no worktree") is False
    assert git("rev-parse", "torve/T-9812") == git("rev-parse", "main")


@pytest.mark.parametrize(
    "active_state",
    [TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED],
)
def test_requeue_clears_an_active_state_with_no_live_run(tmp_path, active_state):
    """S-0125/D-5: a serve that died mid-run leaves its state file claimed,
    running, gated or reviewed — not escalated — and every next dispatch
    refused on it. A requeue clears it once no live engine run holds it,
    committing the tree as stopped work so the next attempt continues it."""

    from types import SimpleNamespace

    from torve.application.reaper import clear_escalated
    from torve.application.runner import _checkpointed_for_resume

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout

    task_id = "T-9821"
    git("init", "-q", "-b", "main")
    (tmp_path / "seed").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "seed")
    workspace = GitWorkspace(tmp_path)
    worktree = workspace.create(task_id, "main")
    (worktree / "work.py").write_text("done = True\n", encoding="utf-8")
    # The heartbeat is the reaper's host liveness: a dead run's goes stale.
    state = state_at(tmp_path, task_id, active_state, age_s=3600)

    assert clear_escalated(tmp_path, MockRuntime(), workspace, task_id) is True

    assert not worktree.exists()
    assert not state.path.exists()
    assert git("show", f"torve/{task_id}:work.py") == "done = True\n"
    assert _checkpointed_for_resume(tmp_path, SimpleNamespace(id=task_id))


def test_the_live_runs_a_requeue_reads_span_every_page(monkeypatch):
    """S-0121/D-19: the listing is keyset-paged, and a live run past its
    first page is still live; a requeue that read one page would clear the
    worktree of a run it could not see."""
    import asyncio
    from types import SimpleNamespace

    import torve.application.taskstore as taskstore
    from torve.adapters.store.durable import open_mock_store
    from torve.config.runconfig import StoreConfig

    runs = [SimpleNamespace(run_id=f"run-{number}") for number in range(450)]
    cursors: list[str | None] = []

    class Admin:
        async def list_runs(self, *, status, limit, cursor=None):
            cursors.append(cursor)
            start = int(cursor or 0)
            end = start + limit
            return SimpleNamespace(
                records=runs[start:end], next_cursor=str(end) if end < len(runs) else None
            )

    monkeypatch.setattr(taskstore, "resolve_durable_run_admin", lambda _ctx: Admin())
    store = taskstore.TaskStore(open_mock_store(), StoreConfig())

    live = asyncio.run(store.live_records())

    assert [record.run_id for record in live] == [run.run_id for run in runs]
    assert cursors == [None, "200", "400"]


def test_requeue_clears_leaves_a_live_active_run_alone(tmp_path):
    """S-0125/D-5: a fresh heartbeat is a live engine run holding the task —
    the requeue is not the one that clears it."""

    from torve.application.reaper import clear_escalated

    state = state_at(tmp_path, "T-9822", TaskState.RUNNING)
    workspace = ListingWorkspace([("T-9822", tmp_path / ".wt" / "T-9822")])

    assert clear_escalated(tmp_path, MockRuntime(), workspace, "T-9822") is False

    assert state.path.exists()
    assert workspace.removed == []


def _seeded(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q", "-b", "main")
    (tmp_path / "seed").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "seed")
    return git


def test_requeue_leaves_a_run_whose_durable_lease_is_live(tmp_path):
    """S-0125/D-5, S-0121/D-19: a stale host heartbeat with a live durable
    lease is a run still executing; only once the store no longer holds it
    is its footprint cleared."""

    from torve.application.reaper import clear_escalated

    state = state_at(tmp_path, "T-9823", TaskState.RUNNING, age_s=3600)
    state.durable_run_id = "dr-1"
    state.save()
    workspace = ListingWorkspace([("T-9823", tmp_path / ".wt" / "T-9823")])

    assert clear_escalated(tmp_path, MockRuntime(), workspace, "T-9823", live={"dr-1"}) is False
    assert state.path.exists()
    assert workspace.removed == []

    assert clear_escalated(tmp_path, MockRuntime(), workspace, "T-9823", live=set()) is True
    assert workspace.removed == ["T-9823"]


def test_requeue_refuses_a_detached_worktree_it_cannot_checkpoint(tmp_path):
    """S-0125/D-5: a detached worktree's uncommitted work cannot be committed
    on its branch, so the cleanup refuses rather than delete it."""

    from torve.application.reaper import clear_escalated

    git = _seeded(tmp_path)
    git("branch", "torve/T-9824")
    worktree = naming.worktree(tmp_path, "T-9824")
    git("worktree", "add", "--detach", str(worktree), "main")
    (worktree / "work.py").write_text("keep\n", encoding="utf-8")
    state = state_at(tmp_path, "T-9824", TaskState.RUNNING, age_s=3600)

    assert clear_escalated(tmp_path, MockRuntime(), GitWorkspace(tmp_path), "T-9824") is False
    assert (worktree / "work.py").read_text(encoding="utf-8") == "keep\n"
    assert state.path.exists()


def test_requeue_refuses_a_detached_worktree_holding_an_unreferenced_commit(tmp_path):
    """S-0125/D-5: a detached worktree can be clean and still hold the only
    copy of a commit `checkpoint` cannot put on its branch; the cleanup
    refuses rather than leave that work nameless."""

    from torve.application.reaper import clear_escalated

    git = _seeded(tmp_path)
    git("branch", "torve/T-9826")
    worktree = naming.worktree(tmp_path, "T-9826")
    git("worktree", "add", "--detach", str(worktree), "main")
    (worktree / "work.py").write_text("keep\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(worktree), "add", "-A"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--no-gpg-sign",
            "-m",
            "the work",
        ],
        check=True,
        capture_output=True,
    )
    state = state_at(tmp_path, "T-9826", TaskState.RUNNING, age_s=3600)

    assert clear_escalated(tmp_path, MockRuntime(), GitWorkspace(tmp_path), "T-9826") is False
    assert (worktree / "work.py").read_text(encoding="utf-8") == "keep\n"
    assert state.path.exists()


def test_a_durable_timeout_restating_killed_still_checkpoints(tmp_path):
    """S-0125/D-6: the worker restates a cancel's `killed` as
    `budget_exhausted`; the requeue still reads it as a stopped run and
    commits its tree before removal."""

    from torve.application.reaper import clear_escalated
    from torve.domain.states import EscalationReason

    git = _seeded(tmp_path)
    workspace = GitWorkspace(tmp_path)
    worktree = workspace.create("T-9825", "main")
    (worktree / "work.py").write_text("keep\n", encoding="utf-8")
    state = state_at(tmp_path, "T-9825", TaskState.RUNNING)
    state.escalate(EscalationReason.KILLED, "cancelled")
    state.restate_escalation(EscalationReason.BUDGET_EXHAUSTED, "durable run timed out after 10s")

    assert clear_escalated(tmp_path, MockRuntime(), workspace, "T-9825") is True
    assert not worktree.exists()
    assert git("show", "torve/T-9825:work.py") == "keep\n"


def test_clear_escalated_leaves_a_non_escalated_task_alone(tmp_path):
    from torve.application.reaper import clear_escalated

    state = state_at(tmp_path, "T-9802", TaskState.RUNNING)
    runtime = MockRuntime()
    workspace = ListingWorkspace([])

    assert clear_escalated(tmp_path, runtime, workspace, "T-9802") is False

    assert runtime.destroyed == []
    assert workspace.removed == []
    assert state.path.exists()


def test_clear_escalated_with_no_state_file_at_all_is_a_true_noop(tmp_path):
    from torve.application.reaper import clear_escalated

    runtime = MockRuntime()
    workspace = ListingWorkspace([])

    assert clear_escalated(tmp_path, runtime, workspace, "T-9803") is False

    assert runtime.destroyed == []
    assert workspace.removed == []


# A returned candidate's run-state file goes back to queued, so the lane, which
# lands from the ready files, does not land what the operator sent back.


def test_requeue_returned_moves_a_ready_file_to_queued(tmp_path):
    from torve.application.reaper import requeue_returned

    state = state_at(tmp_path, "T-9804", TaskState.READY)

    assert requeue_returned(tmp_path, "T-9804", "gamed a gate") is True

    reloaded = RunState.load(state.path)
    assert reloaded.state is TaskState.QUEUED
    assert reloaded.history[-1]["fact"] == "returned by an operator: gamed a gate"


def test_requeue_returned_leaves_other_states_and_missing_files_alone(tmp_path):
    from torve.application.reaper import requeue_returned

    state = state_at(tmp_path, "T-9805", TaskState.RUNNING)

    assert requeue_returned(tmp_path, "T-9805", "too early") is False
    assert RunState.load(state.path).state is TaskState.RUNNING
    assert requeue_returned(tmp_path, "T-9806", "no file") is False


def test_the_lane_lock_is_waited_for_while_a_pass_holds_it(tmp_path):
    """A return made while a lane pass runs waits for the pass, so the pass
    never lands from a ready file read before the return."""
    import threading

    from torve.adapters.vcs.git import GitLane
    from torve.application.lane import lane_held, process_lane

    entered = threading.Event()

    def second() -> None:
        with lane_held(tmp_path):
            entered.set()

    with lane_held(tmp_path):
        assert [r.action for r in process_lane(tmp_path, GitLane())] == ["lane busy"]
        waiter = threading.Thread(target=second)
        waiter.start()
        assert not entered.wait(0.2)

    assert entered.wait(5)
    waiter.join()
