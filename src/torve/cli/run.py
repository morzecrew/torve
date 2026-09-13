"""`torve run` and `torve cancel` — parsing, front-door policy and
rendering only (S-0015/D-6); the attempt loop lives in
`torve.application.runner` (S-0003) and every adapter it runs on is
built by the composition root, `torve.cli.assembly`. The task's tier
picks the adapter (S-0004/adapters); the exit code projection is S-0011/D-4.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from torve.cli.assembly import (
    # Re-exports: the drafting, corpus and eval verbs import these helpers
    # from this module; the builders themselves live in the composition
    # root.
    build_reviewer_agent as build_reviewer_agent,
)
from torve.cli.assembly import (
    build_tier_agent as build_tier_agent,
)
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

# ....................... #

# The contract argument the reading verbs take — `brief`, `size`,
# `lint-contract` — resolved the way this module resolves one for `torve run`
# (S-0067/D-10), so that where a contract file lives stops being the caller's
# problem.
ContractArgument = Annotated[
    str,
    typer.Argument(
        metavar="CONTRACT",
        help="A task id, resolved to its contract the way `torve run` "
        "resolves one; a path is accepted for a draft that has no id yet.",
    ),
]


def contract_for(root: Path, contract: str) -> Path:
    """Resolve a contract argument to the file it names.

    A task id goes through the repository's task layout, the same resolution
    `torve run` does; a path stays accepted for a draft that has no id yet.
    Exits 3 when neither is a file — a bad argument is the operator's to fix,
    not a red gate.
    """

    resolved = layout.task_file(root, contract)

    if resolved.is_file():
        return resolved

    given = Path(contract)

    if given.is_file():
        return given

    raise fail(f"configuration error: no contract at {given} or {resolved}", EXIT_CONFIG)


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
    lint_red: Annotated[
        bool,
        typer.Option(
            "--lint-red",
            help="Dispatch a contract the lint refuses anyway. Recorded on the run.",
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

    from torve.application.runner import (
        RoleNotDispatchable,
        check_dispatch_role,
        run_task,
    )
    from torve.cli import assembly
    from torve.config.runconfig import (
        ProviderDenied,
        resolve_character_tier,
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
    # S-0034 S-0034/D-3: resolved once, here, before anything reads the
    # task's tier — sizing, provider routing, agent construction and the
    # dispatched run all see the same already-resolved task.
    task = resolve_character_tier(config, task)

    # T-0183: the front door refuses any role the generic attempt path does
    # not implement the isolation contract for — review (S-0005/D-2) and draft
    # (S-0020/D-2) each have a runner-minted path with a read-only workspace,
    # and this path would mount the workspace writable. Refused here,
    # before sizing, provider routing or an agent exists, with the way out;
    # run_task enforces the same guard, so no caller can bypass the door.
    try:
        check_dispatch_role(task)

    except RoleNotDispatchable as exc:
        raise fail(str(exc), EXIT_CONFIG) from exc

    # S-0026 S-0026/D-7: a too_large verdict routes to decomposition; a
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

    # The `--agent fake` override and the `--scenario` file are this verb's
    # front door; the composition root applies them to every tier rule.
    make_agent = assembly.dispatch_agent_factory(agent_name=agent_name, scenario=scenario)

    try:
        tier = tier_for(config, tier_name_for(task))

        # Provider routing is enforced here — at dispatch, before a sandbox
        # exists (S-0004/D-8). The --agent fake override sends nothing anywhere,
        # so it routes as fake does; every retry rung routes too, which is
        # the assembly's rule for every dispatching consumer.
        if agent_name is None:
            assembly.route_dispatch_providers(config, root, tier)

        agent = make_agent(tier)

    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    # S-0067/D-9: a contract the lint refuses can never go green, so dispatch
    # refuses it before an attempt is paid for rather than after three of
    # them — the rung the size check stands on: the reasons named, and an
    # explicit override recorded for the operator who means it. It stands
    # below the seat's own refusals so that a misconfigured repository still
    # hears about its configuration first; nothing has run yet either way.
    from torve.application.intake import lint_contract

    lint_errors = lint_contract(root, task_file)

    if lint_errors and not lint_red:
        raise fail(
            "contract lint refuses this contract: " + "; ".join(lint_errors) + " — "
            "amend the contract, or pass --lint-red to dispatch it as-is",
            EXIT_CONFIG,
        )

    if lint_errors:
        engine_event(root, "lint_red_dispatch", {"task": task.id, "errors": lint_errors})

    review_agent: Agent | None = None

    if "task_gated" in config.review.on:
        try:
            review_agent = build_reviewer_agent(config, root)

        except ValueError as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    deps = assembly.build_run_deps(
        root,
        config,
        agent=agent,
        retry_agent=make_agent,
        review_agent=review_agent,
        runtime_name=runtime_name,
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

    # S-0022/D-11, S-0022/A-3: the envelope prints beside the size verdict — expected
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
