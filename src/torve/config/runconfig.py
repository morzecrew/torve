""".torve/config.yaml — runner configuration, reviewed like the gate manifest
but on its own cadence (D-3.7; root `torve.yaml` read as a fallback per RFC
0013). Read from where the runner was launched, never from the repository
under work (D-13.3). RFC 0004 adds the tier mapping here.

The OpenSandbox section carries the name of the environment variable holding
the API key, never the key itself — configuration is committed, credentials
are not (D-4b in spirit at the operator level too).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from torve.config import layout
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #


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


class RfcsConfig(BaseModel):
    """Where the specification corpus lives (0013 A-16, D-13.7): one path,
    never a list or a glob — numbering is continuous across a corpus, and two
    roots mean two counters and a colliding identifier at the first merge
    (D-A.16). Read from the runner's configuration per D-13.3, never from the
    repository under work."""

    model_config = ConfigDict(extra="forbid")

    path: str = "rfcs"


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
    rfcs: RfcsConfig = Field(default_factory=RfcsConfig)


def load_runner_config(root: Path, path: Path | None = None) -> RunnerConfig:
    """Explicit `path` is a flag-level override (D-13.4); otherwise the file
    resolves under `.torve/` with the legacy root name as fallback. A missing
    default file means defaults; a missing explicit file is an error."""
    resolved = path if path is not None else layout.config_file(root)
    if not resolved.is_file():
        if path is not None:
            raise ValueError(f"no runner configuration at {resolved}")
        return RunnerConfig()
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if raw is None:
        return RunnerConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{resolved}: runner configuration must be a mapping")
    return RunnerConfig.model_validate(raw)
