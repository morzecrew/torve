#!/usr/bin/env python3
"""A finishing check that can pass, carrying its own ceiling (S-0066/D-6).

A Stop hook: when the attempt says it is done, ask the worktree three
questions it can actually answer — do the contract's acceptance commands
pass, does the log still owe an entry for a LOCKED row the diff touches, and
has the spec projection drifted — and block the stop with the answer
attached.

The acceptance commands are run here, on the attempt's behalf, and reach it
only when they fail (S-0077/D-1): an attempt that would otherwise run the
suite to see green does not need to, because green is the condition of being
allowed to stop. This is reverted if attempts per landing rises, whatever
happens to the turn count it was meant to remove (S-0077/D-3).

The ceiling is the point. A condition the model cannot clear (drift outside
its scope, say) would otherwise re-fire every turn until the turn budget
died; one block is on offer and then the check is done talking, because the
gate, not the hook, is what decides the attempt. One block therefore carries
every finding at once — a block spent on the log alone would leave the
acceptance unrun for the rest of the attempt.
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

ACCEPTANCE_TIMEOUT = 1800
"""Seconds one acceptance command may take before the check stops waiting.
A command that never returns is not a finding this hook can report: the
battery runs the same command outside the session and stays the judge."""

ACCEPTANCE_TAIL = 3000
"""Characters of a failing command's output that reach the attempt. The end
is where the verdict is — the summary line and the failures above it — so
what a noisy command loses is its head."""


def acceptance_commands(contract: Path) -> list[str]:
    """The contract's acceptance commands, read from the projected file.

    Read line-wise, the way the write-time guard reads the scope block: no
    YAML library is on this side of the seam, and the projection writes the
    list in one shape — a top-level `acceptance:` then `- command` lines. A
    file that will not yield that shape yields nothing, and nothing to run
    is a finish with nothing to say.
    """

    try:
        lines = contract.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    commands: list[str] = []
    inside = False

    for line in lines:
        stripped = line.strip()

        if not inside:
            inside = stripped == "acceptance:" and not line[:1].isspace()
            continue

        if stripped.startswith("- "):
            commands.append(stripped[2:].strip().strip("'\""))
        else:
            break  # the next key, or the end of the file: the list is read

    return commands


def failures(root: Path, commands: list[str]) -> list[str]:
    """The acceptance commands that did not pass, each with its output.

    A green command returns nothing, which is the whole point: green is the
    condition of being allowed to stop, not something to read. A command
    that could not be run at all is not a failing one — an inquiry that
    fails is not a finding.
    """

    problems: list[str] = []

    for command in commands:
        try:
            # A shell string is what an acceptance command is: the contract
            # writes pipes and `||` into it, and splitting it would run
            # something the battery will not.
            run = subprocess.run(  # nosec B602
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=root,
                timeout=ACCEPTANCE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if run.returncode == 0:
            continue

        output = (run.stdout + run.stderr).strip()
        tail = output[-ACCEPTANCE_TAIL:]

        if len(tail) < len(output):
            tail = "…\n" + tail

        problems.append(
            f"the acceptance command `{command}` failed — it was run for you, "
            f"so this is what the battery will see:\n{tail}"
        )

    return problems


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
    problems: list[str] = failures(root, acceptance_commands(contracts[0]))

    changed = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=root,
    )

    if changed.returncode == 0:
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
    # One finding per line: a failing command brings its own output with it,
    # and joining those inline would bury the short findings in it.
    sys.stdout.write(json.dumps({"decision": "block", "reason": "\n".join(problems)}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
