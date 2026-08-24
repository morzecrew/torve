"""GitHub Issues as the first Tracker adapter (RFC 0008, D-8.8), through
the gh CLI with the credential resolved by NAME at call time — the GhScm
rule. One issue per task, found by its `T-nnnn:` title prefix; states are
labels; comments carry their idempotency key as an HTML marker so the
destination absorbs an at-least-once duplicate; inline annotation is
unsupported here — Issues have no file locations — and says so (D-8.6).

Inbound (D-8.3, D-8.5): comments are untrusted text. The parse is a fixed
`/torve <verb>` prefix match, allow-listed against the application's
vocabulary; anything else is not a command and is never interpreted. A
command already answered — its comment id marked in a reply — is not
returned again.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any, cast

from torve.application.ports import ReflectResult, TrackerCommand
from torve.application.tracker import COMMANDS

# ----------------------- #

COMMAND_RE = re.compile(r"^/torve\s+([a-z]+)\s*$", re.MULTILINE)
KEY_MARK = "torve-key:"

# Network-shaped failures worth one retry (T-0058). Kept in each GitHub
# adapter separately — adapters are independent and may not share code.
TRANSIENT = ("timeout", "tls handshake", "connection reset",
             "connection refused", "temporary failure", "no such host",
             "unexpected eof", "network is unreachable", "502", "503")


class GithubIssues:
    def __init__(self, repo: str, token_env: str | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.repo = repo
        self.token_env = token_env
        self.sleeper = sleeper
        self._issues: dict[str, int] = {}  # projection cache, never authority

    def _run(self, command: list[str]) -> str:
        env = None
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(
                    f"tracker.token_env names {self.token_env!r} but the "
                    "runner's environment does not carry it"
                )
            env = {**os.environ, "GH_TOKEN": token}
        for attempt in (1, 2):
            proc = subprocess.run(command, capture_output=True, text=True,
                                  check=False, env=env)
            if proc.returncode == 0:
                return proc.stdout
            error = proc.stderr.strip() or f"{command[1]} failed"
            # One retry for a transient transport failure (T-0058): every
            # destination write is idempotent, so at-least-once is safe.
            if attempt == 1 and any(mark in error.lower() for mark in TRANSIENT):
                self.sleeper(2.0)
                continue
            raise RuntimeError(error)
        raise RuntimeError("unreachable")  # for the type checker

    def _gh(self, *args: str) -> str:
        return self._run(["gh", *args, "--repo", self.repo])

    def _api(self, *args: str) -> str:
        # `gh api` takes the repo in the endpoint, never as a flag.
        return self._run(["gh", "api", *args])

    def _issue_for(self, task_id: str, title: str, create: bool = True) -> int | None:
        if task_id in self._issues:
            return self._issues[task_id]
        listed = cast("list[dict[str, Any]]", json.loads(self._gh(
            "issue", "list", "--state", "all", "--search", f"{task_id} in:title",
            "--json", "number,title") or "[]"))
        for issue in listed:
            if str(issue.get("title", "")).startswith(f"{task_id}:"):
                self._issues[task_id] = int(issue["number"])
                return self._issues[task_id]
        if not create:
            return None
        out = self._gh("issue", "create", "--title", title,
                       "--body", "projection of the torve run store — "
                                 "the store is the authority, this issue is a view")
        number = int(out.strip().rsplit("/", 1)[-1])
        self._issues[task_id] = number
        return number

    def _set_label(self, number: int, label: str) -> None:
        # Labels are created idempotently, then applied — and the board
        # wears one state label at a time (D-8.12): stale state siblings
        # are retired, history stays in the comments and the store.
        listed = cast("dict[str, Any]", json.loads(
            self._gh("issue", "view", str(number), "--json", "labels") or "{}"))
        for worn in cast("list[dict[str, Any]]", listed.get("labels", [])):
            name = str(worn.get("name", ""))
            if name.startswith("state:") and name != label:
                self._gh("issue", "edit", str(number), "--remove-label", name)
        self._gh("label", "create", label, "--force", "--color", "5319e7")
        self._gh("issue", "edit", str(number), "--add-label", label)

    def label(self, task_id: str, name: str) -> ReflectResult:
        # A plain label (D-8.13/D-8.14): applied to an existing issue —
        # never creating one — and never touching the state:* family.
        number = self._issue_for(task_id, f"{task_id}: task", create=False)
        if number is None:
            return ReflectResult("applied", "no issue to label")
        self._gh("label", "create", name, "--force", "--color", "0e8a16")
        self._gh("issue", "edit", str(number), "--add-label", name)
        return ReflectResult("applied", f"issue #{number} labelled {name}")

    def link(self, task_id: str, parent_task_id: str) -> ReflectResult:
        """Sub-issue nesting (D-8.15): the review indents under the work it
        reviews. Links existing issues only; a forge or plan without the
        concept refuses, which the relay logs as a divergence (D-8.6)."""
        child = self._issue_for(task_id, f"{task_id}: task", create=False)
        parent = self._issue_for(parent_task_id, f"{parent_task_id}: task",
                                 create=False)
        if child is None or parent is None:
            return ReflectResult("applied", "nothing to link")
        try:
            child_id = self._api(f"repos/{self.repo}/issues/{child}",
                                 "--jq", ".id").strip()
            self._api("-X", "POST",
                      f"repos/{self.repo}/issues/{parent}/sub_issues",
                      "-F", f"sub_issue_id={child_id}")
        except RuntimeError as error:
            if "already" in str(error).lower():
                return ReflectResult("applied", "already linked")
            return ReflectResult("refused", str(error))
        return ReflectResult("applied", f"#{child} nested under #{parent}")

    def reflect(self, task_id: str, state: str, title: str) -> ReflectResult:
        if state == "landed":
            # The close-out for landed work (D-8.11): no issue means
            # nothing to close — never create one just to close it.
            number = self._issue_for(task_id, title, create=False)
            if number is None:
                return ReflectResult("applied", "no issue to close")
            self._set_label(number, "state:landed")
            self._gh("issue", "close", str(number))
            return ReflectResult("applied", f"issue #{number} closed: landed")
        number = self._issue_for(task_id, title)
        assert number is not None
        if state == "created":
            return ReflectResult("applied", f"issue #{number}")
        self._set_label(number, f"state:{state}")
        if state == "abandoned":
            self._gh("issue", "close", str(number))
        return ReflectResult("applied", f"issue #{number} labelled state:{state}")

    def comment(self, task_id: str, body: str, key: str) -> ReflectResult:
        number = self._issue_for(task_id, f"{task_id}: task")
        assert number is not None
        marker = f"<!-- {KEY_MARK}{key} -->"
        existing = self._gh("issue", "view", str(number), "--json", "comments")
        if marker in existing:
            # The at-least-once duplicate, absorbed at the destination.
            return ReflectResult("applied", "already commented")
        self._gh("issue", "comment", str(number), "--body", f"{body}\n\n{marker}")
        return ReflectResult("applied", f"comment on #{number}")

    def notify(self, task_id: str, login: str, body: str, key: str) -> ReflectResult:
        # The @mention comment is the notification; assignment is
        # best-effort decoration (a login the forge cannot assign — not a
        # collaborator — must not leave the notification pending forever).
        number = self._issue_for(task_id, f"{task_id}: task")
        assert number is not None
        assigned = "assigned"
        try:
            self._gh("issue", "edit", str(number), "--add-assignee", login)
        except RuntimeError as error:
            assigned = f"assign failed: {error}"
        result = self.comment(task_id, f"@{login} — {body}", key)
        if result.outcome != "applied":
            return result
        return ReflectResult("applied", f"notified @{login} on #{number} ({assigned})")

    def annotate(self, task_id: str, location: str, body: str, key: str) -> ReflectResult:
        # Issues have no inline file annotations: the D-8.6 unsupported
        # path, honestly — the finding still reaches the issue as a comment
        # through the caller's divergence handling if it chooses to.
        return ReflectResult("unsupported",
                             "github issues carry no inline annotations")

    def poll_commands(self) -> list[TrackerCommand]:
        commands: list[TrackerCommand] = []
        listed = cast("list[dict[str, Any]]", json.loads(self._gh(
            "issue", "list", "--state", "all", "--search", "in:title T-",
            "--json", "number,title") or "[]"))
        for issue in listed:
            title = str(issue.get("title", ""))
            task_id = title.split(":", 1)[0].strip()
            if not re.fullmatch(r"T-\d{4}", task_id):
                continue
            detail = cast("dict[str, Any]", json.loads(self._gh(
                "issue", "view", str(issue["number"]), "--json", "comments")))
            comments = cast("list[dict[str, Any]]", detail.get("comments", []))
            answered = {m for c in comments
                        for m in re.findall(re.escape(KEY_MARK) + r"cmd:(\S+?) ",
                                            str(c.get("body", "")))}
            for comment in comments:
                body = str(comment.get("body", ""))
                if KEY_MARK in body:
                    continue  # our own projections are never commands
                found = COMMAND_RE.search(body)
                if not found or found.group(1) not in COMMANDS:
                    continue
                source = str(comment.get("url", "")).rsplit("-", 1)[-1] or str(hash(body))
                if source in answered:
                    continue
                author = cast("dict[str, Any]", comment.get("author") or {})
                commands.append(TrackerCommand(
                    verb=found.group(1), task_id=task_id,
                    actor=str(author.get("login", "unknown")), source=source))
        return commands
