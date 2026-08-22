"""Shared option types and the configuration loaders behind them.

Files resolve under `.torve/` with the legacy root-level names as fallback
(RFC 0013); `--gates` and `--config` are the only overrides (D-13.4). A
malformed manifest or runner configuration exits 3 (D-13.6).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
import yaml

if TYPE_CHECKING:
    from torve.application.ports import Runtime
    from torve.config.runconfig import RunnerConfig

from torve.cli.console import Format, fail
from torve.domain.states import EXIT_CONFIG

# ----------------------- #


class RuntimeName(StrEnum):
    DOCKER = "docker"
    OPENSANDBOX = "opensandbox"


ConfigOption = Annotated[Path | None, typer.Option(
    "--config", exists=True, dir_okay=False,
    help="Runner configuration; defaults to .torve/config.yaml, then torve.yaml.")]
RootOption = Annotated[Path, typer.Option(
    "--root", exists=True, file_okay=False, help="Repository root.")]
FormatOption = Annotated[Format, typer.Option(
    "--format", help="text for a person, json for a machine (D-11.2).")]


def load_config(root: Path, config_path: Path | None) -> RunnerConfig:
    """Configuration errors exit 3 (D-13.6): a bad file is the operator's to
    fix, distinct from red gates (1) and infrastructure failure (4)."""
    from torve.config.runconfig import load_runner_config

    try:
        return load_runner_config(root, config_path)
    except (ValueError, yaml.YAMLError) as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from exc


def runtime_for(config: RunnerConfig, override: RuntimeName | None) -> Runtime:
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.adapters.runtime.opensandbox import OpenSandboxRuntime

    adapter = override.value if override else config.runtime.adapter
    if adapter == "docker":
        return DockerRuntime(network=config.runtime.network)
    if adapter == "opensandbox":
        return OpenSandboxRuntime(config.runtime.opensandbox)
    raise fail(f"configuration error: unknown runtime adapter {adapter!r}", EXIT_CONFIG)
