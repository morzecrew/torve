"""`torve tick` — one bounded pass of the standing loop (RFC 0019).
Parsing, front-door policy and rendering only (D-15.6); order, lock, pause
and selection live in `torve.application.loop`, and every leg is wired by
the composition root, `torve.cli.assembly`. Cadence belongs to the
environment: schedule this verb with cron, a CI schedule, or a timer —
there is no daemon.
"""

from __future__ import annotations

from pathlib import Path

import typer

from torve.cli.console import (
    STYLE_DIM,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ....................... #


def tick_cmd(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """One pass of the standing loop: reap, poll the board, dispatch at
    most one queued task, process the lane if auto-merge is on, sync the
    board. Exits when the pass is done; schedule it for cadence."""

    from torve.application.loop import run_tick
    from torve.cli import assembly

    root = root.resolve()
    config = load_config(root, config_path)

    # Front door, not wiring: a CI gate with no named remote is this
    # verb's configuration error (exit 3), before any adapter is built.
    if config.promotion.require_ci and not config.scm.repo:
        raise fail(
            "configuration error: promotion.require_ci needs "
            "scm.repo to name the remote whose verdict counts",
            EXIT_CONFIG,
        )

    report = run_tick(root, config, assembly.build_tick_deps(root, config))

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "noop": report.noop,
                "locked_out": report.locked_out,
                "legs": dict(report.legs),
            }
        )
    else:
        console = out(fmt)
        header(console, "tick", "one bounded pass")
        table = make_table("leg", "outcome")

        for name, detail in report.legs:
            table.add_row(name, detail)

        console.print(table)

        closing(
            console,
            "noop — nothing moved" if report.noop else "work done",
            STYLE_DIM if report.noop else "",
        )

    raise typer.Exit(EXIT_OK)
