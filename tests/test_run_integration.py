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

from torve.adapters.agent.fake import FakeAgent
from torve.adapters.runtime.docker import DockerRuntime
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs, NullScm
from torve.adapters.workspace.git import GitWorkspace
from torve.application.reaper import reap
from torve.application.runner import RunDeps, run_task
from torve.application.runstate import RunState
from torve.config import layout
from torve.config.runconfig import RunnerConfig, RuntimeConfig
from torve.domain.states import TaskState
from torve.gates.context import load_task
from torve.gates.sabotage import TASK_ID, base_task

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
                   agent=agent, vcs=GitVcs(), scm=NullScm(), store=open_store)


def test_one_task_end_to_end(repo):
    # Also the plan-gate-deadlock scenario (0003 A-18): a well-formed contract
    # must end in a diff — a run terminating with no diff and no `blocked`
    # entry is the deadlock's signature, and this asserting READY plus the
    # written file is what catches its return.
    seed_run_repo(repo)
    agent = FakeAgent([{"writes": {"src/feature.py": "FEATURE = True\n"}, "exit": 0}])
    task = load_task(layout.task_file(repo.root, TASK_ID))

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


def test_the_sandbox_receives_no_rfc_document(repo):
    # 0003 §5a as amended by A-18: `rfc` on the contract is provenance — a
    # reference, never the document. The task names a specification that does
    # not exist anywhere; a runner that tried to read or copy it would fail,
    # and the worktree the sandbox sees must contain no corpus at all.
    seed_run_repo(repo)
    task_path = layout.task_file(repo.root, TASK_ID)
    contract = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    contract["rfc"] = "rfcs/0042-imaginary.md"
    task_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    repo.commit("provenance points at a document this repository does not hold")
    agent = FakeAgent([{"writes": {"src/feature.py": "FEATURE = True\n"}, "exit": 0}])
    task = load_task(task_path)

    state = run_task(repo.root, task, CONFIG, deps_for(repo, agent))

    assert state.state is TaskState.READY, state.history
    worktree = repo.root / ".wt" / TASK_ID
    assert not (worktree / "rfcs").exists()
    assert not list(worktree.rglob("0042-imaginary.md"))


def test_log_entry_written_before_failure_is_on_disk(repo):
    # A-13/D-3.20: the log is created by its first entry, flushed as written —
    # an abnormal end must not lose what the agent honestly wrote.
    seed_run_repo(repo)
    entry = ("  - decision: D-1\n    grade: LOCKED\n    kind: resolved\n"
             "    action: decided\n    claim: written before dying\n")
    agent = FakeAgent([{"log_entry": entry, "exit": 137}])
    task = load_task(layout.task_file(repo.root, TASK_ID))

    state = run_task(repo.root, task, CONFIG, deps_for(repo, agent))

    assert state.state is TaskState.ESCALATED  # poison ceiling; the run never went green
    log = repo.root / ".wt" / TASK_ID / ".torve" / "tasks" / TASK_ID / "log.yaml"
    assert log.is_file(), "the entry written before the crash must be on disk"
    assert "written before dying" in log.read_text()


def test_harness_tier_end_to_end(repo):
    """RFC 0004 §1 through the whole loop: the executor tier maps to an api
    adapter, routing admits the provider, the harness command runs inside the
    sandbox against the staged prompt, and the attempt record carries the
    adapter block plus a trace_ref (§6)."""
    from torve.adapters.agent.harness import HarnessAgent
    from torve.config.runconfig import ProvidersConfig, TierConfig

    seed_run_repo(repo)
    tier = TierConfig(
        adapter="api", provider="test-vendor", model="fake-model-9",
        command=("grep -q 'Torve task' {prompt} && echo FEATURE = True > src/feature.py"
                 " && echo '{\"total_cost_usd\": 0.05, \"model\": \"{model}\"}'"),
        api_key_env=["TORVE_TEST_KEY"],
    )
    config = CONFIG.model_copy(update={
        "tiers": {"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        "providers": ProvidersConfig(default=["test-vendor"]),
    })
    task = load_task(layout.task_file(repo.root, TASK_ID))

    state = run_task(repo.root, task, config, deps_for(repo, HarnessAgent(tier)))

    assert state.state is TaskState.READY, state.history
    record = json.loads(
        (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()[-1])
    agent_block = record["agent"]
    assert agent_block["adapter"] == "api"
    assert agent_block["provider"] == "test-vendor"
    assert agent_block["model"] == "fake-model-9"
    assert agent_block["model_version"] == "fake-model-9"  # echoed by the command
    assert agent_block["cost_usd"] == 0.05
    # The sandbox's identity rides the record: the runtime resolved the
    # image to its content digest at dispatch.
    assert str(agent_block["image_digest"]).startswith("sha256:")
    # Per-skill attribution (T-0070): the record names what materialize
    # wrote for the role, so cohorts group by skill regime.
    assert agent_block["skills"] == ["flag-dont-flip", "ratchet-what-you-build"]
    trace = agent_block["trace_ref"]
    assert trace and (repo.root / ".wt" / f"{TASK_ID}.a1.trace.log").is_file()
