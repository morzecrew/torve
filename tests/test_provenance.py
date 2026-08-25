"""RFC 0010 phase 1: the commit as the runner's provenance record — agent
author, Torve committer, full trailers, signing at the runner boundary — and
revert as a mechanical role through the same loop, gates and landing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_run_loop import OK, MockRuntime, MockScm, ScriptedAgent

import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs
from torve.adapters.workspace.git import GitWorkspace
from torve.application.runner import RunDeps, run_task
from torve.base import naming
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import Scope, Task


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def vcs_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "config", "user.name", "A Human")
    git(root, "config", "user.email", "human@example.invalid")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", "init")
    return root


def test_the_commit_is_authored_by_the_agent_and_committed_by_torve(vcs_repo):
    (vcs_repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    sha = GitVcs().commit_all(
        vcs_repo, "torve(T-8101): attempt 1 green\n\nTorve-Task: T-8101",
        author="harness/deepseek-chat@v3 <agents@torve.local>")
    assert sha
    author, email, committer = git(
        vcs_repo, "log", "-1", "--format=%an|%ae|%cn").split("|")
    assert author == "harness/deepseek-chat@v3"
    assert email == "agents@torve.local"
    assert committer == "Torve"


def test_landed_shas_reconstruct_a_task_from_trailers_alone(vcs_repo):
    vcs = GitVcs()
    for n in (1, 2):
        (vcs_repo / "app.py").write_text(f"value = {n + 1}\n", encoding="utf-8")
        vcs.commit_all(vcs_repo, f"torve(T-8102): attempt {n} green\n\n"
                                 f"Torve-Task: T-8102\nTorve-Attempt: {n}")
    assert vcs.landed_shas(vcs_repo, "T-8102") == [
        git(vcs_repo, "rev-parse", "HEAD"), git(vcs_repo, "rev-parse", "HEAD~1")]
    assert vcs.landed_shas(vcs_repo, "T-9999") == []


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="no ssh-keygen")
def test_a_signed_commit_with_the_key_outside_the_worktree(vcs_repo, tmp_path):
    # The 0010 §9 criterion in miniature: the key lives beside the runner,
    # never under the tree the sandbox sees, and verification succeeds.
    keydir = tmp_path / "runner-keys"
    keydir.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                    "-f", str(keydir / "signing")], check=True)
    (vcs_repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    sha = GitVcs().commit_all(vcs_repo, "torve(T-8103): attempt 1 green",
                              author="fake <agents@torve.local>",
                              sign_key=str(keydir / "signing"))
    assert sha
    signers = tmp_path / "allowed_signers"
    pubkey = (keydir / "signing.pub").read_text(encoding="utf-8").strip()
    signers.write_text(f"torve@local {pubkey}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(vcs_repo), "-c", "gpg.format=ssh",
         "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-commit", sha],
        capture_output=True, text=True, check=True)
    assert not list(vcs_repo.rglob("signing*"))  # the key never entered the tree


def test_revert_stages_the_inverse_and_a_conflict_aborts_clean(vcs_repo):
    vcs = GitVcs()
    (vcs_repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    vcs.commit_all(vcs_repo, "torve(T-8104): attempt 1 green\n\nTorve-Task: T-8104")
    target = git(vcs_repo, "rev-parse", "HEAD")

    assert vcs.revert(vcs_repo, [target]) is True
    assert (vcs_repo / "app.py").read_text() == "value = 1\n"  # staged, uncommitted
    git(vcs_repo, "reset", "--hard", "HEAD")

    # A later commit rewrites the same line: the revert now conflicts,
    # aborts, and leaves the worktree exactly as it stood.
    (vcs_repo / "app.py").write_text("value = 99\n", encoding="utf-8")
    vcs.commit_all(vcs_repo, "later work")
    assert vcs.revert(vcs_repo, [target]) is False
    assert git(vcs_repo, "status", "--porcelain") == ""
    assert (vcs_repo / "app.py").read_text() == "value = 99\n"


# ----------------------- #


@pytest.fixture
def engine_repo(tmp_path: Path, monkeypatch) -> Path:
    """A real repository with a landed target task, driven through run_task
    with the real workspace and vcs adapters; gate passes are scripted."""
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "config", "user.name", "Engine Operator")
    git(root, "config", "user.email", "operator@example.invalid")
    (root / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".wt/\n.torve/telemetry.jsonl\n", encoding="utf-8")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", "init")
    # The target task's landed commit, trailer and all (D-10.4 is what
    # makes it findable later).
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    GitVcs().commit_all(root, "torve(T-8200): attempt 1 green\n\n"
                              "Torve-Task: T-8200\nTorve-Attempt: 1")

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)
    return root


def revert_task(target: str = "T-8200") -> Task:
    from torve.domain.task import InheritedDecision
    return Task(id="T-8201", role="revert", targets=[target], scope=Scope(),
                decisions=[InheritedDecision(id="D-77", grade="LOCKED",
                                             text="the reverted rule")])


def engine_deps(root: Path) -> RunDeps:
    return RunDeps(workspace=GitWorkspace(root), runtime=MockRuntime(),
                   agent=ScriptedAgent([OK]), vcs=GitVcs(), scm=MockScm(),
                   store=open_store)


def test_a_revert_runs_as_a_task_and_lands_with_its_own_provenance(engine_repo):
    state = run_task(engine_repo, revert_task(), RunnerConfig(), engine_deps(engine_repo))
    assert state.state is TaskState.READY, state.history

    branch = naming.branch("T-8201")
    subject = git(engine_repo, "log", "-1", "--format=%s", branch)
    body = git(engine_repo, "log", "-1", "--format=%B", branch)
    # The subject carries the intent's head (owner feedback: a history
    # readable without opening the task) and still ends with the verdict.
    assert subject.startswith("torve(T-8201):")
    assert subject.endswith("attempt 1 green")
    assert "Torve-Task: T-8201" in body
    assert "Torve-Agent: revert" in body  # mechanical, named for what it is
    assert git(engine_repo, "show", f"{branch}:app.py") == "value = 1"
    # The machine-written resolved entry survived into the landed tree, and
    # the trailer carries the inherited decision with its grade.
    assert "Torve-Decisions: D-77(LOCKED)" in body
    log = git(engine_repo, "show", f"{branch}:.torve/tasks/T-8201/log.yaml")
    assert "kind: resolved" in log
    assert "undone by T-8201" in log


def test_a_conflicting_revert_escalates_as_merge_conflict(engine_repo):
    # The base moves over the same line after the target landed: the
    # dependent-commit conflict RFC 0010 refuses to resolve.
    (engine_repo / "app.py").write_text("value = 99\n", encoding="utf-8")
    git(engine_repo, "add", "-A")
    git(engine_repo, "commit", "-q", "--no-gpg-sign", "-m", "later work")

    state = run_task(engine_repo, revert_task(), RunnerConfig(), engine_deps(engine_repo))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "merge_conflict"


def test_an_unresolvable_target_fails_loudly_before_dispatch(engine_repo):
    with pytest.raises(ValueError, match="no landed commits"):
        run_task(engine_repo, revert_task("T-0000"), RunnerConfig(),
                 engine_deps(engine_repo))


def test_a_revert_contract_names_its_targets():
    with pytest.raises(ValueError, match="revert task names what it undoes"):
        Task(id="T-1", role="revert", scope=Scope(), decisions=[])
