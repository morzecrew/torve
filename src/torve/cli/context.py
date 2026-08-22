"""`torve context` — parsing and rendering only (D-15.6); the projections
live in `torve.application.projections`. Markdown for a planning session,
JSON for machines — one report, two renderings (D-7.4).
"""

from __future__ import annotations

from pathlib import Path

import typer

from torve.cli.console import Format, emit_json, out
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_OK

# ----------------------- #


def context_cmd(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Project accumulated facts for a planning session (RFC 0007 §4): tasks
    by state, escalations by reason, proposals awaiting the author, gate
    health, cost against config_hash, and the programme view."""
    from torve.application.projections import context_report, render_markdown

    root = root.resolve()
    config = load_config(root, config_path)
    report = context_report(root, root / config.rfcs.path)

    if fmt is Format.JSON:
        emit_json(report)
    else:
        out(fmt).print(render_markdown(report))
    raise typer.Exit(EXIT_OK)
