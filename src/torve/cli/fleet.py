"""`torve fleet` — the operator-side manifest and one pass over every root
(S-0024). Parsing, manifest resolution and rendering only (S-0015/D-6);
survey, the shared pause decision, deterministic order and
failure-recorded continuation live in `torve.application.fleet`, and each
root's legs are wired by the composition root, `torve.cli.assembly`.

A fleet is many solo ticks under one shared decision, not a new execution
regime: each root gets the same legs a solo tick builds — with the two
corners the fleet loop has never run (the post-push forge bookkeeping and
the dispatch envelope line) refused by name inside the composition root,
so a wiring change is now one edit, not three copies' drift.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    Format,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import FormatOption, runtime_for
from torve.config.fleet import default_manifest_path, load_fleet_manifest
from torve.domain.states import EXIT_CONFIG

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from torve.application.fleet import FleetServeReport
    from torve.application.intake import IntakeOutcome
    from torve.application.manager import Board
    from torve.application.ports import Agent
    from torve.application.residency import Issues
    from torve.config.fleet import FleetManifest, FleetRepository
    from torve.config.runconfig import RunnerConfig
    from torve.domain.events import EventRecord

# ----------------------- #

fleet_app = typer.Typer(
    no_args_is_help=True,
    help="Run the manager over every repository in the operator's manifest.",
)

ManifestOption = Annotated[
    Path | None,
    typer.Option(
        "--manifest",
        dir_okay=False,
        help="Fleet manifest; defaults to ~/.config/torve/fleet.yaml.",
    ),
]


# ....................... #


def _load_manifest(manifest_path: Path | None) -> FleetManifest:
    import yaml

    path = manifest_path or default_manifest_path()

    if not path.is_file():
        raise fail(
            f"no fleet manifest at {path} — set --manifest or write one",
            EXIT_CONFIG,
        )

    try:
        return load_fleet_manifest(path)

    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"fleet manifest error: {exc}", EXIT_CONFIG) from exc


# ....................... #


@fleet_app.command("status")
def fleet_status_cmd(
    manifest_path: ManifestOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every root's escalation queue in one table, oldest first — the
    primary alert, in its fleet form. Read-only:
    nothing here writes, and there is no fleet store to read from instead
    of the roots themselves."""

    from torve.application.fleet import fleet_escalations

    manifest = _load_manifest(manifest_path)
    rows = fleet_escalations(manifest)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "escalations": [asdict(r) for r in rows]})
        return

    console = out(fmt)

    if not rows:
        console.print("no escalations across the fleet")
        return

    header(console, "fleet status", f"{len(rows)} escalation(s)")
    table = make_table("root", "task", "reason", "detail", "age")

    for row in rows:
        table.add_row(
            row.root, row.task_id, row.reason, row.detail, Text(f"{row.age_s:.0f}s ago", STYLE_DIM)
        )

    console.print(table)


# ....................... #


def _issues_leg(
    root: Path,
    config: RunnerConfig,
    records: Callable[[], Awaitable[Sequence[EventRecord]]] | None = None,
) -> Issues:
    """One root's intake leg (S-0119/D-8), as the manager's pass builds it.

    Always built, because the leg's own switch is the label: with
    `intake.issue_label` empty it returns "issue intake off" and reads
    nothing, so no drafting agent is built and a planner tier that cannot be
    built never stops a root's pass (S-0119/D-1). With the label set the
    agent is built once for the leg — and the fleet keeps one leg for the
    root, so once per serve — and the leg's `draft` closes over it, so the
    issue drafts the way `torve intake` drafts (S-0119/D-7). Built inside the
    leg for S-0044/A-11's reason: `_leg` protects the call, so an error here
    is the leg's own detail line and the rest of the pass still runs.
    """

    from torve.cli.assembly import build_tier_agent

    agent: Agent | None = None
    failure = ""

    if config.intake.issue_label:
        try:
            agent = build_tier_agent(config, root, "planner")

        except Exception as exc:
            failure = str(exc)

    async def issues() -> tuple[str, bool]:
        # Built inside the leg for S-0044/A-11's reason, as the manager's is:
        # a forge read or a drafting run that raises is recorded and the rest
        # of that root's pass still runs.
        from torve.adapters.broker import build_broker
        from torve.adapters.vcs.git import GhScm
        from torve.application.intake import draft_request
        from torve.application.issues import issue_leg

        if not config.intake.issue_label:
            return "issue intake off", False

        if agent is None:
            return f"issue intake: no planner agent — {failure}", False

        planner: Agent = agent
        taken = [one.subject_id for one in await records()] if records is not None else []
        runtime = runtime_for(config, None)

        def draft(request: str, source: str) -> IntakeOutcome:
            return draft_request(
                root,
                request,
                config,
                planner,
                source=source,
                runtime=runtime,
                taken=taken,
                broker=build_broker(config.broker),
            )

        return issue_leg(root, config, GhScm(config.scm.repo, config.scm.token_env), draft)

    return issues


# ....................... #


async def _serve_fleet(
    manifest: FleetManifest,
    dsn: str | None,
    *,
    worker: str,
    rounds: int | None,
    interval: float,
) -> FleetServeReport:
    """One log for the fleet, one worker per repository.

    One log because the partition column is what separates boards (S-0044/D-1),
    and one worker per repository because an executor is bound to a root and
    a partition — a worker over several would have to rebind mid-pass.
    """

    import sys

    from forze.application.execution import DepsRegistry, ExecutionRuntime
    from forze.base.logging import configure_logging

    from torve.adapters.eventstore.document import closing_module, mock_module, postgres_module
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.fleet import serve_fleet
    from torve.application.manager import project
    from torve.application.projections import landed
    from torve.application.residency import once, ran_here
    from torve.application.worker import Worker
    from torve.cli import assembly
    from torve.config.runconfig import load_runner_config
    from torve.domain.events import SubjectType

    # The runtime narrates itself on stdout, and stdout is where this verb's
    # JSON goes. Warnings and worse, on stderr (S-0015/D-6).
    configure_logging(level="warning", stream=sys.stderr)

    module = await postgres_module(dsn) if dsn else mock_module()
    runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

    # `closing_module` is entered first, so its client close runs after the
    # scope it guards has shut down (S-0113/D-4).
    async with closing_module(module), runtime.scope():
        log = event_log(runtime.get_context())

        # S-0119/D-8: one intake leg per repository for the whole serve, so the
        # planner tier's agent is built once rather than once per pass, as the
        # manager builds its one. The key is the manifest entry's root and
        # partition, because the leg's record read is partition-scoped; a config
        # that moves under the process moves every other leg, not this one.
        issues_legs: dict[tuple[str, str], Issues] = {}

        async def run_pass(repo: FleetRepository, paused: bool) -> str | None:
            # Built per pass rather than cached: the root's own configuration
            # can change under a resident process, and the trust check the
            # loop just ran read it from disk for the same reason.
            root = repo.path
            config = load_runner_config(root)
            seat = f"{worker}:{repo.partition}"
            landings = landed(root)
            ran = ran_here(root)

            def standing() -> tuple[str, bool]:
                from torve.application.standing import standing_leg

                return standing_leg(root, config, runtime_for(config, None), landings.__contains__)

            async def records() -> list[EventRecord]:
                return list(await log.of_subject_type(SubjectType.TASK, partition=repo.partition))

            key = (repo.root, repo.partition)
            leg = issues_legs.get(key)

            if leg is None:
                leg = _issues_leg(root, config, records=records)
                issues_legs[key] = leg

            return await once(
                log,
                Worker(
                    log=log,
                    name=seat,
                    execute=runner_execute(
                        root,
                        config,
                        assembly.build_dispatch_prepare(root, config),
                        log=log,
                        partition=repo.partition,
                        seat=seat,
                    ),
                ),
                root,
                repo.partition,
                landed=landings.get,
                ran=ran.__contains__,
                paused=paused,
                standing=standing,
                issues=leg,
            )

        async def boards() -> dict[str, Board]:
            """Every partition's board, folded once a round for the pause
            decision (S-0048/D-5). One read per repository per round, beside a
            round that may run an agent."""

            return {
                repo.root: project(
                    await log.of_subject_type(SubjectType.TASK, partition=repo.partition)
                )
                for repo in manifest.ticking_order()
                if repo.partition
            }

        return await serve_fleet(
            manifest, run_pass, idle_seconds=interval, rounds=rounds, boards=boards
        )
