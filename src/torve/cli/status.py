"""`torve status` and `torve reap` — parsing and rendering only (D-15.6); the
sweep logic lives in `torve.application.reaper` (RFC 0003 §4.2: cleanup by
convention).
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
    Format,
    emit_json,
    header,
    id_list,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    RuntimeName,
    load_config,
    runtime_for,
)

# ----------------------- #


def status(
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Run states from the .wt/ state files."""
    from torve.application.runstate import RunState
    from torve.base import naming

    states = RunState.load_all(root.resolve() / naming.WORKTREE_DIR)
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "runs": [s.to_record() for s in states]})
        return
    console = out(fmt)
    if not states:
        console.print("no runs")
        return
    header(console, "status", f"{len(states)} run(s)")
    table = make_table("task", "state", "attempts", "heartbeat", "escalation")
    for state in states:
        terminal_ready = str(state.state) == "ready"
        escalated = str(state.state) == "escalated"
        table.add_row(
            Text(state.task_id, STYLE_ID),
            Text(
                str(state.state), STYLE_PASS if terminal_ready else STYLE_FAIL if escalated else ""
            ),
            str(state.attempts),
            Text(f"{state.heartbeat_age_s():.0f}s ago", STYLE_DIM),
            (
                f"{state.escalation.reason}: {state.escalation.detail}"
                if state.escalation is not None
                else ""
            ),
        )
    console.print(table)


# ....................... #


def reap_cmd(
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Treat every non-terminal run as orphaned regardless of heartbeat age."
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
    from torve.adapters.store.durable import open_store
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.reaper import reap

    root = root.resolve()
    config = load_config(root, config_path)
    report = reap(
        root,
        config,
        runtime_for(config, runtime_name),
        GitWorkspace(root),
        force=force,
        dry_run=dry_run,
        store=open_store,
    )
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
