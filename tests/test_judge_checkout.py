"""S-0114/D-1: the judge checkout. A gate reads only what the commit would
carry and can run git over the candidate's history, so the host snapshots a
candidate's stageable state — tracked changes and untracked files, never an
ignored one — without moving the worktree's index or branch, and builds a
standalone, detached checkout of that snapshot alone."""

from __future__ import annotations

import subprocess

from torve.adapters.workspace.git import GitWorkspace
from torve.application.ports import JUDGE_SUFFIX, judge_ref
from torve.base import naming

# ----------------------- #


def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    return root


def out(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def code(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    ).returncode


def task_worktree(root, task_id):
    """The task's worktree with a tracked change, a new file and an ignored
    file the attempt wrote."""

    worktree = GitWorkspace(root).create(task_id, "HEAD")
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (worktree / "added.txt").write_text("new\n", encoding="utf-8")
    (worktree / "ignored.txt").write_text("not staged\n", encoding="utf-8")
    return worktree


# ....................... #


def test_snapshot_stages_tracked_and_new_files_but_never_an_ignored_one(tmp_path):
    root = repo(tmp_path)
    worktree = task_worktree(root, "T-1001")
    before_status = out(worktree, "status", "--porcelain")
    before_head = out(worktree, "rev-parse", "HEAD")
    before_branch = out(worktree, "rev-parse", "--abbrev-ref", "HEAD")

    GitWorkspace(root).judge_checkout("T-1001")

    snapshot = out(root, "rev-parse", judge_ref("T-1001"))
    assert out(root, "show", f"{snapshot}:tracked.txt") == "changed"
    assert out(root, "show", f"{snapshot}:added.txt") == "new"
    # The ignored file is in no commit, so it is in no snapshot.
    assert code(root, "cat-file", "-e", f"{snapshot}:ignored.txt") != 0

    # The worktree's index and branch are exactly as they were: the real index
    # was never written, and no ref moved.
    assert out(worktree, "status", "--porcelain") == before_status
    assert out(worktree, "rev-parse", "HEAD") == before_head
    assert out(worktree, "rev-parse", "--abbrev-ref", "HEAD") == before_branch


# ....................... #


def test_the_checkout_is_a_standalone_detached_snapshot(tmp_path):
    root = repo(tmp_path)
    worktree = task_worktree(root, "T-1002")
    base = out(worktree, "rev-parse", "HEAD")

    path = GitWorkspace(root).judge_checkout("T-1002")

    assert path == root / naming.WORKTREE_DIR / f"T-1002{JUDGE_SUFFIX}"
    # git runs in the checkout — it is a real repository of its own, not a
    # worktree of the root.
    assert out(path, "rev-parse", "--is-inside-work-tree") == "true"
    # Its HEAD is the snapshot, checked out detached.
    snapshot = out(root, "rev-parse", judge_ref("T-1002"))
    assert out(path, "rev-parse", "HEAD") == snapshot
    assert code(path, "symbolic-ref", "-q", "HEAD") != 0
    # The tracked change and the new file are there; the ignored one is not.
    assert (path / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
    assert (path / "added.txt").read_text(encoding="utf-8") == "new\n"
    assert not (path / "ignored.txt").exists()
    # Its history ends at the snapshot: the fetch transferred only the objects
    # the snapshot reaches, so the base it was cut from is not among them.
    assert out(path, "rev-list", "--count", "HEAD") == "1"
    assert code(path, "cat-file", "-e", base) != 0


# ....................... #


def test_remove_judge_takes_the_checkout_and_its_ref(tmp_path):
    root = repo(tmp_path)
    task_worktree(root, "T-1003")
    workspace = GitWorkspace(root)
    path = workspace.judge_checkout("T-1003")
    assert code(root, "rev-parse", "--verify", "--quiet", judge_ref("T-1003")) == 0

    workspace.remove_judge("T-1003")

    assert not path.exists()
    assert code(root, "rev-parse", "--verify", "--quiet", judge_ref("T-1003")) != 0


# ....................... #


def test_a_shadow_replay_judges_a_snapshot_of_its_own_clone(tmp_path):
    """S-0114/D-2: a replay's clone is a repository of its own, not a worktree
    of the root, so it opens its own judge checkout: the clone's stageable
    state, written to the clone's judge ref, with the replayed parent in its
    history and the ignored file the replay wrote nowhere in it."""

    from torve.adapters.workspace.git import ShadowWorkspace

    root = repo(tmp_path)
    parent = out(root, "rev-parse", "HEAD")
    shadow = ShadowWorkspace(root, depth=10)
    clone = shadow.create("T-2001", parent)
    (clone / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (clone / "added.txt").write_text("new\n", encoding="utf-8")
    (clone / "ignored.txt").write_text("not staged\n", encoding="utf-8")
    before_status = out(clone, "status", "--porcelain")

    path = shadow.judge_checkout("T-2001")

    assert path == root / naming.WORKTREE_DIR / f"{naming.shadow_id('T-2001')}{JUDGE_SUFFIX}"
    assert out(path, "rev-parse", "HEAD") == out(clone, "rev-parse", judge_ref("T-2001"))
    assert (path / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
    assert (path / "added.txt").read_text(encoding="utf-8") == "new\n"
    assert not (path / "ignored.txt").exists()
    # The replayed parent is the gates' base, and the checkout holds it.
    assert code(path, "cat-file", "-e", f"{parent}^{{commit}}") == 0
    # The clone's index is untouched, and the root holds no ref of the replay.
    assert out(clone, "status", "--porcelain") == before_status
    assert code(root, "rev-parse", "--verify", "--quiet", judge_ref("T-2001")) != 0

    shadow.remove_judge("T-2001")

    assert not path.exists()
