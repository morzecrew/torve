"""Shared option types and the configuration loaders behind them.

Files resolve under `.torve/`, one location each (RFC 0013 A-48); `--gates`
and `--config` are the only overrides (D-13.4). A
malformed manifest or runner configuration exits 3 (D-13.6). `--format json`
rides every result-producing command (D-11.2).

`--dsn` and `--partition` are the record-backed readers' shared pair, and
naming a partition is what selects the record over this repository's files
(RFC 0050 D-50.2). `dsn_for` resolves the DSN the configuration names
(D-4b) when the caller supplies none (RFC 0032 A-123).
"""

from __future__ import annotations

import os
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
from torve.domain.states import EXIT_CONFIG

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

# Where a run's secrets are named. The file is never committed (it is in
# `.gitignore`, and D-4b says the DSN is named by variable rather than
# written down); reading it here only saves the operator from exporting the
# same eight names into every shell.
DOTENV = ".env"


def load_dotenv(directory: Path | None = None) -> list[str]:
    """Put `.env`'s names into the environment, and return the ones taken.

    The real environment always wins: a name already exported is a name the
    operator set deliberately for this invocation, and a file on disk does
    not get to overrule it. A malformed line is skipped rather than fatal —
    this is a convenience, and a convenience that refuses to start the
    program is not one.

    Values are never returned, logged or rendered. The names are, because
    "which secrets did this pick up" is a question worth answering and
    "what are they" is not.
    """

    path = (directory or Path.cwd()) / DOTENV

    try:
        lines = path.read_text(encoding="utf-8").splitlines()

    except OSError:
        return []

    taken: list[str] = []

    for line in lines:
        entry = line.strip().removeprefix("export ").strip()

        if not entry or entry.startswith("#") or "=" not in entry:
            continue

        name, _, value = entry.partition("=")
        name = name.strip()

        if not name or name in os.environ:
            continue

        os.environ[name] = value.strip().strip("\"'")
        taken.append(name)

    return taken


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


def dsn_for(root: Path, override: str = "") -> str:
    """The DSN a record read should use, given what the caller typed.

    An explicit `--dsn` wins. Otherwise the repository's own configuration
    answers: it names the variable and the environment holds the value,
    which `.env` fills in for a terminal. A mock store has no DSN and says
    so by returning nothing.

    Without this, naming a partition and omitting `--dsn` read an empty
    mock, found nothing, and fell back to the files — the safe direction,
    and silent about a misconfiguration the operator could not see.
    """

    if override:
        return override

    from torve.config.runconfig import load_runner_config

    try:
        config = load_runner_config(root, None)

    except (ValueError, OSError):
        return ""

    if config.store.adapter != "postgres":
        return ""

    return os.environ.get(config.store.dsn_env, "")


# ....................... #


def dsn_to_write(root: Path, override: str = "") -> str:
    """The same resolution, for a command that *writes* to the log.

    A read that finds nothing is wrong and recoverable; a write that lands
    nowhere is wrong and gone. Without a DSN the runtime stands up a mock —
    "a real log for the life of the process and nothing afterwards" — so
    `torve manager resolve` closed an escalation against a store that
    ceased to exist when the process did, and printed that it had. An
    operator's triage cannot be a no-op that reports success.

    So this refuses instead of standing one in: when the configuration
    names postgres and nothing supplies the DSN, the write stops. A
    repository whose store really is mock still gets a mock, because that
    is what it asked for.
    """

    dsn = dsn_for(root, override)

    if dsn:
        return dsn

    from torve.config.runconfig import load_runner_config

    try:
        config = load_runner_config(root, None)

    except (ValueError, OSError):
        return ""

    if config.store.adapter == "postgres":
        raise fail(
            f"configuration error: store.adapter is 'postgres' but ${config.store.dsn_env} "
            "is not set and no --dsn was given — this write would land in a mock store "
            "that vanishes with the process",
            EXIT_CONFIG,
        )

    return ""


# ....................... #


def task_events(dsn: str, partition: str) -> list[EventRecord] | None:
    """Every task fact one partition's log holds, or None when no partition
    was named — which is how a caller says to read this repository's files
    instead.

    The read pages to the end of the partition: a projection folded from a
    prefix of the log reports states that have since moved, and nothing in
    the answer says so.
    """

    if not partition:
        return None

    from torve.domain.events import SubjectType

    return read_log(dsn, lambda log: log.of_subject_type(SubjectType.TASK, partition=partition))
