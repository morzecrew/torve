"""`torve ledger` — the record printed as rates, per seat and per gate.

Parsing and rendering only (S-0015/D-6): the arithmetic is
`application.ledger.ledger_report`, and this module divides nothing of its
own. The exit code reports the read, not the rates' fortunes — an expensive
seat read successfully is a successful read.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.text import Text

from torve.adapters.agent.harness import parse_tool_calls
from torve.application.ledger import contract_at, counted_rows, ledger_report
from torve.application.projections import stream_rows
from torve.application.telemetry import burn_population
from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_WARN,
    Format,
    emit_json,
    footer,
    header,
    make_table,
    out,
)
from torve.cli.options import FormatOption, RootOption
from torve.domain.states import EXIT_OK

# ----------------------- #


def _money(value: Any) -> str:
    """An absent price is unreported, never zero (S-0064/D-12): a subscription
    seat genuinely has no per-token cost."""

    return f"${value:.3f}" if isinstance(value, int | float) else "unreported"


def _rate(value: Any, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if isinstance(value, int | float) else "—"


def _tokens(value: Any) -> str:
    """A per-line token count at the scale the record shows — 153000 reads as
    a number with grouping, not a run of digits."""

    return f"{value:,.0f}" if isinstance(value, int | float) else "—"


def _percent(value: Any) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, int | float) else "—"


def _duration(value: Any) -> str:
    """Seconds in the unit that shows them: a record holds both a
    three-second boot failure and thirty-seven agent-hours, and one unit
    rounds one of them away."""

    if not isinstance(value, int | float):
        return "—"

    if value >= 3600:
        return f"{value / 3600:.1f}h"

    return f"{value / 60:.1f}m" if value >= 60 else f"{value:.1f}s"


# ....................... #


def ledger_cmd(
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Fold the record into rates: per seat, what a landed task cost, how
    many attempts a landing took, how many blocking convictions came before
    one, and what share of elapsed time the seat spent running; per changed
    line and per changed file, what a line of that work cost in cache-read
    tokens, wall time, tool calls and dollars; per gate, the wall time it
    spent against the convictions it produced.

    A seat is a tier and the image it was pointed at together, because one
    tier name aimed at several images averages into a figure that describes
    nothing. Only attempts that ran a model are counted — fake adapters and
    shadow replays are excluded, and the exclusion is printed. A seat whose
    provider carries no price reports its cost as unreported, never as zero.

    Burn profiles are classified from each attempt's retained trace when the
    trace is still on disk, and from the block the attempt recorded only when
    it is not — so a corrected class reclassifies the record's history rather
    than only the attempts that came after it.

    Rates, not rows: use `torve why` and `torve status` for the attempts and
    gate runs behind them.
    """

    report = ledger_report(root)
    # S-0075/D-6: the profile is derived where it is read, from the trace the
    # recorded block is only a cache of. The trace scanner is the harness
    # adapter's and the contract reader reaches git, so both are wired here —
    # adapter imports belong to this layer (S-0015/permitted-imports) and the
    # classification stays the engine's. The population is the same counted
    # attempts the rates above divide by.
    report["burn"] = burn_population(
        counted_rows(stream_rows(root))[0],
        root,
        parse_tool_calls,
        contracts=partial(contract_at, root),
    )

    if fmt is Format.JSON:
        emit_json(report)
        raise typer.Exit(EXIT_OK)

    _render(report)


# ....................... #


def _render(report: dict[str, Any]) -> None:
    console = out()
    header(
        console,
        "ledger",
        f"{report['attempts']} counted attempt(s) · {report['landed_tasks']} landed task(s)",
    )
    console.print()

    # The total spend rides the JSON envelope, not this table: the verb
    # exists to print rates, and an eighth column costs the seat name its
    # width on an 80-column terminal.
    seats = make_table(
        "seat",
        "attempts",
        "landed",
        "cost/landing",
        "attempts/landing",
        "convictions/landing",
        "duty",
        title="seats",
    )

    for seat in report["seats"]:
        seats.add_row(
            f"{seat['tier']} @ {seat['image']}",
            str(seat["attempts"]),
            str(seat["landed_tasks"]),
            _money(seat["cost_per_landed_task_usd"]),
            _rate(seat["attempts_per_landing"]),
            _rate(seat["convictions_before_landing"]),
            _percent(seat["duty_cycle"]),
        )

    if report["seats"]:
        console.print(seats)

        # The work-shaped rates beside the task-shaped ones (S-0075/D-3): the
        # four figures that show what a changed line cost, then the same
        # figures per file in scope — the path that carried the cost is the
        # one that says so. The seat's own changed lines ride with the rates,
        # the same both-sides discipline the duty ratio above follows. A rate
        # whose diff or whose numerator never resolved prints as a dash.
        per_line = make_table(
            "seat",
            "lines",
            "cache-read/line",
            "wall/line",
            "calls/line",
            "$/line",
            title="per changed line",
        )

        for seat in report["seats"]:
            per_line.add_row(
                f"{seat['tier']} @ {seat['image']}",
                str(seat["changed_lines"]),
                _tokens(seat["cache_read_tokens_per_line"]),
                _rate(seat["wall_time_s_per_line"]),
                _tokens(seat["tool_calls_per_line"]),
                _money(seat["cost_usd_per_line"]),
            )

        console.print(per_line)

        files = make_table(
            "seat",
            "file",
            "lines",
            "cache-read/line",
            "wall/line",
            "calls/line",
            "$/line",
            title="per file in scope",
        )

        for seat in report["seats"]:
            for entry in seat["files"]:
                files.add_row(
                    f"{seat['tier']} @ {seat['image']}",
                    entry["path"],
                    str(entry["lines"]),
                    _tokens(entry["cache_read_tokens_per_line"]),
                    _rate(entry["wall_time_s_per_line"]),
                    _tokens(entry["tool_calls_per_line"]),
                    _money(entry["cost_usd_per_line"]),
                )

        if any(seat["files"] for seat in report["seats"]):
            console.print(files)
        else:
            console.print(Text("no changed file in a landed diff to divide by", STYLE_DIM))
            console.print()

        # Duty cycle is a ratio whose denominator the specification left
        # open (S-0065/Q-2, decided in this task's log): both sides are printed
        # so nobody has to trust the word.
        footer(
            console,
            "duty is agent wall time over the elapsed span of the seat's own attempts — "
            + " · ".join(
                f"{seat['tier']}@{seat['image']}: {_duration(seat['wall_time_s'])} of "
                f"{_duration(seat['span_s'])}"
                for seat in report["seats"]
            ),
        )
    else:
        console.print(Text("no seat has an attempt a rate may count", STYLE_DIM))
        console.print()

    _burn(console, report["burn"])

    gates = make_table("gate", "runs", "wall", "convictions", "seconds/conviction", title="gates")

    for gate in report["gates"]:
        gates.add_row(
            gate["gate"],
            str(gate["runs"]),
            _duration(gate["wall_time_s"]),
            Text(str(gate["convictions"]), style=STYLE_FAIL if gate["convictions"] else ""),
            _rate(gate["seconds_per_conviction"], 1),
        )

    if report["gates"]:
        console.print(gates)
    else:
        console.print(Text("no gate has run on a counted attempt", STYLE_DIM))
        console.print()

    _exclusions(console, report)


def _burn(console: Any, burn: dict[str, Any]) -> None:
    """What the attempts' tool calls were spent on, classified when this
    command read them (S-0075/D-6). The population is printed beside the two
    baselines, because a profile of one attempt says nothing a mitigation can
    be judged by and a corpus of them does."""

    if not burn["profiled"]:
        console.print(Text("no attempt has a trace or a recorded profile to classify", STYLE_DIM))
        console.print()

        return

    profiles = make_table(
        "attempts",
        "profiled",
        "from trace",
        "from record",
        "reclassified",
        "with an edit",
        title="burn profiles",
    )
    profiles.add_row(
        str(burn["attempts"]),
        str(burn["profiled"]),
        str(burn["sources"]["trace"]),
        str(burn["sources"]["recorded"]),
        Text(str(burn["reclassified"]), style=STYLE_WARN if burn["reclassified"] else ""),
        str(burn["with_edit"]),
    )
    console.print(profiles)
    footer(
        console,
        "a profile is classified from the attempt's own retained trace, and from the block the "
        "attempt recorded only where the trace is gone — median orientation share of bytes read: "
        f"{_percent(burn['median_orientation_share_of_bytes'])} · median share of calls before "
        f"the first edit: {_percent(burn['median_calls_before_first_edit_share'])}",
    )


def _exclusions(console: Any, report: dict[str, Any]) -> None:
    """What was left out, said rather than assumed (S-0065/D-5, LOCKED): every
    later comparison rests on this denominator, so a reader is told what it
    excluded."""

    excluded = report["excluded"]
    contracts = report["contracts"]

    console.print(
        Text(
            "excluded from every rate — "
            f"{excluded['fake_adapter']} fake-adapter, "
            f"{excluded['shadow_replay']} shadow replay, "
            f"{excluded['not_an_attempt']} not an attempt, "
            f"{excluded['no_base_sha']} unjoinable (no base sha)",
            style=STYLE_WARN if excluded["no_base_sha"] else STYLE_DIM,
        )
    )
    console.print(
        Text(
            "contracts joined — "
            f"{contracts['tree']} from the working tree, "
            f"{contracts['history']} from git history, "
            f"{contracts['absent']} never committed",
            style=STYLE_DIM,
        )
    )

    raise typer.Exit(EXIT_OK)
