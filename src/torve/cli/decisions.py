"""`torve decisions` — the decision graph the record holds, and the import
that fills it (RFC 0047 §5.4). Parsing and rendering only (D-15.6); the fold
and the comparison are `torve.application.decisions`.

`import` is idempotent by construction: it compares the corpus to what the
record already holds and appends only the difference, so running it twice
over an unchanged corpus writes nothing. `--check` is that same comparison
with the write skipped.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from torve.cli.console import (
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

    from torve.application.decisions import DecisionState, Graph, PendingEvent

# ----------------------- #

decisions_app = typer.Typer(
    no_args_is_help=True, help="Read the decision graph and import the corpus into it."
)

DsnOption = Annotated[
    str, typer.Option("--dsn", help="Postgres DSN holding the log; omitted reads an empty log.")
]
PartitionArgument = Annotated[
    str, typer.Argument(help="The repository whose record these decisions belong to.")
]


# ....................... #


@asynccontextmanager
async def _runtime(dsn: str | None) -> AsyncGenerator[ExecutionRuntime]:
    """The store this reads and writes. Without a DSN the mock stands in — a
    real log for the life of the process and nothing afterwards, which makes
    `--check` against an empty record show the whole corpus as pending."""

    import sys

    from forze.application.execution import DepsRegistry, ExecutionRuntime
    from forze.base.logging import configure_logging

    from torve.adapters.eventstore.document import mock_module, postgres_module

    # The runtime narrates itself on stdout, and stdout is where this verb's
    # JSON goes. Warnings and worse, on stderr (D-15.6).
    configure_logging(level="warning", stream=sys.stderr)

    module = await postgres_module(dsn) if dsn else mock_module()
    runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

    async with runtime.scope():
        yield runtime


# ....................... #


def _corpus_dir(root: Path, config_path: Path | None) -> Path:
    config = load_config(root, config_path)
    resolved = root / config.rfcs.path

    if not resolved.is_dir():
        raise fail(
            f"configuration error: no corpus directory at {resolved} "
            "(the rfcs.path configuration key)",
            EXIT_CONFIG,
        )

    return resolved


# ....................... #


async def _graph(dsn: str | None, partition: str) -> Graph:
    from torve.application.decisions import load
    from torve.application.eventlog import event_log

    async with _runtime(dsn) as runtime:
        return await load(event_log(runtime.get_context()), partition=partition)


# ....................... #


async def _import(
    dsn: str | None, partition: str, rfc_dir: Path, *, actor: str, write: bool
) -> list[PendingEvent]:
    from torve.application.decisions import import_corpus, load, record_all
    from torve.application.eventlog import event_log

    async with _runtime(dsn) as runtime:
        log = event_log(runtime.get_context())
        pending = import_corpus(await load(log, partition=partition), rfc_dir)

        if write:
            await record_all(log, pending, partition=partition, actor_id=actor)

        return pending


# ....................... #


@decisions_app.command("import")
def import_cmd(
    partition: PartitionArgument,
    dsn: DsnOption = "",
    check: Annotated[
        bool,
        typer.Option("--check", help="Report what an import would append; write nothing."),
    ] = False,
    actor: Annotated[
        str, typer.Option("--actor", help="Who is importing, as the record will name them.")
    ] = "operator",
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Bring the record level with the corpus.

    Only accepted, non-superseded documents are read: a draft's decisions do
    not stand, so recording them would date them wrongly. A row whose grade,
    text or declared paths changed is recorded again as a new version of the
    same decision; a row an accepted table no longer carries is recorded as
    retired rather than left standing.

    Running this twice over an unchanged corpus appends nothing the second
    time, so it is safe on a schedule.
    """

    rfc_dir = _corpus_dir(root, config)
    pending = asyncio.run(_import(dsn or None, partition, rfc_dir, actor=actor, write=not check))

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "written": not check,
                "events": [{"kind": str(one.kind), "subject": one.subject_id} for one in pending],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "decisions import", partition)

    if not pending:
        closing(console, "the record is level with the corpus — nothing to append")
        raise typer.Exit(EXIT_OK)

    table = make_table("event", "subject")
    withheld = add_rows_truncated(table, [(str(one.kind), one.subject_id) for one in pending])
    console.print(table)
    footer(
        console,
        f"{len(pending)} event(s) {'the record is missing — nothing written' if check else 'appended'}"
        + (f" — … {withheld} more (see JSON)" if withheld else ""),
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


def _row(state: DecisionState) -> tuple[str, ...]:
    return (
        state.id,
        state.grade,
        state.source_id,
        str(state.version),
        ", ".join(state.paths[:2]) + ("…" if len(state.paths) > 2 else "") or "—",
    )


# ....................... #


@decisions_app.command("list")
def list_cmd(
    partition: PartitionArgument,
    dsn: DsnOption = "",
    source: Annotated[
        str, typer.Option("--source", help="Only this source's rows, by source id.")
    ] = "",
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every decision in force, as the record holds it. A retired decision
    keeps its history and leaves this list."""

    graph = asyncio.run(_graph(dsn or None, partition))
    states = graph.by_source(source) if source else graph.current()

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "decisions": [
                    {
                        "id": one.id,
                        "grade": one.grade,
                        "source": one.source_id,
                        "version": one.version,
                        "paths": one.paths,
                        "at": one.at.isoformat(),
                    }
                    for one in states
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "decisions", source or partition)
    table = make_table("decision", "grade", "source", "version", "paths")
    withheld = add_rows_truncated(table, [_row(one) for one in states])
    console.print(table)
    footer(
        console,
        f"{len(states)} decision(s) in force"
        + (f" — … {withheld} more (see JSON)" if withheld else ""),
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@decisions_app.command("show")
def show_cmd(
    partition: PartitionArgument,
    identifier: Annotated[
        str, typer.Argument(help="A decision identifier, as its table spells it.")
    ],
    dsn: DsnOption = "",
    fmt: FormatOption = Format.TEXT,
) -> None:
    """One decision as it stands, and every version behind it — the question
    `git log -p` over the corpus answers today, asked of the record."""

    graph = asyncio.run(_graph(dsn or None, partition))
    history = graph.history(identifier)

    if not history:
        raise fail(
            f"configuration error: the record holds no decision {identifier!r} in "
            f"{partition} — import the corpus first, or check the identifier",
            EXIT_CONFIG,
        )

    standing = history[-1]

    if fmt is Format.JSON:
        emit_json(
            {
                "id": standing.id,
                "grade": standing.grade,
                "text": standing.text,
                "paths": standing.paths,
                "source": standing.source_id,
                "retired": standing.retired,
                "versions": [
                    {
                        "version": one.version,
                        "grade": one.grade,
                        "text": one.text,
                        "at": one.at.isoformat(),
                    }
                    for one in history
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "decision", standing.id)
    console.print(
        f"{standing.grade}{' · retired' if standing.retired else ''} · {standing.source_id}"
    )
    console.print()
    console.print(standing.text)
    console.print()
    table = make_table("version", "grade", "recorded (UTC)")
    add_rows_truncated(
        table,
        [(str(one.version), one.grade, one.at.strftime("%Y-%m-%d %H:%M")) for one in history],
    )
    console.print(table)
    footer(console, ", ".join(standing.paths) or "no declared paths")
    raise typer.Exit(EXIT_OK)


# ....................... #


@decisions_app.command("paths")
def paths_cmd(
    partition: PartitionArgument,
    globs: Annotated[
        list[str], typer.Argument(help="Path globs, as a task's scope.allow spells them.")
    ],
    dsn: DsnOption = "",
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The decisions in force whose declared paths cross these globs — what
    work under them inherits.

    Rows without declared paths never appear: they govern their own
    document's work only. The rule is deliberately conservative, since a
    false inclusion costs a few contract lines and a false exclusion costs
    the silence check.
    """

    graph = asyncio.run(_graph(dsn or None, partition))
    states = graph.for_paths(list(globs))

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "globs": list(globs),
                "decisions": [
                    {
                        "id": one.id,
                        "grade": one.grade,
                        "source": one.source_id,
                        "paths": one.paths,
                    }
                    for one in states
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "decisions crossing", ", ".join(globs))
    table = make_table("decision", "grade", "source", "version", "paths")
    withheld = add_rows_truncated(table, [_row(one) for one in states])
    console.print(table)
    footer(
        console,
        f"{len(states)} decision(s) cross these paths"
        + (f" — … {withheld} more (see JSON)" if withheld else ""),
    )
    raise typer.Exit(EXIT_OK)
