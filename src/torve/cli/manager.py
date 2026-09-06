"""`torve manager` — the board a partition's event log adds up to (RFC 0044
§5.4). Parsing and rendering only (D-15.6); the fold is
`torve.application.manager`. `serve` rebuilds its whole view from the log
every pass (D-44.5); `note` writes the manager's half of the live channel,
which the run polls for rather than being interrupted by (RFC 0045 §5.3,
D-45.7).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from torve.application.manager import Board, TaskView, stalled
from torve.application.residency import IDLE_SECONDS
from torve.cli.console import (
    STYLE_DIM,
    Format,
    add_rows_truncated,
    closing,
    emit_json,
    fail,
    footer,
    header,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    load_config,
    runtime_for,
)
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from forze.application.execution import ExecutionRuntime

    from torve.application.residency import Lane
    from torve.config.runconfig import RunnerConfig

# ----------------------- #

manager_app = typer.Typer(no_args_is_help=True, help="Run and read the engine's board.")


# ....................... #


@asynccontextmanager
async def _runtime(dsn: str | None) -> AsyncGenerator[ExecutionRuntime]:
    """The store this manager reads and writes. A DSN names the Postgres
    log; without one the mock stands in, which is a real log for the life of
    the process and nothing afterwards — useful for a dry pass, useless as a
    board (D-44.11: the port is what makes the two interchangeable)."""

    import sys

    from forze.application.execution import DepsRegistry, ExecutionRuntime
    from forze.base.logging import configure_logging

    from torve.adapters.eventstore.document import mock_module, postgres_module

    # The runtime narrates itself on stdout by default, and stdout is where
    # this verb's JSON goes. Warnings and worse, on stderr: a machine-read
    # channel carries one thing (D-15.6).
    configure_logging(level="warning", stream=sys.stderr)

    module = await postgres_module(dsn) if dsn else mock_module()
    runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

    async with runtime.scope():
        yield runtime


# ....................... #


async def _board(dsn: str | None, partition: str) -> Board:
    from torve.application.eventlog import event_log
    from torve.application.manager import project

    async with _runtime(dsn) as runtime:
        log = event_log(runtime.get_context())

        return project(await log.since(partition=partition))


# ....................... #


def _lane_leg(root: Path, config: RunnerConfig, *, only: str | None) -> Lane | None:
    """The pass's landing leg, or None when the switch is off.

    Off, a pass behaves exactly as it did before the leg existed: the
    switch's default is the whole of the safety story here, and landing
    stays the manual verb's job. On, the leg calls the same `process_lane`
    the operator calls, with the same CI, approvals, review and quiet
    window arguments this configuration feeds the verb — it adds a caller,
    not a policy, so no refusal changes and nothing is gated twice.
    """

    if not config.promotion.auto_merge:
        return None

    from torve.adapters.vcs.git import GitLane
    from torve.application.lane import process_lane
    from torve.cli.merge import _resolve_ci

    vcs = GitLane()
    ci = _resolve_ci(config)

    async def lane() -> list[str]:
        # Landing is git in a blocking world: run it off the loop so a slow
        # rebase cannot stall the pass's clock. The arguments are `merge_cmd`
        # to the letter — the verb passes no conflict disposal (its own
        # automatic re-queue is separate, later work), and a leg that passed
        # one would leave the same conflicting candidate disposed of one way
        # by `torve merge` and another by the pass.
        results = await asyncio.to_thread(
            process_lane,
            root,
            vcs,
            only=only,
            ci=ci,
            approvals_required=config.promotion.approvals,
            require_review=config.promotion.require_review,
            quiet_window_s=config.promotion.quiet_window,
        )

        return [result.task for result in results if result.landed]

    return lane


# ....................... #


async def _serve(
    dsn: str | None,
    partition: str,
    *,
    root: Path,
    config_path: Path | None,
    worker: str,
    passes: int | None,
    interval: float,
    only: str | None,
    dispatch: bool,
) -> int:
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.fleet import escalated_tasks
    from torve.application.manager import project
    from torve.application.projections import shipped_landings
    from torve.application.residency import ran_here, serve
    from torve.application.worker import Worker
    from torve.cli import assembly
    from torve.cli.assembly import build_notifier
    from torve.domain.events import SubjectType

    config = load_config(root, config_path)

    # The repository's own answer (A-29): a contract the tree already landed
    # is minted onto the board as landed, so a pass over a repository with
    # history does not offer a worker somebody's finished work. One batched
    # log pass, and the same evidence the projections call shipped (A-97) —
    # the engine's trailer and a human's citation both mean finished.
    landings = shipped_landings(root)
    ran = ran_here(root)

    async def paused() -> bool:
        """This root's own pause rule, re-decided every pass (A-110).

        The queue is the union of both carriers (D-48.5): a task escalated
        under v1 left a run-state file, one this manager escalated is in
        the log, and a task in both is one task a person has to look at.
        """

        board = project(await log.of_subject_type(SubjectType.TASK, partition=partition))

        return len(escalated_tasks(root, board)) >= config.loop.pause_escalations

    notifier = build_notifier(config)

    async def relay() -> list[str]:
        """Drain the undelivered queue to whatever destination is
        configured. Built per pass from the same log the pass reads."""

        from torve.application.notify import relay as drain

        return await drain(
            log,
            notifier,
            partition=partition,
            actor_id=worker,
            max_attempts=config.notify.attempts,
        )

    def standing() -> tuple[str, bool]:
        # RFC 0023's leg, unchanged — it mints a contract through the
        # ordinary adoption path, and the scan above imports whatever it
        # minted onto the board. Nothing about it had to move for the
        # manager to run it (A-106).
        from torve.application.standing import standing_leg

        return standing_leg(root, config, runtime_for(config, None), landings.__contains__)

    # None unless the auto-merge switch is on: an unarmed serve is the
    # same pass it was before the landing leg existed.
    lane_leg = _lane_leg(root, config, only=only)

    async with _runtime(dsn) as runtime:
        log = event_log(runtime.get_context())

        return await serve(
            log,
            Worker(
                log=log,
                name=worker,
                execute=runner_execute(
                    root,
                    config,
                    assembly.build_dispatch_prepare(root, config),
                    log=log,
                    partition=partition,
                    seat=worker,
                ),
            ),
            root,
            partition,
            idle_seconds=interval,
            passes=passes,
            landed=landings.get,
            ran=ran.__contains__,
            only=only,
            dispatch=dispatch,
            standing=standing,
            paused=paused,
            relay=relay,
            lane=lane_leg,
        )


# ....................... #


def _burn(view: TaskView) -> str:
    """What the burn stream says about this task: how long since it last
    spent anything, and how much. `stalled` is the reading, not a verdict —
    nothing acts on it (D-45.8 is open)."""

    if view.last_burn is None:
        return "—"

    age = (datetime.now(UTC) - view.last_burn).total_seconds() / 60

    return f"{age:.0f}m ago ${view.burned_usd:.2f}" + (" · stalled" if stalled(view) else "")


# ....................... #


@manager_app.command("board")
def board_cmd(
    partition: Annotated[str, typer.Argument(help="The repository this board is for.")],
    dsn: Annotated[
        str,
        typer.Option("--dsn", help="Postgres DSN holding the log; omitted reads an empty log."),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Show what a partition's recorded facts add up to: every task the log
    has mentioned, the state its events leave it in, and who holds it.

    Nothing here is stored — the board is rebuilt from the log on every
    call, which is the same thing a manager does when it restarts.
    """

    result = asyncio.run(_board(dsn or None, partition))

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "tasks": [
                    {
                        "task": view.task_id,
                        # A-96: the record holds every contract, and a
                        # review or draft one is queued in the sense that
                        # nobody will ever claim it. The role is what tells
                        # the two kinds of queued apart.
                        "role": view.contract.role if view.contract else None,
                        "state": str(view.state),
                        "attempts": view.attempts,
                        "claimed_by": view.claimed_by,
                        "landed_sha": view.landed_sha,
                        "escalation": view.escalation,
                        "last_burn": view.last_burn.isoformat() if view.last_burn else None,
                        "burned_usd": round(view.burned_usd, 4),
                        "stalled": stalled(view),
                    }
                    for view in sorted(result.tasks.values(), key=lambda one: one.task_id)
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "manager board", partition)
    table = make_table("task", "role", "state", "attempts", "held by", "burn", "landing")
    withheld = add_rows_truncated(
        table,
        [
            (
                view.task_id,
                view.contract.role if view.contract else "—",
                view.escalation or str(view.state),
                str(view.attempts),
                view.claimed_by or "—",
                _burn(view),
                (view.landed_sha or "")[:12] or "—",
            )
            for view in sorted(result.tasks.values(), key=lambda one: one.task_id)
        ],
    )
    console.print(table)
    footer(
        console,
        f"{len(result.tasks)} task(s) the log has mentioned"
        + (f" — … {withheld} more (see JSON)" if withheld else ""),
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@manager_app.command("serve")
def serve_cmd(
    partition: Annotated[str, typer.Argument(help="The repository this manager owns.")],
    dsn: Annotated[
        str,
        typer.Option("--dsn", help="Postgres DSN holding the log; omitted runs against the mock."),
    ] = "",
    worker: Annotated[
        str, typer.Option("--worker", help="This process's name in the log.")
    ] = "worker-1",
    task: Annotated[
        str,
        typer.Option("--task", help="Run only this contract; omitted takes the board's order."),
    ] = "",
    passes: Annotated[
        int,
        typer.Option("--passes", help="Stop after this many passes; 0 runs until interrupted."),
    ] = 0,
    no_dispatch: Annotated[
        bool,
        typer.Option(
            "--no-dispatch",
            help="Import contracts and release expired leases, but claim nothing.",
        ),
    ] = False,
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds an idle pass waits before looking again.")
    ] = IDLE_SECONDS,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run the manager: mint what the repository has added, claim one task
    at a time, execute it, and record what happened.

    When the configuration arms auto-merge, each pass also lands finished
    candidates through the serialized lane — the same lane the merge
    command runs, with the same approvals, review, CI and quiet-window
    refusals, and stopped by the same pause. Off, which is the default,
    landing stays a human act.

    The process holds nothing. Every pass rebuilds its view from the log, so
    interrupting this is safe at any moment — the cost of a kill is the
    lease on whatever was in flight, and a restart reads back exactly what
    the previous process knew.
    """

    root = root.resolve()

    interrupted = False

    try:
        handled = asyncio.run(
            _serve(
                dsn or None,
                partition,
                root=root,
                config_path=config_path,
                worker=worker,
                passes=passes or None,
                interval=interval,
                only=task or None,
                dispatch=not no_dispatch,
            )
        )

    except KeyboardInterrupt:
        # Not an error and not a loss: the log already carries everything
        # this process did, and the next one reads it back.
        handled, interrupted = 0, True

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "worker": worker,
                "handled": handled,
                "interrupted": interrupted,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    closing(
        console,
        "interrupted — the log holds the pass" if interrupted else f"{handled} task(s) handled",
        STYLE_DIM if interrupted or not handled else "",
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


async def _note(dsn: str | None, partition: str, task_id: str, topic: str, body: str) -> None:
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    async with _runtime(dsn) as runtime:
        await event_log(runtime.get_context()).record(
            EventKind.MESSAGE_SENT,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"to_role": "implement", "topic": topic, "body": body},
        )


# ....................... #


async def _resolve(
    dsn: str | None, partition: str, task_id: str, resolution: str, note: str
) -> None:
    from torve.application.eventlog import event_log
    from torve.domain.events import ActorKind, EventKind, SubjectType

    async with _runtime(dsn) as runtime:
        await event_log(runtime.get_context()).record(
            EventKind.ESCALATION_RESOLVED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.OPERATOR,
            actor_id="operator",
            payload={"resolution": resolution, "note": note},
        )


# ....................... #


@manager_app.command("resolve")
def resolve_cmd(
    partition: Annotated[str, typer.Argument(help="The repository the task belongs to.")],
    task_id: Annotated[str, typer.Argument(help="The escalated task.")],
    resolution: Annotated[
        str,
        typer.Option("--resolution", help="requeued, abandoned or landed."),
    ] = "requeued",
    note: Annotated[str, typer.Option("--note", help="Why, for whoever reads this later.")] = "",
    dsn: Annotated[str, typer.Option("--dsn", help="Postgres DSN holding the log.")] = "",
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Close an escalation, and say how.

    An escalation is the engine handing a task to a person; this is the
    person handing it back. `requeued` returns it to the board, `abandoned`
    takes it off, `landed` records that it was finished by hand. Only an
    operator may write this — an agent that could close its own escalation
    could escalate its way out of every rule it dislikes.
    """

    if resolution not in ("requeued", "abandoned", "landed"):
        raise fail(
            f"configuration error: resolution must be requeued, abandoned or landed, "
            f"not {resolution!r}",
            EXIT_CONFIG,
        )

    asyncio.run(_resolve(dsn or None, partition, task_id, resolution, note))

    if fmt is Format.JSON:
        emit_json({"partition": partition, "task": task_id, "resolution": resolution})
        raise typer.Exit(EXIT_OK)

    closing(out(fmt), f"{task_id}: {resolution}")
    raise typer.Exit(EXIT_OK)


# ....................... #


@manager_app.command("note")
def note_cmd(
    partition: Annotated[str, typer.Argument(help="The repository the task belongs to.")],
    task_id: Annotated[str, typer.Argument(help="The task to address.")],
    body: Annotated[str, typer.Argument(help="What the agent should know.")],
    topic: Annotated[str, typer.Option("--topic", help="One word naming what this is about.")] = (
        "note"
    ),
    dsn: Annotated[str, typer.Option("--dsn", help="Postgres DSN holding the log.")] = "",
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Say something to a running attempt.

    A note is a recorded fact, not a prompt edit: the agent polls for it
    with `torve log notes`, nothing interrupts it mid-thought, and what the
    engine tried to say is auditable afterwards whether or not it was read.
    """

    asyncio.run(_note(dsn or None, partition, task_id, topic, body))

    if fmt is Format.JSON:
        emit_json({"partition": partition, "task": task_id, "topic": topic, "sent": True})
        raise typer.Exit(EXIT_OK)

    closing(out(fmt), f"note sent to {task_id} — the agent reads it when it polls")
    raise typer.Exit(EXIT_OK)
