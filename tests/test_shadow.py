"""S-0004 phase 2: shadow runs. The load-bearing property is S-0004/D-7 — a
shadow workspace holds truncated history and no refs beyond the replayed
task's parent, so the agent cannot read the answer out of the repository's
future — and S-0004/D-4's "never merging" is a construction fact: the landing hook
records prose, no vcs call exists on the shadow path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_decisions import landed
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
from torve.application.dispatch import RunDeps
from torve.application.runner import HOLDOUT_REL
from torve.application.shadow import (
    ShadowSource,
    replay_source,
    run_shadow,
    shadow_replay,
    shadow_workspace,
    take_holdout,
)
from torve.cli import app
from torve.config import layout
from torve.config.runconfig import RunnerConfig, RuntimeConfig
from torve.gates.context import load_task
from torve.gates.sabotage import TASK_ID, base_task

# ----------------------- #
# The truncated clone (S-0004/D-7)


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
        git("commit", "-q", "-m", f"c{n}")
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        shas.append(proc.stdout.strip())
    return root, shas


def test_shadow_workspace_has_truncated_history_and_no_later_refs(tmp_path):
    root, (_c1, c2, c3) = scratch_history(tmp_path)
    workspace = ShadowWorkspace(root, depth=2).create("T-7002", c2)

    assert (workspace / "f.txt").read_text(encoding="utf-8") == "two\n"
    # The future is unreachable: the shipped commit's objects were never sent.
    missing = subprocess.run(
        ["git", "-C", str(workspace), "cat-file", "-t", c3],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    # No refs beyond the single shadow branch at the parent.
    refs = subprocess.run(
        ["git", "-C", str(workspace), "for-each-ref", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert refs == ["refs/heads/shadow"]
    # Depth bounds the past too.
    count = subprocess.run(
        ["git", "-C", str(workspace), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert count == "2"


def test_shipped_commit_is_the_commit_the_newest_landing_names(tmp_path):
    """S-0059/D-12: a task shipped iff a landing of it names a commit — the
    lookup reads the landings the tree holds, the newest of them wins, and a
    commit no landing names is not a shipped commit. The `Torve-Task:`
    trailer this once grepped, and the subject convention behind it, are
    both gone with the trailer."""

    root, (_c1, c2, c3) = scratch_history(tmp_path)
    assert shipped_commit(root, "T-7002") is None  # nothing has landed yet

    landed(root, "T-7002", c2, at="2026-09-09T12:00:00Z")
    assert shipped_commit(root, "T-7002") == c2

    # A second attempt landed later: the newest landing is what shipped.
    landed(root, "T-7002", c3, attempt=2, at="2026-09-09T13:00:00Z")
    assert shipped_commit(root, "T-7002") == c3

    # An attempt that changed nothing lands without a commit — landed, but
    # with no shipped commit to replay, and no other task's either.
    landed(root, "T-7003")
    assert shipped_commit(root, "T-7003") is None
    assert shipped_commit(root, "T-9999") is None


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
    for args in (
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "agent's own commit"],
    ):
        subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, check=True)
    committed = diff_worktree(workspace, c2)
    assert committed["files_changed"] == 2
    assert set(committed["files"]) == {"f.txt", "new.txt"}


def test_a_stage_that_fails_is_no_measurement(tmp_path):
    """`git add -A` refused — here by an index another process holds — leaves
    a partial stage; diffing it would measure part of the work as all of it."""

    root, (_, c2, _) = scratch_history(tmp_path)
    workspace = ShadowWorkspace(root, depth=2).create("T-7003", c2)
    (workspace / "new.txt").write_text("replayed\n", encoding="utf-8")
    (workspace / ".git" / "index.lock").write_text("", encoding="utf-8")

    with pytest.raises(WorkspaceError, match=r"index\.lock"):
        diff_worktree(workspace, c2)


# ....................... #
# The shadow loop end to end (real Docker)


def ship(repo) -> str:
    """The shipped work, and the landing that names the commit it shipped as
    — what a replay looks for now that a task shipped iff a landing of it
    names a commit (S-0059/D-12). Returns that commit."""

    repo.write("src/feature.py", "FEATURE = 'shipped'\n")
    repo.commit(f"torve({TASK_ID}): shipped")
    sha = subprocess.run(
        ["git", "-C", str(repo.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # A landing is a file in the tree, and the lookup reads it from there —
    # so this needs no commit of its own to be found.
    landed(repo.root, TASK_ID, sha)

    return sha


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
def test_shadow_replay_end_to_end(repo):
    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    parent_sha = subprocess.run(
        ["git", "-C", str(repo.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    ship(repo)

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90), poison_ceiling=2
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent([{"writes": {"src/feature.py": "FEATURE = 'replayed'\n"}, "exit": 0}]),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    opened: list[str] = []

    class OpeningShadow(ShadowWorkspace):
        def judge_checkout(self, task_id: str):
            opened.append(task_id)
            return super().judge_checkout(task_id)

    shadow_ws = OpeningShadow(repo.root, depth=10)
    from functools import partial

    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
        judge=shadow_ws,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    record = run_shadow(repo.root, task, config, deps, source)

    assert opened == [TASK_ID]

    assert record["kind"] == "shadow"
    assert record["state"] == "ready"
    assert record["attempts"] == 1
    assert record["parent"] == parent_sha
    # The replay found the shipped commit by the landing that names it, and
    # worked from the parent — where the answer does not exist.
    workspace = repo.root / ".wt" / f"shadow-{TASK_ID}"
    unreachable = subprocess.run(
        ["git", "-C", str(workspace), "cat-file", "-t", record["commit"]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unreachable.returncode != 0
    # The battery judged a snapshot of the clone (S-0114/D-2), and its
    # checkout went with the battery (S-0114/D-5).
    assert not (repo.root / ".wt" / f"shadow-{TASK_ID}.judge").exists()
    # Comparison recorded; the replay touched the same file.
    assert record["overlap_files"] == ["src/feature.py"]
    # Never merged: the source repository's head is untouched, the shipped
    # content is still what shipped.
    head = subprocess.run(
        ["git", "-C", str(repo.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == record["commit"]
    # One stream, separable populations: the summary is kind=shadow and the
    # gate passes inside the replay are marked agent.shadow=true.
    lines = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
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
        "schema_version: 1\nid: T-0042\ndecisions: []\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "unrelated"], check=True, capture_output=True
    )

    result = CliRunner().invoke(app, ["shadow", "T-0042", "--root", str(root)])
    assert result.exit_code == 3
    assert "no shipped commit" in result.stderr


# ....................... #
# The warm-state exclusion (S-0035/D-3): a tier's `cache_volume` is ignored
# under `shadow=True` — the replay measures the cold truth, and an eval
# comparing arms never compares caches.


def _shadow_deps(repo, runtime):
    from test_run_loop import OK, MockScm, ScriptedAgent

    from torve.config.runconfig import TierConfig

    cold = RunnerConfig()

    config = RunnerConfig(
        worker_slot=3,
        tiers={**cold.tiers, "executor": TierConfig(cache_volume="torve-cache")},
        poison_ceiling=2,
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=runtime,
        agent=ScriptedAgent([OK]),
        vcs=GitVcs(),
        scm=MockScm(),
        store=open_store,
    )
    return config, deps


def test_replay_never_mounts_the_cache_volume_even_when_the_tier_names_one(
    repo, tmp_path, monkeypatch
):
    from functools import partial

    from test_run_loop import MockRuntime

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    repo.task(task_doc, None)
    repo.commit("task minted")
    shipped = ship(repo)

    specs: list = []

    class RecordingRuntime(MockRuntime):
        def create(self, spec, workspace):
            specs.append(spec)
            return super().create(spec, workspace)

    gate_caches: list[dict] = []

    def scripted_gates(run, _state):
        # Read off the dispatch, not counted out of a positional list: the
        # pass and the attempt derive the mount from one function, so this
        # is the mount the battery would actually have carried.
        gate_caches.append(cache_volumes(run))
        return 0, "scripted", "cafecafe1234", [], ""

    import torve.application.runner as run_module
    from torve.application.dispatch import cache_volumes

    monkeypatch.setattr(run_module, "run_gate_pass", scripted_gates)

    config, deps = _shadow_deps(repo, RecordingRuntime())
    shadow_ws = ShadowWorkspace(repo.root, depth=10)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
        judge=shadow_ws,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))

    assert config.tiers["executor"].cache_volume == "torve-cache"  # the tier names one

    record = run_shadow(repo.root, task, config, deps, source)

    assert record["state"] == "ready"
    assert record["commit"] == shipped
    # Every sandbox the replay created — and every gate pass it would have
    # mounted — is cold: the named volume is ignored under `shadow`, so the
    # wall clock this record stamps is the cold truth.
    assert specs, "the replay created no sandbox to inspect"
    assert all(spec.volumes == {} for spec in specs)
    assert gate_caches and all(caches == {} for caches in gate_caches)


# ....................... #
# The composed arm (S-0116/D-12): one clone replaying more than one task, the
# judge lookup that has to name the clone rather than the task, and the
# held-out properties the verifier left behind for the implementer's battery.


def test_a_shared_judge_judges_under_the_clones_name():
    """Every judge lookup goes through the workspace, and the workspace is
    keyed by task — so a clone replaying two tasks hands the judge the one
    task the clone belongs to, never the task being replayed."""

    class Recording:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def judge_checkout(self, task_id: str) -> Path:
            self.calls.append(("open", task_id))
            return Path(f"/tmp/{task_id}.judge")

        def remove_judge(self, task_id: str) -> None:
            self.calls.append(("close", task_id))

    inner = Recording()
    source = ShadowSource(
        create_workspace=lambda _task, _base: Path("/tmp/clone"),
        shipped_commit=lambda _task: None,
        parent_of=lambda sha: sha,
        diff_range=lambda _sha: {},
        diff_worktree=lambda _ws, _base: {},
        judge=inner,
    )

    composed = replay_source(source, "T-0042")

    assert composed.judge.judge_checkout("T-0041") == Path("/tmp/T-0042.judge")
    composed.judge.remove_judge("T-0041")

    assert inner.calls == [("open", "T-0042"), ("close", "T-0042")]
    # The source the composed arm was built from is untouched.
    assert source.judge.judge_checkout("T-0041") == Path("/tmp/T-0041.judge")


def test_shadow_workspace_cuts_at_the_parent_of_the_commit_the_landing_names(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cuts: list[tuple[str, str]] = []

    def create_workspace(task_id: str, base: str) -> Path:
        cuts.append((task_id, base))
        return root / ".wt" / f"shadow-{task_id}"

    source = ShadowSource(
        create_workspace=create_workspace,
        shipped_commit=lambda task: "a" * 40 if task == "T-0042" else None,
        parent_of=lambda sha: "b" * 40 if sha == "a" * 40 else sha,
        diff_range=lambda _sha: {},
        diff_worktree=lambda _ws, _base: {},
        judge=None,
    )

    workspace, resolved, parent = shadow_workspace(source, "T-0042")

    assert (resolved, parent) == ("a" * 40, "b" * 40)
    assert cuts == [("T-0042", "b" * 40)]
    assert workspace == root / ".wt" / "shadow-T-0042"

    # A task no landing names has no tree to cut from — a configuration
    # problem, refused by name rather than replayed from nowhere.
    with pytest.raises(ValueError, match="no shipped commit found for T-0043"):
        shadow_workspace(source, "T-0043")


def test_take_holdout_reads_the_verifier_files_and_removes_the_directory(tmp_path):
    worktree = tmp_path / "clone"
    holdout = worktree / HOLDOUT_REL
    (holdout / "nested").mkdir(parents=True)
    (holdout / "props.py").write_text("ESCAPED = True\n", encoding="utf-8")
    (holdout / "nested" / "case.txt").write_text("one\n", encoding="utf-8")

    files = take_holdout(worktree)

    # Read by name, in a stable order, so the injected battery is the same
    # battery twice over.
    assert [(f.name, f.text) for f in files] == [
        ("nested/case.txt", "one\n"),
        ("props.py", "ESCAPED = True\n"),
    ]
    # And gone from the worktree: the implementer's tree never carries them,
    # in the replay as in a live run (S-0116/D-9).
    assert not holdout.exists()
    assert take_holdout(worktree) == []


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
def test_a_caller_cut_workspace_replays_twice_in_one_clone(repo):
    """D-12: the candidate arm is two replays in one clone — the verifier,
    then the task. The clone is cut once and never again, the judge snapshot
    names the clone both times, and neither replay merges anything."""

    repo.seed()
    task_doc = base_task(allow=["src/**"])
    task_doc["acceptance"] = ["test -f src/feature.py"]
    repo.task(task_doc, None)
    repo.commit("task minted")
    sha = ship(repo)

    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90), poison_ceiling=2
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=FakeAgent([{"writes": {"src/feature.py": "FEATURE = 'replayed'\n"}, "exit": 0}]),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    opened: list[str] = []

    class OpeningShadow(ShadowWorkspace):
        def judge_checkout(self, task_id: str):
            opened.append(task_id)
            return super().judge_checkout(task_id)

    shadow_ws = OpeningShadow(repo.root, depth=10)
    from functools import partial

    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, repo.root),
        parent_of=partial(parent_of, repo.root),
        diff_range=partial(diff_range, repo.root),
        diff_worktree=diff_worktree,
        judge=shadow_ws,
    )
    task = load_task(layout.task_file(repo.root, TASK_ID))
    workspace, _resolved, parent = shadow_workspace(source, TASK_ID)

    shadow_replay(repo.root, task, config, deps, source, workspace, parent, sha)

    assert (repo.root / ".wt" / f"shadow-{TASK_ID}").is_dir()

    # The verifier's work, left where a verifier agent leaves it: a file in the
    # tree, and its held-out properties in the holdout directory (S-0116/D-9).
    holdout = workspace / HOLDOUT_REL
    holdout.mkdir(parents=True, exist_ok=True)
    (holdout / "props.py").write_text("ESCAPED = True\n", encoding="utf-8")

    held = take_holdout(workspace)
    record = shadow_replay(
        repo.root,
        task,
        config,
        deps,
        replay_source(source, TASK_ID),
        workspace,
        parent,
        sha,
        annotation={"verifier": "candidate"},
    )

    # Read once, removed from the tree, and the file the verifier wrote is what
    # the second replay measured.
    assert [(prop.name, prop.text) for prop in held] == [("props.py", "ESCAPED = True\n")]
    assert not holdout.exists()
    assert "src/feature.py" in record["shadow_diff"]["files"]
    # One clone, judged twice under its own name, and the judge checkout went
    # with the battery — no second clone was ever cut.
    assert opened == [TASK_ID, TASK_ID]
    assert not (repo.root / ".wt" / f"shadow-{TASK_ID}.judge").exists()
    assert record["parent"] == parent
