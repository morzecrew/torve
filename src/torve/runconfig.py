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


class StoreConfig(BaseModel):
    """The durable run store (D-5, D-5a): mock for tests and simulation,
    Postgres for real runs (D-3.6). The mock is in-process, so cross-process
    guarantees — a reaper seeing another runner's leases — need Postgres.

    `dsn_env` names the environment variable holding the DSN; the value never
    enters a committed file."""

    model_config = ConfigDict(extra="forbid")

    adapter: str = "mock"  # mock | postgres
    dsn_env: str = "TORVE_PG_DSN"
    schema_name: str = "public"
    run_relation: str = "torve_durable_run"
    step_relation: str = "torve_durable_step"
    lease_for: float = 60.0  # lease duration; cancel asks ride back on renewal
    heartbeat_divisor: int = 3
    max_run_duration: float = 7200.0  # hard cap on one durable body


def _default_skill_sets() -> dict[str, list[str]]:
    return {
        "implement": ["flag-dont-flip", "ratchet-what-you-build"],
        "review": [],
        "revert": ["flag-dont-flip"],
        "author": ["rfc-writer"],
    }


class SkillsConfig(BaseModel):
    """Role-scoped skill sets (RFC 0009 §3, D-9.1) materialized into the
    sandbox from package data at dispatch (A-3, D-9.7)."""

    model_config = ConfigDict(extra="forbid")

    sets: dict[str, list[str]] = Field(default_factory=_default_skill_sets)


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
    store: StoreConfig = Field(default_factory=StoreConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
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
