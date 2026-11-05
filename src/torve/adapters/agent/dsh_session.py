"""The dsh session driver (S-0122/D-11).

The engine drives a dsh session through headless `--json` and continues it with
`--session-id`. The image's `run` takes both from the environment, and its one
stream carries the events — tool calls, their results, each step's usage — and,
last, the session's totals. A turn past its clock is killed inside the sandbox;
the session it opened stays in the sandbox's dsh home, where a later turn
resumes it.
"""

from __future__ import annotations

import json
import shlex

from torve.application.ports import ExecResult, Runtime, SandboxHandle, Spawned

# ----------------------- #


def session_id(output: str) -> str | None:
    """The session a run's opening `session` event names, if it named one."""

    for line in output.splitlines():
        line = line.strip()

        if not line.startswith("{"):
            continue

        try:
            event = json.loads(line)
        except ValueError:
            continue

        if isinstance(event, dict) and event.get("type") == "session":
            value = event.get("sessionId")

            return value if isinstance(value, str) and value else None

    return None


# ....................... #


class DshSession:
    """One dsh conversation in a live sandbox. *start* is the script the first
    turn runs and *resume* the one every later turn does: equipping again
    would empty the dsh home the session lives in."""

    def __init__(
        self,
        runtime: Runtime,
        handle: SandboxHandle,
        env: dict[str, str],
        *,
        start: str,
        resume: str,
    ) -> None:
        self.runtime = runtime
        self.handle = handle
        self.env = env
        self.scripts = (start, resume)
        self.id: str | None = None
        self.live: Spawned | None = None

    # ....................... #

    def start(self, timeout_s: float) -> ExecResult:
        """The first turn, on the prompt the environment names."""

        return self._turn({}, self.scripts[0], timeout_s)

    # ....................... #

    def follow_up(self, prompt: str, timeout_s: float) -> ExecResult:
        """One more turn in the same session, its task read from *prompt*, a
        path inside the sandbox."""

        if self.id is None:
            raise RuntimeError("no dsh session to continue: the first turn named none")

        return self._turn(
            {"TORVE_SESSION_ID": self.id, "TORVE_PROMPT": prompt}, self.scripts[1], timeout_s
        )

    # ....................... #

    def kill(self) -> None:
        """Stop the turn in flight, if any; the session stays resumable."""

        live = self.live

        if live is not None:
            live.kill()

    # ....................... #

    def _turn(self, extra: dict[str, str], script: str, timeout_s: float) -> ExecResult:
        env = {**self.env, "TORVE_JSON": "1", **extra}
        exported = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
        self.live = self.runtime.spawn(self.handle, f"export {exported}; {script}")

        try:
            result = self.live.wait(timeout_s)
        finally:
            self.live = None

        self.id = self.id or session_id(result.output)

        return result
