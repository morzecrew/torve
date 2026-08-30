"""`torve run` and `torve cancel` — parsing and rendering only (D-15.6); the
attempt loop lives in `torve.application.runner` (RFC 0003). The task's tier
picks the adapter (RFC 0004 §1); the exit code projection is D-11.4.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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

if TYPE_CHECKING:
    from torve.application.ports import Agent
    from torve.config.runconfig import RunnerConfig

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


def build_tier_agent(config: RunnerConfig, root: Path, tier_name: str) -> Agent:
    """A named tier's agent, provider-routed before a sandbox exists —
    shared by the run loop's review hook, the regression corpus, and the
    drafting run."""

    from torve.adapters.vcs.git import repository_name
    from torve.config.runconfig import route_provider, tier_for

    tier = tier_for(config, tier_name)
    route_provider(config.providers, repository_name(root), tier.provider)

    if tier.adapter == "fake":
        from torve.adapters.agent.fake import FakeAgent

        return FakeAgent(None)

    from torve.adapters.agent.harness import HarnessAgent

    return HarnessAgent(tier)


# ....................... #


def build_reviewer_agent(config: RunnerConfig, root: Path) -> Agent:
    return build_tier_agent(config, root, "reviewer")


# ....................... #


def run_cmd(
    task_id: Annotated[str, typer.Argument()],
    agent_name: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Override the tier's adapter with 'fake' (scenario replay); "
            "by default the task's tier picks the adapter.",
        ),
    ] = None,
    scenario: Annotated[
        Path | None,
        typer.Option(
            exists=True, help="FakeAgent scenario YAML; default writes one marker file and exits 0."
        ),
    ] = None,
    oversize: Annotated[
        bool,
        typer.Option(
            "--oversize",
            help="Dispatch a too_large contract anyway, bypassing the "
            "await-decomposition route. Recorded on the run.",
        ),
    ] = False,
    runtime_name: Annotated[
        RuntimeName | None,
        typer.Option("--runtime", help="Override the configured runtime adapter."),
    ] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run one task synchronously; the exit code carries the outcome."""

    from torve.adapters.agent.fake import FakeAgent, load_scenario
    from torve.adapters.broker import build_broker
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GhScm, GitVcs, NullScm, repository_name
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import RunDeps, run_task
    from torve.config.runconfig import (
        ProviderDenied,
        TierConfig,
        route_provider,
        tier_for,
        tier_name_for,
    )

    if agent_name not in (None, "fake"):
        raise fail(f"configuration error: unknown agent {agent_name!r}", EXIT_CONFIG)

    root = root.resolve()
    task_file = layout.task_file(root, task_id)

    if not task_file.is_file():
        raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)

    task = load_task(task_file)
    config = load_config(root, config_path)

    # RFC 0026 D-26.7: a too_large verdict routes to decomposition; a
    # manual dispatch needs the explicit, recorded override to bypass it.
    from torve.application import sizing
    from torve.application.telemetry import engine_event

    verdict = sizing.estimate(task)
    blocked = verdict.size == "too_large" and not sizing.has_children(root, task.id)

    if blocked and not oversize:
        raise fail(
            "awaiting decomposition: " + "; ".join(verdict.reasons) + " — "
            "run `torve decompose` against this contract, or pass "
            "--oversize to dispatch it as-is",
            EXIT_CONFIG,
        )

    if blocked and oversize:
        engine_event(root, "oversize_dispatch", {"task": task.id, "reasons": verdict.reasons})

    def _tier_agent(tier: TierConfig) -> Agent:
        if agent_name == "fake" or tier.adapter == "fake":
            return FakeAgent(load_scenario(scenario) if scenario else None)

        from torve.adapters.agent.harness import HarnessAgent

        if scenario is not None:
            raise ValueError("--scenario is FakeAgent-only")

        return HarnessAgent(tier)

    try:
        tier = tier_for(config, tier_name_for(task))

        # Provider routing is enforced here — at dispatch, before a sandbox
        # exists (D-4.8). A repository with no permitted provider for its
        # tier is a configuration error, never a quiet fallback. The --agent
        # fake override sends nothing anywhere, so it routes as fake does.
        # A configured retry_variant (D-27.11) routes too — the run may
        # reach it after the first gate-red, and D-27.1 refuses to dispatch
        # under a regime it has not already validated.
        if agent_name is None:
            route_provider(config.providers, repository_name(root), tier.provider)

            if tier.retry_variant:
                retry_tier = tier_for(config, tier.retry_variant)
                route_provider(config.providers, repository_name(root), retry_tier.provider)

        agent = _tier_agent(tier)

    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    review_agent: Agent | None = None

    if "task_gated" in config.review.on:
        try:
            review_agent = build_reviewer_agent(config, root)

        except ValueError as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, runtime_name),
        agent=agent,
        vcs=GitVcs(),
        scm=(GhScm(config.scm.repo, config.scm.token_env) if config.scm.open_pr else NullScm()),
        store=open_store,
        review_agent=review_agent,
        # The egress broker in force (RFC 0021): `none` by default, `local`
        # when configured — the run's keys never enter the sandbox either way.
        broker=build_broker(config.broker),
        # D-27.11: builds the tier a retry_variant names, mid-run — the same
        # rule `_tier_agent` already applies to the tier that dispatched.
        retry_agent=_tier_agent,
    )

    from torve.application.runner import BlockedDispatch

    try:
        state = run_task(root, task, config, deps)

    except BlockedDispatch as exc:
        # Never a silent wait: the cause prints, the refusal is counted.
        raise fail(str(exc), EXIT_GATES_RED) from exc

    except ValueError as exc:
        # A broker misconfiguration (an unrouted provider, a brokered tier
        # naming a credential) is a configuration error, never a traceback.
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    # D-22.11, A-62: the envelope prints beside the size verdict — expected
    # attempts, cost and wall minutes for tasks that shared this dispatch's
    # size class, a base rate the operator reads, never a bound the engine
    # acts on.
    from torve.application import specquality

    envelope = specquality.dispatch_envelope(root, verdict.size)

    if fmt is Format.JSON:
        emit_json({**state.to_record(), "size": verdict.size, "envelope": envelope})
    else:
        console = out(fmt)
        console.print(f"{task.id}: {state.state} after {state.attempts} attempt(s)")
        console.print(specquality.render_envelope(envelope))

        if state.escalation is not None:
            console.print(f"  escalated: {state.escalation.reason} — {state.escalation.detail}")

        for event in state.history[-4:]:
            console.print(f"  {event['from']} -> {event['to']}: {event['fact']}")

    if state.state is TaskState.READY:
        raise typer.Exit(EXIT_OK)

    if state.escalation is not None:
        reason = EscalationReason(state.escalation.reason)
        raise typer.Exit(EXIT_BY_REASON[reason])

    raise typer.Exit(EXIT_GATES_RED)


# ....................... #


def kill(
    task_id: Annotated[str, typer.Argument()],
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Force-terminate a run: sandbox destroyed, state escalated as killed.
    The operator override for a run that ignores the cooperative ask."""

    from torve.application.runstate import RunState
    from torve.application.telemetry import engine_event
    from torve.base import naming
    from torve.domain.states import EscalationReason

    root = root.resolve()
    state_path = naming.state_file(root, task_id)

    if not state_path.exists():
        raise fail(f"configuration error: no run state for {task_id}", EXIT_CONFIG)

    state = RunState.load(state_path)

    if state.state in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
        raise fail(
            f"configuration error: {task_id} is already {state.state} — nothing to kill",
            EXIT_CONFIG,
        )

    config = load_config(root, config_path)
    destroyed = ""

    if state.sandbox_id:
        try:
            runtime_for(config, runtime_name).destroy_by_id(state.sandbox_id)
            destroyed = state.sandbox_id

        except Exception as exc:  # the kill proceeds; the sandbox is reported
            destroyed = f"destroy failed: {exc}"

    state.sandbox_id = None
    state.escalate(EscalationReason.KILLED, "operator kill")
    engine_event(root, "killed", {"task": task_id, "sandbox": destroyed or None})

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "task_id": task_id,
                "state": str(state.state),
                "sandbox": destroyed or None,
            }
        )

        return

    console = out(fmt)
    console.print(f"{task_id}: killed — escalated for triage")

    if destroyed:
        console.print(f"  sandbox: {destroyed}")


# ....................... #


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
        raise fail(f"configuration error: {task_id} has no durable run to cancel", EXIT_CONFIG)

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
            if recorded
            else "nothing to stop (run already terminal or ask refused)"
        )
