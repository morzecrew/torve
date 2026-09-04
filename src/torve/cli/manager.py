"""`torve manager` — the board a partition's event log adds up to (RFC 0044
§5.4). Parsing and rendering only (D-15.6); the fold is
`torve.application.manager`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from torve.application.manager import Board
from torve.application.residency import IDLE_SECONDS
from torve.cli.console import (
    STYLE_DIM,
    Format,
    add_rows_truncated,
    closing,
    emit_json,
    footer,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_OK

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
) -> int:
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.residency import serve
    from torve.application.worker import Worker
    from torve.cli import assembly

    config = load_config(root, config_path)

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
        )


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
                    }
                    for view in sorted(result.tasks.values(), key=lambda one: one.task_id)
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "manager board", partition)
    table = make_table("task", "state", "attempts", "held by", "landing")
    withheld = add_rows_truncated(
        table,
        [
            (
                view.task_id,
                view.escalation or str(view.state),
                str(view.attempts),
                view.claimed_by or "—",
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
    the previous process knew (D-44.5).
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
