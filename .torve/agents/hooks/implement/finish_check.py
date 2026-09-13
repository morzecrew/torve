#!/usr/bin/env python3
"""A finishing check that can pass, carrying its own ceiling (S-0066/D-6).

A Stop hook: when the attempt says it is done, ask the worktree two
questions it can actually answer — does the log still owe an entry for a
LOCKED row the diff touches, and has the spec projection drifted — and block
the stop with the answer attached.

The ceiling is the point. A condition the model cannot clear (drift outside
its scope, say) would otherwise re-fire every turn until the turn budget
died; one block is on offer and then the check is done talking, because the
gate, not the hook, is what decides the attempt.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

CEILING = 1
"""Blocks this check may ever hand out. One: the stop it blocks plus the
stop that passes is two turns, which is what an unattended night spends on a
hook that was wrong — not a turn budget."""


def question() -> list[str]:
    """What the attempt still owes, as of this stop, or nothing if asked
    and clean. None when the check itself could not run — an inquiry that
    fails is not a finding, and a hook must not convict on its own outage."""

    root = Path.cwd()
    # Assembled part-wise, the way the runner assembles its own: a committed
    # file that named the engine's runtime path in one literal is the
    # dependency the layout ledger exists to catch.
    contracts = sorted((root / ".torve" / "tasks").glob("*/contract.yaml"))

    if len(contracts) != 1:
        return []

    task_id = contracts[0].parent.name
    problems: list[str] = []

    changed = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=root,
    )

    if changed.returncode != 0:
        return []

    touched = [
        line[3:].split(" -> ")[-1].strip("'\"")
        for line in changed.stdout.splitlines()
        if line[3:].strip()
    ]

    args = ["uv", "run", "torve", "log", "owed", task_id, "--format", "json"]
    args += [one for path in touched for one in ("--touched", path)]

    owed = subprocess.run(args, capture_output=True, text=True, cwd=root)

    if owed.returncode == 0:
        with contextlib.suppress(ValueError, KeyError):  # unparseable answer is no answer
            problems += [f"the log owes {one}" for one in json.loads(owed.stdout)["owed"]]

    drift = subprocess.run(
        ["uv", "run", "torve", "spec", "project", "--check"],
        capture_output=True,
        text=True,
        cwd=root,
    )

    if drift.returncode == 3:
        problems.append("the spec projection has drifted — re-run the projection")

    return problems


def main() -> int:
    counter = Path(f"/tmp/torve-finish-blocked-{Path.cwd().name}")

    try:
        count = int(counter.read_text().strip())
    except (OSError, ValueError):
        count = 0

    if count >= CEILING:
        return 0  # the ceiling is spent; the gate decides from here

    problems = question()

    if not problems:
        return 0

    counter.write_text(str(count + 1))
    sys.stdout.write(json.dumps({"decision": "block", "reason": "; ".join(problems)}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
