"""`torve merge` — the serialized lane (RFC 0006 §1, D-6.1). Parsing and
rendering only (D-15.6); the lane lives in `torve.application.lane`. The
operator's invocation is the recorded approval (§3); `--dry-run` previews
the queue without moving anything, per the house convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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

if TYPE_CHECKING:
    from rich.console import Console

    from torve.application.lane import LaneResult
    from torve.application.ports import CiStatus
    from torve.config.runconfig import RunnerConfig

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


def _resolve_ci(config: RunnerConfig) -> CiStatus | None:
    """`promotion.require_ci` needs `scm.repo` to name the remote the
    lane consults; anything else is a configuration error.

    The refusal is a `ValueError` and not this command's exit protocol:
    the manager's landing leg resolves CI through here too, and a
    `typer.Exit` carries its code where a leg's record wants the reason —
    "lane leg failed: 3" is the sentence that taught us (T-0284). The
    command below translates it back into EXIT_CONFIG.
    """

    if not config.promotion.require_ci:
        return None

    if not config.scm.repo:
        raise ValueError(
            "configuration error: promotion.require_ci needs "
            "scm.repo to name the remote whose ci is consulted"
        )

    from torve.adapters.vcs.git import GhCi

    return GhCi(config.scm.repo, config.scm.token_env)


# ....................... #


def _action_style(action: str) -> str:
    if action in ("conflict", "gates red"):
        return STYLE_FAIL

    if "land" in action:
        return STYLE_PASS

    return STYLE_DIM


# ....................... #


def _render_text(console: Console, dry_run: bool, results: list[LaneResult]) -> None:
    header(console, "merge", "dry run" if dry_run else "serialized lane")

    if not results:
        console.print("no ready candidates")
        return

    table = make_table("", "task", "action", "detail", "sha")

    for result in results:
        table.add_row(
            mark(_MARKS.get(result.action, "skipped")),
            Text(result.task, STYLE_ID),
            Text(result.action, _action_style(result.action)),
            result.detail,
            Text(result.sha[:10], STYLE_DIM),
        )

    console.print(table)
    landed = sum(1 for r in results if r.landed)

    closing(
        console,
        f"{landed} landed of {len(results)} candidate(s)" + (" (dry run)" if dry_run else ""),
    )


# ....................... #


def _exit_code(results: list[LaneResult]) -> int:
    if any(r.action == "conflict" for r in results):
        return EXIT_ESCALATED

    if any(
        r.action
        in ("gates red", "ci not green", "approvals short", "review missing", "quiet window")
        for r in results
    ):
        return EXIT_GATES_RED

    return EXIT_OK


# ....................... #


def approve_cmd(
    task: Annotated[str, typer.Argument(help="The task whose current tip is approved.")],
    actor: Annotated[
        str, typer.Option("--actor", help="Who is approving; defaults to the git user.")
    ] = "",
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Approve a candidate's current branch tip.

    An approval is bound to the sha it was given for: a push after it
    approves nothing, which is the point — the lane counts approvals of the
    tip it is about to land and no others. Approving the same tip twice is
    one approval.
    """

    from torve.adapters.vcs.git import GitLane
    from torve.application.lane import record_approval
    from torve.base import naming

    root = root.resolve()

    if not naming.state_file(root, task).is_file():
        raise fail(f"configuration error: no run state for {task}", EXIT_CONFIG)

    lane = GitLane()
    tip = lane.tip(root, naming.branch(task))

    if not tip:
        raise fail(f"configuration error: no candidate branch for {task}", EXIT_CONFIG)

    who = actor or lane.approver(root)
    fresh = record_approval(root, task, who, tip)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "task": task,
                "actor": who,
                "sha": tip,
                "recorded": fresh,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    console.print(f"{task}: {'approved' if fresh else 'already approved'} {tip[:10]} by {who}")

    raise typer.Exit(EXIT_OK)


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

    from torve.adapters.vcs.git import GitLane
    from torve.application.lane import process_lane
    from torve.cli.options import load_config

    root = root.resolve()
    config = load_config(root, config_path)

    try:
        ci = _resolve_ci(config)

    except ValueError as exc:
        raise fail(str(exc), EXIT_CONFIG) from exc

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
        _render_text(out(fmt), dry_run, results)

    raise typer.Exit(_exit_code(results))
