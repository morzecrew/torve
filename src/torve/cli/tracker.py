"""`torve tracker` — sync projects run state onto the configured board and
relays it; poll reads the board's commands back as intents. Parsing and
rendering only; the projection lives in `torve.application.tracker`, and the
run store stays the authority whatever the board shows.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from torve.adapters.tracker.github import GithubIssues
    from torve.config.runconfig import TrackerConfig

from torve.cli.console import (
    STYLE_DIM,
    STYLE_ID,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    mark,
    out,
    styled,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

tracker_app = typer.Typer(no_args_is_help=True)


def _adapter(root: Path, config_path: Path | None) -> tuple[GithubIssues, TrackerConfig]:
    from torve.adapters.tracker.github import GithubIssues

    config = load_config(root, config_path)
    if config.tracker.kind != "github-issues":
        raise fail("configuration error: no tracker configured "
                   "(tracker.kind: github-issues)", EXIT_CONFIG)
    if not config.tracker.repo:
        raise fail("configuration error: tracker.repo names the board's "
                   "repository", EXIT_CONFIG)
    return (GithubIssues(config.tracker.repo, config.tracker.token_env),
            config.tracker)


@tracker_app.command()
def sync(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Project run state onto the board and relay pending effects; a rerun
    against unchanged state delivers nothing."""
    from torve.adapters.vcs.git import GitVcs
    from torve.application.tracker import project, project_landings, relay_to_tracker

    root = root.resolve()
    tracker, tracker_config = _adapter(root, config_path)
    vcs = GitVcs()
    staged = project(root, tracker_config.notify)
    staged += project_landings(root, lambda t: bool(vcs.landed_shas(root, t)))
    report = relay_to_tracker(root, tracker)
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "staged": staged,
                   "delivered": report.delivered, "skipped": report.skipped,
                   "failed": report.failed})
    else:
        console = out(fmt)
        header(console, "tracker", "sync")
        console.print(f"staged {staged} new effect(s)")
        closing(console,
                f"delivered {len(report.delivered)}, "
                f"skipped {len(report.skipped)} already delivered, "
                f"failed {len(report.failed)}")
    raise typer.Exit(EXIT_OK)


@tracker_app.command()
def poll(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Read the board's commands and apply each as an intent against the
    store; refusals are answered on their thread."""
    import os

    from torve.adapters.vcs.git import GitLane, GitVcs
    from torve.application.tracker import poll_and_apply
    from torve.base import naming

    root = root.resolve()
    config = load_config(root, config_path)
    tracker, tracker_config = _adapter(root, config_path)

    def requeue(task_id: str) -> str:
        token = (os.environ.get(config.scm.token_env)
                 if config.scm.token_env else None)
        deleted = GitVcs().delete_remote_branch(
            root, naming.branch(task_id), token)
        return "remote branch deleted" if deleted else "no remote branch"

    def approve_tip(task_id: str) -> str | None:
        return GitLane().tip(root, naming.branch(task_id))

    report = poll_and_apply(root, tracker,
                            tuple(tracker_config.commanders), requeue,
                            approve_tip)
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "commands": [vars(o) for o in report.outcomes]})
    else:
        console = out(fmt)
        header(console, "tracker", "poll")
        if not report.outcomes:
            console.print("no commands on the board")
        else:
            table = make_table("", "task", "command", "actor", "outcome")
            for o in report.outcomes:
                table.add_row(mark("pass" if o.applied else "fail"),
                              styled(o.task_id, STYLE_ID), o.verb,
                              styled(o.actor, STYLE_DIM), o.detail)
            console.print(table)
            closing(console, f"{sum(o.applied for o in report.outcomes)} applied "
                             f"of {len(report.outcomes)} command(s)")
    raise typer.Exit(EXIT_OK)
