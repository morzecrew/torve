"""`torve migrate` — parsing and rendering only (D-15.6); the histories live
in `torve.application.migrate` (RFC 0012: owner-grouped, forward-only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.cli.console import STYLE_PASS, closing, fail, header, out
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE

# ----------------------- #


def migrate_cmd(
    target: Annotated[str | None, typer.Argument(help="torve | substrate | telemetry")] = None,
    apply_all: Annotated[
        bool, typer.Option("--all", help="Apply every target's pending steps.")
    ] = False,
    show_status: Annotated[
        bool,
        typer.Option(
            "--status", help="Available and applied steps per target, plus the forze pin."
        ),
    ] = False,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
) -> None:
    """Apply forward-only SQL migrations, grouped by schema owner.

    Three histories — torve, substrate (pinned to a forze version), telemetry
    (stage 3+) — each with its own version counter. No downgrade exists.
    `--status` is this command's preview; there is no partial dry run of a
    forward-only history."""

    from torve.adapters.store.durable import resolve_dsn
    from torve.application.migrate import MigrateError, apply
    from torve.application.migrate import status as migrate_status

    all_targets = ["torve", "substrate", "telemetry"]
    config = load_config(root.resolve(), config_path)
    console = out()

    try:
        if show_status:
            dsn = None

            if config.store.adapter == "postgres":
                import contextlib

                with contextlib.suppress(RuntimeError):
                    dsn = resolve_dsn(config.store)

            header(console, "migrate", "status")

            for line in migrate_status(dsn):
                console.print(line)

            return

        if apply_all:
            targets = all_targets
        elif target is None:
            raise fail("configuration error: give a target, --all, or --status", EXIT_CONFIG)
        elif target not in all_targets:
            raise fail(f"configuration error: unknown target {target!r}", EXIT_CONFIG)
        else:
            targets = [target]

        dsn = resolve_dsn(config.store)

        for name in targets:
            applied = apply(name, dsn)
            closing(console, f"{name}: {applied} step(s) applied", STYLE_PASS)

    except MigrateError as exc:
        raise fail(str(exc), exc.exit_code) from exc

    except RuntimeError as exc:
        raise fail(f"infrastructure failure: {exc}", EXIT_INFRASTRUCTURE) from exc
