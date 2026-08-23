"""RFC 0006 phase 1: dispatch refusal on scope overlap, torve kill, engine
events on the telemetry stream, and the escalation queue's age and route in
the context projection."""

from __future__ import annotations

import json

import pytest
import yaml
from test_run_loop import OK, MockRuntime, MockScm, MockVcs, MockWorkspace, ScriptedAgent
from typer.testing import CliRunner

from torve.adapters.store.durable import open_store
from torve.application.projections import _escalation_route, context_report
from torve.application.runner import BlockedDispatch, RunDeps, run_task
from torve.application.runstate import RunState
from torve.base import naming
from torve.cli.main import app
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import Scope, Task


def active_run(root, task_id: str, allow: list[str]) -> RunState:
    contract_dir = root / ".torve" / "tasks" / task_id
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "contract.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "id": task_id, "role": "implement",
        "scope": {"allow": allow, "deny": []}, "acceptance": [], "decisions": [],
    }), encoding="utf-8")
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "seeded active run")
    state.transition(TaskState.RUNNING, "seeded active run")
    state.save()
    return state


def deps(repo):
    return RunDeps(workspace=MockWorkspace(repo.root), runtime=MockRuntime(),
                   agent=ScriptedAgent([OK]), vcs=MockVcs(), scm=MockScm(),
                   store=open_store)


def test_overlapping_dispatch_is_refused_and_counted(repo):
    repo.seed()
    active_run(repo.root, "T-8001", ["src/app/**"])
    task = Task(id="T-8002", scope=Scope(allow=["src/app/**"]), decisions=[])

    with pytest.raises(BlockedDispatch, match="blocked_by_overlap: T-8001"):
        run_task(repo.root, task, RunnerConfig(), deps(repo))

    records = [json.loads(line) for line in
               (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()]
    blocked = [r for r in records if r.get("event") == "blocked_dispatch"]
    assert blocked and blocked[-1]["blocked_by"] == "T-8001"
    assert blocked[-1]["path"] == "src/app/**"
    # No run state was created for the refused task.
    assert not naming.state_file(repo.root, "T-8002").exists()


def test_disjoint_scopes_dispatch_freely(repo):
    repo.seed()
    active_run(repo.root, "T-8001", ["src/app/**"])
    task = Task(id="T-8003", scope=Scope(allow=["docs/**"]), decisions=[])
    # Dispatch proceeds (no BlockedDispatch): the run claims and executes —
    # its eventual outcome is the loop's business, not this check's.
    state = run_task(repo.root, task, RunnerConfig(), deps(repo))
    assert naming.state_file(repo.root, "T-8003").exists()
    assert state.attempts >= 1


def test_an_unconstrained_scope_contends_with_everything(repo):
    repo.seed()
    active_run(repo.root, "T-8001", ["src/app/**"])
    task = Task(id="T-8004", scope=Scope(), decisions=[])
    with pytest.raises(BlockedDispatch, match="unconstrained"):
        run_task(repo.root, task, RunnerConfig(), deps(repo))


# ....................... #
# torve kill


def test_kill_escalates_and_destroys_the_sandbox(repo):
    repo.seed()
    state = active_run(repo.root, "T-8005", ["src/**"])
    state.sandbox_id = "sbx-live"
    state.save()

    result = CliRunner().invoke(app, ["kill", "T-8005", "--root", str(repo.root),
                                      "--format", "json"])
    assert result.exit_code == 0, result.output

    reloaded = RunState.load(naming.state_file(repo.root, "T-8005"))
    assert reloaded.state is TaskState.ESCALATED
    assert reloaded.escalation.reason == "killed"
    records = [json.loads(line) for line in
               (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "killed" and r.get("task") == "T-8005"
               for r in records)


def test_kill_refuses_a_terminal_run(repo):
    repo.seed()
    state = RunState(task_id="T-8006", path=naming.state_file(repo.root, "T-8006"))
    state.state = TaskState.READY
    state.save()
    result = CliRunner().invoke(app, ["kill", "T-8006", "--root", str(repo.root)])
    assert result.exit_code == 3


# ....................... #
# queue age and routing


def test_escalations_carry_age_and_route(repo, tmp_path):
    repo.seed()
    (repo.root / "rfcs").mkdir(exist_ok=True)
    state = active_run(repo.root, "T-8007", ["src/**"])
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.BLOCKER_FINDING, "seeded")
    report = context_report(repo.root, repo.root / "rfcs")
    items = report["escalations"]["blocker_finding"]
    assert items[0]["route"] == "notify"
    assert items[0]["age_s"] is not None and items[0]["age_s"] >= 0


def test_route_classes_follow_the_attention_table():
    assert _escalation_route("blocker_finding") == "notify"
    assert _escalation_route("locked_conflict") == "notify"
    assert _escalation_route("gate_infrastructure_failure") == "harness owner"
    assert _escalation_route("poison_ceiling") == "batch"
    assert _escalation_route("budget_exhausted") == "batch"
