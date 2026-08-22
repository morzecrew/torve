"""RFC 0004 phase 2: shadow runs. The load-bearing property is D-4.7 — a
shadow workspace holds truncated history and no refs beyond the replayed
task's parent, so the agent cannot read the answer out of the repository's
future — and D-4.4's "never merging" is a construction fact: the landing hook
records prose, no vcs call exists on the shadow path."""

from __future__ import annotations

import json
import subprocess

import pytest
from test_runtime_conformance import docker_available
from typer.testing import CliRunner

from torve.adapters.agent.fake import FakeAgent
from torve.adapters.runtime.docker import DockerRuntime
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs, NullScm
from torve.adapters.workspace.git import (
    GitWorkspace,
    ShadowWorkspace,
    WorkspaceError,
    diff_range,
    diff_worktree,
    parent_of,
    shipped_commit,
)
from torve.application.runner import RunDeps
from torve.application.shadow import ShadowSource, run_shadow
from torve.cli import app
from torve.config import layout
from torve.config.runconfig import RunnerConfig, RuntimeConfig
from torve.gates.context import load_task
from torve.gates.sabotage import TASK_ID, base_task

# ----------------------- #
# The truncated clone (D-4.7)


def scratch_history(tmp_path):
    """Three commits; returns (root, [sha1, sha2, sha3])."""
    root = tmp_path / "src-repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    shas = []
    for n, content in enumerate(("one", "two", "FUTURE-ANSWER"), start=1):
        (root / "f.txt").write_text(content + "\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", f"c{n}" + ("\n\nTorve-Task: T-7002" if n == 3 else ""))
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        shas.append(proc.stdout.strip())
    return root, shas


def test_shadow_workspace_has_truncated_history_and_no_later_refs(tmp_path):
    root, (_c1, c2, c3) = scratch_history(tmp_path)
    workspace = ShadowWorkspace(root, depth=2).create("T-7002", c2)

    assert (workspace / "f.txt").read_text(encoding="utf-8") == "two\n"
    # The future is unreachable: the shipped commit's objects were never sent.
    missing = subprocess.run(["git", "-C", str(workspace), "cat-file", "-t", c3],
                             capture_output=True, text=True, check=False)
    assert missing.returncode != 0
    # No refs beyond the single shadow branch at the parent.
    refs = subprocess.run(["git", "-C", str(workspace), "for-each-ref", "--format=%(refname)"],
                          capture_output=True, text=True, check=True).stdout.split()
    assert refs == ["refs/heads/shadow"]
    # Depth bounds the past too.
    count = subprocess.run(["git", "-C", str(workspace), "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert count == "2"


def test_shipped_commit_lookup_by_trailer_and_subject(tmp_path):
    root, (_c1, _c2, c3) = scratch_history(tmp_path)
    assert shipped_commit(root, "T-7002") == c3  # the Torve-Task trailer
    assert shipped_commit(root, "T-9999") is None
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty",
                    "-m", "docs: adopt patch (T-7003)"], capture_output=True, check=True)
    found = shipped_commit(root, "T-7003")  # the hand-committed subject fallback
    assert found is not None and found != c3
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty",
                    "-m", "feat: containment (A-19, T-7004)"], capture_output=True, check=True)
    multi = shipped_commit(root, "T-7004")
    assert multi is not None  # multi-id subjects too
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty",
                    "-m", "fix: lookup fallback\n\nquotes the subject style (A-19, T-7004)"],
                   capture_output=True, check=True)
    # A later commit mentioning the id in its BODY must not shadow the
    # shipping commit — the fallback matches subjects only.
    assert shipped_commit(root, "T-7004") == multi


def test_parent_of_and_diffstats(tmp_path):
    root, (c1, c2, c3) = scratch_history(tmp_path)
    assert parent_of(root, c3) == c2
    with pytest.raises(WorkspaceError):
        parent_of(root, c1)  # the root commit has none

    shipped = diff_range(root, c3)
    assert shipped["files_changed"] == 1
    assert "f.txt" in shipped["files"]

    workspace = ShadowWorkspace(root, depth=2).create("T-7002", c2)
    (workspace / "new.txt").write_text("replayed\n", encoding="utf-8")
    (workspace / "f.txt").write_text("edited\n", encoding="utf-8")
    produced = diff_worktree(workspace, c2)
    assert produced["files_changed"] == 2
    assert set(produced["files"]) == {"f.txt", "new.txt"}
    # An agent may commit inside the self-contained clone — that moves HEAD,
    # and the measurement must still read the work (found by the first dsh
    # replay of a real task, which committed and measured as an empty diff).
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "agent's own commit"]):
        subprocess.run(["git", "-C", str(workspace), *args],
                       capture_output=True, check=True)
    committed = diff_worktree(workspace, c2)
    assert committed["files_changed"] == 2
    assert set(committed["files"]) == {"f.txt", "new.txt"}


# ....................... #
# The shadow loop end to end (real Docker)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
def test_shadow_replay_end_to_end(repo):
    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    parent_sha = subprocess.run(["git", "-C", str(repo.root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    repo.write("src/feature.py", "FEATURE = 'shipped'\n")
    repo.commit(f"torve({TASK_ID}): shipped\n\nTorve-Task: {TASK_ID}")

    config = RunnerConfig(runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
                          poison_ceiling=2)
    deps = RunDeps(workspace=GitWorkspace(repo.root), runtime=DockerRuntime(),
                   agent=FakeAgent([{"writes": {"src/feature.py": "FEATURE = 'replayed'\n"},
                                     "exit": 0}]),
                   vcs=GitVcs(), scm=NullScm(), store=open_store)
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    from functools import partial
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_shadow(repo.root, task, config, deps, source)

    assert record["kind"] == "shadow"
    assert record["state"] == "ready"
    assert record["attempts"] == 1
    assert record["parent"] == parent_sha
    # The replay found the shipped commit by its trailer and worked from the
    # parent — where the answer does not exist.
    workspace = repo.root / ".wt" / f"shadow-{TASK_ID}"
    unreachable = subprocess.run(
        ["git", "-C", str(workspace), "cat-file", "-t", record["commit"]],
        capture_output=True, text=True, check=False)
    assert unreachable.returncode != 0
    # Comparison recorded; the replay touched the same file.
    assert record["overlap_files"] == ["src/feature.py"]
    # Never merged: the source repository's head is untouched, the shipped
    # content is still what shipped.
    head = subprocess.run(["git", "-C", str(repo.root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == record["commit"]
    # One stream, separable populations: the summary is kind=shadow and the
    # gate passes inside the replay are marked agent.shadow=true.
    lines = [json.loads(line) for line in
             (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()]
    kinds = [line.get("kind") for line in lines]
    assert "shadow" in kinds
    gate_passes = [line for line in lines if line.get("agent")]
    assert gate_passes and all(line["agent"]["shadow"] for line in gate_passes)


# ....................... #
# CLI


def test_shadow_without_a_findable_commit_exits_3(tmp_path):
    root = tmp_path / "repo"
    (root / ".torve" / "tasks" / "T-0042").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / ".torve" / "tasks" / "T-0042" / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0042\ndecisions: []\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "unrelated"],
                   check=True, capture_output=True)

    result = CliRunner().invoke(app, ["shadow", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "no shipped commit" in result.stderr
