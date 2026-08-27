"""`torve shadow` — replay a completed task from its parent commit (RFC 0004
§5). Parsing and rendering only (D-15.6); the loop lives in
`torve.application.shadow`. The workspace is a truncated-history clone
(D-4.7) and nothing is ever merged from a shadow run (D-4.4).

The exit code reports the measurement, not the replay's fortunes: a red
replay is a successful measurement of a red outcome, so a completed replay
exits 0 regardless of how the shadow attempt fared. 3 is a configuration
problem (no shipped commit findable, bad tier), 4 an infrastructure failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_PASS,
    Format,
    closing,
    emit_json,
    fail,
    header,
    live_status,
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
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE, EXIT_OK
from torve.gates.context import load_task

# ----------------------- #


def shadow_cmd(
    task_id: Annotated[str, typer.Argument()],
    commit: Annotated[
        str | None,
        typer.Option(
            "--commit",
            help="The commit that shipped the task; found by its Torve-Task trailer when omitted.",
        ),
    ] = None,
    agent_name: Annotated[
        str | None,
        typer.Option("--agent", help="Override the tier's adapter with 'fake' (scenario replay)."),
    ] = None,
    scenario: Annotated[
        Path | None, typer.Option(exists=True, help="FakeAgent scenario YAML.")
    ] = None,
    runtime_name: Annotated[
        RuntimeName | None,
        typer.Option("--runtime", help="Override the configured runtime adapter."),
    ] = None,
    depth: Annotated[
        int, typer.Option(min=1, help="History depth of the truncated shadow clone.")
    ] = 50,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Replay a completed task from its parent commit in a truncated-history
    workspace, never merging, and record the comparison."""

    from functools import partial

    from torve.adapters.agent.fake import FakeAgent, load_scenario
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
    from torve.application.ports import Agent
    from torve.application.runner import RunDeps
    from torve.application.shadow import ShadowSource, run_shadow
    from torve.config.runconfig import ProviderDenied, route_provider, tier_for

    if agent_name not in (None, "fake"):
        raise fail(f"configuration error: unknown agent {agent_name!r}", EXIT_CONFIG)

    root = root.resolve()
    task_file = layout.task_file(root, task_id)

    if not task_file.is_file():
        raise fail(f"configuration error: no task contract at {task_file}", EXIT_CONFIG)

    task = load_task(task_file)
    config = load_config(root, config_path)

    try:
        tier = tier_for(config, task.tier)

        # Same dispatch-time routing as a live run (D-4.8): a shadow replay
        # sends the repository to the provider exactly like a live one.
        if agent_name is None:
            route_provider(config.providers, repository_name(root), tier.provider)

    except (ProviderDenied, ValueError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    agent: Agent

    if agent_name == "fake" or tier.adapter == "fake":
        agent = FakeAgent(load_scenario(scenario) if scenario else None)
    else:
        from torve.adapters.agent.harness import HarnessAgent

        if scenario is not None:
            raise fail("configuration error: --scenario is FakeAgent-only", EXIT_CONFIG)

        agent = HarnessAgent(tier)

    deps = RunDeps(
        workspace=GitWorkspace(root),
        runtime=runtime_for(config, runtime_name),
        agent=agent,
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
        with live_status(f"shadow replay of {task_id}", fmt):
            record = run_shadow(root, task, config, deps, source, commit=commit)

    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc

    if fmt is Format.JSON:
        emit_json(record)
    else:
        console = out(fmt)
        header(console, "shadow", f"{task_id} · replay of {record['commit'][:10]}")
        ready = record["state"] == "ready"

        console.print(
            Text(
                f"  {record['state']} after {record['attempts']} attempt(s)",
                STYLE_PASS if ready else STYLE_FAIL,
            )
        )

        if record["escalation"]:
            console.print(Text(f"  escalated: {record['escalation']}", STYLE_FAIL))

        cost = record["cost_usd_total"]

        console.print(
            f"  cost: {'$' + format(cost, '.2f') if cost is not None else 'unrecorded'}"
            f" · adapter {record['adapter']}"
        )

        for label in ("shadow_diff", "shipped_diff"):
            stat = record[label]

            console.print(
                f"  {label.replace('_', ' ')}: {stat['files_changed']} file(s), "
                f"+{stat['insertions']} -{stat['deletions']}"
            )

        console.print(f"  overlap: {', '.join(record['overlap_files']) or 'none'}")
        closing(console, "nothing merged", STYLE_DIM)

    raise typer.Exit(EXIT_OK)
