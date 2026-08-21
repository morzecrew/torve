"""FakeAgent — the first adapter built (D-3.2): replays a scripted scenario so
the entire runner is testable without spending a token, separating "is the
runner correct" from "is the agent good".

The scenario still executes *inside the sandbox* (D-4): the adapter stages a
generated Python script under the workspace's gitignored `.torve/tmp/`
scratch directory (RFC 0013 §5 — generated, never tracked) and asks the
Runtime to run it. Scenario steps are indexed by attempt; the
last step repeats if attempts outnumber steps.

Step fields (all optional):
    writes: {relative/path: content}      files written into the workspace
    log_entry: str                        appended to logs/<task-id>.md
    exit: int (default 0)
    sleep: float seconds before exiting
    ignore_cancellation: bool             traps SIGTERM and sleeps forever —
                                          only the sandbox's death ends it
    crash: bool                           dies mid-write with no cleanup
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

from torve.ports import AgentContext, AgentResult

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
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", step["task_id"] + ".yaml")
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


def load_scenario(path: Path) -> list[dict[str, Any]]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps: Any = cast(dict[str, Any], raw).get("attempts") if isinstance(raw, dict) else raw
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{path}: scenario must carry a non-empty 'attempts' list")
    return cast(list[dict[str, Any]], steps)


DEFAULT_SCENARIO: list[dict[str, Any]] = [
    {"writes": {"TORVE_FAKE.md": "written by the fake agent\n"}, "exit": 0}
]


class FakeAgent:
    def __init__(self, steps: list[dict[str, Any]] | None = None) -> None:
        self.steps = steps or DEFAULT_SCENARIO

    def run(self, ctx: AgentContext) -> AgentResult:
        step = dict(self.steps[min(ctx.attempt - 1, len(self.steps) - 1)])
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
