"""`torve plan` — parsing and rendering only (D-15.6); the minter lives in
`torve.application.planner`. Dry-run is the default (RFC 0007 §3, and the
D-11 convention: extend `--dry-run`, never invent a sibling flag). Exactly
one document per invocation (D-7.8); `--reconcile` is §3.3 under charter
A-22.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
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
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #


def plan_cmd(
    identifier: Annotated[
        str | None, typer.Argument(help="Exactly one document — no sets, no subgraphs, no --all.")
    ] = None,
    reconcile: Annotated[
        bool,
        typer.Option(
            "--reconcile",
            help="Mark non-terminal tasks minted from superseded documents as "
            "stale_inheritance; takes no document.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Preview without writing (the default); --no-dry-run mints.",
        ),
    ] = True,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Mint task contracts from one accepted, committed specification.
    Deterministic; no model is called at any point."""

    from torve.application.planner import PlanError, plan_document, write_contracts

    root = root.resolve()
    config = load_config(root, config_path)
    rfc_dir = root / config.rfcs.path

    if reconcile:
        if identifier is not None:
            raise fail(
                "configuration error: --reconcile sweeps the whole corpus and takes no document",
                EXIT_CONFIG,
            )

        _reconcile(root, rfc_dir, dry_run, fmt)

        return

    if identifier is None:
        raise fail("configuration error: torve plan takes exactly one document", EXIT_CONFIG)

    try:
        report = plan_document(root, rfc_dir, identifier)
        written = [] if dry_run else write_contracts(root, report)

    except PlanError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": SCHEMA_VERSION,
                "document": report.document,
                "dry_run": dry_run,
                "tasks": [
                    {**planned.task.model_dump(), "title": planned.title, "size": planned.size.size}
                    for planned in report.tasks
                ],
                "written": [str(path) for path in written],
            }
        )
    else:
        console = out(fmt)
        header(console, "plan", f"{report.document} · {len(report.tasks)} task(s)")
        table = make_table("task", "phase", "title", "size", "decisions", "scope", "after")

        for planned in report.tasks:
            task = planned.task

            table.add_row(
                Text(task.id, STYLE_ID),
                str(task.phase),
                planned.title,
                Text(planned.size.size, "" if planned.size.size == "ok" else STYLE_WARN),
                str(len(task.decisions)),
                str(len(task.scope.allow)),
                Text(", ".join(task.depends_on), STYLE_DIM),
            )

        console.print(table)

        if dry_run:
            closing(console, "dry run — nothing written; pass --no-dry-run to mint", STYLE_DIM)
        else:
            for path in written:
                console.print(Text(f"  minted {path}", ""))

            closing(console, f"minted {len(written)} contract(s)", STYLE_PASS)

    raise typer.Exit(EXIT_OK)


# ....................... #


def _reconcile(root: Path, rfc_dir: Path, dry_run: bool, fmt: Format) -> None:
    from torve.application.planner import reconcile

    found = reconcile(root, rfc_dir, dry_run=dry_run)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": SCHEMA_VERSION,
                "dry_run": dry_run,
                "stale": [
                    {
                        "task": s.task_id,
                        "rfc": s.document,
                        "superseded_by": s.superseded_by,
                        "state": s.state,
                        "action": s.action,
                    }
                    for s in found
                ],
            }
        )
    else:
        console = out(fmt)
        header(console, "plan --reconcile", f"{len(found)} stale task(s)")

        if not found:
            closing(console, "no tasks minted from superseded documents — nothing to reconcile")
        else:
            table = make_table("task", "state", "document", "superseded by", "action")

            for stale in found:
                table.add_row(
                    Text(stale.task_id, STYLE_ID),
                    stale.state,
                    stale.document,
                    stale.superseded_by or "an unset successor",
                    Text(stale.action, STYLE_FAIL if "escalate" in stale.action else STYLE_DIM),
                )

            console.print(table)

        if dry_run and any(s.action == "would escalate" for s in found):
            closing(console, "dry run — nothing written; pass --no-dry-run to escalate", STYLE_DIM)

    raise typer.Exit(EXIT_OK)
