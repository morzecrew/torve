"""`torve status` and `torve reap` — parsing and rendering only (S-0015/D-6); the
sweep logic lives in `torve.application.reaper` (S-0003/reaper: cleanup by
convention).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.text import Text

from torve.base.clock import parse
from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    Format,
    emit_json,
    fail,
    header,
    id_list,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    DsnOption,
    FormatOption,
    PartitionOption,
    RootOption,
    RuntimeName,
    dsn_for,
    load_config,
    runtime_for,
    task_events,
)
from torve.domain.states import EXIT_INFRASTRUCTURE

if TYPE_CHECKING:
    from torve.adapters.vcs.git import GitVcs
    from torve.application.manager import Board
    from torve.application.reaper import ReapReport
    from torve.config.runconfig import RunnerConfig

# ----------------------- #


def _board(dsn: str, partition: str) -> Board | None:
    """The partition's board, when one was named. None means nobody asked
    for the record, which is different from a record holding no run
    (S-0050 S-0050/D-2)."""

    events = task_events(dsn, partition)

    if events is None:
        return None

    from torve.application.manager import project

    return project(events)


# ....................... #


def _age(heartbeat: Any) -> str:
    """How long ago this run last said anything. Unparseable reads as
    unknown rather than as zero: a run whose stamp nobody can read is not a
    run that just checked in."""

    try:
        stamp = parse(str(heartbeat))

    except ValueError:
        return "unknown"

    return f"{(datetime.now(UTC) - stamp).total_seconds():.0f}s ago"


# ....................... #


def status(
    dsn: DsnOption = "",
    partition: PartitionOption = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run states — from the partition's log when one is named, and from
    this host's .wt/ state files when none is.

    A log that holds no run falls back to the files, never the other way
    round: a v1 run left a state file and no log, so an empty record means
    ask the files rather than report that nothing ever ran.
    """

    from torve.application.projections import status_report

    # The projection, verbatim: the serve endpoint renders the same
    # envelope, so the browser and the terminal cannot disagree (S-0032/D-1).
    envelope = status_report(root, board=_board(dsn_for(root, dsn), partition))

    if fmt is Format.JSON:
        emit_json(envelope)
        return

    console = out(fmt)
    runs: list[dict[str, Any]] = envelope["runs"]

    if not runs:
        console.print("no runs")
        return

    header(console, "status", f"{len(runs)} run(s)")
    table = make_table("task", "state", "attempts", "heartbeat", "escalation")

    for run in runs:
        state = str(run.get("state"))
        escalation = run.get("escalation")

        table.add_row(
            Text(str(run.get("task_id")), STYLE_ID),
            Text(
                state,
                STYLE_PASS if state == "ready" else STYLE_FAIL if state == "escalated" else "",
            ),
            str(run.get("attempts")),
            Text(_age(run.get("heartbeat")), STYLE_DIM),
            (
                f"{escalation.get('reason')}: {escalation.get('detail')}"
                if isinstance(escalation, dict)
                else ""
            ),
        )

    console.print(table)


# ....................... #


def _swept(
    root: Path,
    config: RunnerConfig,
    runtime_name: RuntimeName | None,
    force: bool,
    dry_run: bool,
    escalated: bool,
    vcs: GitVcs,
) -> ReapReport:
    from torve.adapters.store.durable import open_store
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.reaper import reap

    return reap(
        root,
        config,
        runtime_for(config, runtime_name),
        GitWorkspace(root),
        force=force,
        dry_run=dry_run,
        store=open_store,
        # The landed oracle (S-0019/D-10): a READY implement state whose landing
        # trailer is in history is collectable — without it this verb kept
        # every landed candidate forever.
        landed=lambda t: bool(vcs.landed_shas(root, t)),
        escalated=escalated,
    )


# ....................... #


def reap_cmd(
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Treat every non-terminal run as orphaned regardless of heartbeat age."
        ),
    ] = False,
    escalated: Annotated[
        bool,
        typer.Option(
            "--escalated",
            help="Also sweep escalated run states — the explicit triage-discard "
            "for an escalation already dealt with outside the state machine.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Report what would be swept without touching anything; "
            "durable lease expiry cannot be predicted and is not shown.",
        ),
    ] = False,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Sweep orphaned sandboxes, worktrees and finished run state, by
    convention."""

    from torve.adapters.vcs.git import GitVcs

    root = root.resolve()
    config = load_config(root, config_path)
    vcs = GitVcs()

    try:
        report = _swept(root, config, runtime_name, force, dry_run, escalated, vcs)

    except RuntimeError as exc:
        # A store the sweep cannot reach is infrastructure, not a crash: the
        # durable half is what decides expiry, so a reap without it would
        # report a sweep it never performed (S-0048/A-1).
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "dry_run": dry_run,
                "sandboxes_destroyed": report.sandboxes_destroyed,
                "runs_expired": report.runs_expired,
                "worktrees_removed": report.worktrees_removed,
                "states_removed": report.states_removed,
            }
        )

        return

    console = out(fmt)
    header(console, "reap", "dry run" if dry_run else "sweep")
    tense = "would be " if dry_run else ""

    for label, names in (
        ("sandboxes destroyed", report.sandboxes_destroyed),
        ("runs expired", report.runs_expired),
        ("worktrees removed", report.worktrees_removed),
        ("run states removed", report.states_removed),
    ):
        detail = f" ({id_list(names)})" if names else ""
        console.print(f"{tense}{label}: {len(names)}{detail}")
