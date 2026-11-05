#!/usr/bin/env python3
"""Refuse a write inside the contract's fence, before the bytes land (S-0066/D-5).

A PreToolUse hook: claude hands the tool call on stdin, this reads the one
task contract the worktree carries, and a `Write`/`Edit` aimed inside its
`scope.fence` — or outside the worktree — is refused with the reason. Only the
fence convicts (S-0122/D-12): a write beyond the contract's files and tests is
evidence the critic reads, so the hook lets it through as the scope gate does.
The hook decides no outcome — the battery stays the judge and convicts on the
diff either way; this only moves the same answer to before the file is
written, so the attempt spends a turn redirecting instead of a run red-lining.

`is_allowed` keeps the wider reading — the files and tests, the contingent
paths the planner named (S-0104/D-1) and a managed `AGENTS.md` beside a path in
scope (D-6) — for the read guard, which answers a large read of anything else
with its shape.

Deliberately forgiving on its own failures: no contract, no payload, a shape
it cannot read — each passes. A hook that misfires by blocking turns the
unsatisfiable into a stop-loss it cannot arm, and the gate behind it loses
nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import globs  # the sibling copy of the engine's reading of a scope glob (D-3)

# Tool calls that name a single target file. Anything else (Bash included) is
# not a declared write and this hook has no claim on it.
PATH_KEYS = ("file_path", "notebook_path")

# The managed projection beside a directory's code (S-0054/D-7); allowed
# beside any path in scope (S-0104/D-6).
AGENTS_FILE = "AGENTS.md"


def scope_patterns(contract: Path, key: str) -> list[str]:
    """The `scope.<key>` globs, read from the projected contract.

    The projection writes each block in one shape — `allow:` (or `deny:`,
    `contingent:`) then `- glob` lines until the next key — so it is read
    line-wise; no YAML library is on this side of the seam. A file that will
    not yield that shape yields nothing, and an inline `deny: []` is no block
    at all.
    """

    try:
        lines = contract.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    patterns: list[str] = []
    inside = False

    for line in lines:
        stripped = line.strip()

        if not inside:
            inside = stripped == f"{key}:" and line[:1].isspace()
            continue

        if stripped.startswith("- "):
            patterns.append(stripped[2:].strip().strip("'\""))
        else:
            break  # the next key or the end of the block: the list is read

    return patterns


def allow_patterns(contract: Path) -> list[str]:
    """What the contract licenses: its files and their tests (S-0122/D-12), or
    a version 2 contract's `allow`. No files is unconstrained."""

    files = scope_patterns(contract, "files")

    if files:
        return [*files, *scope_patterns(contract, "tests")]

    return scope_patterns(contract, "allow")


def deny_patterns(contract: Path) -> list[str]:
    """The fence (S-0122/D-12), or a version 2 contract's `deny`."""

    return scope_patterns(contract, "fence") or scope_patterns(contract, "deny")


def contingent_patterns(contract: Path) -> list[str]:
    return scope_patterns(contract, "contingent")


def _beside_scoped(
    rel: str, patterns: list[str], contingent: list[str] | tuple[str, ...] = ()
) -> bool:
    """Whether *rel* is the managed `AGENTS.md` of a directory the scope can
    write a file directly in (S-0104/D-6): one an allow glob can match, or a
    contingent path's own. The projection sits beside the code it governs,
    which the scope gate checks against the files the attempt changed; before
    a write the hook can only ask whether such a file is possible there."""

    if rel.rsplit("/", 1)[-1] != AGENTS_FILE:
        return False

    where = rel[: -len(AGENTS_FILE)].rstrip("/")

    if any(path.rpartition("/")[0] == where for path in contingent):
        return True

    return globs.has_file_in(where, patterns)


def is_allowed(
    rel: str,
    patterns: list[str],
    deny: list[str] | tuple[str, ...] = (),
    contingent: list[str] | tuple[str, ...] = (),
) -> bool:
    """Whether one worktree-relative path may be written: not inside any deny
    glob, and inside some allow or contingent glob — or any path at all when
    no allow glob is declared, which is what an empty allow means to the
    scope gate.

    Beyond the globs, a managed `AGENTS.md` beside a scoped directory passes
    too (S-0104/D-6). The globs are read through the sibling copy of the
    engine's reading (D-3): gitignore's semantics, the same ones the scope
    gate itself applies, so the two never disagree about a path.
    """

    if globs.matches(rel, deny):
        return False

    # Contingent paths are literal, as the scope gate holds them: a file name
    # with `[` or `*` in it is that file, never a glob.
    if rel in contingent:
        return True

    if not patterns or globs.matches(rel, patterns):
        return True

    return _beside_scoped(rel, patterns, contingent)


def refusal(target: str, deny: list[str] | tuple[str, ...] = ()) -> str:
    if globs.matches(target, deny):
        return (
            f"refused: {target} is inside this task's fence ({', '.join(deny)}). "
            "The scope gate will convict on it. Work outside the fenced paths, "
            "or say in your reply why the task cannot be done without one."
        )

    return f"refused: {target} is outside the worktree, where this task writes nothing."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = payload.get("tool_input") or {}
    target = next((tool_input[key] for key in PATH_KEYS if tool_input.get(key)), "")

    if not target:
        return 0

    root = Path.cwd()
    # Assembled part-wise, the way the runner assembles its own: a committed
    # file that named the engine's runtime path in one literal is the
    # dependency the layout ledger exists to catch.
    contracts = sorted((root / ".torve" / "tasks").glob("*/contract.yaml"))

    if len(contracts) != 1:
        return 0  # not one unambiguous contract to read a scope from

    deny = deny_patterns(contracts[0])

    try:
        rel = str(Path(target).resolve().relative_to(root))
    except (ValueError, OSError):
        rel = ""  # outside the worktree entirely: never allowed

    if rel and not globs.matches(rel, deny):
        return 0

    sys.stderr.write(refusal(rel or target, deny) + "\n")
    return 2  # exit 2 feeds the reason back to the model and blocks the call


if __name__ == "__main__":
    sys.exit(main())
