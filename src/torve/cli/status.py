"""`torve status` and `torve reap` — parsing and rendering only (D-15.6); the
sweep logic lives in `torve.application.reaper`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.cli.console import Format, emit_json, out
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
    for state in states:
        line = (f"{state.task_id:<8} {state.state:<10} attempts={state.attempts} "
                f"heartbeat={state.heartbeat_age_s():.0f}s ago")
        if state.escalation is not None:
            line += f"  [{state.escalation.reason}: {state.escalation.detail}]"
        console.print(line)


def reap_cmd(
    force: Annotated[bool, typer.Option(
        "--force",
        help="Treat every non-terminal run as orphaned regardless of heartbeat age.")]
    = False,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run", help="Report what would be swept without touching anything; "
                          "durable lease expiry cannot be predicted and is not shown.")]
    = False,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Sweep orphaned sandboxes and worktrees, by convention (RFC 0003 §4.2)."""
    from torve.adapters.store.durable import open_store
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.reaper import reap

    root = root.resolve()
    config = load_config(root, config_path)
    report = reap(root, config, runtime_for(config, runtime_name), GitWorkspace(root),
                  force=force, dry_run=dry_run, store=open_store)
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "dry_run": dry_run,
                   "sandboxes_destroyed": report.sandboxes_destroyed,
                   "runs_expired": report.runs_expired,
                   "worktrees_removed": report.worktrees_removed})
        return
    console = out(fmt)
    tense = "would be " if dry_run else ""
    for label, names in (("sandboxes destroyed", report.sandboxes_destroyed),
                         ("runs expired", report.runs_expired),
                         ("worktrees removed", report.worktrees_removed)):
        detail = f" ({', '.join(names)})" if names else ""
        console.print(f"{tense}{label}: {len(names)}{detail}")
