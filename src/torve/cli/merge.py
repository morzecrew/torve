"""`torve merge` — the serialized lane (RFC 0006 §1, D-6.1). Parsing and
rendering only (D-15.6); the lane lives in `torve.application.lane`. The
operator's invocation is the recorded approval (§3); `--dry-run` previews
the queue without moving anything, per the house convention.
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
    closing,
    emit_json,
    fail,
    header,
    make_table,
    mark,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption
from torve.domain.states import (
    EXIT_CONFIG,
    EXIT_ESCALATED,
    EXIT_GATES_RED,
    EXIT_INFRASTRUCTURE,
    EXIT_OK,
)

# ----------------------- #

_MARKS = {
    "landed": "pass",
    "already landed": "pass",
    "would land": "pass",
    "would rebase": "pass",
    "conflict": "fail",
    "gates red": "fail",
    "ci not green": "fail",
    "approvals short": "fail",
    "review missing": "fail",
    "quiet window": "fail",
    "no branch": "skipped",
}


# ....................... #


def merge_cmd(
    task: Annotated[
        str | None, typer.Argument(help="One candidate to land; omit to process the whole queue.")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what the lane would do without moving anything."),
    ] = False,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Land ready candidates one at a time: an unmoved base fast-forwards as
    measured; a moved base rebases and re-runs the gates first; a conflict
    is reported and left for a human — the lane never resolves one."""

    from torve.adapters.vcs.git import GhCi, GitLane
    from torve.application.lane import process_lane
    from torve.cli.options import load_config

    root = root.resolve()
    config = load_config(root, config_path)
    ci = None

    if config.promotion.require_ci:
        if not config.scm.repo:
            raise fail(
                "configuration error: promotion.require_ci needs "
                "scm.repo to name the remote whose ci is consulted",
                EXIT_CONFIG,
            )

        ci = GhCi(config.scm.repo, config.scm.token_env)

    try:
        results = process_lane(
            root,
            GitLane(),
            dry_run=dry_run,
            only=task,
            ci=ci,
            approvals_required=config.promotion.approvals,
            require_review=config.promotion.require_review,
            quiet_window_s=config.promotion.quiet_window,
        )
    except RuntimeError as exc:
        raise fail(str(exc), EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "dry_run": dry_run, "results": [vars(r) for r in results]})
    else:
        console = out(fmt)
        header(console, "merge", "dry run" if dry_run else "serialized lane")

        if not results:
            console.print("no ready candidates")
        else:
            table = make_table("", "task", "action", "detail", "sha")

            for result in results:
                table.add_row(
                    mark(_MARKS.get(result.action, "skipped")),
                    Text(result.task, STYLE_ID),
                    Text(
                        result.action,
                        STYLE_FAIL
                        if result.action in ("conflict", "gates red")
                        else STYLE_PASS
                        if "land" in result.action
                        else STYLE_DIM,
                    ),
                    result.detail,
                    Text(result.sha[:10], STYLE_DIM),
                )

            console.print(table)
            landed = sum(1 for r in results if r.landed)
            closing(
                console,
                f"{landed} landed of {len(results)} candidate(s)"
                + (" (dry run)" if dry_run else ""),
            )

    if any(r.action == "conflict" for r in results):
        raise typer.Exit(EXIT_ESCALATED)

    if any(
        r.action
        in ("gates red", "ci not green", "approvals short", "review missing", "quiet window")
        for r in results
    ):
        raise typer.Exit(EXIT_GATES_RED)

    raise typer.Exit(EXIT_OK)
