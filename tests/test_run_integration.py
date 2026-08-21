"""End to end against the real Docker daemon (skipped where there is none):
one task through claim -> sandbox -> fake agent -> gates-in-fresh-sandbox ->
commit -> ready, and the RFC 0003 exit criterion that `torve reap` provably
cleans up after a `kill -9` mid-run."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest
import yaml
from test_runtime_conformance import docker_available

from torve.adapters.agent_fake import FakeAgent
from torve.adapters.runtime_docker import DockerRuntime
from torve.adapters.vcs_git import GitVcs, NullScm
from torve.adapters.workspace_git import GitWorkspace
from torve.context import load_task
from torve.domain import TaskState
from torve.reaper import reap
from torve.run import RunDeps, run_task
from torve.runconfig import RunnerConfig, RuntimeConfig
from torve.runstate import RunState
from torve.sabotage import TASK_ID, base_task

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker daemon not available")

CONFIG = RunnerConfig(
    runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
    poison_ceiling=2,
)


def seed_run_repo(repo) -> None:
    repo.seed()
    task = base_task(allow=["src/**"])
    task["acceptance"] = ["test -f src/feature.py"]
    repo.task(task, None)
    repo.write("torve.yaml", yaml.safe_dump({
        "runtime": {"sandbox_timeout": 300, "agent_timeout": 90},
        "poison_ceiling": 2,
    }))
    repo.commit("task minted")


def deps_for(repo, agent) -> RunDeps:
    return RunDeps(workspace=GitWorkspace(repo.root), runtime=DockerRuntime(),
                   agent=agent, vcs=GitVcs(), scm=NullScm())


def test_one_task_end_to_end(repo):
    seed_run_repo(repo)
    agent = FakeAgent([{"writes": {"src/feature.py": "FEATURE = True\n"}, "exit": 0}])
    task = load_task(repo.root / "tasks" / f"{TASK_ID}.yaml")

    state = run_task(repo.root, task, CONFIG, deps_for(repo, agent))

    assert state.state is TaskState.READY, state.history
    assert state.attempts == 1
    worktree = repo.root / ".wt" / TASK_ID
    assert (worktree / "src" / "feature.py").read_text() == "FEATURE = True\n"
    assert any("committed" in event["fact"] for event in state.history)
    # Telemetry carries the gate pass that ran inside the fresh sandbox.
    telemetry = (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    record = json.loads(telemetry[-1])
    outcomes = {r["name"]: r["outcome"] for r in record["results"]}
    assert outcomes["acceptance"] == "pass"
    assert outcomes["scope"] == "pass"
    # Nothing survived the run: every torve sandbox for this task is gone.
    leftovers = [i for i in DockerRuntime().list_torve_sandboxes()
                 if i.labels.get("torve.task") == TASK_ID]
    assert not leftovers


def test_reap_cleans_up_after_kill_nine(repo):
    seed_run_repo(repo)
    scenario = repo.root / "sleeping.yaml"
    scenario.write_text(yaml.safe_dump({"attempts": [{"sleep": 300}]}), encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-m", "torve.cli", "run", TASK_ID,
         "--root", str(repo.root), "--scenario", str(scenario)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    state_path = repo.root / ".wt" / f"{TASK_ID}.state.json"
    runtime = DockerRuntime()
    try:
        deadline = time.monotonic() + 120
        sandbox_id = None
        while time.monotonic() < deadline:
            if state_path.exists():
                state = RunState.load(state_path)
                if state.sandbox_id:
                    sandbox_id = state.sandbox_id
                    break
            time.sleep(0.5)
        assert sandbox_id, "run never reached a live sandbox"

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)

        alive = [i for i in runtime.list_torve_sandboxes()
                 if i.labels.get("torve.task") == TASK_ID]
        assert alive, "the orphan must still exist before reap to prove anything"

        report = reap(repo.root, CONFIG, runtime, GitWorkspace(repo.root), force=True)

        assert report.runs_expired == [TASK_ID]
        assert report.sandboxes_destroyed
        after = [i for i in runtime.list_torve_sandboxes()
                 if i.labels.get("torve.task") == TASK_ID]
        assert not after
        state = RunState.load(state_path)
        assert state.state is TaskState.ESCALATED
        assert state.escalation.reason == "lease_expired"
        # The worktree survives for triage: the crash left evidence in it.
        assert (repo.root / ".wt" / TASK_ID).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        for info in runtime.list_torve_sandboxes():
            if info.labels.get("torve.task") == TASK_ID:
                runtime.destroy_by_id(info.id)
