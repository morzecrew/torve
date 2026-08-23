"""`torve run` and `torve cancel` — parsing and rendering only (D-15.6); the
attempt loop lives in `torve.application.runner` (RFC 0003). The task's tier
picks the adapter (RFC 0004 §1); the exit code projection is D-11.4.
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
    agent_name: Annotated[str | None, typer.Option(
        "--agent", help="Override the tier's adapter with 'fake' (scenario replay); "
                        "by default the task's tier picks the adapter.")] = None,
    scenario: Annotated[Path | None, typer.Option(
        exists=True,
        help="FakeAgent scenario YAML; default writes one marker file and exits 0.")] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option(
        "--runtime", help="Override the configured runtime adapter.")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run one task synchronously; the exit code carries the outcome."""
    from torve.adapters.agent.fake import FakeAgent, load_scenario
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GhScm, GitVcs, NullScm, repository_name
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import RunDeps, run_task
    from torve.config.runconfig import ProviderDenied, route_provider, tier_for

    if agent_name not in (None, "fake"):
        raise fail(f"configuration error: unknown agent {agent_name!r}", EXIT_CONFIG)
    root = root.resolve()
    task_file = layout.task_file(root, task_id)
    if not task_file.is_file():
        raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)
    task = load_task(task_file)
    config = load_config(root, config_path)

    try:
        tier = tier_for(config, task.tier)
        # Provider routing is enforced here — at dispatch, before a sandbox
        # exists (D-4.8). A repository with no permitted provider for its
        # tier is a configuration error, never a quiet fallback. The --agent
        # fake override sends nothing anywhere, so it routes as fake does.
        if agent_name is None:
            route_provider(config.providers, repository_name(root), tier.provider)
    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    from torve.application.ports import Agent

    agent: Agent
    if agent_name == "fake" or tier.adapter == "fake":
        agent = FakeAgent(load_scenario(scenario) if scenario else None)
    else:
        from torve.adapters.agent.harness import HarnessAgent

        if scenario is not None:
            raise fail("configuration error: --scenario is FakeAgent-only", EXIT_CONFIG)
        agent = HarnessAgent(tier)

    review_agent: Agent | None = None
    if "task_gated" in config.review.on:
        try:
            reviewer_tier = tier_for(config, "reviewer")
            # The reviewer's egress routes like any tier's — enforced here,
            # before a sandbox exists.
            route_provider(config.providers, repository_name(root), reviewer_tier.provider)
        except (ProviderDenied, ValueError) as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
        if reviewer_tier.adapter == "fake":
            review_agent = FakeAgent(None)
        else:
            from torve.adapters.agent.harness import HarnessAgent

            review_agent = HarnessAgent(reviewer_tier)

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, runtime_name),
        agent=agent,
        vcs=GitVcs(),
        scm=GhScm() if config.scm.open_pr else NullScm(),
        store=open_store,
        review_agent=review_agent,
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
