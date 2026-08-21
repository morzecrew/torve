"""torve.yaml — runner configuration, reviewed like gates.yaml but on its own
cadence (see logs/T-0003.md). RFC 0004 adds the tier mapping here.

The OpenSandbox section carries the name of the environment variable holding
the API key, never the key itself — configuration is committed, credentials
are not (D-4b in spirit at the operator level too).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from torve.models import SCHEMA_VERSION


class OpenSandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "localhost:5266"
    api_key_env: str = "OPENSANDBOX_API_KEY"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = "docker"  # docker | opensandbox
    image: str = "python:3.13-slim"
    sandbox_timeout: float = 1800  # platform-enforced lifecycle bound, seconds
    agent_timeout: float = 1200  # hard cap per agent attempt, on top of cooperative asks
    opensandbox: OpenSandboxConfig = Field(default_factory=OpenSandboxConfig)


class ReapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stale_after: float = 600  # non-terminal state with a heartbeat older than this is orphaned


class ScmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_pr: bool = False  # no remote yet; flip per repository when one exists


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    poison_ceiling: int = 3  # checked before dispatch; ceiling reached -> escalated, never retry
    base: str | None = None  # base ref for worktrees; None -> origin/main, then main
    reap: ReapConfig = Field(default_factory=ReapConfig)
    scm: ScmConfig = Field(default_factory=ScmConfig)


def load_runner_config(root: Path) -> RunnerConfig:
    path = root / "torve.yaml"
    if not path.is_file():
        return RunnerConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return RunnerConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: torve.yaml must be a mapping")
    return RunnerConfig.model_validate(raw)
