"""Vcs/Scm adapters (RFC 0010 §2, grown from the RFC 0003 skeleton). The
commit is the runner's artefact: author is the agent identity the runner
passes in (D-10.2 — never a human), committer is Torve, and when a signing
key path is configured the commit is SSH-signed here, at the runner
boundary, with a key no sandbox ever saw (D-10.3). Revert is mechanical
git — `revert --no-commit` staging the inverse tree for the normal landing
commit (one commit per attempt, D-10.8); a conflicted revert aborts and
returns False, the engine never resolves one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

# ----------------------- #


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False
    )


def repository_name(root: Path) -> str:
    """The name provider routing keys on (RFC 0004 §6b): `org/repo` from the
    origin remote when one exists, the directory name otherwise — stable
    across checkouts, which a path is not."""
    proc = _git(root, "remote", "get-url", "origin")
    if proc.returncode == 0:
        found = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?/?$", proc.stdout.strip())
        if found:
            return found.group(1)
    return root.resolve().name


class GitVcs:
    def commit_all(self, worktree: Path, message: str, author: str | None = None,
                   sign_key: str | None = None) -> str | None:
        _git(worktree, "add", "-A")
        status = _git(worktree, "status", "--porcelain")
        if not status.stdout.strip():
            return None
        config = ["-c", "user.name=Torve", "-c", "user.email=torve@local"]
        commit = ["commit", "-m", message]
        if sign_key:
            config += ["-c", "gpg.format=ssh", "-c", f"user.signingkey={sign_key}"]
            commit.append("-S")
        else:
            commit.append("--no-gpg-sign")
        if author:
            commit += ["--author", author]
        proc = _git(worktree, *config, *commit)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git commit failed")
        return _git(worktree, "rev-parse", "HEAD").stdout.strip()

    def landed_shas(self, worktree: Path, task_id: str) -> list[str]:
        """The commits a task landed, newest first — reconstructed from the
        Torve-Task trailer alone (D-10.4: git log is the surviving record)."""
        proc = _git(worktree, "log", "--format=%H", "--fixed-strings",
                    f"--grep=Torve-Task: {task_id}")
        return [line for line in proc.stdout.split() if line]

    def revert(self, worktree: Path, shas: list[str]) -> bool:
        """Stage the inverse of the given commits without committing — the
        landing commit carries the revert's own provenance. A conflict
        aborts, leaves the worktree clean, and returns False."""
        proc = _git(worktree, "-c", "user.name=Torve", "-c", "user.email=torve@local",
                    "revert", "--no-commit", *shas)
        if proc.returncode == 0:
            return True
        _git(worktree, "revert", "--abort")
        _git(worktree, "reset", "--hard")
        return False

    def push(self, worktree: Path, branch: str, token: str | None = None) -> bool:
        """Push targets only the task's own branch — additive, no force path
        exists (D-10.5 held structurally). The token, when given, reaches git
        through a credential helper reading the runner's environment: never
        on argv, never in the worktree (D-4b)."""
        remotes = _git(worktree, "remote")
        if "origin" not in remotes.stdout.split():
            return False
        config: list[str] = []
        env = None
        if token:
            helper = ("!f() { echo username=x-access-token; "
                      'echo "password=$TORVE_PUSH_TOKEN"; }; f')
            config = ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]
            env = {**os.environ, "TORVE_PUSH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(
            ["git", "-C", str(worktree), *config,
             "push", "-u", "origin", f"HEAD:refs/heads/{branch}"],
            capture_output=True, text=True, check=False, env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git push failed")
        return True


class GitLane:
    """The lane's git surface (RFC 0006 §1). The rebase happens in a
    disposable worktree so the operator's checkout never moves; a conflicted
    rebase aborts and removes it — the engine never resolves a conflict."""

    def tip(self, root: Path, ref: str) -> str | None:
        proc = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return proc.stdout.strip() or None if proc.returncode == 0 else None

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool:
        return _git(root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0

    def current_branch(self, root: Path) -> str:
        return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def dirty_paths(self, root: Path) -> list[str]:
        paths: list[str] = []
        for line in _git(root, "status", "--porcelain").stdout.splitlines():
            if not line.strip():
                continue
            # Porcelain v1: two status columns, a space, then the path —
            # renames carry "orig -> dest" and the destination is the dirt.
            entry = line[3:].split(" -> ")[-1].strip().strip('"')
            paths.append(entry)
        return paths

    def rebase_in_worktree(self, root: Path, branch: str, onto: str, workdir: Path) -> bool:
        added = _git(root, "worktree", "add", str(workdir), branch)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or f"worktree add failed for {branch}")
        rebased = _git(workdir, "rebase", onto)
        if rebased.returncode != 0:
            _git(workdir, "rebase", "--abort")
            self.remove_worktree(root, workdir)
            return False
        return True

    def remove_worktree(self, root: Path, workdir: Path) -> None:
        _git(root, "worktree", "remove", "--force", str(workdir))

    def merge_ff(self, root: Path, ref: str) -> str:
        proc = _git(root, "merge", "--ff-only", ref)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"fast-forward to {ref} refused")
        return _git(root, "rev-parse", "HEAD").stdout.strip()

    def approver(self, root: Path) -> str:
        return _git(root, "config", "user.name").stdout.strip() or "unknown"


class GhScm:
    """Pull requests through the gh CLI — the runner speaks to the forge,
    the agent never does (D-10.1). The credential is resolved from the
    CONFIGURED environment-variable name at call time and handed to gh as
    GH_TOKEN in the subprocess environment only."""

    def __init__(self, repo: str | None = None, token_env: str | None = None) -> None:
        self.repo = repo
        self.token_env = token_env

    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str:
        command = ["gh", "pr", "create", "--head", branch,
                   "--title", title, "--body", body]
        if self.repo:
            command += ["--repo", self.repo]
        env = None
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(
                    f"scm.token_env names {self.token_env!r} but the runner's "
                    "environment does not carry it"
                )
            env = {**os.environ, "GH_TOKEN": token}
        proc = subprocess.run(command, capture_output=True, text=True,
                              check=False, cwd=worktree, env=env)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "gh pr create failed")
        return proc.stdout.strip()


class NullScm:
    """The --no-pr mode: no remote exists yet, so the PR leg is recorded as
    deferred rather than silently skipped."""

    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str:
        return ""


class GhCi:
    """CI verdict for a commit via the gh CLI (RFC 0006 §3): the workflow
    runs endpoint filtered by head sha — lightweight, polled with backoff
    because the rate budget is shared with the agents (§1). The credential
    follows GhScm's rule: resolved by NAME at call time, environment only."""

    def __init__(self, repo: str, token_env: str | None = None,
                 attempts: int = 6, delay_s: float = 20.0,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.repo = repo
        self.token_env = token_env
        self.attempts = attempts
        self.delay_s = delay_s
        self.sleeper = sleeper

    def _runs(self, sha: str) -> list[dict[str, Any]]:
        env = None
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(
                    f"scm.token_env names {self.token_env!r} but the runner's "
                    "environment does not carry it"
                )
            env = {**os.environ, "GH_TOKEN": token}
        proc = subprocess.run(
            ["gh", "api", f"repos/{self.repo}/actions/runs?head_sha={sha}",
             "--jq", "[.workflow_runs[] | {status, conclusion, workflow_id}]"],
            capture_output=True, text=True, check=False, env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "gh api workflow runs failed")
        loaded: object = json.loads(proc.stdout or "[]")
        return cast("list[dict[str, Any]]", loaded)

    def conclusion(self, sha: str) -> str:
        verdict = "absent"
        for attempt in range(self.attempts):
            # Newest first from the API; only the latest run of each
            # workflow counts — a re-run supersedes the run it replaces,
            # and a stale failure must not veto a green rerun.
            latest: dict[object, dict[str, Any]] = {}
            for run in self._runs(sha):
                latest.setdefault(run.get("workflow_id"), run)
            if not latest:
                verdict = "absent"  # the remote may not have started yet
            elif any(r.get("status") != "completed" for r in latest.values()):
                verdict = "pending"
            else:
                conclusions = {str(r.get("conclusion")) for r in latest.values()}
                return "success" if conclusions == {"success"} else \
                    next(c for c in sorted(conclusions) if c != "success")
            if attempt + 1 < self.attempts:
                self.sleeper(self.delay_s)
        return verdict
