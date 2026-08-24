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

from torve.application.ports import PrInfo

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

    def delete_remote_branch(self, root: Path, branch: str,
                             token: str | None = None) -> bool:
        """The retry command's re-queue cleanup (T-0059): delete the task's
        own remote branch — a ref deletion under the commander's explicit
        authority, never a history rewrite (D-10.5 stands). Returns True
        when the branch is gone (deleted now, or already absent — some
        transports report each differently), False when there is no
        origin; raises on anything else, so a half-applied retry refuses
        loudly."""
        remotes = _git(root, "remote")
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
            ["git", "-C", str(root), *config,
             "push", "origin", "--delete", f"refs/heads/{branch}"],
            capture_output=True, text=True, check=False, env=env,
        )
        if proc.returncode != 0:
            if "remote ref does not exist" in proc.stderr:
                return True  # already gone — the postcondition holds
            raise RuntimeError(proc.stderr.strip() or "git push --delete failed")
        return True

    def fetch_pr(self, root: Path, number: int, base_ref: str,
                 token: str | None = None) -> tuple[str, str]:
        """Fetch the pull request's head and its base branch into local
        refs and return (base_sha, head_sha). The token reaches git the
        same way push's does: a credential helper reading the runner's
        environment, never argv (D-4b)."""
        config: list[str] = []
        env = None
        if token:
            helper = ("!f() { echo username=x-access-token; "
                      'echo "password=$TORVE_PUSH_TOKEN"; }; f')
            config = ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]
            env = {**os.environ, "TORVE_PUSH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
        head_ref, base_local = f"refs/torve/pr-{number}", f"refs/torve/pr-{number}-base"
        proc = subprocess.run(
            ["git", "-C", str(root), *config, "fetch", "origin",
             f"+refs/pull/{number}/head:{head_ref}",
             f"+refs/heads/{base_ref}:{base_local}"],
            capture_output=True, text=True, check=False, env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git fetch failed")
        return (_git(root, "rev-parse", base_local).stdout.strip(),
                _git(root, "rev-parse", head_ref).stdout.strip())

    def worktree_at(self, root: Path, sha: str, workdir: Path) -> None:
        added = _git(root, "worktree", "add", "--detach", str(workdir), sha)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or f"worktree add failed at {sha}")

    def remove_worktree(self, root: Path, workdir: Path) -> None:
        _git(root, "worktree", "remove", "--force", str(workdir))

    def diff(self, root: Path, base: str, head: str) -> str:
        # Three-dot: the pull request's own changes since the merge base,
        # not the base branch's drift.
        return _git(root, "diff", f"{base}...{head}").stdout

    def task_trailers(self, root: Path, base: str, head: str) -> list[str]:
        log = _git(root, "log", "--format=%B", f"{base}..{head}").stdout
        seen: list[str] = []
        for found in re.findall(r"^Torve-Task: (T-\d{4})$", log, re.MULTILINE):
            if found not in seen:
                seen.append(found)
        return seen


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

    def tip_age_s(self, root: Path, ref: str) -> float:
        """Seconds since the ref's tip commit — the quiet window's clock
        (RFC 0006 §3): a push resets it, because the new tip is young."""
        out = _git(root, "log", "-1", "--format=%ct", ref).stdout.strip()
        return max(0.0, time.time() - float(out)) if out else 0.0

    def adopt_identical(self, root: Path, ref: str) -> list[str]:
        """D-19.11 (A-28): remove untracked root files the incoming landing
        carries with byte-identical content, so git's overwrite refusal is
        reserved for real differences. Engine records are text (contracts,
        ledgers); a file that does not decode is left for git to refuse."""
        incoming = _git(root, "diff", "--name-only", f"HEAD..{ref}").stdout.splitlines()
        tracked = set(_git(root, "ls-files").stdout.splitlines())
        adopted: list[str] = []
        for rel in (line.strip() for line in incoming):
            if not rel or rel in tracked:
                continue
            target = root / rel
            if not target.is_file():
                continue
            shown = _git(root, "show", f"{ref}:{rel}")
            try:
                same = shown.returncode == 0 and \
                    shown.stdout == target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if same:
                target.unlink()
                adopted.append(rel)
        return adopted

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


# Network-shaped failures worth one retry (T-0058). Kept in each GitHub
# adapter separately — adapters are independent and may not share code.
TRANSIENT = ("timeout", "tls handshake", "connection reset",
             "connection refused", "temporary failure", "no such host",
             "unexpected eof", "network is unreachable", "502", "503")


class GhScm:
    """Pull requests through the gh CLI — the runner speaks to the forge,
    the agent never does (D-10.1). The credential is resolved from the
    CONFIGURED environment-variable name at call time and handed to gh as
    GH_TOKEN in the subprocess environment only."""

    def __init__(self, repo: str | None = None, token_env: str | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.repo = repo
        self.token_env = token_env
        self.sleeper = sleeper

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

    def _gh(self, *args: str) -> str:
        command = ["gh", *args]
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
        for attempt in (1, 2):
            proc = subprocess.run(command, capture_output=True, text=True,
                                  check=False, env=env)
            if proc.returncode == 0:
                return proc.stdout
            error = proc.stderr.strip() or f"gh {args[0]} failed"
            # One retry for a transient transport failure (T-0058): every
            # destination write is idempotent, so at-least-once is safe.
            if attempt == 1 and any(mark in error.lower() for mark in TRANSIENT):
                self.sleeper(2.0)
                continue
            raise RuntimeError(error)
        raise RuntimeError("unreachable")  # for the type checker

    def pr_info(self, number: int) -> PrInfo:
        document = cast("dict[str, Any]", json.loads(self._gh(
            "pr", "view", str(number), "--json",
            "number,title,author,isDraft,headRefOid,baseRefName,changedFiles,state")))
        author = cast("dict[str, Any]", document.get("author") or {})
        return PrInfo(
            number=int(document["number"]),
            title=str(document.get("title", "")),
            author=str(author.get("login", "unknown")),
            draft=bool(document.get("isDraft", False)),
            head_sha=str(document.get("headRefOid", "")),
            base_ref=str(document.get("baseRefName", "")),
            changed_files=int(document.get("changedFiles", 0)),
            state=str(document.get("state", "")).lower(),
        )

    def comment(self, number: int, body: str, key: str) -> str:
        # The same marker dedupe as the tracker's comments: the destination
        # absorbs an at-least-once duplicate.
        marker = f"<!-- torve-key:{key} -->"
        existing = self._gh("pr", "view", str(number), "--json", "comments")
        if marker in existing:
            return ""
        return self._gh("pr", "comment", str(number),
                        "--body", f"{body}\n\n{marker}").strip()

    def _api(self, *args: str) -> str:
        # `gh api` takes the repo in the endpoint, never as a flag.
        env = None
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(
                    f"scm.token_env names {self.token_env!r} but the runner's "
                    "environment does not carry it"
                )
            env = {**os.environ, "GH_TOKEN": token}
        for attempt in (1, 2):
            proc = subprocess.run(["gh", "api", *args], capture_output=True,
                                  text=True, check=False, env=env)
            if proc.returncode == 0:
                return proc.stdout
            error = proc.stderr.strip() or "gh api failed"
            if attempt == 1 and any(mark in error.lower() for mark in TRANSIENT):
                self.sleeper(2.0)
                continue
            raise RuntimeError(error)
        raise RuntimeError("unreachable")  # for the type checker

    def review_threads(self, branch: str,
                       allowed: tuple[str, ...]) -> list[dict[str, Any]]:
        """The branch's pull-request review threads whose root author is
        allow-listed (RFC 0005 §4a, D-5.12): line-anchored comments only,
        whole threads — replies from anyone ride along, they carry
        resolution — attributed per comment. No pull request, or an empty
        allow-list, is an empty capture."""
        if not allowed or not self.repo:
            return []
        listed = cast("list[dict[str, Any]]", json.loads(self._gh(
            "pr", "list", "--head", branch, "--state", "all",
            "--json", "number") or "[]"))
        if not listed:
            return []
        number = int(listed[0]["number"])
        raw = cast("list[dict[str, Any]]", json.loads(self._api(
            f"repos/{self.repo}/pulls/{number}/comments", "--paginate") or "[]"))
        roots: dict[int, dict[str, Any]] = {}
        for comment in raw:
            if comment.get("in_reply_to_id") is None:
                user = cast("dict[str, Any]", comment.get("user") or {})
                author = str(user.get("login", ""))
                if author not in allowed:
                    continue
                roots[int(comment["id"])] = {
                    "path": comment.get("path"), "line": comment.get("line"),
                    "comments": [{"author": author,
                                  "body": str(comment.get("body", ""))}]}
        for comment in raw:
            parent = comment.get("in_reply_to_id")
            if parent is not None and int(parent) in roots:
                user = cast("dict[str, Any]", comment.get("user") or {})
                cast("list[dict[str, Any]]",
                     roots[int(parent)]["comments"]).append({
                    "author": str(user.get("login", "")),
                    "body": str(comment.get("body", ""))})
        return list(roots.values())

    def close_pr(self, branch: str, comment: str) -> bool:
        """Close the branch's open pull request after its content landed by
        fast-forward (T-0072): the forge cannot always tell an ff landing
        from an abandoned branch, so the engine says so itself — and
        deletes the head branch, whose commits are on the base. False when
        no open pull request exists for the branch."""
        listed = cast("list[dict[str, Any]]", json.loads(self._gh(
            "pr", "list", "--head", branch, "--state", "open",
            "--json", "number") or "[]"))
        if not listed:
            return False
        number = int(listed[0]["number"])
        self._gh("pr", "close", str(number), "--comment", comment,
                 "--delete-branch")
        return True


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
