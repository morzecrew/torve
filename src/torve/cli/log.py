"""`torve log` — the divergence intake, the verb an agent calls instead of
writing the execution log by hand (RFC 0044 D-44.10). Parsing and rendering
only (D-15.6); the checking, the serialization and the staging are
`torve.application.divergence`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.application.divergence import IntakeRefused, record
from torve.cli.console import Format, closing, emit_json, err, out
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

    The entry is checked before anything is written, and a rejected entry
    leaves the log untouched: the message names what to repair, in the same
    words the gate would use later. An accepted entry is serialized into the
    log and staged, so the diff the gate judges carries it.
    """

    root = root.resolve()

    try:
        path, document, staged = record(
            root,
            task_id,
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

    entries = len(document["entries"])

    if fmt is Format.JSON:
        emit_json(
            {
                "accepted": True,
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
