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
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from forze.application.execution import ExecutionRuntime

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
) -> int:
    from torve.adapters.vcs.git import GitVcs
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.loop import run_record_exists
    from torve.application.residency import serve
    from torve.application.worker import Worker
    from torve.cli import assembly

    config = load_config(root, config_path)
    vcs = GitVcs()

    def landed(task_id: str) -> str | None:
        # The repository's own answer (A-29): a contract the tree already
        # landed is minted onto the board as landed, so a first pass over a
        # repository with history does not offer a worker somebody's
        # finished work.
        shas = vcs.landed_shas(root, task_id)

        return shas[0] if shas else None

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
            landed=landed,
            ran=lambda task_id: run_record_exists(root, task_id),
            only=only,
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
    table = make_table("task", "state", "attempts", "held by", "burn", "landing")
    withheld = add_rows_truncated(
        table,
        [
            (
                view.task_id,
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
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds an idle pass waits before looking again.")
    ] = IDLE_SECONDS,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run the manager: mint what the repository has added, claim one task
    at a time, execute it, and record what happened.

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
