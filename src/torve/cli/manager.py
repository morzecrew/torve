"""`torve manager` — the board a partition's event log adds up to (RFC 0044
§5.4). Parsing and rendering only (D-15.6); the fold is
`torve.application.manager`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from torve.application.manager import Board
from torve.cli.console import Format, add_rows_truncated, emit_json, footer, header, make_table, out
from torve.cli.options import FormatOption, RootOption
from torve.domain.states import EXIT_OK

# ----------------------- #

manager_app = typer.Typer(no_args_is_help=True, help="Read the engine's board.")


# ....................... #


async def _board(dsn: str | None, partition: str) -> Board:
    from forze.application.execution import DepsRegistry, ExecutionRuntime

    from torve.adapters.eventstore.document import mock_module, postgres_module
    from torve.application.eventlog import event_log
    from torve.application.manager import project

    module = await postgres_module(dsn) if dsn else mock_module()
    runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

    async with runtime.scope():
        log = event_log(runtime.get_context())

        return project(await log.since(partition=partition))


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
    shown = add_rows_truncated(
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
    footer(console, f"{shown} task(s) the log has mentioned")
    raise typer.Exit(EXIT_OK)
