"""The dsh session driver (S-0122/D-11): a session run through headless
`--json`, continued with `--session-id`, its events read as the facts the
other harnesses' streams give, and one follow-up turn in the same sandbox
(S-0122/D-8)."""

from __future__ import annotations

import json

import pytest

from torve.adapters.agent.dsh_session import DshSession
from torve.adapters.agent.harness import HarnessAgent, parse_burn, parse_tool_calls
from torve.application.ports import AgentContext, AgentResult, ExecResult, SandboxHandle
from torve.base import naming
from torve.config.runconfig import TierConfig
from torve.domain.task import Scope, Task

# ----------------------- #


def _lines(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _step(turn: int, step: int, usage: dict, *calls: tuple[str, str, dict, str]) -> list[dict]:
    """One step as dsh's `--json` projection writes it: the step opens, its
    calls and their results, then its end with the step's usage."""

    events: list[dict] = [{"type": "status", "phase": "step_start", "turn": turn, "step": step}]

    for call_id, tool, input_, result in calls:
        events.append({"type": "tool_call", "callId": call_id, "tool": tool, "input": input_})
        events.append(
            {"type": "tool_result", "callId": call_id, "status": "completed", "result": result}
        )

    events.append(
        {"type": "status", "phase": "step_end", "turn": turn, "step": step, "usage": usage}
    )

    return events


def _usage(inputs: int, outputs: int) -> dict:
    return {"inputTokens": inputs, "outputTokens": outputs, "cacheReadTokens": 0}


def _envelope(cost: float, inputs: int, outputs: int) -> dict:
    """`report-usage --envelope`: the session's totals, from its own log."""

    return {
        "total_cost_usd": cost,
        "model": "deepseek-v4.1-flash-1017",
        "usage": {**_usage(inputs, outputs), "cacheCreationTokens": 0},
    }


FIRST = _lines(
    {"type": "session", "sessionId": "session-abc", "cwd": "/work"},
    {"type": "status", "phase": "turn_start", "turn": 1},
    *_step(1, 1, _usage(100, 20), ("c1", "read", {"file_path": "/work/src/app.py"}, "x = 1\n")),
    *_step(1, 2, _usage(150, 40), ("c2", "bash", {"command": "uv run pytest -q"}, "1 failed")),
    {"type": "status", "phase": "turn_end", "turn": 1, "reason": "completed"},
    {"type": "final", "text": "done"},
    _envelope(0.001, 250, 60),
)

SECOND = _lines(
    {"type": "session", "sessionId": "session-abc", "cwd": "/work"},
    {"type": "status", "phase": "turn_start", "turn": 2},
    *_step(2, 1, _usage(300, 30), ("c3", "edit", {"file_path": "/work/src/app.py"}, "ok")),
    {"type": "status", "phase": "turn_end", "turn": 2, "reason": "completed"},
    {"type": "final", "text": "fixed"},
    _envelope(0.002, 550, 90),
)


# ....................... #


class _Spawned:
    def __init__(self, output: str) -> None:
        self.output = output

    def wait(self, timeout_s: float) -> ExecResult:
        return ExecResult(exit_code=0, output=self.output, duration_s=0.1)

    def kill(self) -> None:
        pass


class _Runtime:
    """Hands each spawn the next scripted stream and keeps the command."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.commands: list[str] = []

    def spawn(self, handle: SandboxHandle, command: str) -> _Spawned:
        self.commands.append(command)
        return _Spawned(self.outputs.pop(0))


HANDLE = SandboxHandle(id="h-1", name="torve-T-9010")


# ....................... #


def test_a_session_starts_equipped_and_learns_its_id():
    runtime = _Runtime(FIRST)
    # A seat's own environment cannot make the first turn resume a session.
    env = {"TORVE_MODEL": "m", "TORVE_SESSION_ID": "session-stale"}
    session = DshSession(runtime, HANDLE, env, start="equip && run", resume="run")  # type: ignore[arg-type]

    result = session.start(60)

    assert result.exit_code == 0
    assert session.id == "session-abc"
    assert "TORVE_JSON=1" in runtime.commands[0]
    assert "TORVE_SESSION_ID=''" in runtime.commands[0]
    assert "session-stale" not in runtime.commands[0]
    assert runtime.commands[0].endswith("; equip && run")


def test_a_follow_up_resumes_the_session_without_equipping_again():
    """`equip` empties the dsh home the session lives in, so a later turn
    runs `run` alone, on the session the first one opened."""

    runtime = _Runtime(FIRST, SECOND)
    session = DshSession(runtime, HANDLE, {}, start="equip && run", resume="run")  # type: ignore[arg-type]
    session.start(60)

    session.follow_up("/work/.torve/tmp/follow-up.md", 60)

    assert "TORVE_SESSION_ID=session-abc" in runtime.commands[1]
    assert "TORVE_PROMPT=/work/.torve/tmp/follow-up.md" in runtime.commands[1]
    assert runtime.commands[1].endswith("; run")


def test_a_follow_up_with_no_session_to_continue_is_refused():
    session = DshSession(_Runtime(), HANDLE, {}, start="s", resume="r")  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="no dsh session"):
        session.follow_up("/work/p.md", 60)


# ....................... #


def test_the_events_read_as_the_facts_a_stream_gives(tmp_path):
    """The calls with their inputs, one message per step, the bytes each
    result returned, and one turn per step that reported its usage."""

    trace = tmp_path / "trace.log"
    trace.write_text(FIRST, encoding="utf-8")

    assert parse_tool_calls(trace, "/work") == [
        {"name": "read", "input": {"file_path": "src/app.py"}, "message": 0, "bytes": 6},
        {"name": "bash", "input": {"command": "uv run pytest -q"}, "message": 1, "bytes": 8},
    ]

    burn = parse_burn(trace)

    assert burn is not None
    assert (burn.turns, burn.tool_calls) == (2, 2)


# ....................... #


def _agent(tmp_path, runtime: _Runtime, harness: str = "dsh"):
    workspace = tmp_path / "wt" / "T-9010"
    workspace.mkdir(parents=True)
    task = Task(id="T-9010", intent="Fix the app.", scope=Scope(allow=["src/**"]), decisions=[])
    ctx = AgentContext(
        task=task,
        attempt=1,
        workspace=workspace,
        handle=HANDLE,
        runtime=runtime,  # type: ignore[arg-type]
        workdir="/work",
        timeout_s=60,
    )

    return ctx, HarnessAgent(
        TierConfig(adapter="harness", harness=harness, provider="p", model="m")
    )


def test_a_dsh_attempt_runs_as_a_session_and_keeps_its_stream_as_the_trace(tmp_path):
    runtime = _Runtime(FIRST)
    ctx, agent = _agent(tmp_path, runtime)

    result = agent.run(ctx)

    assert result.exit_code == 0
    assert result.cost_usd == 0.001
    assert result.model_version == "deepseek-v4.1-flash-1017"
    assert naming.trace_file(ctx.workspace, 1).read_text(encoding="utf-8") == FIRST
    assert runtime.commands[0].endswith("; /opt/torve/equip && /opt/torve/run")


def test_a_follow_up_turn_joins_the_trace_and_counts_the_whole_session(tmp_path):
    """S-0122/D-8: the turn runs in the same sandbox on the same session, its
    task staged beside the prompt, and the result is the attempt's whole."""

    runtime = _Runtime(FIRST, SECOND)
    ctx, agent = _agent(tmp_path, runtime)
    first = agent.run(ctx)

    result = agent.follow_up(ctx, first, "tests/test_app.py::test_x failed")

    assert result is not None
    assert result.cost_usd == 0.002
    assert (ctx.workspace / ".torve" / "tmp" / "follow-up.md").read_text(
        encoding="utf-8"
    ) == "tests/test_app.py::test_x failed"
    assert "TORVE_SESSION_ID=session-abc" in runtime.commands[1]

    trace = naming.trace_file(ctx.workspace, 1)
    assert [fact["name"] for fact in parse_tool_calls(trace)] == ["read", "bash", "edit"]
    assert [fact["message"] for fact in parse_tool_calls(trace)] == [0, 1, 2]


def test_a_seat_that_runs_no_session_has_no_follow_up(tmp_path):
    runtime = _Runtime(FIRST)
    ctx, agent = _agent(tmp_path, runtime, harness="claude")
    first = AgentResult(exit_code=0, output=FIRST)

    assert agent.follow_up(ctx, first, "red") is None
    assert runtime.commands == []
