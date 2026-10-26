"""`scope` — files outside `allow` or inside `deny` (S-0002/scope-in-detail).

deny wins over allow; an empty allow means unconstrained. The task's scope
governs when a task exists; the manifest's scope otherwise. The task's own
contract and log files are implicitly in scope — other gates require the log
to exist, so a scope that forbids writing it would deadlock the task.

Three rules admit a path the declared globs do not, each reported as
*promoted* (S-0104/D-7): a task's contingent neighbours (S-0104/D-1), a file
the manifest's `scope.derived` map pairs with an in-scope source (S-0104/D-5),
and a managed `AGENTS.md` beside a path in scope (S-0104/D-6), which `torve
spec project` writes. Nothing is widened silently: the gate names every path
it let through.

A modified file outside allow whose change is only whitespace — at line ends,
a final newline, blank lines — is still a touch outside the scope, and red
(S-0055/D-17), but reported apart: it is a formatter's reach, not a scope drawn
too narrow, and the remedy is to undo it.
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from torve.config import layout
from torve.config.manifest import Gate
from torve.gates.context import GateContext, GitError, git
from torve.gates.contract import BuiltinOutcome, spec

# ----------------------- #

#: The managed projection beside a directory's code (S-0054/D-7); allowed
#: beside any path in scope (S-0104/D-6).
AGENTS_FILE = "AGENTS.md"


def _derived_allowed(
    derived: dict[str, list[str]],
    in_declared: pathspec.GitIgnoreSpec | None,
    contingent: set[str],
) -> set[str]:
    """The derived map applied both ways (S-0104/D-5): the riders of an
    in-scope source, and the source of an in-scope rider."""

    def held(path: str) -> bool:
        return (in_declared is not None and in_declared.match_file(path)) or path in contingent

    found: set[str] = set()

    for source, riders in derived.items():
        if held(source):
            found.update(riders)

        if any(held(rider) for rider in riders):
            found.add(source)

    return found


def _whitespace_only(ctx: GateContext, paths: set[str]) -> set[str]:
    """The modified *paths* whose change is whitespace alone: at line ends, a
    final newline, blank lines — what end-of-file and trailing-whitespace
    fixers write. Indentation counts, since in Python it is the code. Compared
    here, not by `git diff --ignore-space-at-eol --name-only`: an older git
    still lists such a file there."""

    if ctx.merge_base is None:
        return set()

    def lines(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if line.strip()]

    found: set[str] = set()

    for path in paths:
        try:
            before = git(ctx.root, "show", f"{ctx.merge_base}:{path}")
            after = (ctx.root / path).read_text(encoding="utf-8")
        except (GitError, OSError, UnicodeDecodeError):
            continue  # unreadable on either side: judged as any other touch

        if lines(before) == lines(after):
            found.add(path)

    return found


def check_scope(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    scope = ctx.task.scope if ctx.task is not None else ctx.manifest.scope
    implicit: set[str] = set()
    prefixes: set[str] = set()

    if ctx.task is not None:
        # Canonical and legacy locations alike (S-0013, S-0001/A-5): the gate
        # judges repositories on either side of the layout migrations.
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/contract.yaml")
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/log.yaml")

        # S-0057/D-7, S-0058/D-6: the landing goes beside the rows it cites,
        # in the execution directory of the document the contract names —
        # written by the engine at landing, so it is the task's own like its
        # log. The directory is the identifier under the corpus the context
        # names (S-0059/D-2); no lookup.
        if ctx.task.spec:
            prefixes.add(f"{ctx.specs.rstrip('/')}/{ctx.task.spec}/execution/")
        else:
            # S-0059/D-11: a task naming no document lands here instead.
            prefixes.add(f"{layout.TORVE_DIR}/execution/")

        for prefix in (f"{layout.TORVE_DIR}/", ""):
            implicit.add(f"{prefix}logs/{ctx.task.id}.yaml")
            implicit.add(f"{prefix}tasks/{ctx.task.id}.yaml")

    allow = spec(scope.allow) if scope.allow else None
    deny = spec(scope.deny) if scope.deny else None

    contingent = set(scope.contingent)
    # S-0104/D-5: the derived map lives on the manifest, applied against
    # whichever scope governs this run.
    derived_allowed = _derived_allowed(ctx.manifest.scope.derived, allow, contingent)

    # A managed `AGENTS.md` beside a scoped path (S-0104/D-6): the projection
    # sits in the same directory as the code it governs.
    scoped_dirs = {
        Path(entry.path).parent.as_posix()
        for entry in ctx.diff
        if entry.path
        and Path(entry.path).name != AGENTS_FILE
        and (
            (allow is not None and allow.match_file(entry.path))
            or entry.path in contingent
            or entry.path in derived_allowed
        )
    }

    def implicit_or_prefix(path: str) -> bool:
        return path in implicit or any(path.startswith(prefix) for prefix in prefixes)

    promoted: set[str] = set()
    denied: list[str] = []
    outside: list[str] = []

    for entry in ctx.diff:
        for path in filter(None, (entry.path, entry.old_path)):
            if implicit_or_prefix(path):
                continue

            if deny is not None and deny.match_file(path):
                denied.append(path)
                continue

            if allow is None or allow.match_file(path):
                continue  # unconstrained, or admitted by the declared globs

            held_beyond = path in contingent or path in derived_allowed
            projected = (
                Path(path).name == AGENTS_FILE and Path(path).parent.as_posix() in scoped_dirs
            )

            if held_beyond or projected:
                promoted.add(path)
            else:
                outside.append(path)

    if not denied and not outside:
        source = "task scope" if ctx.task is not None else "manifest scope"
        constraint = "unconstrained" if not scope.allow and not scope.deny else "clean"
        note = f"{len(ctx.diff)} changed path(s), {constraint} ({source})"

        if promoted:
            note += f"; promoted: {', '.join(sorted(promoted))}"

        return BuiltinOutcome("pass", note)

    modified = {entry.path for entry in ctx.diff if entry.status == "M"}
    whitespace = _whitespace_only(ctx, set(outside) & modified)

    lines = [f"denied path: {p}" for p in sorted(set(denied))]
    lines += [f"outside allow: {p}" for p in sorted(set(outside) - whitespace)]
    lines += [f"outside allow, whitespace only: {p}" for p in sorted(whitespace)]

    if whitespace:
        lines.append("undo the whitespace change in those files: the task does not own them")

    if promoted:
        lines.append(f"promoted: {', '.join(sorted(promoted))}")

    return BuiltinOutcome("fail", "\n".join(lines))
