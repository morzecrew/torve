""".torve/config.yaml — runner configuration, reviewed like the gate manifest
but on its own cadence (D-3.7; root `torve.yaml` read as a fallback per RFC
0013). Read from where the runner was launched, never from the repository
under work (D-13.3). The tier mapping and provider policy are RFC 0004's.

The OpenSandbox section carries the name of the environment variable holding
the API key, never the key itself — configuration is committed, credentials
are not (D-4b in spirit at the operator level too).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from torve.config import layout
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #

ADAPTERS = ("fake", "api", "harness", "subscription")


class TierConfig(BaseModel):
    """One tier's adapter (RFC 0004 §1): `tier` on the task maps to an entry
    here, and the concern leaks no further into the design. The command runs
    *inside* the sandbox (D-4.1) — the engine never links a harness SDK, it
    only shells a line into a container it created.

    `api_key_env` carries names, never values (D-4b): the runtime forwards the
    variables from its own environment, so the secret never transits a spec.
    `auth_volume` is the subscription route (§2, D-4.2): one volume per worker
    slot, `-<slot>` appended, mounted read-write because token refresh writes.
    """

    model_config = ConfigDict(extra="forbid")

    adapter: str = "fake"  # fake | api | harness | subscription
    command: str = ""  # in-sandbox command line; {prompt} and {model} substituted
    model: str = ""  # recorded in telemetry, substituted into the command
    provider: str = ""  # routing identity (§6b); empty only for fake
    # The tier's sandbox image — harness identity is the image (RFC 0017 §3,
    # D-17.4). Empty falls back to runtime.image.
    image: str = ""
    api_key_env: list[str] = Field(default_factory=list)
    auth_volume: str = "torve-auth"
    auth_mount: str = "/auth"

    @model_validator(mode="after")
    def _real_adapters_are_fully_named(self) -> TierConfig:
        if self.adapter not in ADAPTERS:
            raise ValueError(f"unknown agent adapter {self.adapter!r}; one of {ADAPTERS}")
        if self.adapter != "fake":
            if not self.command:
                raise ValueError(f"adapter {self.adapter!r} needs a command to run in the sandbox")
            if not self.provider:
                # Silence is not a policy (§6b): a real adapter sends the
                # repository somewhere, and routing needs to know where.
                raise ValueError(f"adapter {self.adapter!r} needs a provider for routing (D-4.8)")
        return self


def _default_tiers() -> dict[str, TierConfig]:
    return {name: TierConfig() for name in ("planner", "executor", "reviewer")}


class RepositoryProviders(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny_reason: str = ""


class ProvidersConfig(BaseModel):
    """Which providers a repository's contents may reach (RFC 0004 §6b):
    repository contents, fixtures and diffs leave the building for whichever
    provider an adapter is pointed at, so the policy is explicit and enforced
    at dispatch, before a sandbox exists (D-4.8)."""

    model_config = ConfigDict(extra="forbid")

    default: list[str] = Field(default_factory=list)
    repositories: dict[str, RepositoryProviders] = Field(default_factory=dict)
    never_send: list[str] = Field(default_factory=list)  # gitwildmatch globs


class ProviderDenied(ValueError):
    """No permitted provider for this repository and tier — a configuration
    error at dispatch (exit 3), never a quiet fallback (D-4.8)."""


def route_provider(providers: ProvidersConfig, repository: str, provider: str) -> None:
    """Raises ProviderDenied unless `provider` may see `repository`. An empty
    provider is the fake adapter: nothing leaves the building, nothing to
    route."""
    if not provider:
        return
    rules = providers.repositories.get(repository)
    allowed = rules.allow if rules is not None and rules.allow else providers.default
    if provider in allowed:
        return
    reason = f" — {rules.deny_reason}" if rules is not None and rules.deny_reason else ""
    raise ProviderDenied(
        f"provider {provider!r} is not permitted for repository {repository!r}{reason}; "
        f"allowed: {', '.join(allowed) if allowed else 'none configured'}"
    )


def tier_for(config: RunnerConfig, tier_name: str) -> TierConfig:
    """The task's tier resolved against the mapping — a missing entry is a
    configuration error, never a quiet default."""
    try:
        return config.tiers[tier_name]
    except KeyError:
        configured = ", ".join(sorted(config.tiers)) or "none"
        raise ValueError(
            f"no tier {tier_name!r} in the runner configuration; configured: {configured}"
        ) from None


def image_for(config: RunnerConfig, tier: TierConfig) -> str:
    """The tier's image when it names one, else the runtime default — the
    harness's identity is the image it runs in (RFC 0017 §3)."""
    return tier.image or config.runtime.image


def configured_images(config: RunnerConfig) -> list[str]:
    """Every image a run under this configuration could use — the runtime
    default plus each tier's override — for the doctor's existence check."""
    images = {config.runtime.image}
    images.update(tier.image for tier in config.tiers.values() if tier.image)
    return sorted(images)


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
    # Docker network mode ("" = the daemon's default bridge). "host" shares
    # the host's network stack, which is what lets a sandbox reach a proxy or
    # VPN listening on the host's loopback — the operator trades network
    # isolation for the host's egress path, knowingly. Docker-only; the
    # OpenSandbox server owns its own egress model (RFC 0003 §4.1).
    network: str = ""
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


class ReviewConfig(BaseModel):
    """Review triggers (RFC 0005 §4). Off by default — a blocker stopping
    the run is configuration deciding a consequence (D-2), and configuring
    nothing decides nothing. Only the board-driven trigger exists today; the
    pull-request triggers arrive with a forge remote."""

    model_config = ConfigDict(extra="forbid")

    on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_triggers(self) -> ReviewConfig:
        supported = {"task_gated"}
        unknown = [trigger for trigger in self.on if trigger not in supported]
        if unknown:
            raise ValueError(
                f"unsupported review trigger(s) {', '.join(unknown)} — only "
                "task_gated exists until a forge remote is integrated"
            )
        return self


class PromotionConfig(BaseModel):
    """Landing policy (RFC 0006 §3). In the local regime the operator's
    `torve merge` invocation is the recorded approval; `auto_merge` is the
    knob a future scheduler consults before landing without one — off by
    default, opt-in per repository, never globally (D-6.2)."""

    model_config = ConfigDict(extra="forbid")

    auto_merge: bool = False


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    poison_ceiling: int = 3  # checked before dispatch; ceiling reached -> escalated, never retry
    base: str | None = None  # base ref for worktrees; None -> origin/main, then main
    reap: ReapConfig = Field(default_factory=ReapConfig)
    scm: ScmConfig = Field(default_factory=ScmConfig)
    rfcs: RfcsConfig = Field(default_factory=RfcsConfig)
    tiers: dict[str, TierConfig] = Field(default_factory=_default_tiers)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    worker_slot: int = 0  # names this worker's auth volume (D-4.2); slots are stable, tasks are not


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
