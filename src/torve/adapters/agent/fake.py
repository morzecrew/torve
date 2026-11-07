"""FakeAgent — the first adapter built (S-0003/D-2): replays a scripted scenario so
the entire runner is testable without spending a token, separating "is the
runner correct" from "is the agent good".

The scenario still executes *inside the sandbox* (S-0001/D-12): the adapter stages a
generated Python script under the workspace's gitignored `.torve/tmp/`
scratch directory (S-0013/what-does-not-belong-in-either — generated, never tracked) and asks the
Runtime to run it. Scenario steps are indexed by attempt; the
last step repeats if attempts outnumber steps.

Step fields (all optional):
    writes: {relative/path: content}      files written into the workspace
    log_entry: str                        appended to .torve/tasks/<id>/log.yaml (S-0003/A-2)
    exit: int (default 0)
    sleep: float seconds before exiting
    ignore_cancellation: bool             traps SIGTERM and sleeps forever —
                                          only the sandbox's death ends it
    crash: bool                           dies mid-write with no cleanup
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from torve.application.ports import AgentContext, AgentResult

# ----------------------- #

SCRIPT = """\
import json, os, signal, sys, time

step = json.loads(open(__file__ + ".json").read())

for rel, content in step.get("writes", {}).items():
    path = os.path.join(os.getcwd(), rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as handle:
        handle.write(content)
    if step.get("crash"):
        os._exit(137)  # died mid-write, no cleanup, files half-staged

entry = step.get("log_entry")
if entry:
    task_dir = os.path.join(".torve", "tasks", step["task_id"])
    os.makedirs(task_dir, exist_ok=True)
    path = os.path.join(task_dir, "log.yaml")
    if not os.path.exists(path):
        with open(path, "w") as handle:
            handle.write("schema_version: 1\\ntask: " + step["task_id"]
                         + "\\ndrift_count: 0\\nentries:\\n")
    # `entries:` is the last top-level key, so a properly indented item
    # appends without a YAML parser (none exists in the sandbox image).
    with open(path, "a") as handle:
        handle.write(entry.rstrip("\\n") + "\\n")

if step.get("ignore_cancellation"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        time.sleep(1)

time.sleep(step.get("sleep", 0))
sys.exit(step.get("exit", 0))
"""


# ....................... #


def load_scenario(path: Path) -> list[dict[str, Any]]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps: Any = cast(dict[str, Any], raw).get("attempts") if isinstance(raw, dict) else raw

    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{path}: scenario must carry a non-empty 'attempts' list")

    return cast(list[dict[str, Any]], steps)


# ....................... #

DEFAULT_SCENARIO: list[dict[str, Any]] = [
    {"writes": {"TORVE_FAKE.md": "written by the fake agent\n"}, "exit": 0}
]


# ....................... #


class FakeAgent:
    kind: str = "fake"
    # S-0124/D-8: a fake keeps a session only when its test scripts one, since
    # `runner.selects` sends every seat with a `follow_up` through selection.
    follow_up: Callable[[AgentContext, AgentResult, str], AgentResult] | None = None

    # ....................... #

    def __init__(
        self,
        steps: list[dict[str, Any]] | None = None,
        follow_ups: list[dict[str, Any]] | None = None,
    ) -> None:
        self.steps = steps or DEFAULT_SCENARIO
        # The turns a session continuation replays. Empty, a follow-up takes
        # the next scenario step, so a two-step script drives both the
        # attempt and the revision it earns.
        self.follow_ups = follow_ups or []
        self.followed_up = 0

        if follow_ups is not None:
            self.follow_up = self._follow_up

    # ....................... #

    def run(self, ctx: AgentContext) -> AgentResult:
        return self._execute(ctx, self.steps[min(ctx.attempt - 1, len(self.steps) - 1)])

    # ....................... #

    def _follow_up(self, ctx: AgentContext, previous: AgentResult, text: str) -> AgentResult:
        """One more turn in the session `previous` ran (S-0122/D-8,
        S-0124/D-8): the next scripted step, or the follow-up list's own.

        *text* is the rendered feedback the engine hands the turn; the fake
        replays a step rather than reading it, because what the runner does
        with the record is the runner's to test."""

        index = self.followed_up
        self.followed_up += 1

        if self.follow_ups:
            step = self.follow_ups[min(index, len(self.follow_ups) - 1)]

        else:
            step = self.steps[min(ctx.attempt + index, len(self.steps) - 1)]

        return self._execute(ctx, step)

    # ....................... #

    def _execute(self, ctx: AgentContext, scenario: dict[str, Any]) -> AgentResult:
        step = dict(scenario)
        step["task_id"] = ctx.task.id
        stage = ctx.workspace / ".torve" / "tmp"
        stage.mkdir(parents=True, exist_ok=True)
        script = stage / "fake_agent.py"
        script.write_text(SCRIPT, encoding="utf-8")
        script.with_suffix(".py.json").write_text(json.dumps(step), encoding="utf-8")

        result = ctx.runtime.exec(
            ctx.handle, f"python {ctx.workdir}/.torve/tmp/fake_agent.py", ctx.timeout_s
        )

        return AgentResult(exit_code=result.exit_code, output=result.output)
