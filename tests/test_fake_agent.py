"""FakeAgent scenarios through a host-shell runtime double — the script the
adapter stages must actually behave as scripted, because layer-2 runner tests
and the integration test both stand on it."""

from __future__ import annotations

import subprocess

from torve.adapters.agent.fake import FakeAgent
from torve.application.ports import AgentContext, ExecResult, SandboxHandle
from torve.domain.task import Task


class HostShellRuntime:
    """Executes the staged script directly on the host, cwd = workspace. A
    test double for the adapter's script generation, not a Runtime port."""

    def __init__(self, workspace):
        self.workspace = workspace

    def exec(self, handle, command, timeout_s):
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                timeout=timeout_s,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(exit_code=None, output="timed out", duration_s=timeout_s)
        return ExecResult(
            exit_code=proc.returncode,
            output=(proc.stdout or "") + (proc.stderr or ""),
            duration_s=0.0,
        )


def ctx_for(tmp_path, steps, attempt=1, timeout=20.0):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    task = Task(id="T-9001", decisions=[])
    return AgentContext(
        task=task,
        attempt=attempt,
        workspace=workspace,
        handle=SandboxHandle(id="h", name="h"),
        runtime=HostShellRuntime(workspace),
        workdir=str(workspace),
        timeout_s=timeout,
    ), FakeAgent(steps)


def test_clean_success_writes_files(tmp_path):
    ctx, agent = ctx_for(tmp_path, [{"writes": {"src/new.py": "x = 1\n"}, "exit": 0}])
    result = agent.run(ctx)
    assert result.exit_code == 0
    assert (ctx.workspace / "src" / "new.py").read_text() == "x = 1\n"


def test_scripted_exit_code(tmp_path):
    ctx, agent = ctx_for(tmp_path, [{"exit": 3}])
    assert agent.run(ctx).exit_code == 3


def test_crash_dies_mid_write(tmp_path):
    ctx, agent = ctx_for(tmp_path, [{"writes": {"a.txt": "a", "z.txt": "z"}, "crash": True}])
    result = agent.run(ctx)
    assert result.exit_code == 137
    written = {p.name for p in ctx.workspace.glob("*.txt")}
    assert len(written) == 1  # died after the first write, no cleanup


def test_locked_conflict_appends_a_halted_entry(tmp_path):
    import yaml

    entry = (
        "  - decision: D-1\n    grade: LOCKED\n    kind: contradicted\n"
        "    at: 2026-08-21T00:00:00Z\n    attempt: 1\n"
        "    claim: sim conflict\n    evidence: src/x.py:1\n    action: halted\n"
    )
    ctx, agent = ctx_for(tmp_path, [{"log_entry": entry, "exit": 0}])
    agent.run(ctx)
    log = ctx.workspace / ".torve" / "tasks" / "T-9001" / "log.yaml"
    document = yaml.safe_load(log.read_text())
    assert document["entries"][0]["action"] == "halted"
    assert document["drift_count"] == 0  # the skeleton was created around the entry


def test_ignoring_cancellation_is_bounded_by_the_hard_timeout(tmp_path):
    ctx, agent = ctx_for(tmp_path, [{"ignore_cancellation": True}], timeout=2.0)
    result = agent.run(ctx)
    assert result.timed_out


def test_attempts_index_into_the_scenario(tmp_path):
    steps = [{"exit": 1}, {"exit": 0}]
    ctx1, agent = ctx_for(tmp_path, steps, attempt=1)
    assert agent.run(ctx1).exit_code == 1
    ctx2, _ = ctx_for(tmp_path, steps, attempt=2)
    assert agent.run(ctx2).exit_code == 0
    ctx9, _ = ctx_for(tmp_path, steps, attempt=9)  # last step repeats
    assert agent.run(ctx9).exit_code == 0
