"""`torve log` — the divergence intake, the verb an agent calls instead of
writing the execution log by hand (RFC 0044 D-44.10). Parsing and rendering
only (D-15.6); the checking, the serialization and the staging are
`torve.application.divergence`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.application.channel import ChannelRefused, open_channel
from torve.application.divergence import IntakeRefused, append, compose, payload_of
from torve.cli.console import Format, closing, emit_json, err, make_table, out
from torve.cli.options import FormatOption, RootOption
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

log_app = typer.Typer(no_args_is_help=True, help="Record execution-log entries.")


# ....................... #


@log_app.command("divergence")
def divergence_cmd(
    task_id: Annotated[str, typer.Argument(help="The task this entry belongs to.")],
    decision: Annotated[
        str,
        typer.Option("--decision", help="The decision id this entry is about, or 'unlisted'."),
    ],
    grade: Annotated[str, typer.Option("--grade", help="LOCKED, ASSUMED, OPEN or UNLISTED.")],
    claim: Annotated[str, typer.Option("--claim", help="What reality says, in one paragraph.")],
    evidence: Annotated[
        str,
        typer.Option(
            "--evidence",
            help="One leading 'path:line - one sentence' citation, or a backticked "
            "command with its output.",
        ),
    ],
    action: Annotated[str, typer.Option("--action", help="halted, departed or decided.")],
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Which attempt this is.")] = 1,
    kind: Annotated[
        str,
        typer.Option("--kind", help="contradicted, departed, resolved or blocked."),
    ] = "",
    klass: Annotated[
        str,
        typer.Option("--class", help="discovery, spec-gap, drift or irreducible."),
    ] = "",
    proposal: Annotated[
        str, typer.Option("--proposal", help="What the specification should say instead.")
    ] = "",
    notes: Annotated[str, typer.Option("--notes", help="Anything a reader needs beside it.")] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Add one divergence entry to a task's execution log.

    The entry is checked before anything is written or sent, and a rejected
    entry leaves the log untouched: the message names what to repair, in the
    same words the gate would use later.

    An accepted entry goes to the run's channel when it has one — the broker
    appends it to the record on this run's behalf, stamping who and what it
    is about (RFC 0045 §5.2) — and into the worktree's log when it does not,
    where the engine picks it up after the attempt. Either way the landed
    log is the same projection, so a run without a broker is unchanged.
    """

    root = root.resolve()

    try:
        entry = compose(
            root,
            decision=decision,
            grade=grade,
            claim=claim,
            evidence=evidence,
            action=action,
            attempt=attempt,
            kind=kind,
            klass=klass,
            proposal=proposal,
            notes=notes,
        )

    except IntakeRefused as refused:
        if fmt is Format.JSON:
            emit_json({"accepted": False, "problems": refused.problems})
        else:
            console = err()
            console.print("entry not recorded — the log is unchanged:")

            for problem in refused.problems:
                console.print(f"  {problem}")

        raise typer.Exit(EXIT_CONFIG) from refused

    channel = open_channel(root)

    if channel is not None:
        try:
            channel.record("divergence.recorded", payload_of(entry))

        except ChannelRefused as refused:
            if fmt is Format.JSON:
                emit_json({"accepted": False, "problems": [str(refused)]})
            else:
                err().print(f"entry not recorded — the channel refused it: {refused}")

            raise typer.Exit(EXIT_CONFIG) from refused

        if fmt is Format.JSON:
            emit_json({"accepted": True, "channel": True, "log": None, "staged": False})
            raise typer.Exit(EXIT_OK)

        closing(out(fmt), "entry recorded — the engine writes the log from it")
        raise typer.Exit(EXIT_OK)

    path, document, staged = append(root, task_id, entry)
    entries = len(document["entries"])

    if fmt is Format.JSON:
        emit_json(
            {
                "accepted": True,
                "channel": False,
                "log": str(path.relative_to(root)),
                "entries": entries,
                "staged": staged,
            }
        )
        raise typer.Exit(EXIT_OK)

    closing(
        out(fmt),
        f"entry {entries} recorded in {path.relative_to(root)}"
        + (" and staged" if staged else " — the engine stages it host-side"),
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@log_app.command("notes")
def notes_cmd(
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Read the notes addressed to this run (RFC 0045 §5.3).

    A poll, never a push: nothing here interrupts an attempt, and a note the
    agent never reads is still a recorded fact about what the engine tried
    to say. A run with no channel has no notes to read — that is not an
    error, it is a run nobody can address.
    """

    channel = open_channel(root.resolve())

    if channel is None:
        if fmt is Format.JSON:
            emit_json({"channel": False, "notes": []})
        else:
            closing(out(fmt), "this run has no channel — nothing can address it")

        raise typer.Exit(EXIT_OK)

    try:
        notes = channel.notes()

    except ChannelRefused as refused:
        if fmt is Format.JSON:
            emit_json({"channel": True, "error": str(refused), "notes": []})
        else:
            err().print(f"notes unavailable: {refused}")

        raise typer.Exit(EXIT_CONFIG) from refused

    if fmt is Format.JSON:
        emit_json({"channel": True, "notes": notes})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)

    if not notes:
        closing(console, "no notes")
        raise typer.Exit(EXIT_OK)

    table = make_table("at", "topic", "note")

    for note in notes:
        table.add_row(
            str(note.get("at") or ""), str(note.get("topic") or ""), str(note.get("body") or "")
        )

    console.print(table)
    raise typer.Exit(EXIT_OK)
