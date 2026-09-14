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


def _given(count: int) -> str:
    return {0: "not given", 1: "given once"}.get(count, f"given {count} times")


def _refuse(problems: list[str], rows: int, fmt: Format, why: str) -> typer.Exit:
    """Say what to repair and write nothing. The batch is one transaction:
    a row the gate would refuse leaves every other row unwritten too, so an
    attempt never has to work out which half of a call landed."""

    if fmt is Format.JSON:
        emit_json({"accepted": False, "problems": problems})
    else:
        console = err()
        console.print(f"{'entry' if rows == 1 else 'entries'} not recorded — {why}:")

        for problem in problems:
            console.print(f"  {problem}")

    return typer.Exit(EXIT_CONFIG)


@log_app.command("divergence")
def divergence_cmd(
    task_id: Annotated[str, typer.Argument(help="The task this entry belongs to.")],
    decision: Annotated[
        list[str] | None,
        typer.Option(
            "--decision",
            help="The decision id this entry is about, or 'unlisted'. Repeat it, "
            "with every other option, to record several rows in one call.",
        ),
    ] = None,
    grade: Annotated[
        list[str] | None, typer.Option("--grade", help="LOCKED, ASSUMED, OPEN or UNLISTED.")
    ] = None,
    claim: Annotated[
        list[str] | None, typer.Option("--claim", help="What reality says, in one paragraph.")
    ] = None,
    evidence: Annotated[
        list[str] | None,
        typer.Option(
            "--evidence",
            help="One leading 'path:line - one sentence' citation, or a backticked "
            "command with its output.",
        ),
    ] = None,
    action: Annotated[
        list[str] | None, typer.Option("--action", help="halted, departed or decided.")
    ] = None,
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Which attempt this is.")] = 1,
    kind: Annotated[
        list[str] | None,
        typer.Option("--kind", help="contradicted, departed, resolved or blocked."),
    ] = None,
    klass: Annotated[
        list[str] | None,
        typer.Option("--class", help="discovery, spec-gap, drift or irreducible."),
    ] = None,
    proposal: Annotated[
        list[str] | None,
        typer.Option("--proposal", help="What the specification should say instead."),
    ] = None,
    notes: Annotated[
        list[str] | None, typer.Option("--notes", help="Anything a reader needs beside it.")
    ] = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Add divergence entries to a task's execution log.

    One row, or several in one call: repeat the options, and the nth
    `--decision` goes with the nth `--grade`, `--claim`, `--evidence` and
    `--action`. An optional option is given for every row or for none of
    them, so a row never silently borrows its neighbour's `--class`. The
    attempt is the call's, not the row's.

    Every entry is checked before anything is written or sent, and a
    rejected batch leaves the log untouched: the message names what to
    repair, in the same words the gate would use later.

    An accepted entry goes to the run's channel when it has one — the broker
    appends it to the record on this run's behalf, stamping who and what it
    is about — and into the worktree's log when it does not,
    where the engine picks it up after the attempt. Either way the landed
    log is the same projection, so a run without a broker is unchanged.
    """

    root = root.resolve()
    decisions = decision or []
    rows = len(decisions)

    if not rows:
        raise _refuse(["--decision is required, once per row"], 1, fmt, "nothing was stated")

    required = {"--grade": grade, "--claim": claim, "--evidence": evidence, "--action": action}
    optional = {"--kind": kind, "--class": klass, "--proposal": proposal, "--notes": notes}
    stated = f"{rows} row{'' if rows == 1 else 's'} stated"
    counts = [
        f"{name} is {_given(len(given or []))} with {stated} — once per row"
        for name, given in required.items()
        if len(given or []) != rows
    ] + [
        f"{name} is {_given(len(given))} with {stated} — once per row or not at all"
        for name, given in optional.items()
        if given and len(given) != rows
    ]

    if counts:
        raise _refuse(counts, rows, fmt, "the options do not line up")

    def field(given: list[str] | None, index: int) -> str:
        return given[index] if given else ""

    entries: list[dict[str, Any]] = []
    problems: list[str] = []

    for index, one in enumerate(decisions):
        try:
            entries.append(
                compose(
                    root,
                    decision=one,
                    grade=field(grade, index),
                    claim=field(claim, index),
                    evidence=field(evidence, index),
                    action=field(action, index),
                    attempt=attempt,
                    kind=field(kind, index),
                    klass=field(klass, index),
                    proposal=field(proposal, index),
                    notes=field(notes, index),
                )
            )

        except IntakeRefused as refused:
            problems += [
                f"row {index + 1} ({one}): {problem}" if rows > 1 else problem
                for problem in refused.problems
            ]

    if problems:
        raise _refuse(problems, rows, fmt, "the log is unchanged")

    channel = open_channel(root)

    if channel is not None:
        for entry in entries:
            try:
                channel.record("divergence.recorded", payload_of(entry))

            except ChannelRefused as refused:
                why = f"the channel refused {'it' if rows == 1 else 'one of them'}"
                raise _refuse([str(refused)], rows, fmt, why) from refused

        if fmt is Format.JSON:
            emit_json({"accepted": True, "channel": True, "log": None, "staged": False})
            raise typer.Exit(EXIT_OK)

        closing(
            out(fmt),
            ("entry" if rows == 1 else f"{rows} entries")
            + " recorded — the engine writes the log from it",
        )
        raise typer.Exit(EXIT_OK)

    staged = True
    path = layout.log_file(root, task_id)
    total = 0

    for entry in entries:
        path, document, one_staged = append(root, task_id, entry)
        staged = staged and one_staged
        total = len(document["entries"])

    if fmt is Format.JSON:
        emit_json(
            {
                "accepted": True,
                "channel": False,
                "log": str(path.relative_to(root)),
                "entries": total,
                "staged": staged,
            }
        )
        raise typer.Exit(EXIT_OK)

    closing(
        out(fmt),
        (f"entry {total}" if rows == 1 else f"entries {total - rows + 1}-{total}")
        + f" recorded in {path.relative_to(root)}"
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
    """Write this task's landing into the execution directory of the
    document its contract names: the task, its phase and attempt, the base
    it built on, when, by whom, the commit it rides in, and the log's
    entries — the worktree's log, or the record's when a partition is
    given. Run it after the work commit, naming that commit, and commit
    the landing on its own. A contract naming no document lands nowhere,
    and this says so."""
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
