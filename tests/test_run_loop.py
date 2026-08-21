"""The run loop over mock ports (RFC 0003 §6 layer 2, minus the sandbox):
every escalation reason the loop can produce, the retry path, and the facts
recorded on the way. Gate outcomes are scripted by patching the gate pass —
the real gate integration is exercised in test_run_integration.py.
"""

from __future__ import annotations

import pytest

import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.application.ports import AgentResult, ExecResult, SandboxHandle, SandboxInfo
from torve.application.runner import RunDeps, run_task
from torve.config.runconfig import RunnerConfig
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


class MockWorkspace:
    def __init__(self, root):
        self.root = root

    def create(self, task_id, base_ref):
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
                "    kind: contradicted\n    action: halted\n", encoding="utf-8")
        return self.results[min(ctx.attempt - 1, len(self.results) - 1)]


class MockVcs:
    def __init__(self):
        self.commits: list[str] = []

    def commit_all(self, worktree, message):
        self.commits.append(message)
        return "abcdef123456"

    def push(self, worktree, branch):
        return False


class MockScm:
    def open_pr(self, worktree, branch, title, body):
        raise AssertionError("PR leg is deferred; nothing should call the forge")


OK = AgentResult(exit_code=0, output="")
CRASH = AgentResult(exit_code=137, output="died mid-write")
TIMEOUT = AgentResult(exit_code=None, output="hard timeout")


def task_for(repo, iterations=None):
    return Task(id="T-9001", scope=Scope(), decisions=[],
                budget=Budget(iterations=iterations))


@pytest.fixture
def rig(repo, monkeypatch):
    repo.seed()  # a real git repo so base resolution works
    runtime, vcs = MockRuntime(), MockVcs()
    deps = RunDeps(workspace=MockWorkspace(repo.root), runtime=runtime,
                   agent=ScriptedAgent([OK]), vcs=vcs, scm=MockScm(), store=open_store)
    gate_outcomes: list[int] = []

    def scripted_gates(*args, **kwargs):
        code = gate_outcomes.pop(0) if gate_outcomes else 0
        return code, "scripted", "cafecafe1234"

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
    repo, deps, _, _, _ = rig
    deps.agent = ScriptedAgent([CRASH])
    state = run_task(repo.root, task_for(repo, iterations=1), RunnerConfig(), deps)
    assert state.escalation.reason == "budget_exhausted"
    assert state.attempts == 1


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
