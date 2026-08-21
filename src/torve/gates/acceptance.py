"""`acceptance` — completion claimed on red (RFC 0002 §4).

Commands come from the task contract (`@task.acceptance`); on runs with no
task, from the gate's `commands` list in the manifest. Quarantined commands
run and are recorded, but their failures stop blocking until fixed
(RFC 0002 §6a).
"""

from __future__ import annotations

from torve.context import GateContext
from torve.gates.base import BuiltinOutcome
from torve.models import Gate
from torve.shell import run_command


def check_acceptance(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
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
        sections.append(f"$ {command}  [{status}, {result.duration_s:.1f}s]\n{result.output}")
        if failed:
            break  # remaining acceptance commands cannot change the outcome

    output = "\n".join(sections)
    if failed:
        return BuiltinOutcome(
            "fail",
            output,
            exit_code=last_code,
            flaky_commands=flaky,
            quarantined_failures=quarantined_failures,
        )
    outcome = "flaky" if flaky else "pass"
    return BuiltinOutcome(
        outcome,
        output,
        exit_code=0,
        flaky_commands=flaky,
        quarantined_failures=quarantined_failures,
    )
