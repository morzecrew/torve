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
    from torve.application.fleet import FleetServeReport
    from torve.application.manager import Board
    from torve.config.fleet import FleetManifest, FleetRepository

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

    from torve.adapters.eventstore.document import mock_module, postgres_module
    from torve.application.eventlog import event_log
    from torve.application.executors import runner_execute
    from torve.application.fleet import serve_fleet
    from torve.application.manager import project
    from torve.application.projections import shipped_landings
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

    async with runtime.scope():
        log = event_log(runtime.get_context())

        async def run_pass(repo: FleetRepository, paused: bool) -> str | None:
            # Built per pass rather than cached: the root's own configuration
            # can change under a resident process, and the trust check the
            # loop just ran read it from disk for the same reason.
            root = repo.path
            config = load_runner_config(root)
            seat = f"{worker}:{repo.partition}"
            landings = shipped_landings(root)
            ran = ran_here(root)

            def standing() -> tuple[str, bool]:
                from torve.application.standing import standing_leg

                return standing_leg(root, config, runtime_for(config, None), landings.__contains__)

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
