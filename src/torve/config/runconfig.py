""".torve/config.yaml — runner configuration, reviewed like the gate manifest
but on its own cadence (D-3.7; one location, no fallback, per RFC 0013 A-48).
Read from where the runner was launched, never from the repository under work
(D-13.3). The tier mapping and provider policy are RFC 0004's.

The OpenSandbox section carries the name of the environment variable holding
the API key, never the key itself — configuration is committed, credentials
are not (D-4b in spirit at the operator level too).
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from torve.config import layout
from torve.config.manifest import GateAxis
from torve.domain.task import SCHEMA_VERSION, Task

# ----------------------- #

ADAPTERS = ("fake", "api", "harness", "subscription")


# ....................... #


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

    # D-27.11: the dotted tier this seat's attempt resolves to after a
    # gate-red — one rung, not a chain. Empty means an attempt that gates
    # red is retried under the same tier, today's behaviour. D-34.6 keeps
    # this scalar as sugar for the functional key of the mapping below.
    retry_variant: str = ""

    # D-34.6: the retry rungs keyed by axis — which conviction routes the
    # next attempt where. Every reader takes the merged view through
    # `resolved_retry_variants()`, so no surface sees only the functional
    # rung; `boundary` may not name a rung here at all (D-34.7).
    retry_variants: dict[GateAxis, str] = Field(default_factory=dict)

    # RFC 0028 §5.1, D-28.1/D-28.2, A-74: the named profile(s) this tier
    # resolved from, if any — resolution happens on the raw mapping in
    # `load_runner_config`, before this model ever validates, so by the time
    # a `TierConfig` exists every other field already carries the merged
    # content. The raw config's `profile` key may be a single name or a list
    # merged left to right (A-74); either way this field ends up holding the
    # chain in order (`"a -> b"`, or just `"a"` for a single name), kept
    # (not popped) so `config_hash` and `torve doctor` can both see where a
    # tier came from.
    profile: str = ""

    # RFC 0029 §5.1, D-29.1/D-29.3: `None` inherits the role-scoped skill set
    # (`SkillsConfig.sets[role]`) exactly as today; a list overrides it
    # wholesale — never additive, so the effective set is readable in one
    # place. Rides the profile merge unchanged (D-28.4's replace-wholesale
    # rule already covers list fields). Names resolve through the same
    # `materialize` path with the same refusals (D-29.2).
    skills: list[str] | None = None

    # RFC 0029 §5.1, D-29.1: lines appended to the built prompt after the
    # charter's base working rules, which stay unaddressable from
    # configuration.
    prompt_extras: list[str] = Field(default_factory=list)

    # ....................... #

    def resolved_retry_variants(self) -> dict[GateAxis, str]:
        """The one resolution of D-27.11's scalar and D-34.6's mapping: the
        axis-keyed rungs with the scalar read as sugar for the functional
        key, so every reader — the runner's routing, the dispatch-time
        provider check in run, tick and fleet — answers from the full map.
        Contradictory spellings of the functional rung are refused at
        validation, so this never has to arbitrate. An axis absent from the
        result resolves no rung: the attempt retries under the tier that
        just ran."""

        rungs = dict(self.retry_variants)

        if self.retry_variant:
            rungs["functional"] = self.retry_variant

        return rungs

    # ....................... #

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

    # ....................... #

    @model_validator(mode="after")
    def _retry_rungs_are_coherent(self) -> TierConfig:
        # D-34.7: no configuration may hang a retry rung on a boundary
        # conviction — a fence defect is repaired by the operator's
        # disclosed chore commit, never escalated to a heavier model. A
        # mappable boundary would be a rung to nowhere: selection resolves
        # none there whatever the mapping says.
        if self.retry_variants.get("boundary"):
            raise ValueError(
                "a boundary conviction resolves no retry rung — remove the "
                f"boundary entry (it names {self.retry_variants['boundary']!r}), "
                "whose rung could never run"
            )

        # D-34.6: the scalar is sugar for the functional key; both spellings
        # saying different things is a configuration error, never a silent
        # precedence.
        scalar, mapped = self.retry_variant, self.retry_variants.get("functional")

        if scalar and mapped and scalar != mapped:
            raise ValueError(
                f"retry_variant ({scalar!r}) and retry_variants.functional ({mapped!r}) "
                "name different tiers for the same axis; say it once"
            )

        if "" in self.retry_variants.values():
            raise ValueError(
                "retry_variants values must name a tier; omit the axis instead "
                "of naming an empty one"
            )

        return self


# ....................... #


def _default_tiers() -> dict[str, TierConfig]:
    return {name: TierConfig() for name in ("planner", "executor", "reviewer")}


# ....................... #


class RepositoryProviders(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny_reason: str = ""


# ....................... #


class ProvidersConfig(BaseModel):
    """Which providers a repository's contents may reach (RFC 0004 §6b):
    repository contents, fixtures and diffs leave the building for whichever
    provider an adapter is pointed at, so the policy is explicit and enforced
    at dispatch, before a sandbox exists (D-4.8)."""

    model_config = ConfigDict(extra="forbid")

    default: list[str] = Field(default_factory=list)
    repositories: dict[str, RepositoryProviders] = Field(default_factory=dict)
    never_send: list[str] = Field(default_factory=list)  # gitwildmatch globs


# ....................... #


class ProviderDenied(ValueError):
    """No permitted provider for this repository and tier — a configuration
    error at dispatch (exit 3), never a quiet fallback (D-4.8)."""


# ....................... #


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


# ....................... #


def effective_skill_sets(
    tier: TierConfig, role: str, sets: dict[str, list[str]]
) -> dict[str, list[str]]:
    """RFC 0029 D-29.1/D-29.3: `tier.skills`, when set, overrides the
    role-scoped set wholesale for `role` alone — every other role's set is
    untouched, and the materializer's own resolution and refusals (D-29.2)
    are unaffected by this: it only changes which names `materialize` sees
    for this role."""

    if tier.skills is None:
        return sets

    return {**sets, role: tier.skills}


# ....................... #


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


# ....................... #


def tier_name_for(task: Task) -> str:
    """D-27.3's dotted lookup key: `seat.variant` when the contract names a
    `tier_variant`, the seat literal otherwise — naming a variant that does
    not exist is a configuration error `tier_for` raises loudly, never a
    fallback to the seat."""

    return f"{task.tier}.{task.tier_variant}" if task.tier_variant else task.tier


# ....................... #


def broker_in_force(config: RunnerConfig) -> bool:
    """A broker adapter other than `none` is configured — the run's keys are
    the broker's business, not the tier's (D-21.1)."""

    return config.broker.adapter != "none"


# ....................... #


def image_for(config: RunnerConfig, tier: TierConfig) -> str:
    """The tier's image when it names one, else the runtime default — the
    harness's identity is the image it runs in (RFC 0017 §3)."""

    return tier.image or config.runtime.image


# ....................... #


def configured_images(config: RunnerConfig) -> list[str]:
    """Every image a run under this configuration could use — the runtime
    default plus each tier's override — for the doctor's existence check."""

    images = {config.runtime.image}
    images.update(tier.image for tier in config.tiers.values() if tier.image)

    return sorted(images)


# ....................... #


class OpenSandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "localhost:5266"
    api_key_env: str = "OPENSANDBOX_API_KEY"


# ....................... #


def split_host_port(authority: str) -> tuple[str, int | None]:
    """``'host'``, ``'host:port'``, ``'[v6]'`` or ``'[v6]:port'`` to
    ``(host, port)``. Shared by the sealed broker (matching CONNECT
    authorities) and the configuration validator (shaping pass-through
    entries) so both read one format; pass-through entries themselves are
    validated as hostnames or IPv4 addresses — a bracketed IPv6 parses
    here but is refused by the entry validator, which is fine: a named
    destination a machine can match is what matters (D-21.11's answer: the
    declared destinations live in the broker block, and the broker is the
    only reader)."""

    if authority.startswith("["):
        end = authority.find("]")

        if end == -1:
            return authority, None

        host = authority[1:end]
        rest = authority[end + 1 :]

        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])

        return host, None

    if ":" in authority:
        host, _, port = authority.rpartition(":")

        if port.isdigit():
            return host, int(port)

    return authority, None


# ....................... #


def pass_through_allows(
    pass_through: list[str] | tuple[str, ...], host: str, port: int
) -> bool:
    """Whether the sealed broker may CONNECT to ``host:port`` without
    inspection (RFC 0021 §5.2): a declared entry matches its host on any
    port, and a ``host:port`` entry narrows to exactly that port — the
    declaration is of a *named host*, ports are the destination's business
    (D-21.3)."""

    for entry in pass_through:
        entry_host, entry_port = split_host_port(entry)

        if entry_host.lower() == host.lower() and (entry_port is None or entry_port == port):
            return True

    return False


# ....................... #


def sealed_broker_port(network: str) -> int:
    """The sealed broker's port, derived from the internal network's name
    (RFC 0003 §4's rule: when a service needs a port, it derives one). The
    broker binds it and the runtime composes the sandbox's proxy env from
    it — two adapters with no channel between them derive the same number
    from the same configured name (the runtime's network and the broker's
    network are validated equal)."""

    digest = hashlib.sha256(network.encode("utf-8")).digest()

    return 20000 + int.from_bytes(digest[:2], "big") % 40000


# ....................... #


def _validate_pass_through_entry(entry: str) -> None:
    """A pass-through entry is a host, optionally :port — a named
    destination, never a URL, a pattern or a wildcard (D-21.3: every other
    destination is declared, and a declaration a machine cannot match is
    not a declaration)."""

    if not entry or entry != entry.strip():
        raise ValueError(
            f"broker pass_through entry {entry!r} must be a host, optionally :port — "
            "no scheme, path, wildcard or surrounding whitespace"
        )

    if any(char in entry for char in ("/", "\\", "?", "#", "*", " ")):
        raise ValueError(
            f"broker pass_through entry {entry!r} must be a host, optionally :port — "
            "no scheme, path, wildcard or surrounding whitespace"
        )

    host, port = split_host_port(entry)

    if not host or not all(char.isalnum() or char in ".-_" for char in host):
        raise ValueError(f"broker pass_through entry {entry!r} names no valid host")

    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"broker pass_through entry {entry!r}: port out of range")


# ....................... #


class BrokerProvider(BaseModel):
    """One routed provider's wire facts (RFC 0021 §5.2): where the broker
    forwards and which environment variable in the broker's own environment
    holds the key — a name, never a value. The configuration names the
    credential once; a brokered tier names none (D-21.1)."""

    model_config = ConfigDict(extra="forbid")

    upstream: str = ""  # the provider's real base URL (http:// or https://)
    # A-70: the broker's own upstream leg tunnels through the host's
    # https_proxy for this provider — for upstreams unreachable from the
    # host directly (region gating). The sandbox never sees a proxy either
    # way; this is the broker's egress, not the run's.
    via_proxy: bool = False
    key_env: str = ""  # the env var name the broker reads the key from

    # ....................... #

    @model_validator(mode="after")
    def _wire_facts_are_fully_named(self) -> BrokerProvider:
        if not self.upstream.startswith(("http://", "https://")):
            raise ValueError(
                f"broker provider upstream {self.upstream!r} must be an http(s) base URL"
            )

        if not self.key_env:
            raise ValueError("broker provider key_env must name the environment variable")

        return self


# ....................... #


class BrokerConfig(BaseModel):
    """The egress broker (RFC 0021 §5.1): which adapter is in force and what
    it is fed. `none` is today's behaviour named explicitly — keys pass
    through, no metering, no wire routing — and stays the phase-1 default;
    `torve doctor` names it and says plainly that it leaves D-4b unmet
    (D-21.9). Under any other adapter a brokered tier names no credential
    (D-21.1): the broker's provider table is the one channel.

    The two modes are D-21.3's split: `endpoint` (the phase-1 default)
    closes custody and metering on the daemon's default bridge; `sealed`
    adds containment — the sandbox joins the user-defined internal Docker
    network named here, whose only host-side address is the broker, and
    every non-provider destination the run needs is declared under
    `pass_through` and CONNECTed without inspection. The same network is
    named in `runtime.network` — egress policy (this block) and sandbox
    provisioning (`runtime`) are two views of one fact, and the runner
    validator refuses them to disagree (D-21.11)."""

    model_config = ConfigDict(extra="forbid")

    adapter: Literal["none", "local", "opensandbox"] = "none"
    mode: Literal["endpoint", "sealed"] = "endpoint"
    # The user-defined --internal Docker network sealed mode joins; the
    # broker attaches to it at its gateway, the sandboxes join it, and
    # nothing on it is reachable except the broker (D-21.3). Empty in
    # endpoint mode; must equal runtime.network when sealed.
    network: str = ""
    # provider -> wire facts; the run's routing is a dispatch-checked subset
    # (D-21.4: the broker exposes one loopback route per routed provider).
    providers: dict[str, BrokerProvider] = Field(default_factory=dict)
    # Sealed mode only: named hosts the broker will CONNECT to without
    # inspection — a package index, the forge. An undeclared destination is
    # refused loudly, and the run fails rather than succeed through a path
    # nobody meant to leave open (D-21.3).
    pass_through: list[str] = Field(default_factory=list)
    # A broker-measured cost that diverges from the adapter's self-report by
    # more than this fraction is an engine event (D-21.5).
    cost_tolerance: float = 0.25

    # ....................... #

    @model_validator(mode="after")
    def _this_phase_supports_only_what_is_built(self) -> BrokerConfig:
        if self.adapter == "opensandbox":
            raise ValueError(
                "broker adapter 'opensandbox' is not built — it is condition-gated "
                "on a live server and arrives as an adapter, never a prerequisite (D-21.2)"
            )

        if self.mode == "sealed":
            if self.adapter == "none":
                raise ValueError(
                    "broker mode 'sealed' needs a broker on the wire — adapter 'none' "
                    "has no wire presence to share the internal network with"
                )

            if not self.network:
                raise ValueError(
                    "broker mode 'sealed' names the internal Docker network the sandbox "
                    "joins — set broker.network (and runtime.network to the same name)"
                )

            if self.network == "host" or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.network
            ):
                raise ValueError(
                    "broker mode 'sealed' needs a user-defined internal network, not a "
                    f"network mode; {self.network!r} is not a valid Docker network name"
                )

            for entry in self.pass_through:
                _validate_pass_through_entry(entry)

            provider_hosts = {
                urlsplit(provider.upstream).hostname for provider in self.providers.values()
            }
            overlap = sorted(
                entry
                for entry in self.pass_through
                if split_host_port(entry)[0].lower() in provider_hosts
            )

            if overlap:
                raise ValueError(
                    "broker pass_through names routed provider host(s) "
                    f"{', '.join(overlap)} — a destination cannot be both a routed "
                    "provider (key injected, metered) and an uninspected pass-through "
                    "(D-21.4's wire enforcement would be bypassable)"
                )

        else:
            if self.network:
                raise ValueError(
                    "broker.network names the sealed internal network; endpoint mode "
                    "keeps the daemon's default bridge and names no network"
                )

            if self.pass_through:
                raise ValueError(
                    "broker.pass_through declares sealed-mode egress; endpoint mode "
                    "keeps the default bridge and declares nothing"
                )

        return self


# ....................... #


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
    # Docker inside the sandbox (RFC 0017 §2a, D-17.9). "socket" mounts the
    # host daemon's socket into every sandbox of the run — attempt and gates
    # alike — and the image supplies the docker CLI. Host-equivalent
    # capability, granted knowingly per repository (D-17.10): a container
    # started over the host socket can mount any host path. The nested
    # daemon is the named, deferred stronger mode. OpenSandbox refuses any
    # value here.
    docker: Literal["", "socket"] = ""
    opensandbox: OpenSandboxConfig = Field(default_factory=OpenSandboxConfig)


# ....................... #


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
    lease_for: float = 60.0  # lease duration; cancel asks ride back on renewal
    heartbeat_divisor: int = 3
    max_run_duration: float = 7200.0  # hard cap on one durable body


# ....................... #


def _default_skill_sets() -> dict[str, list[str]]:
    return {
        "implement": ["flag-dont-flip", "ratchet-what-you-build"],
        "review": [],
        "revert": ["flag-dont-flip"],
        "author": ["rfc-writer"],
    }


# ....................... #


class SkillsConfig(BaseModel):
    """Role-scoped skill sets (RFC 0009 §3, D-9.1) materialized into the
    sandbox from package data at dispatch (A-3, D-9.7)."""

    model_config = ConfigDict(extra="forbid")

    sets: dict[str, list[str]] = Field(default_factory=_default_skill_sets)


# ....................... #


class RfcsConfig(BaseModel):
    """Where the specification corpus lives (0013 A-16, D-13.7): one path,
    never a list or a glob — numbering is continuous across a corpus, and two
    roots mean two counters and a colliding identifier at the first merge
    (D-A.16). Read from the runner's configuration per D-13.3, never from the
    repository under work."""

    model_config = ConfigDict(extra="forbid")

    path: str = "rfcs"


# ....................... #


class ReapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stale_after: float = 600  # non-terminal state with a heartbeat older than this is orphaned


# ....................... #


class VcsConfig(BaseModel):
    """Local git at the runner boundary (RFC 0010 §4). The signing key is a
    path to an SSH private key the RUNNER holds — it is never mounted into a
    sandbox, and the signature attests "Torve produced this under its task",
    never that a human reviewed it. Unset means unsigned."""

    model_config = ConfigDict(extra="forbid")

    signing_key: str | None = None


# ....................... #


class ScmConfig(BaseModel):
    """The remote forge (RFC 0010 §2). The credential is named, never held:
    `token_env` is the NAME of the environment variable the runner reads at
    push/PR time (D-4b) — the value stays in the runner's process and no
    sandbox ever sees it. `repo` is owner/name on the forge."""

    model_config = ConfigDict(extra="forbid")

    open_pr: bool = False  # flip per repository once a remote exists
    repo: str | None = None
    token_env: str | None = None


# ....................... #


class TrackerConfig(BaseModel):
    """The tracker projection (RFC 0008). `kind` names the adapter — only
    `github-issues` exists (D-8.8); empty means no projection. The board is
    a view, never authority (D-8.1); the credential is named, never held
    (D-4b), and needs the forge's Issues scope."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["", "github-issues"] = ""
    repo: str | None = None
    token_env: str | None = None
    # The forge login notified on interrupt-class escalations (D-6.4 routes
    # "notify" and "harness owner"; RFC 0003 D-3.18). A name, never a
    # secret; empty keeps the notifier inert.
    notify: str = ""
    # Forge logins whose /torve commands apply (T-0054): authorization
    # precedes validation, and an empty list refuses every command —
    # configuring nothing decides nothing.
    commanders: list[str] = Field(default_factory=list)


# ....................... #


class ReviewConfig(BaseModel):
    """Review triggers (RFC 0005 §4). Off by default — a blocker stopping
    the run is configuration deciding a consequence (D-2), and configuring
    nothing decides nothing. `task_gated` is board-driven; the pull-request
    triggers admit `torve review pr` as the forge's event delivery.
    `skip_authors` is §4's author skip rule; draft and zero-changed-files
    pull requests always skip."""

    model_config = ConfigDict(extra="forbid")
    on: list[str] = Field(default_factory=list)
    skip_authors: list[str] = Field(default_factory=list)

    # The revision loop's allow-list (RFC 0005 §4a, A-52): forge logins
    # whose review threads become revision context at retry. Empty = off;
    # a stranger's comment never reaches an agent.
    feedback_from: list[str] = Field(default_factory=list)

    # ....................... #

    @model_validator(mode="before")
    @classmethod
    def _unquoted_on_is_a_yaml_bool_not_a_key(cls, data: Any) -> Any:
        # YAML 1.1 resolves an unquoted on/off/yes/no/true/false key (any
        # case) to a boolean, not the string it looks like — `on:` under
        # `review:` becomes key `True`, `on` keeps its empty default, and
        # the trigger list never loads (T-0134). Caught here, before
        # pydantic's own "keys should be strings" check, so the error names
        # the actual fix instead of a generic key-type complaint.
        if not isinstance(data, dict):
            return data

        # mypy sees dict[Any, Any] here and pyright sees dict[Unknown,
        # Unknown]; a cast satisfies one and offends the other, so the
        # pyright reading is silenced at the source instead.
        if any(isinstance(key, bool) for key in data):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            raise ValueError(
                "review config has a boolean key (True/False) instead of a string — "
                "YAML parses an unquoted on/off/yes/no/true/false key as a boolean; "
                'quote it, e.g. "on": [...] under review:'
            )

        return data  # pyright: ignore[reportUnknownVariableType]

    # ....................... #

    @model_validator(mode="after")
    def _known_triggers(self) -> ReviewConfig:
        supported = {"task_gated", "pr_opened", "pr_synchronized"}
        unknown = [trigger for trigger in self.on if trigger not in supported]

        if unknown:
            raise ValueError(
                f"unsupported review trigger(s) {', '.join(unknown)} — "
                f"the vocabulary is {', '.join(sorted(supported))}"
            )

        return self


# ....................... #


class PromotionConfig(BaseModel):
    """Landing policy (RFC 0006 §3). In the local regime the operator's
    `torve merge` invocation is the recorded approval; `auto_merge` is the
    knob a future scheduler consults before landing without one — off by
    default, opt-in per repository, never globally (D-6.2).

    `require_ci` is §3's `ci: green_on_current_head` requirement: the lane
    refuses to land a candidate whose branch tip is not green on the
    configured remote's CI (`scm.repo`), polled with backoff against the
    lightweight runs endpoint (§1). A rebased tree is additionally judged
    by the local battery re-run — the remote verdict covers the tip the
    remote actually saw."""

    model_config = ConfigDict(extra="forbid")

    auto_merge: bool = False
    require_ci: bool = False
    # §3's review criterion (D-6.14, A-43): the lane lands only a candidate
    # whose producing run recorded a concluded review (`reviewed_by` on the
    # run state) — the unconfigured-review bridge never satisfies it.
    require_review: bool = False
    # §3's approvals requirement (T-0060): the lane lands only a candidate
    # with this many recorded approvals of its CURRENT branch tip — an
    # approval that predates the last push approves nothing (D-6.3). Zero
    # requires none: configuring nothing decides nothing.
    approvals: int = 0
    # §3's quiet window, in seconds: a landing whose branch tip is younger
    # than this refuses — pushing resets the window. Zero disables it.
    quiet_window: int = 0


# ....................... #


class LoopConfig(BaseModel):
    """The standing loop's knobs (RFC 0019 §7). There is no enabled
    flag — scheduling `torve tick` is the enablement."""

    model_config = ConfigDict(extra="forbid")

    # Intake pauses while the escalation queue holds this many (D-19.5).
    pause_escalations: int = 1
    # Up to this many dispatches per tick, admitted only while their
    # scopes are provably disjoint (D-19.14, A-39). The default keeps
    # D-19.4's original one-dispatch regime; raising it is RFC 0006 §4's
    # parallelism raise — one dimension, after measured escalation rate.
    dispatch_workers: int = 1
    # Seconds; a tick lock older than this is stale and broken loudly.
    tick_budget: int = 3600
    # RFC 0023 D-23.6: instantiations across every standing job, bounded
    # like dispatch's one-per-tick doctrine — spend per unit time stays
    # cadence times a known bound.
    standing_max_per_tick: int = 1


# ....................... #


class IntakeConfig(BaseModel):
    """The drafting run's knobs (RFC 0020). `max_drafts` is D-20.8's
    decomposition ceiling — how many contracts one request may yield;
    `iterations` bounds the draft-lint loop like any attempt budget.
    `document_threshold` is the document-threshold rule's starting point —
    the number of distinct documents whose settled ground a scope must
    cross before the work needs one of its own (RFC 0030 D-30.3), a
    calibration knob rather than a truth."""

    model_config = ConfigDict(extra="forbid")

    max_drafts: int = 4
    iterations: int = 3
    document_threshold: int = 2


# ....................... #


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
    vcs: VcsConfig = Field(default_factory=VcsConfig)
    scm: ScmConfig = Field(default_factory=ScmConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    rfcs: RfcsConfig = Field(default_factory=RfcsConfig)
    tiers: dict[str, TierConfig] = Field(default_factory=_default_tiers)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    intake: IntakeConfig = Field(default_factory=IntakeConfig)
    worker_slot: int = 0  # names this worker's auth volume (D-4.2); slots are stable, tasks are not

    # ....................... #

    @model_validator(mode="after")
    def _brokered_tiers_name_no_credential(self) -> RunnerConfig:
        """D-21.1: a brokered tier names no credential. `api_key_env` must be
        empty and a non-empty one is a refused configuration, not a warning —
        a second channel for a secret is the leak the broker exists to
        remove (D-17.4)."""

        if self.broker.adapter == "none":
            return self

        offenders = sorted(name for name, tier in self.tiers.items() if tier.api_key_env)

        if offenders:
            raise ValueError(
                f"broker adapter {self.broker.adapter!r} is in force but tier(s) "
                f"{', '.join(offenders)} name api_key_env — a brokered tier names "
                "no credential; the broker's provider table is the one channel"
            )

        return self

    # ....................... #

    @model_validator(mode="after")
    def _retry_variant_names_a_configured_tier(self) -> RunnerConfig:
        """D-27.11: a rung to nowhere is a configuration error at load time,
        not a dispatch-time surprise after the first gate-red. D-34.6's
        mapping makes every axis's rung reachable, so every one of them is
        checked, not only the scalar's functional mirror."""

        offenders = {
            (seat, axis, rung)
            for seat, tier in self.tiers.items()
            for axis, rung in tier.resolved_retry_variants().items()
            if rung not in self.tiers
        }

        if offenders:
            named = ", ".join(
                f"{seat!r} on {axis} -> {rung!r}" for seat, axis, rung in sorted(offenders)
            )

            raise ValueError(f"retry_variant names no configured tier: {named}")

        return self

    # ....................... #

    @model_validator(mode="after")
    def _sealed_runtime_holds_the_network(self) -> RunnerConfig:
        """D-21.3's containment is a property of the run's wiring, so the
        two halves must agree: the sandbox joins the internal network the
        broker attaches to (D-21.11 — egress policy in the broker block,
        sandbox provisioning in runtime, one fact). Sealed mode also needs
        the one runtime that has user-defined internal networks, and
        refuses the host daemon socket — a socket is host-equivalent
        capability (D-17.10), which is exactly the trust sealed mode is
        for removing."""

        if self.broker.mode != "sealed":
            return self

        if self.runtime.adapter != "docker":
            raise ValueError(
                "broker mode 'sealed' needs the docker runtime — only Docker has "
                "user-defined internal networks; the opensandbox runtime owns its "
                "own egress model"
            )

        if self.runtime.docker:
            raise ValueError(
                "broker mode 'sealed' refuses runtime.docker: socket — mounting the "
                "host daemon into the sandbox is host-equivalent capability "
                "(D-17.10), the exact trust sealed containment exists to remove"
            )

        if self.runtime.network != self.broker.network:
            raise ValueError(
                "broker mode 'sealed' joins the sandbox to the internal network the "
                "broker attaches to — runtime.network "
                f"({self.runtime.network!r}) and broker.network "
                f"({self.broker.network!r}) must name the same network"
            )

        return self


# ....................... #


def profiles_dir() -> Path:
    """`~/.config/torve/agents/` (RFC 0028 §5.1, D-28.1) — beside the fleet
    manifest, on the operator's machine, never the repository under work.
    XDG_CONFIG_HOME when set, matching `fleet.default_manifest_path`."""

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")

    return Path(base) / "torve" / "agents"


# ....................... #


def _load_profile_body(name: str, key: str, agents_dir: Path) -> tuple[dict[str, Any], Path]:
    """One named profile's body, validated in isolation (D-28.3): every
    refusal below names this profile's own file, never the chain it is
    part of."""

    path = agents_dir / f"{name}.yaml"

    if not path.is_file():
        present = sorted(p.stem for p in agents_dir.glob("*.yaml")) if agents_dir.is_dir() else []
        raise ValueError(
            f"tier {key!r} names profile {name!r}, resolving to {path} — no such "
            f"file; profiles present: {', '.join(present) or 'none'}"
        )

    try:
        raw_body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: could not be read as a profile — {exc}") from exc

    if raw_body is None:
        raw_body = {}

    if not isinstance(raw_body, dict):
        raise ValueError(f"{path}: profile body must be a mapping")

    body = cast("dict[str, Any]", raw_body)
    unknown = sorted(k for k in body if k not in TierConfig.model_fields)

    if unknown:
        raise ValueError(f"{path}: unknown key(s) {', '.join(unknown)} — not a TierConfig field")

    return body, path


# ....................... #


def _resolve_profiles(
    tiers: dict[str, Any], agents_dir: Path
) -> dict[str, list[tuple[str, Path]]]:
    """D-28.2: a raw-mapping merge, on `raw["tiers"]`, before
    `RunnerConfig.model_validate` ever runs — locally-present keys win, and
    the merged mapping is all `TierConfig` sees. One merge level, no
    profile-to-profile inheritance (D-28.4): a profile body's own `profile`
    key, if any, is never itself resolved.

    A-74: `profile` also accepts a list of names, merged left to right under
    this same shallow rule before local overrides — a tier composing flat
    layers, never a profile referencing a profile. Each named profile is
    loaded and validated on its own (`_load_profile_body`), so every refusal
    class still names that profile's own path.

    D-28.3: every failure below refuses the configuration load, naming the
    file — there is no fallback to inline defaults.

    Returns the tier key -> ordered [(profile name, profile path), ...] map
    for every tier resolved through a profile, so a later TierConfig
    validation failure on the merged result can be traced back to the chain
    that supplied it."""

    sources: dict[str, list[tuple[str, Path]]] = {}

    for key, raw_entry in tiers.items():
        if not isinstance(raw_entry, dict):
            continue

        entry = cast("dict[str, Any]", raw_entry)
        names_field = entry.get("profile")

        if not names_field:
            continue

        names = cast(
            "list[str]", names_field if isinstance(names_field, list) else [names_field]
        )

        merged_body: dict[str, Any] = {}
        chain: list[tuple[str, Path]] = []

        for name in names:
            body, path = _load_profile_body(name, key, agents_dir)
            # Left to right (A-74): each next profile's keys win over the
            # ones before it, same shallow-merge rule as local-over-profile
            # below — list fields replace wholesale, never concatenate.
            merged_body = {**merged_body, **body}
            chain.append((name, path))

        # Local wins last (D-28.2); list fields (api_key_env) replace
        # wholesale, never concatenate (D-28.4) — this is a plain dict
        # merge, no per-field logic, so that falls out for free.
        merged = {**merged_body, **{k: v for k, v in entry.items() if k != "profile"}}
        merged["profile"] = " -> ".join(names)
        tiers[key] = merged
        sources[key] = chain

    return sources


# ....................... #


def load_runner_config(root: Path, path: Path | None = None) -> RunnerConfig:
    """Explicit `path` is a flag-level override (D-13.4); otherwise the file
    is `.torve/config.yaml` and nowhere else. A missing default file means
    defaults; a missing explicit file is an error."""

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

    config = cast("dict[str, Any]", raw)
    tiers = config.get("tiers")
    profile_sources: dict[str, list[tuple[str, Path]]] = {}

    if isinstance(tiers, dict):
        profile_sources = _resolve_profiles(cast("dict[str, Any]", tiers), profiles_dir())

    try:
        return RunnerConfig.model_validate(config)
    except ValidationError as exc:
        # D-28.3's fourth refusal class: a merged result invalid enough that
        # TierConfig itself refuses it. Pydantic's error names the field, not
        # the profile that supplied it — named here so the offending
        # profile chain and its files are as locatable as the other three
        # refusal classes.
        offenders = sorted(
            {
                str(error["loc"][1])
                for error in exc.errors()
                if len(error["loc"]) >= 2
                and error["loc"][0] == "tiers"
                and error["loc"][1] in profile_sources
            }
        )

        if not offenders:
            raise

        named = "; ".join(
            f"tier {name!r} via profile "
            + " -> ".join(f"{pname!r} ({ppath})" for pname, ppath in profile_sources[name])
            for name in offenders
        )

        raise ValueError(f"{named}: invalid merged tier configuration — {exc}") from exc
