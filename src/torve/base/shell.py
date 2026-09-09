"""Shell execution with the flaky protocol (S-0002/three-outcomes-gates-need-beyond-pass-and-fail, S-0002/D-6).

A command that fails and then passes on immediate re-run is `flaky`: recorded,
counted, and not a red result — otherwise flakes silently eat the poison
ceiling once a runner exists.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# ----------------------- #

OUTPUT_LIMIT = 8000

# One execution of one command: (command, timeout_s) -> (exit code | None on
# timeout, combined output). The flaky protocol sits above this seam, so it is
# identical whether the command runs on the host or in a sandbox (S-0001/D-11, S-0001/D-12).
ExecuteOnce = Callable[[str, float], tuple[int | None, str]]


# ....................... #


# A harness envelope is one long final JSON line; a clip landing inside it
# destroys the only machine-readable verdict (a review died this way). The
# final line survives whole up to this bound — outputs whose last line is
# short (test logs, build output) keep the ordinary clip.
FINAL_LINE_LIMIT = 262_144


def _document(line: str) -> bool:
    """Whether this line is a whole JSON document.

    Size was the wrong test for a verdict (T-0283): a review envelope is as
    large as the session it summarises — that one echoed the reviewer's own
    probe script back inside `permission_denials` and cleared the bound, so
    the ordinary clip cut through the JSON and a paid, correct review was
    recorded as unparseable. Shape is the test the bound was standing in
    for; the bound stays for a line that is merely long.
    """

    if not (line.startswith("{") and line.endswith("}")):
        return False

    try:
        json.loads(line)

    except ValueError:
        return False

    return True


def truncate(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text

    head, tail = text[:2000], text[-(OUTPUT_LIMIT - 2000) :]
    final_line = text[text.rfind("\n") + 1 :]

    if len(tail) < len(final_line) and (
        len(final_line) <= FINAL_LINE_LIMIT or _document(final_line)
    ):
        tail = final_line

    return f"{head}\n… truncated …\n{tail}"


# ....................... #


@dataclass
class CommandResult:
    command: str
    exit_code: int | None  # None when the command timed out
    output: str
    duration_s: float
    flaky: bool = False


# ....................... #


def host_executor(cwd: Path) -> ExecuteOnce:
    def execute(command: str, timeout: float) -> tuple[int | None, str]:
        try:
            proc = subprocess.run(
                command,
                # Acceptance commands are shell lines by contract (pipes and
                # redirects included); they come from the task contract the
                # gates themselves vet, not from untrusted input.
                shell=True,  # nosec B602
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False,
            )

        except subprocess.TimeoutExpired:
            return None, f"timed out after {timeout:.0f}s"

        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    return execute


# ....................... #


def run_command(
    command: str,
    cwd: Path,
    timeout: float,
    retry_flaky: bool = True,
    execute: ExecuteOnce | None = None,
) -> CommandResult:
    execute = execute or host_executor(cwd)
    started = time.monotonic()
    code, output = execute(command, timeout)
    flaky = False

    if code not in (0, None) and retry_flaky:
        second_code, second_output = execute(command, timeout)

        if second_code == 0:
            flaky = True
            code = 0
            output += "\n--- immediate re-run passed: flaky ---\n" + second_output
        else:
            output += "\n--- immediate re-run also failed ---\n" + second_output

    return CommandResult(
        command=command,
        exit_code=code,
        output=truncate(output),
        duration_s=time.monotonic() - started,
        flaky=flaky,
    )
