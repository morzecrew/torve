"""`torve run` and `torve cancel` — parsing and rendering only (D-15.6); the
attempt loop lives in `torve.application.runner`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.cli.console import Format, emit_json, fail, out
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    RuntimeName,
    load_config,
    runtime_for,
)
from torve.config import layout
from torve.domain.states import (
    EXIT_BY_REASON,
    EXIT_CONFIG,
    EXIT_GATES_RED,
    EXIT_INFRASTRUCTURE,
    EXIT_OK,
    EscalationReason,
    TaskState,
)
from torve.gates.context import load_task

# ----------------------- #


def run_cmd(
    task_id: Annotated[str, typer.Argument()],
    agent_name: Annotated[str, typer.Option(
        "--agent", help="Only 'fake' today; real adapters arrive with RFC 0004.")] = "fake",
    scenario: Annotated[Path | None, typer.Option(
        exists=True,
        help="FakeAgent scenario YAML; default writes one marker file and exits 0.")] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option(
        "--runtime", help="Override the configured runtime adapter.")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run one task synchronously; the exit code is the outcome (RFC 0003,
    projected onto codes 0–5 per D-11.4)."""
    from torve.adapters.agent.fake import FakeAgent, load_scenario
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GhScm, GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import RunDeps, run_task

    if agent_name != "fake":
        raise fail(f"configuration error: unknown agent {agent_name!r}", EXIT_CONFIG)
    root = root.resolve()
    task_file = layout.task_file(root, task_id)
    if not task_file.is_file():
        raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)
    task = load_task(task_file)
    config = load_config(root, config_path)

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, runtime_name),
        agent=FakeAgent(load_scenario(scenario) if scenario else None),
        vcs=GitVcs(),
        scm=GhScm() if config.scm.open_pr else NullScm(),
        store=open_store,
    )
    try:
        state = run_task(root, task, config, deps)
    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json(state.to_record())
    else:
        console = out(fmt)
        console.print(f"{task.id}: {state.state} after {state.attempts} attempt(s)")
        if state.escalation is not None:
            console.print(
                f"  escalated: {state.escalation.reason} — {state.escalation.detail}")
        for event in state.history[-4:]:
            console.print(f"  {event['from']} -> {event['to']}: {event['fact']}")

    if state.state is TaskState.READY:
        raise typer.Exit(EXIT_OK)
    if state.escalation is not None:
        reason = EscalationReason(state.escalation.reason)
        raise typer.Exit(EXIT_BY_REASON[reason])
    raise typer.Exit(EXIT_GATES_RED)


def cancel(
    task_id: Annotated[str, typer.Argument()],
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Ask a running task to stop — cooperative on the ask, fenced on the
    landing. Fails closed when the store backend cannot deliver a cancel."""
    import asyncio

    from torve.adapters.store.durable import open_store
    from torve.application.runstate import RunState
    from torve.application.taskstore import TaskStore
    from torve.base import naming

    root = root.resolve()
    state_path = naming.state_file(root, task_id)
    if not state_path.exists():
        raise fail(f"configuration error: no run state for {task_id}", EXIT_CONFIG)
    state = RunState.load(state_path)
    run_id = state.durable_run_id
    if not run_id:
        raise fail(f"configuration error: {task_id} has no durable run to cancel",
                   EXIT_CONFIG)

    config = load_config(root, config_path)

    async def _cancel() -> bool:
        taskstore = TaskStore(await open_store(config.store), config.store)
        return await taskstore.request_cancel(run_id)

    try:
        recorded = asyncio.run(_cancel())
    except Exception as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "task_id": task_id, "recorded": recorded})
    else:
        out(fmt).print(
            "cancel recorded — the holder observes it on the next lease renewal"
            if recorded else "nothing to stop (run already terminal or ask refused)")
