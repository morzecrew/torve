"""`torve log` — the divergence intake, the verb an agent calls instead of
writing the execution log by hand (S-0044 S-0044/D-10). Parsing and rendering
only (S-0015/D-6); the checking, the serialization and the staging are
`torve.application.divergence`. The entry travels through the run's live
channel when it has one and into the worktree's log when it does not, and
the notes verb reads the other direction of that channel (S-0045/the-intake-route,
§5.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.application.channel import ChannelRefused, open_channel
from torve.application.divergence import (
    IntakeRefused,
    append,
    compose,
    open_log,
    payload_of,
)
from torve.cli.console import STYLE_DIM, Format, closing, emit_json, err, fail, make_table, out
from torve.cli.options import ConfigOption, DsnOption, FormatOption, PartitionOption, RootOption
from torve.config import layout
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
    is about — and into the worktree's log when it does not,
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
    """Read the notes addressed to this run.

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


# ....................... #


@log_app.command("owed")
def owed_cmd(
    task_id: Annotated[str, typer.Argument(help="The task whose log to check.")],
    touched: Annotated[
        list[str] | None,
        typer.Option("--touched", help="A file this attempt changed; repeat for each."),
    ] = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """What this attempt's log still owes, before a gate says so.

    Name the files you changed; this answers which LOCKED decisions govern
    them with no entry citing them yet. It is the same check the
    `decisions-reported` gate performs on the diff — the same code, so a
    green answer here and a conviction later cannot disagree.

    Nothing is written. A run with a channel is asked what it has recorded;
    a run without one reads the worktree's log.
    """

    from torve.application.channel import ChannelRefused, open_channel
    from torve.gates.context import load_task
    from torve.gates.decisions_reported import owed

    root = root.resolve()
    contract = layout.task_file(root, task_id)

    if not contract.is_file():
        raise fail(f"configuration error: no task contract at {contract}", EXIT_CONFIG)

    channel = open_channel(root)
    cited: list[str] = []

    if channel is not None:
        try:
            cited = [str(one.get("decision_id") or "") for one in channel.records()]

        except ChannelRefused as refused:
            raise fail(f"configuration error: {refused}", EXIT_CONFIG) from refused

    else:
        cited = [str(one.get("decision") or "") for one in open_log(root, task_id)["entries"]]

    problems, skipped = owed(load_task(contract).decisions, touched or [], cited)

    if fmt is Format.JSON:
        emit_json({"task": task_id, "owed": problems, "skipped": skipped})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)

    for one in skipped:
        console.print(Text(f"  {one}", STYLE_DIM))

    if not problems:
        closing(console, "nothing owed — every LOCKED decision your changes touch has an entry")
        raise typer.Exit(EXIT_OK)

    console.print("these decisions govern what you changed and have no entry yet:")

    for problem in problems:
        console.print(f"  {problem}")

    closing(console, "record one entry per decision with `torve log divergence`")
    raise typer.Exit(EXIT_OK)


# ....................... #


@log_app.command("land")
def land_cmd(
    task_id: Annotated[str, typer.Argument(help="The task that landed.")],
    commit: Annotated[
        str, typer.Option("--commit", help="The commit the landing rides in, when known.")
    ] = "",
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Which attempt landed.")] = 1,
    at: Annotated[
        str, typer.Option("--at", help="The landing's date (YYYY-MM-DD); today by default.")
    ] = "",
    agent: Annotated[
        str, typer.Option("--agent", help="Who landed it, as the trailers name them.")
    ] = "",
    partition: PartitionOption = "",
    dsn: DsnOption = "",
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Append this task's landing to the execution file of the document its
    contract names: the task, its phase and attempt, when, by whom, the
    commit when known, and the log's entries — the worktree's log, or the
    record's when a partition is given. Staged, so the commit that lands
    the task carries it. A contract naming no document lands nowhere, and
    this says so."""
    # S-0057 S-0057/D-7: the by-hand lander; the runner calls the same function.

    from torve.application import decisions, divergence
    from torve.cli.options import dsn_for, load_config, read_log
    from torve.gates.context import load_task

    task_path = layout.task_file(root, task_id)

    if not task_path.is_file():
        raise fail(f"configuration error: no contract at {task_path}", EXIT_CONFIG)

    task = load_task(task_path)
    entries: list[dict[str, Any]] | None = None

    if partition:

        async def _recorded(log: Any) -> list[dict[str, Any]]:
            return [
                divergence.entry_of(event)
                for event in await divergence.recorded_entries(log, task_id, partition=partition)
            ]

        entries = read_log(dsn_for(root, dsn), _recorded)

    spec_dir = root / load_config(root, config).specs.path

    try:
        path = decisions.land(
            root,
            spec_dir,
            task,
            attempt=attempt,
            at=at or None,
            agent=agent or _git_user(root),
            commit=commit,
            entries=entries,
        )
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    count = (
        len(entries) if entries is not None else len(divergence.open_log(root, task_id)["entries"])
    )

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "task": task_id,
                "attempt": attempt,
                "commit": commit,
                "entries": count,
                "path": str(path.relative_to(root)),
            }
        )
        raise typer.Exit(EXIT_OK)

    out(fmt).print(
        f"landed {task_id} attempt {attempt} into {path.relative_to(root)} — {count} entr"
        f"{'y' if count == 1 else 'ies'}"
    )
    raise typer.Exit(EXIT_OK)


def _git_user(root: Path) -> str:
    import subprocess

    try:
        done = subprocess.run(
            ["git", "-C", str(root), "config", "user.name"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""

    return done.stdout.strip() if done.returncode == 0 else ""
