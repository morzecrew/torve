"""`torve why <task-id>` — one task's execution history, read from the
durable record (RFC 0040 §5.2). Parsing and rendering only (D-15.6): the
envelope is computed once by `application.projections.why_report`, which the
MCP tool and the serve endpoint re-expose identically — this command renders
that same envelope, it derives nothing of its own.

The exit code reports the read, not history's fortunes: a red history read
successfully is a successful read and exits 0; 3 is a configuration problem —
the task id is not in the corpus, so a typo cannot read as a task with no
history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    emit_json,
    fail,
    header,
    out,
)
from torve.cli.options import FormatOption, RootOption
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


def _money(value: Any) -> str:
    return f"${value:.3f}" if isinstance(value, int | float) else "unreported"


def _seconds(value: Any, estimated: bool = False) -> str:
    if not isinstance(value, int | float):
        return ""

    return f"{'~' if estimated else ''}{value:.1f}s"


# ....................... #


def _attempt_line(entry: dict[str, Any]) -> Text:
    line = Text()
    line.append(f"{entry.get('at') or '?'}  ", style=STYLE_DIM)

    if entry.get("attempt") is not None:
        line.append(f"attempt {entry['attempt']}", style=STYLE_ID)
    else:
        # Pre-verdict history is shown as partial, never retrofitted.
        line.append("attempt (pre-verdict record)", style=STYLE_WARN)

    verdict = entry.get("verdict")

    if verdict:
        line.append(f" — {verdict}", style=STYLE_PASS if verdict == "green" else STYLE_FAIL)
    else:
        line.append(" — verdict unrecorded", style=STYLE_DIM)

    seats = " / ".join(str(part) for part in (entry.get("tier"), entry.get("model")) if part)

    if seats:
        line.append(f" · {seats}")

    line.append(f" · {_money(entry.get('cost_usd'))}")

    for part in (_seconds(entry.get("wall_time_s"), bool(entry.get("wall_est"))),):
        if part:
            line.append(f" · {part}")

    tokens = " ".join(
        f"{key.removesuffix('_tokens')} {entry[key]}"
        for key in ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens")
        if key in entry
    )

    if tokens:
        line.append(f" · tokens: {tokens}", style=STYLE_DIM)

    if entry.get("convictions"):
        line.append(f" · convicted: {', '.join(entry['convictions'])}", style=STYLE_FAIL)

    if entry.get("gates_run") is False:
        line.append(" · gates never ran", style=STYLE_DIM)

    if entry.get("escalation"):
        line.append(f" · escalation: {entry['escalation']}", style=STYLE_WARN)

    if entry.get("trace_ref"):
        where = "present" if entry.get("trace_present") else "absent"
        line.append(f" · trace {entry['trace_ref']} ({where})", style=STYLE_DIM)

    return line


def _event_line(entry: dict[str, Any]) -> Text:
    line = Text()
    line.append(f"{entry.get('at') or '?'}  ", style=STYLE_DIM)
    line.append(f"event — {entry.get('event')}", style=STYLE_WARN)

    detail = " ".join(
        f"{key}={value}"
        for key, value in entry.items()
        if key not in {"at", "event", "schema_version", "kind", "task", "run_id"}
        and value not in (None, "")
    )

    if detail:
        line.append(f" · {detail}", style=STYLE_DIM)

    return line


def _review_line(entry: dict[str, Any]) -> Text:
    line = Text()
    line.append(f"{entry.get('at') or '?'}  ", style=STYLE_DIM)
    line.append(f"review — {entry.get('review')}", style=STYLE_ID)

    if entry.get("unparseable"):
        line.append(" · output unparseable", style=STYLE_WARN)
    else:
        line.append(
            f" · {entry.get('verdict_findings', 0)} finding(s), "
            f"{entry.get('blockers', 0)} blocker(s)",
        )

    return line


# ....................... #


def why_cmd(
    task_id: Annotated[
        str,
        typer.Argument(help="The task to interrogate, e.g. T-0213."),
    ],
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Project one task's execution history from the durable record: every
    attempt with its verdict, tier, gate convictions, cost, clock and trace;
    the engine events and reviews around them; totals; and the task's cost
    against its own regime's attempt distribution. The stream answers a
    landed or reaped task as completely as a live one."""

    from torve.application.projections import why_report

    envelope = why_report(root.resolve(), task_id)

    if fmt is Format.JSON:
        # The envelope, verbatim — the same bytes the serve endpoint hands
        # the browser. An unknown id still gets its envelope, then the
        # configuration exit, so a machine reader is told either way.
        emit_json(envelope)

        if not envelope.get("found"):
            raise typer.Exit(EXIT_CONFIG)

        raise typer.Exit(EXIT_OK)

    if not envelope.get("found"):
        raise fail(f"no task {task_id} in this repository's contract history", EXIT_CONFIG)

    _render(envelope)


# ....................... #


def _render(envelope: dict[str, Any]) -> None:
    console = out()
    state = envelope.get("state")
    header(
        console,
        "why",
        f"{envelope['task']} · " + (state if state else "state not provable from the stream"),
        envelope["regime"]["config_hash"],
    )
    console.print(Text(f"contract: {envelope.get('rfc') or 'unknown document'}", STYLE_DIM))
    console.print()

    # The rendered form interleaves chronologically (the envelope keeps the
    # three kinds separate so renderers choose); attempts lead a tie.
    timeline = sorted(
        [(str(a.get("at") or ""), 0, _attempt_line(a)) for a in envelope["attempts"]]
        + [(str(e.get("at") or ""), 1, _event_line(e)) for e in envelope["events"]]
        + [(str(r.get("at") or ""), 2, _review_line(r)) for r in envelope["reviews"]],
        key=lambda item: (item[0], item[1]),
    )

    if not timeline:
        console.print(Text("no recorded runs — the contract exists, the stream is silent", STYLE_DIM))

    for _, _, line in timeline:
        console.print(line)

    console.print()

    totals = envelope["totals"]
    human = (
        f"{totals['human_minutes']} min of human time"
        if totals["human_minutes"] is not None
        else "no human time recorded"
    )
    console.print(
        Text(
            f"total — {totals['attempts']} attempt(s) · {_money(totals['cost_usd'])} · "
            f"{totals['input_tokens'] if totals['input_tokens'] is not None else 'unreported'} in / "
            f"{totals['output_tokens'] if totals['output_tokens'] is not None else 'unreported'} out · "
            f"{_seconds(totals['wall_time_s']) or 'unreported'} wall · {human}"
        )
    )

    regime = envelope["regime"]

    if regime["config_hash"]:
        console.print(
            Text(
                f"regime {regime['config_hash']} — same-regime attempt cost: "
                f"median {_money(regime['attempt_cost_median_usd'])}, "
                f"p90 {_money(regime['attempt_cost_p90_usd'])} (n={regime['attempt_cost_n']})",
                STYLE_DIM,
            )
        )
    else:
        console.print(Text("regime — never recorded on this task's attempts", STYLE_DIM))

    # The caveat rides every rendering because it rides the envelope: a
    # comparator must never be displayed without its wording.
    console.print(Text(regime["caveat"], STYLE_WARN))

    raise typer.Exit(EXIT_OK)
