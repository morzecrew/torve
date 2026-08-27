"""Shell execution with the flaky protocol (RFC 0002 §6a, D-2.6).

A command that fails and then passes on immediate re-run is `flaky`: recorded,
counted, and not a red result — otherwise flakes silently eat the poison
ceiling once a runner exists.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# ----------------------- #

OUTPUT_LIMIT = 8000

# One execution of one command: (command, timeout_s) -> (exit code | None on
# timeout, combined output). The flaky protocol sits above this seam, so it is
# identical whether the command runs on the host or in a sandbox (D-3, D-4).
ExecuteOnce = Callable[[str, float], tuple[int | None, str]]


# ....................... #


def truncate(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text

    head, tail = text[:2000], text[-(OUTPUT_LIMIT - 2000) :]

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
                shell=True,
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
