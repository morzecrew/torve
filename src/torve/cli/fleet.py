"""`torve fleet` — the operator-side manifest and one pass over every root
(RFC 0024). Parsing, manifest resolution and rendering only (D-15.6);
survey, the shared pause decision, deterministic order and
failure-recorded continuation live in `torve.application.fleet`, and each
root's legs are wired by the composition root, `torve.cli.assembly`.

A fleet is many solo ticks under one shared decision, not a new execution
regime: each root gets the same legs a solo tick builds — with the two
corners the fleet loop has never run (the post-push forge bookkeeping and
the dispatch envelope line) refused by name inside the composition root,
so a wiring change is now one edit, not three copies' drift.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import FormatOption
from torve.config.fleet import default_manifest_path, load_fleet_manifest
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from torve.application.loop import TickReport
    from torve.config.fleet import FleetManifest, FleetRepository

# ----------------------- #

fleet_app = typer.Typer(
    no_args_is_help=True,
    help="Run the standing loop over every repository in the operator's manifest.",
)

ManifestOption = Annotated[
    Path | None,
    typer.Option(
        "--manifest",
        dir_okay=False,
        help="Fleet manifest; defaults to ~/.config/torve/fleet.yaml.",
    ),
]


# ....................... #


def _load_manifest(manifest_path: Path | None) -> FleetManifest:
    import yaml

    path = manifest_path or default_manifest_path()

    if not path.is_file():
        raise fail(
            f"no fleet manifest at {path} — set --manifest or write one",
            EXIT_CONFIG,
        )

    try:
        return load_fleet_manifest(path)

    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"fleet manifest error: {exc}", EXIT_CONFIG) from exc


# ....................... #


@fleet_app.command("tick")
def fleet_tick_cmd(
    manifest_path: ManifestOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Survey every root's escalation queue, decide the pause once for the
    fleet, tick each root in the manifest's order under its own lock with
    that decision passed down, and record one fleet event.
    A locked-out or failing root is recorded and the pass continues."""

    from torve.application.fleet import fleet_tick
    from torve.application.loop import run_tick
    from torve.cli import assembly
    from torve.config.runconfig import load_runner_config

    manifest = _load_manifest(manifest_path)

    def tick_one(repo: FleetRepository, paused: bool) -> TickReport:
        root = repo.path
        config = load_runner_config(root)

        return run_tick(
            root, config, assembly.build_fleet_tick_deps(root, config), fleet_pause=paused
        )

    report = fleet_tick(manifest, tick_one)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "escalated_total": report.escalated_total,
                "paused": report.paused,
                "roots": [asdict(o) for o in report.outcomes],
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "fleet tick", f"{len(report.outcomes)} root(s)")
    table = make_table("root", "trust", "escalated", "outcome")

    for outcome in report.outcomes:
        style = (
            STYLE_FAIL
            if outcome.outcome.startswith("error") or outcome.outcome == "locked out"
            else (STYLE_DIM if outcome.noop else STYLE_PASS)
        )
        table.add_row(
            outcome.root, outcome.trust, str(outcome.escalated), Text(outcome.outcome, style)
        )

    console.print(table)
    closing(
        console,
        f"fleet-wide pause {'in force' if report.paused else 'not in force'} "
        f"(queue at {report.escalated_total})",
        STYLE_WARN if report.paused else STYLE_DIM,
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@fleet_app.command("status")
def fleet_status_cmd(
    manifest_path: ManifestOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every root's escalation queue in one table, oldest first — the
    primary alert, in its fleet form. Read-only:
    nothing here writes, and there is no fleet store to read from instead
    of the roots themselves."""

    from torve.application.fleet import fleet_escalations

    manifest = _load_manifest(manifest_path)
    rows = fleet_escalations(manifest)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "escalations": [asdict(r) for r in rows]})
        return

    console = out(fmt)

    if not rows:
        console.print("no escalations across the fleet")
        return

    header(console, "fleet status", f"{len(rows)} escalation(s)")
    table = make_table("root", "task", "reason", "detail", "age")

    for row in rows:
        table.add_row(
            row.root, row.task_id, row.reason, row.detail, Text(f"{row.age_s:.0f}s ago", STYLE_DIM)
        )

    console.print(table)
