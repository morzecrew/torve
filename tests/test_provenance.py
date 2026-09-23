"""S-0010 phase 1: the commit as the runner's provenance record — agent
author, Torve committer, full trailers, signing at the runner boundary — and
revert as a mechanical role through the same loop, gates and landing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_decisions import landed
from test_run_loop import OK, MockRuntime, MockScm, ScriptedAgent

import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.adapters.vcs.git import GitVcs
from torve.adapters.workspace.git import GitWorkspace
from torve.application.dispatch import RunDeps
from torve.application.runner import run_task
from torve.base import naming
from torve.config.runconfig import RunnerConfig
from torve.domain.spec import Landing
from torve.domain.states import TaskState
from torve.domain.task import Scope, Task


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
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
        vcs_repo,
        "torve(T-8101): attempt 1 green\n\nTorve-Task: T-8101",
        author="harness/deepseek-chat@v3 <agents@torve.local>",
    )
    assert sha
    author, email, committer = git(vcs_repo, "log", "-1", "--format=%an|%ae|%cn").split("|")
    assert author == "harness/deepseek-chat@v3"
    assert email == "agents@torve.local"
    assert committer == "Torve"


def test_landed_commits_reconstruct_a_task_from_its_landings(vcs_repo):
    """S-0059/D-12: a task's commits come from the landings the tree holds,
    newest first — what the `Torve-Task` trailer and a `git log --grep`
    answered until the landing file named the commit. A tree without git
    answers this too."""

    from torve.application.decisions import landed_commits

    vcs = GitVcs()
    shas = []

    for n in (1, 2):
        (vcs_repo / "app.py").write_text(f"value = {n + 1}\n", encoding="utf-8")
        sha = vcs.commit_all(vcs_repo, f"torve(T-8102): attempt {n} green")
        assert sha
        shas.append(sha)
        landed(vcs_repo, "T-8102", sha, attempt=n, at=f"2026-09-09T12:0{n}:00Z")

    specs = vcs_repo / ".torve" / "specs"

    assert landed_commits(vcs_repo, specs, "T-8102") == [shas[1], shas[0]]
    assert landed_commits(vcs_repo, specs, "T-9999") == []


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="no ssh-keygen")
def test_a_signed_commit_with_the_key_outside_the_worktree(vcs_repo, tmp_path):
    # The S-0010/exit-criteria criterion in miniature: the key lives beside the runner,
    # never under the tree the sandbox sees, and verification succeeds.
    keydir = tmp_path / "runner-keys"
    keydir.mkdir()
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(keydir / "signing")], check=True
    )
    (vcs_repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    sha = GitVcs().commit_all(
        vcs_repo,
        "torve(T-8103): attempt 1 green",
        author="fake <agents@torve.local>",
        sign_key=str(keydir / "signing"),
    )
    assert sha
    # Committed under the host's identity when a key signs: the forge checks
    # the signature against the account holding the key through the
    # committer's email, so `torve@local` would verify nowhere but here.
    assert git(vcs_repo, "log", "-1", "--format=%cn|%ce") == "A Human|human@example.invalid"
    assert git(vcs_repo, "log", "-1", "--format=%an") == "fake"
    signers = tmp_path / "allowed_signers"
    pubkey = (keydir / "signing.pub").read_text(encoding="utf-8").strip()
    signers.write_text(f"human@example.invalid {pubkey}\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(vcs_repo),
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={signers}",
            "verify-commit",
            sha,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not list(vcs_repo.rglob("signing*"))  # the key never entered the tree


def test_workspace_resume_cuts_from_the_branch_tip_not_base(vcs_repo):
    # S-0026/D-9: a continuation checks out whatever the branch already carries
    # — its own candidate tip — instead of resetting it back to base.
    task_id = "T-8199"
    ws = GitWorkspace(vcs_repo)

    path = ws.create(task_id, "main")
    (path / "wip.txt").write_text("checkpoint\n", encoding="utf-8")
    git(path, "add", "-A")
    git(
        path,
        "-c",
        "user.name=Torve",
        "-c",
        "user.email=torve@local",
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "wip",
    )
    checkpoint_sha = git(path, "rev-parse", "HEAD")

    # The base moves on independently of the task's own branch.
    (vcs_repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(vcs_repo, "add", "-A")
    git(vcs_repo, "commit", "-q", "--no-gpg-sign", "-m", "later base work")

    resumed = ws.create(task_id, "main", resume=True)
    assert git(resumed, "rev-parse", "HEAD") == checkpoint_sha
    assert (resumed / "wip.txt").read_text() == "checkpoint\n"

    restarted = ws.create(task_id, "main", resume=False)
    assert git(restarted, "rev-parse", "HEAD") == git(vcs_repo, "rev-parse", "main")
    assert not (restarted / "wip.txt").exists()


def test_workspace_recut_keeps_the_branch_s_checkpoint_under_a_ref(vcs_repo):
    """A recut resets the task's branch to base; a checkpoint the branch held
    and no landing carried is kept under `refs/torve/checkpoints/<task>/<sha>`
    first (bloomery T-0020: the engine's own S-0086/D-2 commit was orphaned by
    the rerun). A branch the base already holds leaves no ref."""
    task_id = "T-8198"
    ws = GitWorkspace(vcs_repo)

    path = ws.create(task_id, "main")
    (path / "wip.txt").write_text("checkpoint\n", encoding="utf-8")
    git(path, "add", "-A")
    git(
        path,
        "-c",
        "user.name=Torve",
        "-c",
        "user.email=torve@local",
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "torve(T-8198): attempt 1 escalated from review",
    )
    checkpoint_sha = git(path, "rev-parse", "HEAD").strip()
    ws.remove(task_id)

    recut = ws.create(task_id, "main")

    assert git(recut, "rev-parse", "HEAD").strip() == git(vcs_repo, "rev-parse", "main").strip()
    ref = f"refs/torve/checkpoints/{task_id}/{checkpoint_sha[:12]}"
    assert git(vcs_repo, "rev-parse", ref).strip() == checkpoint_sha
    ws.remove(task_id)

    # Nothing to keep: the branch sits exactly on base.
    ws.create(task_id, "main")
    ws.remove(task_id)
    ws.create(task_id, "main")
    refs = git(vcs_repo, "for-each-ref", f"refs/torve/checkpoints/{task_id}/")
    assert refs.strip().count("\n") == 0 and checkpoint_sha[:12] in refs


def test_workspace_resume_with_no_prior_branch_falls_back_to_base(vcs_repo):
    # A budget-exhausted first attempt that never wrote anything leaves the
    # branch never created (S-0001/D-36 base HEAD); resume then has nothing to
    # cut from and behaves exactly like a fresh dispatch.
    ws = GitWorkspace(vcs_repo)
    path = ws.create("T-8198", "main", resume=True)
    assert git(path, "rev-parse", "HEAD") == git(vcs_repo, "rev-parse", "main")


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
    (root / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    (root / ".gitignore").write_text(".wt/\n.torve/telemetry.jsonl\n", encoding="utf-8")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", "init")
    # The target task's work commit and the landing that names it — what
    # makes it findable later (S-0059/D-9, S-0059/D-12).
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    shipped = GitVcs().commit_all(root, "torve(T-8200): attempt 1 green")
    assert shipped
    landed(root, "T-8200", shipped)
    GitVcs().commit_all(root, "torve(T-8200): landing of attempt 1")

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], ""

    monkeypatch.setattr(run_module, "run_gate_pass", scripted_gates)
    return root


def revert_task(target: str = "T-8200") -> Task:
    from torve.domain.task import InheritedDecision

    return Task(
        id="T-8201",
        role="revert",
        targets=[target],
        scope=Scope(),
        decisions=[InheritedDecision(id="D-77", grade="LOCKED", text="the reverted rule")],
    )


def engine_deps(root: Path) -> RunDeps:
    return RunDeps(
        workspace=GitWorkspace(root),
        runtime=MockRuntime(),
        agent=ScriptedAgent([OK]),
        vcs=GitVcs(),
        scm=MockScm(),
        store=open_store,
    )


def _landing_of(root: Path, branch: str, task_id: str) -> Landing:
    """The landing the branch carries for one task, as the tree holds it."""

    import yaml

    names = git(root, "ls-tree", "-r", "--name-only", branch).splitlines()
    found = [n for n in names if f"/{task_id}-" in n and "/execution/" in n]

    assert len(found) == 1, found

    return Landing.model_validate(yaml.safe_load(git(root, "show", f"{branch}:{found[0]}")))


def test_a_revert_runs_as_a_task_and_lands_with_its_own_provenance(engine_repo):
    state = run_task(engine_repo, revert_task(), RunnerConfig(), engine_deps(engine_repo))
    assert state.state is TaskState.READY, state.history

    branch = naming.branch("T-8201")
    # S-0059/D-9: two commits per attempt — the work, then the landing that
    # names it, and no `Torve-` trailer on either (S-0059/D-10).
    subjects = git(engine_repo, "log", "-2", "--format=%s", branch).splitlines()

    assert subjects == ["torve(T-8201): landing of attempt 1", "torve(T-8201): attempt 1 green"]

    bodies = git(engine_repo, "log", "-2", "--format=%B", branch)

    assert "Torve-" not in bodies
    assert git(engine_repo, "show", f"{branch}:app.py") == "value = 1"

    work = git(engine_repo, "rev-parse", f"{branch}~1")
    landing = _landing_of(engine_repo, branch, "T-8201")

    # The landing carries what the five trailers said: the task, the
    # attempt, the agent — mechanical, named for what it is — the commit it
    # landed, and the inherited row with its grade.
    assert (landing.task, landing.attempt, landing.agent) == ("T-8201", 1, "revert")
    assert landing.commit == work
    assert [(one.id, one.grade) for one in landing.decisions] == [("D-77", "LOCKED")]
    # The machine-written resolved entry survived into the landed tree.
    assert landing.entries[0].kind == "resolved"
    log = git(engine_repo, "show", f"{branch}:.torve/tasks/T-8201/log.yaml")
    assert "kind: resolved" in log
    assert "undone by T-8201" in log


def test_a_conflicting_revert_escalates_as_merge_conflict(engine_repo):
    # The base moves over the same line after the target landed: the
    # dependent-commit conflict S-0010 refuses to resolve.
    (engine_repo / "app.py").write_text("value = 99\n", encoding="utf-8")
    git(engine_repo, "add", "-A")
    git(engine_repo, "commit", "-q", "--no-gpg-sign", "-m", "later work")

    state = run_task(engine_repo, revert_task(), RunnerConfig(), engine_deps(engine_repo))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "merge_conflict"


def test_an_unresolvable_target_fails_loudly_before_dispatch(engine_repo):
    with pytest.raises(ValueError, match="has no landing naming a commit"):
        run_task(engine_repo, revert_task("T-0000"), RunnerConfig(), engine_deps(engine_repo))


def test_a_revert_contract_names_its_targets():
    with pytest.raises(ValueError, match="revert task names what it undoes"):
        Task(id="T-1", role="revert", scope=Scope(), decisions=[])
