"""Shared option types and the configuration loaders behind them.

Files resolve under `.torve/`, one location each (RFC 0013 A-48); `--gates`
and `--config` are the only overrides (D-13.4). A
malformed manifest or runner configuration exits 3 (D-13.6). `--format json`
rides every result-producing command (D-11.2).

`--dsn` and `--partition` are the record-backed readers' shared pair, and
naming a partition is what selects the record over this repository's files
(RFC 0050 D-50.2).
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
import yaml

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from torve.application.eventlog import EventLog
    from torve.application.ports import Runtime
    from torve.config.runconfig import RunnerConfig
    from torve.domain.events import EventRecord

from torve.cli.console import Format, fail
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE

# ----------------------- #


class RuntimeName(StrEnum):
    DOCKER = "docker"
    OPENSANDBOX = "opensandbox"


# ....................... #

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        exists=True,
        dir_okay=False,
        help="Runner configuration; defaults to .torve/config.yaml.",
    ),
]
RootOption = Annotated[
    Path, typer.Option("--root", exists=True, file_okay=False, help="Repository root.")
]
FormatOption = Annotated[
    Format, typer.Option("--format", help="text for a person, json for a machine.")
]


# ....................... #


def load_config(root: Path, config_path: Path | None) -> RunnerConfig:
    """Configuration errors exit 3: a bad file is the operator's to
    fix, distinct from red gates (1) and infrastructure failure (4)."""

    from torve.config.runconfig import load_runner_config

    try:
        return load_runner_config(root, config_path)

    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc


# ....................... #


def runtime_for(config: RunnerConfig, override: RuntimeName | None) -> Runtime:
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.adapters.runtime.opensandbox import OpenSandboxRuntime

    adapter = override.value if override else config.runtime.adapter

    if adapter == "docker":
        return DockerRuntime(network=config.runtime.network, docker_mode=config.runtime.docker)

    if adapter == "opensandbox":
        try:
            return OpenSandboxRuntime(config.runtime.opensandbox, docker_mode=config.runtime.docker)

        except ValueError as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc

    raise fail(f"configuration error: unknown runtime adapter {adapter!r}", EXIT_CONFIG)


# ....................... #

# The two options every record-backed reader takes (RFC 0050 D-50.2). A
# partition is what selects the record: naming one says which repository's
# log to read, and naming none says to read this repository's files.
DsnOption = Annotated[
    str, typer.Option("--dsn", help="Postgres DSN holding the log; omitted reads the files.")
]
PartitionOption = Annotated[
    str, typer.Option("--partition", help="The repository whose log holds these tasks.")
]

Read = TypeVar("Read")


def read_log(dsn: str, reader: Callable[[EventLog], Awaitable[Read]]) -> Read:
    """Open the log, ask it one question, close it. Every record-backed
    reader wants the same six lines of wiring around a different query, so
    the wiring lives here and the query arrives as an argument."""

    import asyncio

    async def opened() -> Read:
        from forze.application.execution import DepsRegistry, ExecutionRuntime
        from forze.base.logging import configure_logging

        from torve.adapters.eventstore.document import mock_module, postgres_module
        from torve.application.eventlog import event_log

        configure_logging(level="warning", stream=sys.stderr)
        module = await postgres_module(dsn) if dsn else mock_module()
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(module).freeze())

        async with runtime.scope():
            return await reader(event_log(runtime.get_context()))

    return asyncio.run(opened())


# ....................... #


def task_events(dsn: str, partition: str) -> list[EventRecord] | None:
    """Every task fact one partition's log holds, or None when no partition
    was named — which is how a caller says to read this repository's files
    instead.

    The guarded read, not the paging one: a projection folded from a prefix
    of the log reports states that have since moved, and a report that is
    confidently stale is worse than one that refuses.
    """

    if not partition:
        return None

    from torve.application.eventlog import TruncatedRead
    from torve.domain.events import SubjectType

    try:
        return read_log(dsn, lambda log: log.of_subject_type(SubjectType.TASK, partition=partition))

    except TruncatedRead as exc:
        raise fail(str(exc), EXIT_INFRASTRUCTURE) from exc
