"""The run loop over mock ports (RFC 0003 §6 layer 2, minus the sandbox):
every escalation reason the loop can produce, the retry path, and the facts
recorded on the way. Gate outcomes are scripted by patching the gate pass —
the real gate integration is exercised in test_run_integration.py.
"""

from __future__ import annotations

import pytest
import yaml

import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.application.ports import AgentResult, ExecResult, SandboxHandle, SandboxInfo
from torve.application.runner import BlockedDispatch, RunDeps, run_task
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig, TierConfig
from torve.domain.states import TaskState
from torve.domain.task import Budget, Scope, Task


class MockRuntime:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.destroyed: list[str] = []
        self.registry: list[SandboxInfo] = []

    def create(self, spec, workspace):
        self.created.append(spec.name)
        info = SandboxInfo(id=f"sbx-{len(self.created)}", name=spec.name, labels=spec.labels)
        self.registry.append(info)
        return SandboxHandle(id=info.id, name=spec.name)

    def exec(self, handle, command, timeout_s):
        return ExecResult(exit_code=0, output="", duration_s=0.0)

    def sync_out(self, handle, workspace):
        pass

    def destroy(self, handle):
        self.destroyed.append(handle.id)
        self.registry = [i for i in self.registry if i.id != handle.id]

    def destroy_by_id(self, sandbox_id):
        self.destroyed.append(sandbox_id)
        self.registry = [i for i in self.registry if i.id != sandbox_id]

    def list_torve_sandboxes(self):
        return list(self.registry)

    def resolve_image(self, image):
        # A stable fake identity: derived from the reference so tier-image
        # tests can tell two images apart, never a real digest.
        return f"sha256:mock-{image}"

    def build_image(self, context, tag):
        return self.resolve_image(tag)


class MockWorkspace:
    def __init__(self, root):
        self.root = root
        self.resumed: list[str] = []

    def create(self, task_id, base_ref, *, resume=False):
        if resume:
            self.resumed.append(task_id)

        path = self.root / ".wt" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove(self, task_id):
        pass

    def list_worktrees(self):
        return []


class ScriptedAgent:
    """AgentResults per attempt; optionally writes a halted log entry."""

    def __init__(self, results, halted_on_attempt=None):
        self.results = results
        self.halted_on_attempt = halted_on_attempt

    def run(self, ctx):
        if self.halted_on_attempt == ctx.attempt:
            log_dir = ctx.workspace / ".torve" / "tasks" / ctx.task.id
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "log.yaml").write_text(
                "schema_version: 1\ntask: " + ctx.task.id + "\ndrift_count: 0\n"
                "entries:\n  - decision: D-1\n    grade: LOCKED\n"
                "    kind: contradicted\n    action: halted\n",
                encoding="utf-8",
            )
        return self.results[min(ctx.attempt - 1, len(self.results) - 1)]


class MockVcs:
    def __init__(self):
        self.commits: list[str] = []
        self.authors: list[str | None] = []
        self.pushed: list[str] = []

    def commit_all(self, worktree, message, author=None, sign_key=None):
        self.commits.append(message)
        self.authors.append(author)
        return "abcdef123456"

    def changed_names(self, worktree):
        return []

    def push(self, worktree, branch, token=None, supersede=False):
        self.pushed.append(branch)
        return False

    def landed_shas(self, worktree, task_id):
        return []

    def revert(self, worktree, shas):
        return True


class MockScm:
    def open_pr(self, worktree, branch, title, body):
        raise AssertionError("PR leg is deferred; nothing should call the forge")


OK = AgentResult(exit_code=0, output="")
CRASH = AgentResult(exit_code=137, output="died mid-write")
TIMEOUT = AgentResult(exit_code=None, output="hard timeout")


def task_for(repo, iterations=None):
    return Task(id="T-9001", scope=Scope(), decisions=[], budget=Budget(iterations=iterations))


def _active_run(root, task_id: str, *, role: str, allow: list[str]) -> RunState:
    """Seed an in-flight run's contract and state file — the fence
    `_blocking_overlap` refuses dispatches against. A review contract
    carries targets, the shape the role's validator demands."""

    contract = {
        "schema_version": 1,
        "id": task_id,
        "role": role,
        "scope": {"allow": allow, "deny": []},
        "acceptance": [],
        "decisions": [],
    }

    if role == "review":
        contract["targets"] = ["T-9000"]

    contract_dir = root / ".torve" / "tasks" / task_id
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")

    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "seeded active run")
    state.transition(TaskState.RUNNING, "seeded active run")
    state.save()
    return state


@pytest.fixture
def rig(repo, monkeypatch):
    repo.seed()  # a real git repo so base resolution works
    runtime, vcs = MockRuntime(), MockVcs()
    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=runtime,
        agent=ScriptedAgent([OK]),
        vcs=vcs,
        scm=MockScm(),
        store=open_store,
    )
    gate_outcomes: list[int] = []

    def scripted_gates(*args, **kwargs):
        code = gate_outcomes.pop(0) if gate_outcomes else 0
        return code, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)
    return repo, deps, runtime, vcs, gate_outcomes


def test_clean_success(rig):
    repo, deps, runtime, vcs, _ = rig
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert state.state is TaskState.READY
    assert state.attempts == 1
    assert len(vcs.commits) == 1
    assert "Torve-Task: T-9001" in vcs.commits[0]
    assert runtime.created and runtime.destroyed  # every sandbox died
    assert "pr deferred" in state.history[-1]["fact"]


def test_gate_failure_then_success(rig):
    repo, deps, _runtime, _vcs, gate_outcomes = rig
    gate_outcomes += [1, 0]
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert state.state is TaskState.READY
    assert state.attempts == 2
    facts = [event["fact"] for event in state.history]
    assert any("gates red" in fact for fact in facts)


def test_poison_ceiling_never_retries_past_the_ceiling(rig):
    repo, deps, runtime, _, _ = rig
    deps.agent = ScriptedAgent([CRASH])
    state = run_task(repo.root, task_for(repo), RunnerConfig(poison_ceiling=3), deps)
    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "poison_ceiling"
    assert state.attempts == 3
    assert len(runtime.created) == 3


def test_iteration_budget_escalates_as_budget_exhausted(rig):
    repo, deps, _, vcs, _ = rig
    deps.agent = ScriptedAgent([CRASH])
    state = run_task(repo.root, task_for(repo, iterations=1), RunnerConfig(), deps)
    assert state.escalation.reason == "budget_exhausted"
    assert state.attempts == 1
    # D-26.8: attempt-count exhaustion is judgement on the work (repeated
    # crashes), not a clock running out — no continuation checkpoint.
    assert not vcs.commits


def test_wallclock_budget_escalates_and_checkpoints(rig):
    repo, deps, _runtime, vcs, _ = rig
    task = Task(id="T-9001", scope=Scope(), decisions=[], budget=Budget(wallclock_minutes=0))

    state = run_task(repo.root, task, RunnerConfig(), deps)

    assert state.escalation.reason == "budget_exhausted"
    assert state.escalation.detail.startswith("wallclock budget exhausted")
    assert state.attempts == 0  # exhausted before the first dispatch
    # D-26.9: the checkpoint is what gives a continuation a candidate tip.
    assert len(vcs.commits) == 1
    assert vcs.commits[0].startswith(f"torve checkpoint {task.id}:")
    assert f"Torve-Checkpoint: {task.id}" in vcs.commits[0]
    assert "Torve-Task:" not in vcs.commits[0]  # never mistaken for a landing


def test_cost_anomaly_is_continuable_iterations_budget_is_not():
    # Pure unit coverage of the eligibility rule itself (D-26.8): tokens
    # continue, attempt-count exhaustion never does.
    from torve.application.runstate import Escalation

    assert run_module._continuable(Escalation(reason="cost_anomaly", detail="broker refused"))
    assert not run_module._continuable(
        Escalation(reason="budget_exhausted", detail="3 attempts, budget 3")
    )
    assert run_module._continuable(
        Escalation(reason="budget_exhausted", detail="wallclock budget exhausted: 5.0m elapsed")
    )


def test_should_resume_ignores_a_stale_escalation_after_a_non_escalation_requeue():
    # D-26.9: `escalation` is never cleared, so a run that escalated on
    # budget exhaustion long ago, landed READY, and was later auto-requeued
    # by the lane over a conflict (READY -> QUEUED, never touching
    # ESCALATED again) must not resume from that dead episode.
    from pathlib import Path

    from torve.application.runstate import Escalation, RunState

    state = RunState(task_id="T-1", path=Path("/tmp/unused"))
    state.state = TaskState.QUEUED
    state.history = [
        {"at": "t0", "from": "queued", "to": "claimed"},
        {"at": "t1", "from": "claimed", "to": "escalated"},
        {"at": "t2", "from": "escalated", "to": "queued"},
        {"at": "t3", "from": "queued", "to": "claimed"},
        {"at": "t4", "from": "claimed", "to": "running"},
        {"at": "t5", "from": "running", "to": "ready"},
        {"at": "t6", "from": "ready", "to": "queued"},  # lane conflict auto-requeue
    ]
    state.escalation = Escalation(
        reason="budget_exhausted", detail="wallclock budget exhausted: 5.0m elapsed"
    )

    assert not run_module._should_resume(state)


def test_should_resume_true_straight_off_a_continuable_escalation():
    from pathlib import Path

    from torve.application.runstate import Escalation, RunState

    state = RunState(task_id="T-1", path=Path("/tmp/unused"))
    state.state = TaskState.QUEUED
    state.history = [
        {"at": "t0", "from": "queued", "to": "claimed"},
        {"at": "t1", "from": "claimed", "to": "escalated"},
        {"at": "t2", "from": "escalated", "to": "queued"},
    ]
    state.escalation = Escalation(
        reason="budget_exhausted", detail="wallclock budget exhausted: 5.0m elapsed"
    )

    assert run_module._should_resume(state)

    state.escalation = Escalation(reason="locked_conflict", detail="halted divergence entry")

    assert not run_module._should_resume(state)


def test_continuation_resumes_the_worktree_and_tells_the_agent(rig, monkeypatch):
    repo, deps, _runtime, _vcs, _ = rig
    task = Task(id="T-9001", scope=Scope(), decisions=[], budget=Budget(wallclock_minutes=5))
    # The budget check is exhausted on the first run only — a second,
    # independent dispatch (the retry) must not trip it again from its own
    # fresh clock.
    monkeypatch.setattr(run_module, "_elapsed_minutes", lambda _state: 999.0)

    first = run_task(repo.root, task, RunnerConfig(), deps)
    assert first.state is TaskState.ESCALATED
    monkeypatch.setattr(run_module, "_elapsed_minutes", lambda _state: 0.0)

    from torve.application.runstate import RunState

    state_path = repo.root / ".wt" / f"{task.id}.state.json"
    requeued = RunState.load(state_path)
    requeued.transition(TaskState.QUEUED, "tracker command retry from operator")
    requeued.save()

    seen = {}

    class PeekingAgent:
        kind = "fake"

        def run(self, ctx):
            seen["resume"] = ctx.resume
            return OK

    deps.agent = PeekingAgent()

    second = run_task(repo.root, task, RunnerConfig(), deps)

    assert second.state is TaskState.READY
    assert seen["resume"] is True
    assert task.id in deps.workspace.resumed


def test_a_convicted_retry_does_not_resume(rig):
    repo, deps, _runtime, _vcs, _ = rig
    task = task_for(repo)
    deps.agent = ScriptedAgent([OK], halted_on_attempt=1)

    first = run_task(repo.root, task, RunnerConfig(), deps)
    assert first.escalation.reason == "locked_conflict"

    from torve.application.runstate import RunState

    state_path = repo.root / ".wt" / f"{task.id}.state.json"
    requeued = RunState.load(state_path)
    requeued.transition(TaskState.QUEUED, "tracker command retry from operator")
    requeued.save()
    # A real worktree recreation would clear the halted entry the first
    # attempt wrote; the mock never wipes the directory, so clear it by
    # hand — this test's only concern is whether `resume` was computed
    # false and threaded through, not the halted-detection path.
    (repo.root / ".wt" / task.id / ".torve" / "tasks" / task.id / "log.yaml").unlink()

    seen = {}

    class PeekingAgent:
        kind = "fake"

        def run(self, ctx):
            seen["resume"] = ctx.resume
            return OK

    deps.agent = PeekingAgent()

    run_task(repo.root, task, RunnerConfig(), deps)

    assert seen["resume"] is False
    assert task.id not in deps.workspace.resumed


def test_agent_timeout_is_a_failed_attempt_not_a_crash(rig):
    repo, deps, _, _, _ = rig
    deps.agent = ScriptedAgent([TIMEOUT, OK])
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert state.state is TaskState.READY
    assert state.attempts == 2
    assert any("hard timeout" in event["fact"] for event in state.history)


def test_locked_conflict_is_terminal_by_design(rig):
    repo, deps, _runtime, vcs, _ = rig
    deps.agent = ScriptedAgent([OK], halted_on_attempt=1)
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "locked_conflict"
    assert not vcs.commits  # stopped on working code, nothing landed


def test_gate_infrastructure_failure_escalates(rig, monkeypatch):
    repo, deps, _, _, _ = rig

    def broken_gates(*args, **kwargs):
        raise OSError("gate machinery down")

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", broken_gates)
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert state.escalation.reason == "gate_infrastructure_failure"


def test_existing_non_terminal_run_refuses_a_second_claim(rig):
    repo, deps, _, _, gate_outcomes = rig
    gate_outcomes += [1, 1, 1]
    first = run_task(repo.root, task_for(repo), RunnerConfig(), deps)
    assert first.state is TaskState.ESCALATED
    with pytest.raises(RuntimeError, match="existing run"):
        run_task(repo.root, task_for(repo), RunnerConfig(), deps)


def test_an_active_review_run_blocks_no_dispatch(rig):
    """T-0180, the incident's shape: a running review-role task carries an
    empty allow-set (it writes nothing by construction) — the overlap gate
    must not read that as "unconstrained scope" and refuse an unrelated
    dispatch against it."""
    repo, deps, _runtime, _vcs, _ = rig
    _active_run(repo.root, "T-8001", role="review", allow=[])
    task = Task(id="T-8002", scope=Scope(allow=["src/app/**"]), decisions=[])
    # Dispatch proceeds (no BlockedDispatch): the review claims no fence.
    state = run_task(repo.root, task, RunnerConfig(), deps)
    assert state.state is TaskState.READY


def test_the_review_exemption_keys_on_the_role_not_on_the_emptiness(rig):
    """Even a review contract that carried a non-empty allow-set (which the
    contract shape does not produce) holds no fence: the exemption is
    role-keyed, so it can never depend on scope.allow being empty."""
    repo, deps, _runtime, _vcs, _ = rig
    _active_run(repo.root, "T-8001", role="review", allow=["src/app/**"])
    task = Task(id="T-8002", scope=Scope(allow=["src/app/**"]), decisions=[])
    state = run_task(repo.root, task, RunnerConfig(), deps)
    assert state.state is TaskState.READY


def test_an_empty_allow_on_a_writing_role_is_still_unconstrained(rig):
    """The exemption keys on the role, not on the emptiness: an empty
    allow-set on an implement task keeps the conservative reading — the
    active run claims the whole tree, so an unrelated dispatch is refused."""
    repo, deps, _runtime, _vcs, _ = rig
    _active_run(repo.root, "T-8001", role="implement", allow=[])
    task = Task(id="T-8002", scope=Scope(allow=["docs/**"]), decisions=[])
    with pytest.raises(BlockedDispatch, match="unconstrained"):
        run_task(repo.root, task, RunnerConfig(), deps)


def test_a_review_dispatch_holds_no_fence_against_active_writers(rig):
    """A review task being dispatched is excluded from overlap fencing
    entirely: it writes nothing, so no active writer's fence can conflict
    with it — the review proceeds while the writer's run is in flight."""
    repo, deps, _runtime, _vcs, _ = rig
    _active_run(repo.root, "T-8001", role="implement", allow=["src/app/**"])
    review = Task(
        id="T-8002",
        role="review",
        targets=["T-8001"],
        scope=Scope(),
        decisions=[],
    )
    state = run_task(repo.root, review, RunnerConfig(), deps)
    assert state.state is TaskState.READY


def test_revision_record_feeds_the_agent_never_the_gates(rig, monkeypatch):
    # D-5.13 (T-0076): the planted feedback record is for the agent's eyes
    # only — present in the worktree while the attempt runs, gone before the
    # gates measure the tree. A record the scope gate could see would fail
    # every revision against its own contract, and a record that survived to
    # the commit would land untrusted review text on the base.
    from torve.application.feedback import capture_feedback

    repo, deps, _runtime, _vcs, _ = rig
    task = task_for(repo)
    assert capture_feedback(repo.root, task.id, "diff --git a/f b/f", [])
    seen: dict[str, bool] = {}

    class PeekingAgent:
        def run(self, ctx):
            seen["during_attempt"] = (ctx.workspace / ".torve" / "feedback.md").is_file()
            return OK

    def peeking_gates(worktree, *args, **kwargs):
        seen["at_gates"] = (worktree / ".torve" / "feedback.md").is_file()
        return 0, "scripted", "cafecafe1234", [], ""

    deps.agent = PeekingAgent()
    monkeypatch.setattr(run_module, "_run_gates_in_worktree", peeking_gates)
    state = run_task(repo.root, task, RunnerConfig(), deps)

    assert state.state is TaskState.READY
    assert seen["during_attempt"] is True
    assert seen["at_gates"] is False
    assert not (repo.root / ".wt" / task.id / ".torve" / "feedback.md").exists()


def test_retry_variant_resolves_after_a_gate_red_and_stamps_its_own_tier(rig, monkeypatch):
    """RFC 0027 §5.1a, D-27.11: the attempt after a gate-red resolves the
    named retry_variant, not the tier that just ran; each attempt's telemetry
    row stamps the tier actually dispatched under, and the retry_variant's
    Agent is only ever built through the wired factory — never fabricated."""
    repo, deps, _runtime, _vcs, gate_outcomes = rig
    gate_outcomes += [1, 0]

    build_tier = TierConfig(
        adapter="api", command="x", provider="p", model="build-model",
        retry_variant="executor.fast",
    )
    fast_tier = TierConfig(adapter="api", command="x", provider="p", model="fast-model")
    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": build_tier,
            "executor.fast": fast_tier,
        }
    )

    seen_agent_metas: list[dict] = []

    def scripted_gates(*args, **kwargs):
        seen_agent_metas.append(dict(args[6]))
        code = gate_outcomes.pop(0) if gate_outcomes else 0
        return code, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

    class BuildAgent:
        def run(self, ctx):
            return OK

    class FastAgent:
        def run(self, ctx):
            return OK

    factory_calls: list[str] = []

    def retry_agent(tier):
        factory_calls.append(tier.model)
        return FastAgent()

    deps.agent = BuildAgent()
    deps.retry_agent = retry_agent

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    assert state.attempts == 2
    # The retry_variant's Agent is built exactly once, for the second attempt.
    assert factory_calls == ["fast-model"]
    assert [m["tier"] for m in seen_agent_metas] == ["executor", "executor.fast"]
    assert [m["model"] for m in seen_agent_metas] == ["build-model", "fast-model"]


def test_no_retry_agent_wired_means_retry_variant_never_fires(rig):
    """D-27.11: a configured retry_variant with no factory wired (dispatch
    never built one — tests, or a CLI path that didn't ask) must not
    fabricate an Agent; every attempt keeps running under the task's own
    tier, today's behaviour."""
    repo, deps, _runtime, _vcs, gate_outcomes = rig
    gate_outcomes += [1, 0]

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(retry_variant="executor.fast"),
            "executor.fast": TierConfig(),
        }
    )
    deps.retry_agent = None

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    assert state.attempts == 2


def test_worktree_config_edits_never_reach_dispatch(rig, monkeypatch):
    """RFC 0027 §5.5, D-27.2: sandbox definitions and tier blocks resolve
    from the root at dispatch, never from the worktree under work. An agent
    that plants a hostile `.torve/config.yaml` in its own worktree — the one
    channel it can write to — must not steer the retry it hands off to: the
    regime the next attempt runs under is provably the root's, not anything
    the run's own content wrote."""
    repo, deps, _runtime, _vcs, gate_outcomes = rig
    gate_outcomes += [1, 0]

    root_tier = TierConfig(adapter="api", command="x", provider="p", model="root-executor")
    config = RunnerConfig(
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": root_tier}
    )

    seen_agent_metas: list[dict] = []

    def scripted_gates(*args, **kwargs):
        seen_agent_metas.append(dict(args[6]))
        code = gate_outcomes.pop(0) if gate_outcomes else 0
        return code, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

    class HostileAgent:
        def run(self, ctx):
            # The one act available to the agent: writing into its own
            # worktree. A hostile edit here must never be read back by the
            # engine — the whole point of D-27.2's refusal.
            hostile = ctx.workspace / ".torve" / "config.yaml"
            hostile.parent.mkdir(parents=True, exist_ok=True)
            hostile.write_text(
                "schema_version: 1\ntiers:\n  executor: {model: HOSTILE}\n", encoding="utf-8"
            )
            return OK

    deps.agent = HostileAgent()

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    assert state.attempts == 2
    # Both attempts — the one before and the one after the hostile write —
    # were dispatched under the root's tier, never the worktree's.
    assert [m["model"] for m in seen_agent_metas] == ["root-executor", "root-executor"]
    assert "HOSTILE" not in str(seen_agent_metas)


def test_no_forge_leg_means_no_push(rig):
    """D-10.11 (A-58): with open_pr off the candidate branch stays local —
    a push is publishing, and on a base never pushed it publishes the
    whole history. The run still commits and reaches ready."""
    repo, deps, _runtime, vcs, _ = rig
    config = RunnerConfig()
    assert config.scm.open_pr is False  # the default regime under test

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    assert vcs.commits, "the candidate still commits locally"
    assert vcs.pushed == []


def test_an_empty_implement_diff_is_a_red_attempt_retried_to_the_ceiling(repo):
    """T-0172: an implement attempt that changed nothing must be a red
    attempt — retried toward the poison ceiling, with a fact naming the
    empty diff — never a green promotion of an unchanged tree. This runs
    the real gate pass (not a scripted one), so the refusal exercised is
    the shipped one. The contract sits on `main`: the worktree is cut
    there, and an untracked contract copy would itself read as a change."""
    import json

    from torve.adapters.workspace.git import GitWorkspace
    from torve.gates.sabotage import base_task

    repo.seed()
    repo.git("checkout", "-q", "main")
    repo.task(base_task(allow=["src/**"]), None)
    repo.commit("task minted")

    config = RunnerConfig(poison_ceiling=2)
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),  # exits 0, writes nothing
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
    )

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "poison_ceiling"
    assert state.attempts == 2
    facts = [event["fact"] for event in state.history]
    assert facts.count("gates red: empty diff against base — no changes produced") == 2

    # RFC 0004 §6: the spend of each refused attempt survives as a red
    # record — two attempts, two red gate records.
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert len(records) == 2
    assert all(r["task_id"] == "T-9001" and r["exit_code"] == 1 for r in records)


def test_a_noop_whose_only_trace_is_the_contract_copy_is_red_to_the_ceiling(repo):
    """T-0172, the incident's own shape end to end: the contract was minted
    after the base, so the worktree cut there lacks it and the gate pass's
    own copy is the only file a no-op attempt leaves — an untracked
    bookkeeping file, implicitly in scope. It must not read as candidate
    work: the no-op is still a red attempt, retried toward the poison
    ceiling, exactly like the empty-diff case with a tracked contract."""
    import json

    from torve.adapters.workspace.git import GitWorkspace
    from torve.gates.sabotage import base_task

    repo.seed()
    repo.git("checkout", "-q", "main")
    # Minted after the base commit and never committed: absent from every
    # worktree cut at base, present only as the root's untracked contract.
    repo.task(base_task(allow=["src/**"]), None)

    config = RunnerConfig(poison_ceiling=2)
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),  # exits 0, writes nothing
        vcs=MockVcs(),
        scm=MockScm(),
        store=open_store,
    )

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "poison_ceiling"
    assert state.attempts == 2
    facts = [event["fact"] for event in state.history]
    assert facts.count("gates red: empty diff against base — no changes produced") == 2

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert len(records) == 2
    assert all(r["task_id"] == "T-9001" and r["exit_code"] == 1 for r in records)
