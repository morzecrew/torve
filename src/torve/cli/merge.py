"""`torve merge` — the serialized lane (S-0006/the-correction-this-document-exists-for, S-0006/D-1). Parsing and
rendering only (S-0015/D-6); the lane lives in `torve.application.lane`. The
operator's invocation is the recorded approval (§3); `--dry-run` previews
the queue without moving anything, per the house convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

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

    from torve.application.lane import Forge, LaneResult, Publisher
    from torve.application.ports import CiStatus
    from torve.config.runconfig import RunnerConfig

# ----------------------- #

_MARKS = {
    "landed": "pass",
    "already landed": "pass",
    "would land": "pass",
    "would rebase": "pass",
    "pull request": "pass",
    "pull request open": "pass",
    "would open pull request": "pass",
    "abandoned": "skipped",
    "pr unresolved": "skipped",
    "pr refused": "fail",
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


def _pr_text(root: Path, task_id: str) -> tuple[str, str]:
    """The pull request's title and body, composed from the landing record
    alone (S-0080/D-4): the contract, the rows it carried, the gates of the
    attempt the stream last recorded, and the divergence entries. Nothing
    the agent wrote as prose reaches it."""

    from torve.application.forge import compose_pr
    from torve.application.projections import stream_rows
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.config import layout
    from torve.domain.attempt import GateResult
    from torve.gates.context import load_task

    task = load_task(layout.task_file(root, task_id))
    state = RunState.load(naming.state_file(root, task_id))

    rows = [r for r in stream_rows(root) if r.get("task_id") == task_id and "results" in r]
    row: dict[str, Any] = rows[-1] if rows else {}
    recorded: list[Any] = row.get("results") or []

    results = [GateResult.model_validate(r) for r in recorded]
    meta: dict[str, Any] = row.get("agent") or {}

    return compose_pr(
        task,
        state.attempts,
        str(row.get("config_hash", "")),
        meta,
        results,
        root,
        landing="pull_request",
    )


# ....................... #


def _document_pr_text(root: Path, task_id: str, branch: str) -> tuple[str, str]:
    """The document pull request's title and body (S-0083/D-8): every task
    the lane's records say the branch carries, plus the one landing now —
    it is published before its own record is written — each with its rows,
    the gates of its last recorded attempt and its landing sha."""

    from torve.application.forge import DocumentLanding, compose_document_pr
    from torve.application.lane import document_tasks
    from torve.application.projections import stream_rows
    from torve.config import layout
    from torve.domain.attempt import GateResult
    from torve.gates.context import load_task

    carried = document_tasks(root, branch)
    task_ids = carried + ([task_id] if task_id not in carried else [])
    rows = stream_rows(root)
    landings = []

    for carried_id in task_ids:
        judged = [r for r in rows if r.get("task_id") == carried_id and "results" in r]
        recorded: list[Any] = (judged[-1].get("results") or []) if judged else []
        landed = [
            r
            for r in rows
            if r.get("event") == "lane_landed"
            and r.get("task") == carried_id
            and r.get("branch") == branch
        ]
        landings.append(
            DocumentLanding(
                task=load_task(layout.task_file(root, carried_id)),
                sha=str(landed[-1].get("sha") or "") if landed else "",
                results=[GateResult.model_validate(r) for r in recorded],
            )
        )

    return compose_document_pr(branch.rsplit("/", 1)[-1], landings, root)


# ....................... #


def _publisher(root: Path, config: RunnerConfig) -> Publisher | None:
    """`pull_request` mode's landing act, or None in `local` mode
    (S-0080/D-1: the mode is this term of configuration and nothing else).

    Push under lease first, so the pull request shows the tree the criteria
    were applied to, then open or refresh the task's one pull request. The
    forge credential is resolved from the configured variable NAME at call
    time and stays in this process (S-0080/D-13)."""

    if config.promotion.landing != "pull_request":
        return None

    import os

    from torve.adapters.vcs.git import GhScm, GitVcs
    from torve.base import naming

    vcs = GitVcs()
    scm = GhScm(config.scm.repo, config.scm.token_env)

    def publish(task_id: str, branch: str) -> str:
        token = os.environ.get(config.scm.token_env) if config.scm.token_env else None

        if not vcs.republish_branch(root, branch, token):
            raise RuntimeError(f"no origin to publish {branch!r} to")

        # A document branch's pull request is the document's, composed from
        # every task it carries (S-0083/D-8); a task branch's is the task's.
        if branch == naming.document_branch(branch.rsplit("/", 1)[-1]):
            title, body = _document_pr_text(root, task_id, branch)
        else:
            title, body = _pr_text(root, task_id)

        return scm.open_pr(root, branch, title, body)

    return publish


# ....................... #


def _forge(config: RunnerConfig) -> Forge | None:
    """The later pass's read-back, or None in `local` mode — the same term of
    configuration the publisher is built from (S-0080/D-1).

    What the forge holds for a branch, asked once per pull request the lane's
    own records say it has open: merged is the landing, closed is a person
    declining the work, and still open against a moved base is rebased,
    re-measured and republished."""

    if config.promotion.landing != "pull_request":
        return None

    from torve.adapters.vcs.git import GhScm

    return GhScm(config.scm.repo, config.scm.token_env).pr_for_branch


# ....................... #


def _action_style(action: str) -> str:
    if action in ("conflict", "gates red", "pr refused"):
        return STYLE_FAIL

    if "land" in action or action.endswith("pull request"):
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
        in (
            "gates red",
            "ci not green",
            "approvals short",
            "review missing",
            "quiet window",
            "pr refused",
        )
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
            publish=_publisher(root, config),
            # The verdict a person gave arrives as an answer to a question the
            # engine asks (S-0080/D-6): once per open pull request the lane's
            # own records name, and nothing at all when it holds none.
            forge=_forge(config),
            # The landing unit is a term of configuration and nothing else
            # (S-0083/D-1); a local landing has no pull request to be one per,
            # and the lane ignores it there (S-0083/D-2).
            unit=config.promotion.unit,
        )

    except RuntimeError as exc:
        raise fail(str(exc), EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "dry_run": dry_run, "results": [vars(r) for r in results]})
    else:
        _render_text(out(fmt), dry_run, results)

    raise typer.Exit(_exit_code(results))
