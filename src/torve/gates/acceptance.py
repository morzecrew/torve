"""`acceptance` — completion claimed on red (S-0002/starting-set).

Commands come from the task contract (`@task.acceptance`); on runs with no
task, from the gate's `commands` list in the manifest. Quarantined commands
run and are recorded, but their failures stop blocking until fixed
(S-0002/three-outcomes-gates-need-beyond-pass-and-fail).

The verdict names the suite it judged: a command that reported skips says how
many and why, so a green here and a green on another machine are not the same
sentence for two different amounts of evidence (S-0070/D-6).
"""

from __future__ import annotations

import re

from torve.base.shell import run_command
from torve.config.manifest import Gate
from torve.domain.vocabulary import GateOutcome
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

# A pytest summary tail — `12 passed, 33 skipped in 4.53s`, decorated or not.
# The duration is what tells a tail from a line that merely counts something.
_TAIL = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_COUNT = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b")
# The `-rs`/`-ra` short summary, the only place a reason is printed.
_REASON = re.compile(r"^SKIPPED \[(\d+)\] (.+)$", re.MULTILINE)

# Counted, but not part of the suite that ran.
_ABSENT = ("skipped", "deselected")


# ....................... #


def _suite_note(output: str) -> tuple[int, str]:
    """How many tests a command left unrun, and the line naming the suite it
    judged — `(0, "")` when the command printed no summary to read.

    Skip reasons only appear when the command asked for them, so a suite with
    absences and no reasons says that rather than inventing them (S-0070/D-3).
    """

    tail = next((line for line in reversed(output.splitlines()) if _TAIL.search(line)), None)

    if tail is None:
        return 0, ""

    counts: dict[str, int] = {}

    for number, word in _COUNT.findall(tail):
        key = "error" if word.startswith("error") else word
        counts[key] = counts.get(key, 0) + int(number)

    if not counts:
        return 0, "suite: the command reported no tests ran"

    absent = sum(counts.get(word, 0) for word in _ABSENT)
    ran = sum(count for word, count in counts.items() if word not in _ABSENT)
    parts = [f"{count} {word}" for word in _ABSENT if (count := counts.get(word, 0))]
    note = f"suite: {ran} of {ran + absent} tests ran"

    if not absent:
        return 0, note + ", none skipped"

    note += f", {' and '.join(parts)}"
    reasons = _REASON.findall(output)

    if reasons:
        return absent, note + "".join(f"\n  skipped: [{n}] {why}" for n, why in reasons)

    return absent, note + (
        "\n  skipped: no reason reported — the command has to be asked for one "
        "(pytest names each skip under -rs)"
    )


def check_acceptance(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is not None and ctx.task.role == "review":
        # Skipped, never passed with an empty list: a review's output is
        # findings, and acceptance does not apply to the role.
        return BuiltinOutcome("skipped", "review role: judged by findings, not commands")

    if ctx.task is not None:
        commands = ctx.task.acceptance
        source = "task contract"
    else:
        commands = gate.commands
        source = "manifest"

    if not commands:
        return BuiltinOutcome("skipped", f"no acceptance commands ({source})")

    timeout = gate.timeout or 600.0
    quarantine = set(ctx.manifest.quarantine)
    sections: list[str] = []
    flaky: list[str] = []
    quarantined_failures: list[str] = []
    failed = False
    last_code: int | None = 0
    absent = 0

    for command in commands:
        result = run_command(command, ctx.root, timeout, execute=ctx.execute)
        status = "ok" if result.exit_code == 0 else f"exit {result.exit_code}"

        if result.flaky:
            status = "flaky (failed, then passed on immediate re-run)"
            flaky.append(command)

        if result.exit_code != 0:
            last_code = result.exit_code

            if command in quarantine:
                status += " — quarantined, not blocking"
                quarantined_failures.append(command)
            else:
                failed = True

        missing, note = _suite_note(result.output)
        absent += missing
        header = f"$ {command}  [{status}, {result.duration_s:.1f}s]"
        sections.append(
            f"{header}\n{note}\n{result.output}" if note else f"{header}\n{result.output}"
        )

        if failed:
            break  # remaining acceptance commands cannot change the outcome

    output = "\n".join(sections)

    if absent:
        # The headline, not a detail below the logs: this verdict judged a
        # smaller suite than the tree holds, and says so before it says pass.
        output = (
            f"suite: {absent} tests in the tree did not run in this battery — "
            "this verdict judged a smaller suite than the tree holds\n\n" + output
        )

    if failed:
        return BuiltinOutcome(
            "fail",
            output,
            exit_code=last_code,
            flaky_commands=flaky,
            quarantined_failures=quarantined_failures,
        )

    outcome: GateOutcome = "flaky" if flaky else "pass"

    return BuiltinOutcome(
        outcome,
        output,
        exit_code=0,
        flaky_commands=flaky,
        quarantined_failures=quarantined_failures,
    )
