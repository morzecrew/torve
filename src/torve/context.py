"""The prepared inputs a gate receives (RFC 0002 §3): worktree, diff, task, log.

Everything is computed once, against `git merge-base` (never current base —
otherwise `scope` reddens on other people's work that landed mid-task), and
handed to gates read-only.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from torve import layout
from torve.manifest import Manifest
from torve.models import BypassRecord, Task
from torve.shell import ExecuteOnce

# ----------------------- #

TASK_BRANCH = re.compile(r"^torve/(T-\d+)")
BYPASS_TRAILER = re.compile(r"^Torve-Bypass:\s*([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$", re.M)


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


@dataclass(frozen=True)
class DiffEntry:
    status: str  # A M D R C T — first letter of git's name-status
    path: str
    old_path: str | None = None


@dataclass
class GateContext:
    root: Path
    manifest: Manifest
    head_sha: str
    base: str | None
    merge_base: str | None
    diff: list[DiffEntry] = field(default_factory=list)
    patch: str = ""
    untracked: list[str] = field(default_factory=list)
    task: Task | None = None
    task_path: Path | None = None
    log_path: Path | None = None
    log_text: str | None = None
    bypasses: list[BypassRecord] = field(default_factory=list)
    # Where shell gates execute. None means the host (the CI runner is the
    # sandbox in that context); `torve run` injects a fresh-sandbox executor
    # so no gate command ever runs where the agent could have staged a shim.
    execute: ExecuteOnce | None = None

    @property
    def changed_paths(self) -> list[str]:
        return [e.path for e in self.diff]


def resolve_base(root: Path, base: str | None) -> str | None:
    """The requested base ref, or the first of origin/main and main that
    exists. None means no base is resolvable (fresh repository) and diff-input
    gates run against an empty diff."""
    candidates = [base] if base else ["origin/main", "main"]
    for candidate in candidates:
        try:
            git(root, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        except GitError:
            continue
        return candidate
    if base:
        raise GitError(f"base ref {base!r} does not exist")
    return None


def _diff_entries(root: Path, merge_base: str) -> list[DiffEntry]:
    # No second revision: the diff includes uncommitted changes, so a local run
    # sees what a CI run of the same tree would. -M keeps renames as renames.
    out = git(root, "diff", "--name-status", "-M", merge_base)
    entries: list[DiffEntry] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][:1]
        if status == "R" and len(parts) == 3:
            entries.append(DiffEntry(status="R", path=parts[2], old_path=parts[1]))
        else:
            entries.append(DiffEntry(status=status, path=parts[1]))
    return entries


def _untracked(root: Path) -> list[str]:
    out = git(root, "ls-files", "--others", "--exclude-standard")
    return [line for line in out.splitlines() if line.strip()]


def parse_bypasses(root: Path, merge_base: str, head: str) -> list[BypassRecord]:
    """Torve-Bypass trailers from every commit in merge_base..head (D-2.7).

    The record carries the commit's author: the signature is authorship of a
    reviewed commit, and RFC 0010 keeps agent-authored commits identifiable, so
    a trailer minted by an agent is visible for exactly what it is.
    """
    out = git(root, "log", "--format=%H%x00%an <%ae>%x00%B%x01", f"{merge_base}..{head}")
    records: list[BypassRecord] = []
    for chunk in out.split("\x01"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        sha, author, body = chunk.split("\x00", 2)
        for match in BYPASS_TRAILER.finditer(body):
            records.append(
                BypassRecord(
                    gate=match.group(1), reason=match.group(2), author=author, commit=sha.strip()
                )
            )
    return records


def load_task(path: Path) -> Task:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: task file must be a mapping")
    return Task.model_validate(raw)


def _discover_task(root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    try:
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        return None
    match = TASK_BRANCH.match(branch)
    if not match:
        return None
    candidate = layout.task_file(root, match.group(1))
    return candidate if candidate.is_file() else None


def build_context(
    root: Path,
    manifest: Manifest,
    base: str | None = None,
    task_path: Path | None = None,
) -> GateContext:
    head_sha = git(root, "rev-parse", "HEAD").strip()
    resolved = resolve_base(root, base)

    merge_base = None
    diff: list[DiffEntry] = []
    patch = ""
    bypasses: list[BypassRecord] = []
    if resolved is not None:
        merge_base = git(root, "merge-base", resolved, "HEAD").strip()
        diff = _diff_entries(root, merge_base)
        patch = git(root, "diff", "-M", merge_base)
        bypasses = parse_bypasses(root, merge_base, head_sha)

    untracked = _untracked(root)
    diff = diff + [DiffEntry(status="A", path=p) for p in untracked]

    found = _discover_task(root, task_path)
    task = load_task(found) if found is not None else None

    log_path = None
    log_text = None
    if task is not None:
        log_path = layout.log_file(root, task.id)
        if log_path.is_file():
            log_text = log_path.read_text(encoding="utf-8")

    return GateContext(
        root=root,
        manifest=manifest,
        head_sha=head_sha,
        base=resolved,
        merge_base=merge_base,
        diff=diff,
        patch=patch,
        untracked=untracked,
        task=task,
        task_path=found,
        log_path=log_path,
        log_text=log_text,
        bypasses=bypasses,
    )
