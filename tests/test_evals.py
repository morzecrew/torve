"""RFC 0009 §5: the eval loop — with-skill versus without-skill shadow
replays of the same task, arms marked on the shadow records, one eval
record in the ledger, and the baseline verdict as direction only."""

from __future__ import annotations

import json
import subprocess
from functools import partial

import pytest

from torve.adapters.agent.fake import FakeAgent
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs, NullScm
from torve.adapters.workspace.git import (
    GitWorkspace,
    ShadowWorkspace,
    diff_range,
    diff_worktree,
    parent_of,
    shipped_commit,
)
from torve.application.evals import run_skill_eval, without_skill
from torve.application.runner import RunDeps
from torve.application.shadow import ShadowSource
from torve.config import layout
from torve.config.runconfig import RunnerConfig, RuntimeConfig
from torve.gates.context import load_task
from torve.gates.sabotage import TASK_ID, base_task

# ----------------------- #


def test_without_skill_strips_every_role_set():
    config = RunnerConfig()
    stripped = without_skill(config, "flag-dont-flip")
    assert all("flag-dont-flip" not in names for names in stripped.skills.sets.values())
    # The with-arm's configuration is untouched.
    assert "flag-dont-flip" in config.skills.sets["implement"]


def test_a_skill_in_no_role_set_refuses():
    with pytest.raises(ValueError, match="no role set"):
        without_skill(RunnerConfig(), "no-such-skill")


def test_skill_eval_runs_both_arms_and_ledgers(repo):
    from test_runtime_conformance import docker_available

    if not docker_available():
        pytest.skip("docker daemon not available")
    from torve.adapters.runtime.docker import DockerRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    repo.write("src/feature.py", "FEATURE = 'shipped'\n")
    repo.commit(f"torve({TASK_ID}): shipped\n\nTorve-Task: {TASK_ID}")

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90), poison_ceiling=2
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent(
            [
                {"writes": {"src/feature.py": "FEATURE = 'a'\n"}, "exit": 0},
                {"writes": {"src/feature.py": "FEATURE = 'b'\n"}, "exit": 0},
            ]
        ),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_skill_eval(repo.root, "flag-dont-flip", [task], config, deps, source)

    assert record["kind"] == "skill-eval" and record["skill"] == "flag-dont-flip"
    assert [r["task"] for r in record["arms"]["with"]] == [TASK_ID]
    assert [r["task"] for r in record["arms"]["without"]] == [TASK_ID]
    assert isinstance(record["baseline_matched"], bool)

    # One line in the ledger; two arm-marked shadow records in telemetry;
    # attribution shows the without-arm ran skill-less (T-0070).
    ledger = (repo.root / ".torve" / "evals.jsonl").read_text().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["skill"] == "flag-dont-flip"
    lines = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    arms = [line["eval"]["arm"] for line in lines if line.get("eval")]
    assert sorted(arms) == ["with", "without"]
    skills_by_shadow = [
        line["agent"]["skills"]
        for line in lines
        if line.get("agent") and line["agent"].get("shadow")
    ]
    assert any("flag-dont-flip" in (s or []) for s in skills_by_shadow)
    assert any("flag-dont-flip" not in (s or []) for s in skills_by_shadow)
    # Never merged: the shipped content is untouched.
    head_file = subprocess.run(
        ["git", "-C", str(repo.root), "show", "HEAD:src/feature.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head_file == "FEATURE = 'shipped'\n"
