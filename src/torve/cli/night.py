"""`torve night` — what a served night did (S-0079/D-3, S-0079/D-4). Parsing
and rendering only (S-0015/D-6); the fold is `torve.application.manager`.

Nothing is stored: the report is recomputed from the partition's log on every
call, like the board it has to agree with.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

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
from torve.cli.options import FormatOption, RootOption, dsn_for, read_log
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from rich.console import Console

    from torve.application.manager import NightReport

# ----------------------- #

night_app = typer.Typer(no_args_is_help=True, help="Read what a served night did.")


# ....................... #


def _table(
    console: Console, title: str, columns: tuple[str, ...], rows: list[tuple[str, ...]]
) -> None:
    """One of the report's lists, with the count of what did not fit."""

    table = make_table(*columns, title=title)
    withheld = add_rows_truncated(table, list(rows))
    console.print(table)
    footer(console, f"{len(rows)}" + (f" — … {withheld} more (see JSON)" if withheld else ""))


# ....................... #


@night_app.command("show")
def night_show(
    partition: Annotated[str, typer.Argument(help="The repository the night ran on.")],
    night: Annotated[
        str,
        typer.Option("--night", help="Which night; omitted takes the most recent one opened."),
    ] = "",
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            help="Postgres DSN holding the log; omitted reads the DSN this repository's "
            "configuration names.",
        ),
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Show what one night's window holds: what landed, what a gate
    convicted, what the engine ended, and what is still waiting on a person
    with the fact it waits on.

    The window is the span between the night's open and its close, and the
    entries are the facts the log already held inside it — so a night with no
    close is an unfinished night and says so, rather than being lost.
    """

    from torve.application.manager import (
        documents,
        night_report,
        pull_requests,
        review_threads,
    )
    from torve.application.projections import (
        cross_document_waits,
        lane_landings,
        shipped_ids,
        stream_rows,
    )

    report = night_report(
        read_log(dsn_for(root, dsn), lambda log: log.since(partition=partition)), night_id=night
    )

    if report is None:
        raise fail(
            f"no night to show: {partition}'s log holds no open"
            + (f" for {night}" if night else ""),
            EXIT_CONFIG,
        )

    # What the night left on the forge (S-0080/D-14, S-0083/D-16): folded from
    # this host's own stream over the window, because the lane records its
    # landings and read-backs here and not in the partition's log.
    rows = stream_rows(root)
    prs = pull_requests(rows, since=report.opened_at, until=report.closed_at)
    docs = documents(rows, since=report.opened_at, until=report.closed_at)
    # What the review-thread leg did inside the same window (S-0084/D-17),
    # folded from its own events: the operator reads whether it removed their
    # thread work or moved it.
    reviews = review_threads(rows, since=report.opened_at, until=report.closed_at)
    # S-0085/D-6: a task waiting on another document's landing names the
    # document, so the reader knows which pull request to look at.
    waits = cross_document_waits(root, set(lane_landings(root)) | shipped_ids(root))

    if fmt is Format.JSON:
        emit_json(
            {
                "partition": partition,
                "night": report.night_id,
                "pull_requests": prs.__dict__,
                "documents": docs.__dict__,
                "review_threads": reviews.__dict__,
                "opened_at": report.opened_at.isoformat(),
                "closed_at": report.closed_at.isoformat() if report.closed_at else None,
                "unfinished": report.unfinished,
                "terms": report.terms.model_dump(mode="json"),
                "close": report.close.model_dump(mode="json") if report.close else None,
                "landed": [
                    {"task": one.task_id, "sha": one.sha, "at": one.at.isoformat()}
                    for one in report.landed
                ],
                "convicted": [
                    {
                        "task": one.task_id,
                        "attempt": one.attempt,
                        "gate": one.gate,
                        "outcome": one.outcome,
                        "at": one.at.isoformat(),
                    }
                    for one in report.convicted
                ],
                "ended": [
                    {"task": one.task_id, "reason": one.reason, "at": one.at.isoformat()}
                    for one in report.ended
                ],
                "waiting": [
                    {"task": one.task_id, "reason": one.reason, "at": one.at.isoformat()}
                    for one in report.waiting
                ],
                "waiting_on_documents": waits,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "night show", f"{partition} · {report.night_id}")
    _render(console, report)
    _table(
        console,
        "on the forge",
        ("unit", "opened", "merged", "conflicted", "closed"),
        [
            (
                "pull requests",
                str(prs.opened),
                str(prs.merged),
                str(prs.conflicted),
                str(prs.closed),
            ),
            ("documents", str(docs.opened), str(docs.merged), "—", str(docs.closed)),
        ],
    )
    _table(
        console,
        "review threads",
        ("seen", "rounds minted", "answered", "refused", "escalated"),
        [
            (
                str(reviews.seen),
                str(reviews.minted),
                str(reviews.answered),
                str(reviews.refused),
                str(reviews.escalated),
            )
        ],
    )
    _table(
        console,
        "waiting on a document's landing",
        ("task", "document", "tasks"),
        [
            (task_id, document, ", ".join(ids))
            for task_id, by_document in sorted(waits.items())
            for document, ids in by_document.items()
        ],
    )
    close = report.close
    closing(
        console,
        "unfinished — this night has an open and no close"
        if close is None
        else f"closed on {close.reason}" + (f" ({close.detail})" if close.detail else ""),
        STYLE_DIM if close is None else "",
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


def _render(console: Console, report: NightReport) -> None:
    """The four lists, in the order the morning reads them."""

    _table(
        console,
        "landed",
        ("task", "sha"),
        [(one.task_id, one.sha[:12]) for one in report.landed],
    )
    _table(
        console,
        "convicted",
        ("task", "attempt", "gate", "outcome"),
        [(one.task_id, str(one.attempt), one.gate, one.outcome) for one in report.convicted],
    )
    _table(
        console,
        "ended",
        ("task", "reason"),
        [(one.task_id, one.reason) for one in report.ended],
    )
    _table(
        console,
        "waiting on a person",
        ("task", "reason", "since"),
        [(one.task_id, one.reason, one.at.isoformat()) for one in report.waiting],
    )
