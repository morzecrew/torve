"""`torve eval` — the RFC 0009 §5 eval loop: parsing and rendering only
(D-15.6); the arms live in `torve.application.evals` over the shadow
machinery (RFC 0004 §5 — nothing merges, D-4.4). Exit codes follow the
shadow doctrine: a completed eval exits 0 whatever the arms measured, 3
is a configuration problem, 4 an infrastructure failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_PASS,
    Format,
    closing,
    emit_json,
    fail,
    header,
    live_status,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    load_config,
    runtime_for,
)
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates.context import load_task

# ----------------------- #


def eval_cmd(
    skill: Annotated[str, typer.Argument(help="The skill under measurement.")],
    task_ids: Annotated[
        list[str],
        typer.Option("--task", help="A completed task to replay in both arms; repeatable."),
    ],
    depth: Annotated[
        int, typer.Option(min=1, help="History depth of the truncated shadow clones.")
    ] = 50,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Measure a skill against its without-skill baseline: every named task
    replays twice in shadow — the configured role sets, then the sets with
    the skill removed — and one eval record lands in the evals ledger.
    Nothing a replay produces is ever merged."""
    from functools import partial

    from torve.adapters.agent.harness import HarnessAgent
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GitVcs, NullScm, repository_name
    from torve.adapters.workspace.git import (
        GitWorkspace,
        ShadowWorkspace,
        diff_range,
        diff_worktree,
        parent_of,
        shipped_commit,
    )
    from torve.application.evals import run_skill_eval, without_skill
    from torve.application.runner import RunDeps
    from torve.application.shadow import ShadowSource
    from torve.config.runconfig import ProviderDenied, route_provider, tier_for
    from torve.domain.task import Task

    root = root.resolve()
    config = load_config(root, config_path)
    tasks: list[Task] = []
    for task_id in task_ids:
        task_file = layout.task_file(root, task_id)
        if not task_file.is_file():
            raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)
        tasks.append(load_task(task_file))
    try:
        without_skill(config, skill)  # refuse before any spend
        tiers = {task.tier for task in tasks}
        for name in tiers:
            tier = tier_for(config, name)
            route_provider(config.providers, repository_name(root), tier.provider)
    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, None),
        agent=HarnessAgent(tier_for(config, tasks[0].tier)),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
    )
    shadow_ws = ShadowWorkspace(root, depth=depth)
    source = ShadowSource(
        create_workspace=shadow_ws.create,
        shipped_commit=partial(shipped_commit, root),
        parent_of=partial(parent_of, root),
        diff_range=partial(diff_range, root),
        diff_worktree=diff_worktree,
    )

    try:
        with live_status(f"eval of {skill} over {len(tasks)} task(s), two arms", fmt):
            record = run_skill_eval(root, skill, tasks, config, deps, source)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc
    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json(record)
        raise typer.Exit(EXIT_OK)
    console = out(fmt)
    header(console, "eval", skill)
    table = make_table("arm", "green", "attempts", "cost usd")
    for arm in ("with", "without"):
        row = record["summary"][arm]
        table.add_row(
            arm,
            f"{row['green']}/{len(record['tasks'])}",
            str(row["attempts"]),
            "-" if row["cost_usd"] is None else f"{row['cost_usd']:.4f}",
        )
    console.print(table)
    if record["baseline_matched"]:
        closing(
            console,
            "baseline matched — this skill did not earn its tokens here; deletion is your call",
            STYLE_DIM,
        )
    else:
        closing(console, "the skill beat its baseline on this evidence", STYLE_PASS)
    console.print(Text("direction, never magnitude — a replay is a quasi-experiment", STYLE_DIM))
    raise typer.Exit(EXIT_OK)
