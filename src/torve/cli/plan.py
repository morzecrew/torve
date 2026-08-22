"""`torve plan` — parsing and rendering only (D-15.6); the minter lives in
`torve.application.planner`. Dry-run is the default (RFC 0007 §3, and the
D-11 convention: extend `--dry-run`, never invent a sibling flag).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.cli.console import Format, emit_json, fail, out
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #


def plan_cmd(
    identifier: Annotated[str | None, typer.Argument(
        help="Exactly one document — no sets, no subgraphs, no --all (D-7.8).")] = None,
    reconcile: Annotated[bool, typer.Option(
        "--reconcile",
        help="Mark non-terminal tasks minted from superseded documents as "
             "stale_inheritance (§3.3, charter A-22); takes no document.")] = False,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run/--no-dry-run",
        help="Preview without writing (the default); --no-dry-run mints.")] = True,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Mint implement-task contracts from one accepted, committed
    specification (RFC 0007 §3). Deterministic; no model call at any point."""
    from torve.application.planner import PlanError, plan_document, write_contracts

    root = root.resolve()
    config = load_config(root, config_path)
    rfc_dir = root / config.rfcs.path

    if reconcile:
        if identifier is not None:
            raise fail("configuration error: --reconcile sweeps the whole corpus "
                       "and takes no document", EXIT_CONFIG)
        _reconcile(root, rfc_dir, dry_run, fmt)
        return
    if identifier is None:
        raise fail("configuration error: torve plan takes exactly one document (D-7.8)",
                   EXIT_CONFIG)

    try:
        report = plan_document(root, rfc_dir, identifier)
        written = [] if dry_run else write_contracts(root, report)
    except PlanError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    if fmt is Format.JSON:
        emit_json({
            "schema_version": SCHEMA_VERSION,
            "document": report.document,
            "dry_run": dry_run,
            "tasks": [
                {**planned.task.model_dump(), "title": planned.title,
                 "size": planned.size.size}
                for planned in report.tasks
            ],
            "written": [str(path) for path in written],
        })
    else:
        console = out(fmt)
        console.print(f"torve plan · {report.document} · {len(report.tasks)} task(s)")
        for planned in report.tasks:
            task = planned.task
            deps = f" after {', '.join(task.depends_on)}" if task.depends_on else ""
            console.print(
                f"  {task.id}  phase {task.phase}  {planned.title}  "
                f"[size {planned.size.size}, {len(task.decisions)} decision(s), "
                f"{len(task.scope.allow)} scope glob(s)]{deps}")
        if dry_run:
            console.print("dry run — nothing written; pass --no-dry-run to mint")
        else:
            for path in written:
                console.print(f"  minted {path}")
    raise typer.Exit(EXIT_OK)


def _reconcile(root: Path, rfc_dir: Path, dry_run: bool, fmt: Format) -> None:
    from torve.application.planner import reconcile

    found = reconcile(root, rfc_dir, dry_run=dry_run)
    if fmt is Format.JSON:
        emit_json({
            "schema_version": SCHEMA_VERSION,
            "dry_run": dry_run,
            "stale": [
                {"task": s.task_id, "rfc": s.document, "superseded_by": s.superseded_by,
                 "state": s.state, "action": s.action}
                for s in found
            ],
        })
    else:
        console = out(fmt)
        if not found:
            console.print("no tasks minted from superseded documents — nothing to reconcile")
        for stale in found:
            by = stale.superseded_by or "an unset successor"
            console.print(f"  {stale.task_id}  [{stale.state}]  from {stale.document} "
                          f"(superseded by {by}) — {stale.action}")
        if dry_run and any(s.action == "would escalate" for s in found):
            console.print("dry run — nothing written; pass --no-dry-run to escalate")
    raise typer.Exit(EXIT_OK)
