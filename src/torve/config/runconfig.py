""".torve/config.yaml — runner configuration, reviewed like the gate manifest
but on its own cadence (S-0003/D-7; one location, no fallback, per S-0013 S-0013/A-2).
Read from where the runner was launched, never from the repository under work
(S-0013/D-3). The tier mapping and provider policy are S-0004's.

The OpenSandbox section carries the name of the environment variable holding
the API key, never the key itself — configuration is committed, credentials
are not (S-0001/D-13 in spirit at the operator level too).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

from torve.base.model import STRICT
from torve.config import layout
from torve.config.agents import (
    AgentError,
    all_harnesses,
    resolve_seats,
    role_skills,
)
from torve.config.equipment import Equipment
from torve.config.providers import APIS, Provider, ProviderError, load_providers
from torve.domain.task import Task
from torve.domain.vocabulary import GateAxis

# ----------------------- #

# The runner configuration's own shape version (T-0321).
SCHEMA_VERSION = 1

ADAPTERS = ("fake", "api", "harness", "subscription")

# The derived-cache volume's fixed mount (S-0035/D-4): outside the workspace,
# beside the image's own toolkit paths, so nothing project-visible ever
# reads from it and no attempt can mistake a cache for history. The one
# address a tier's cache volume gets — the slot-suffixed naming is the
# runner's, the toolchain homes are the adapter's, the mount point is this.
CACHE_MOUNT = "/opt/torve/cache"


# ....................... #


class TierConfig(BaseModel):
    """One tier's adapter (S-0004/adapters): `tier` on the task maps to an entry
    here, and the concern leaks no further into the design. The command runs
    *inside* the sandbox (S-0004/D-1) — the engine never links a harness SDK, it
    only shells a line into a container it created.

    `api_key_env` carries names, never values (S-0001/D-13): the runtime forwards the
    variables from its own environment, so the secret never transits a spec.
    `auth_volume` is the subscription route (§2, S-0004/D-2): one volume per worker
    slot, `-<slot>` appended, mounted read-write because token refresh writes.
    """

    model_config = STRICT
    adapter: str = "fake"
    """Which agent adapter this tier runs: fake, api, harness or subscription."""
    model: str = ""
    """The model, recorded in telemetry and named to the image as `TORVE_MODEL`."""
    provider: str = ""
    """The routing identity (§6b); empty only for fake."""

    image: str = ""
    """The tier's sandbox image — harness identity is the image
    (S-0017/configuration-routes-by-nature, S-0017/D-4). Empty falls back to
    runtime.image."""
    api: list[str] = Field(default_factory=list)
    """S-0064/D-4: the API dialects this seat's harness speaks, merged off the manifest. A
    provider serving none of them is refused when the seat resolves, naming both files."""
    route: str = ""
    """S-0064/D-2: what the broker calls this seat's route — provider and dialect together,
    because a provider serving two dialects is two upstreams. Derived, never written."""
    base_url: str = ""
    """S-0064/D-1: where this seat's dialect is served, merged off the provider record. The
    broker's loopback route replaces it at dispatch when one is in force — brokered and
    direct differ in this value and never in whether a variable is there (S-0064/D-8)."""
    key_env: str = ""
    """S-0064/D-9: the variable holding this provider's credential, merged off the record. A
    name, never a value: the runtime forwards it by name and the secret never transits
    torve or the spec."""
    model_id: str = ""
    """S-0064/D-3: what actually reaches the provider. The seat writes a roster key, which
    may be a local shorthand for a slug that is awkward to type; this is what travels."""
    context_window: int = 0
    """S-0064/D-1: the model's window, merged off the roster; 0 is unstated."""
    max_tokens: int = 0
    """S-0064/D-1: the cap on one response, merged off the roster; 0 is unstated."""
    request_timeout_s: float | None = None
    """S-0064/D-1: how long one request may take, merged off the record."""
    stream_idle_timeout_s: float | None = None
    """S-0064/D-1: how long a started stream may go silent, merged off the record."""
    dialect: str = ""
    """S-0064/D-4: which of its harness's dialects this seat reaches, when the harness and
    the provider share more than one. Empty resolves to the single shared dialect, and is
    refused when there is a choice to make — the engine picking would be a decision in the
    one place nobody would look for it."""
    reasoning: str = ""
    """S-0064/D-5, S-0064/D-6: how hard this seat's model should think, as one of the levels
    that model declares. The seat's and never the profile's: a profile is what the agent is
    and must survive being seated on a model that does not reason at all, and a level has to
    validate against the model. Empty leaves the harness's own default in force."""
    auth_volume: str = "torve-auth"
    """The subscription route's volume (§2, S-0004/D-2): one volume per worker slot,
    `-<slot>` appended."""
    auth_mount: str = "/auth"
    """Where the subscription route's auth volume is mounted in the sandbox, read-write
    because token refresh writes (§2, S-0004/D-2)."""

    cache_volume: str = ""
    """S-0035/the-derived-cache-volume, S-0035/D-4: a named tier opts its run's sandboxes
    into the derived-cache volume `cache_volume-<worker_slot>` — slot-suffixed like the
    auth volume, so two concurrent workers never share a cache — mounted read-write at
    the fixed CACHE_MOUNT with the toolchain cache homes pointed at it by the runtime
    adapter. Empty (the default) is cold exactly as today. The volume holds derived state
    only: deleting it may change nothing but wall clock (S-0035/D-1), and shadow replays
    never mount it (S-0035/D-3 — the exclusion is applied where the mount is composed,
    under the runner's `shadow` flag)."""

    retry_variant: str = ""
    """S-0027/D-11: the dotted tier this seat's attempt resolves to after a gate-red — one
    rung, not a chain. Empty means an attempt that gates red is retried under the same
    tier, today's behaviour. S-0034/D-6 keeps this scalar as sugar for the functional key
    of the mapping below."""

    retry_variants: dict[GateAxis, str] = Field(default_factory=dict)
    """S-0034/D-6: the retry rungs keyed by axis — which conviction routes the next attempt
    where. Every reader takes the merged view through `resolved_retry_variants()`, so no
    surface sees only the functional rung; `boundary` may not name a rung here at all
    (S-0034/D-7)."""

    character_routing: dict[str, str] = Field(default_factory=dict)
    """S-0034 S-0034/D-3: character -> variant, resolved once at dispatch
    (`resolve_character_tier`) before anything reads `tier_name_for`. A value is this
    seat's own variant suffix, the same bare shape a contract's `tier_variant` already
    carries (S-0027/D-3) — character routing never reassigns a task to a different seat.
    An unmapped or undeclared character falls through to the seat default."""

    harness: str = ""
    """S-0061/D-2/D-3: the harness manifest this seat is reached through, by name —
    `.torve/harnesses/<name>.yaml`. Resolution happens on the raw mapping in
    `load_runner_config`, before this model validates, so by the time a `TierConfig`
    exists the manifest's fields are already merged onto it; the name is kept so
    `config_hash` and `torve doctor` can both say where a value came from."""

    profile: str = ""
    """S-0061/D-1/D-3: the agent profile this seat runs, by name —
    `.torve/agents/<name>.yaml`, merged the same way and kept for the same reason. Empty
    resolves the profile that declares the task's role (S-0061/D-11, S-0061/D-13)."""

    equipment: list[Equipment] = Field(default_factory=list)
    """S-0062/D-1: everything the seat's profile gives its agent, one item per thing.
    The role's own profile contributes a layer under this one, merged per task
    (S-0062/D-12), because the role varies within a seat."""

    kinds: list[str] = Field(default_factory=list)
    """S-0063/D-4: the equipment kinds this seat's harness accepts, merged off the
    manifest. A kind the profile declares and this does not name is refused when the
    seat resolves; how each reaches the harness is the image's own `equip`."""

    equip_root: str = ""
    """S-0063/D-19: where this seat's harness reads equipment from inside the workspace,
    merged off the manifest. The engine excludes it in the worktree so an attempt commits
    its own work and nothing else."""

    env: dict[str, str] = Field(default_factory=dict)
    """S-0063/D-10: the knobs this seat's image reads, merged off the manifest. Set
    into the sandbox and never interpreted here."""

    prepare: str = ""
    """S-0062/D-7: the command run in the sandbox before the agent, on its own clock —
    a non-zero exit is an infrastructure failure and convicts nothing."""

    skills: list[str] | None = None
    """S-0029/equipment-on-the-tier, S-0029/D-1: the package-data skill names this seat's
    agent materializes, derived from `equipment` (S-0062/D-1) — `None` is a seat whose
    profile named none, which falls through to the profile declaring the task's role
    (S-0061/D-11). Names resolve through the same `materialize` path with the same
    refusals (S-0029/D-2)."""

    prompt_extras: str = ""
    """S-0029/equipment-on-the-tier, S-0029/D-1: the block appended to the built prompt
    after the charter's base working rules, which stay unaddressable from configuration.
    One string since S-0061/A-5."""

    agent_timeout: float | None = None
    """S-0035/the-tier-clock, S-0035/D-6: the tier's own agent attempt clock. `None`
    (absent) falls through to the RuntimeConfig global, exactly as today; a named value —
    even 0 — is the tier's, so the heavy rung and the reviewer seat carry their own clocks
    without the operator editing global configuration mid-incident. Rides the profile merge
    like every other field; read through `agent_timeout_for` where the runner's attempt
    hook takes its timeout."""
    sandbox_timeout: float | None = None
    """S-0035/the-tier-clock, S-0035/D-6: the tier's own sandbox lifecycle clock. `None`
    (absent) falls through to the RuntimeConfig global, exactly as today; a named value —
    even 0 — is the tier's. Rides the profile merge like every other field; read through
    `sandbox_timeout_for` where the review lane takes its timeout."""

    # ....................... #

    def resolved_retry_variants(self) -> dict[GateAxis, str]:
        """The one resolution of S-0027/D-11's scalar and S-0034/D-6's mapping: the
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

        # No command to require any more (S-0063/D-1): the shell that starts a
        # harness is the image's own `/opt/torve/run`, and a seat naming no image
        # runs the runtime's default one — `image_for` answers that, and `torve
        # doctor` reds on an image the runtime cannot resolve. What is left is
        # routing: silence is not a policy (§6b), and a real adapter sends the
        # repository somewhere.
        if self.adapter != "fake" and not self.provider:
            raise ValueError(f"adapter {self.adapter!r} needs a provider for routing (S-0004/D-8)")

        return self

    # ....................... #

    @model_validator(mode="after")
    def _retry_rungs_are_coherent(self) -> TierConfig:
        # S-0034/D-7: no configuration may hang a retry rung on a boundary
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

        # S-0034/D-6: the scalar is sugar for the functional key; both spellings
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
    model_config = STRICT

    allow: list[str] = Field(default_factory=list)
    """The providers this repository's contents may reach; a non-empty list replaces
    `ProvidersConfig.default` for this repository alone (`route_provider`)."""
    deny_reason: str = ""
    """The sentence appended to the ProviderDenied refusal when this repository denies a
    provider, so the error says why and not only that."""


# ....................... #


class ProvidersConfig(BaseModel):
    """Which providers a repository's contents may reach (S-0004/provider-routing-and-data-boundaries):
    repository contents, fixtures and diffs leave the building for whichever
    provider an adapter is pointed at, so the policy is explicit and enforced
    at dispatch, before a sandbox exists (S-0004/D-8)."""

    model_config = STRICT

    default: list[str] = Field(default_factory=list)
    """The providers every repository may reach unless its own entry names another set."""
    repositories: dict[str, RepositoryProviders] = Field(default_factory=dict)
    """Per-repository overrides of the default provider set, keyed by repository."""
    never_send: list[str] = Field(default_factory=list)
    """gitwildmatch globs whose files are withheld from the worktree the agent sees, so
    their contents never leave the building for any provider."""


# ....................... #


class ProviderDenied(ValueError):
    """No permitted provider for this repository and tier — a configuration
    error at dispatch (exit 3), never a quiet fallback (S-0004/D-8)."""


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
    """S-0029 S-0029/D-1/D-29.3: `tier.skills`, when set, overrides the
    role-scoped set wholesale for `role` alone — every other role's set is
    untouched, and the materializer's own resolution and refusals (S-0029/D-2)
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
    """S-0027/D-3's dotted lookup key: `seat.variant` when the contract names a
    `tier_variant`, the seat literal otherwise — naming a variant that does
    not exist is a configuration error `tier_for` raises loudly, never a
    fallback to the seat."""

    return f"{task.tier}.{task.tier_variant}" if task.tier_variant else task.tier


# ....................... #


def resolve_character_tier(config: RunnerConfig, task: Task) -> Task:
    """S-0034 S-0034/D-3, the one resolution point every dispatch surface
    reads: an explicit `tier_variant` always wins, an unmapped or absent
    `character` falls through to the seat default, and a mapped character
    resolves to its variant here — before `tier_name_for` is ever read —
    so `torve run`/`tick`/`fleet`/`shadow`, the broker routing derivation
    and the runner's telemetry all see one already-resolved task."""

    if task.tier_variant or not task.character:
        return task

    variant = tier_for(config, task.tier).character_routing.get(task.character)

    return task.model_copy(update={"tier_variant": variant}) if variant else task


# ....................... #


def broker_in_force(config: RunnerConfig) -> bool:
    """A broker adapter other than `none` is configured — the run's keys are
    the broker's business, not the tier's (S-0021/D-1)."""

    return config.broker.adapter != "none"


# ....................... #


def image_for(config: RunnerConfig, tier: TierConfig) -> str:
    """The tier's image when it names one, else the runtime default — the
    harness's identity is the image it runs in (S-0017/configuration-routes-by-nature)."""

    return tier.image or config.runtime.image


# ....................... #


def agent_timeout_for(config: RunnerConfig, tier: TierConfig) -> float:
    """The resolved tier's agent clock when it names one, else the runtime
    global (S-0035/the-tier-clock, S-0035/D-6). `is None`, not truthiness: an explicit
    `0` is a named value that wins, absence is what falls through."""

    return config.runtime.agent_timeout if tier.agent_timeout is None else tier.agent_timeout


# ....................... #


def sandbox_timeout_for(config: RunnerConfig, tier: TierConfig) -> float:
    """The resolved tier's sandbox lifecycle bound when it names one, else
    the runtime global (S-0035/the-tier-clock, S-0035/D-6)."""

    return config.runtime.sandbox_timeout if tier.sandbox_timeout is None else tier.sandbox_timeout


# ....................... #


def configured_images(config: RunnerConfig) -> list[str]:
    """Every image a run under this configuration could use — the runtime
    default plus each tier's override — for the doctor's existence check."""

    images = {config.runtime.image}
    images.update(tier.image for tier in config.tiers.values() if tier.image)

    return sorted(images)


# ....................... #


class OpenSandboxConfig(BaseModel):
    """The OpenSandbox server the runtime talks to. The advertised broker
    proxy is not a configured key here (extra="forbid" refuses it in the
    yaml): `RunnerConfig` resolves it once at load from `broker.bind` /
    `broker.advertise` and publishes it on this instance — one location for
    the address, the broker block, and one derived read, the runtime's
    proxy-env composition (S-0041/D-6: two adapters, no channel, one fact)."""

    model_config = STRICT

    domain: str = "localhost:5266"
    """The host:port of the OpenSandbox server the runtime adapter builds its client
    against."""
    api_key_env: str = "OPENSANDBOX_API_KEY"
    """The name of the environment variable holding the server's API key — the name, never
    the key itself: configuration is committed, credentials are not."""

    _remote_broker_proxy: str = PrivateAttr(default="")

    # ....................... #

    @property
    def remote_broker_proxy(self) -> str:
        """The URL of the run's broker at its advertised address in remote
        endpoint mode — the address the sandbox's proxy env is composed
        from; empty when the broker derives its address from the Docker
        gateway as before."""

        return self._remote_broker_proxy

    # ....................... #

    def publish_remote_broker_proxy(self, url: str) -> None:
        """The load-time injection point, called by the `RunnerConfig`
        validator; never a channel an operator writes through."""

        self._remote_broker_proxy = url


# ....................... #


def split_host_port(authority: str) -> tuple[str, int | None]:
    """``'host'``, ``'host:port'``, ``'[v6]'`` or ``'[v6]:port'`` to
    ``(host, port)``. Shared by the sealed broker (matching CONNECT
    authorities) and the configuration validator (shaping pass-through
    entries) so both read one format; pass-through entries themselves are
    validated as hostnames or IPv4 addresses — a bracketed IPv6 parses
    here but is refused by the entry validator, which is fine: a named
    destination a machine can match is what matters (S-0021/D-11's answer: the
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


def pass_through_allows(pass_through: list[str] | tuple[str, ...], host: str, port: int) -> bool:
    """Whether the sealed broker may CONNECT to ``host:port`` without
    inspection (S-0021/two-modes-because-custody-and-containment-are-different-problems): a declared entry matches its host on any
    port, and a ``host:port`` entry narrows to exactly that port — the
    declaration is of a *named host*, ports are the destination's business
    (S-0021/D-3)."""

    for entry in pass_through:
        entry_host, entry_port = split_host_port(entry)

        if entry_host.lower() == host.lower() and (entry_port is None or entry_port == port):
            return True

    return False


# ....................... #


def sealed_broker_port(network: str) -> int:
    """The sealed broker's port, derived from the internal network's name
    (S-0003/isolation's rule: when a service needs a port, it derives one). The
    broker binds it and the runtime composes the sandbox's proxy env from
    it — two adapters with no channel between them derive the same number
    from the same configured name (the runtime's network and the broker's
    network are validated equal)."""

    digest = hashlib.sha256(network.encode("utf-8")).digest()

    return 20000 + int.from_bytes(digest[:2], "big") % 40000


# ....................... #


# The bind-all-interfaces address: it listens everywhere and names no one,
# so a remote endpoint whose broker binds it must advertise a real
# destination (an in-sandbox client dialing 0.0.0.0 dials itself).
WILDCARD_BIND_HOST = "0.0.0.0"


def remote_broker_proxy(broker: BrokerConfig) -> str:
    """The broker's advertised URL in remote endpoint mode (S-0041/D-6): the
    `broker.advertise` host — falling back to `broker.bind`'s — and the
    advertised port, falling back to bind's. This replaces the
    Docker-gateway derivation for runs whose sandboxes are elsewhere: the
    broker binds this address and the opensandbox runtime composes the
    sandbox's proxy env from it — the sealed doctrine with the configured
    address standing in for the two network facts (the adapters share this
    function, never a channel). Empty string is no remote endpoint: an
    unconfigured bind keeps today's loopback/bridge behaviour, and a bind
    without a usable port is a configuration the validators never admit,
    answered here as no remote endpoint rather than a fabricated address.
    Transport is http by configuration: whether the wire between sandbox
    and broker crosses a trusted network is the operator's deployment
    choice."""

    if broker.mode != "endpoint" or not broker.bind:
        return ""

    host, port = split_host_port(broker.advertise or broker.bind)

    if port is None:
        port = split_host_port(broker.bind)[1]

    if port is None or not host:
        return ""

    return f"http://{host}:{port}"


# ....................... #


def _validate_host_port_shape(value: str, label: str) -> tuple[str, int | None]:
    """A named address — ``host``, optionally ``:port`` — never a URL, a
    pattern or a wildcard, and never with surrounding whitespace."""

    if not value or value != value.strip():
        raise ValueError(
            f"{label} must be a host, optionally :port — "
            "no scheme, path, wildcard or surrounding whitespace"
        )

    if any(char in value for char in ("/", "\\", "?", "#", "*", " ")):
        raise ValueError(
            f"{label} must be a host, optionally :port — "
            "no scheme, path, wildcard or surrounding whitespace"
        )

    host, port = split_host_port(value)

    if not host or not all(char.isalnum() or char in ".-_" for char in host):
        raise ValueError(f"{label} names no valid host")

    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label}: port out of range")

    return host, port


# ....................... #


def _validate_pass_through_entry(entry: str) -> None:
    """A pass-through entry is a host, optionally :port — a named
    destination, never a URL, a pattern or a wildcard (S-0021/D-3: every other
    destination is declared, and a declaration a machine cannot match is
    not a declaration)."""

    _validate_host_port_shape(entry, f"broker pass_through entry {entry!r}")


# ....................... #


class BrokerProvider(BaseModel):
    """One routed provider's wire facts (S-0021/two-modes-because-custody-and-containment-are-different-problems): where the broker
    forwards and which environment variable in the broker's own environment
    holds the key — a name, never a value. The configuration names the
    credential once; a brokered tier names none (S-0021/D-1).

    Derived since S-0064/D-1, never written: the provider record under
    `.torve/providers/` holds these three values beside the roster and the
    clocks, and `load_runner_config` projects them here for the broker, which
    needs exactly this much and nothing else."""

    model_config = STRICT

    upstream: str = ""
    """The provider's real base URL (http:// or https://)."""
    via_proxy: bool = False
    """S-0021/A-1: the broker's own upstream leg tunnels through the host's https_proxy for
    this provider — for upstreams unreachable from the host directly (region gating). The
    sandbox never sees a proxy either way; this is the broker's egress, not the run's."""
    key_env: str = ""
    """The env var name the broker reads the key from."""

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


class NotifyConfig(BaseModel):
    """Where an interrupt-class escalation is delivered (S-0051).

    `none` by default and explicitly (S-0051/D-4): a repository that has not
    chosen a destination sends nothing because somebody decided that, the
    same shape the broker's `none` adapter takes.

    The URL is the destination itself, not a credential — a webhook URL is
    a bearer secret in practice, so it names an environment variable rather
    than carrying a value into a committed file (S-0001/D-13).
    """

    model_config = STRICT

    adapter: str = "none"
    """Which notify adapter is in force: none or webhook."""
    url_env: str = "TORVE_NOTIFY_URL"
    """The name of the environment variable holding the webhook URL the relay posts to —
    the URL is a bearer secret in practice, so it is named, never carried here
    (S-0001/D-13)."""
    attempts: int = 5
    """How many deliveries one escalation earns before the relay parks it (S-0051/D-7). A
    queue that retries forever is a queue that never drains."""
    timeout_s: float = 10.0
    """How long one delivery attempt may take, in seconds."""


# ....................... #


class BrokerConfig(BaseModel):
    """The egress broker (S-0021/the-port): which adapter is in force and what
    it is fed. `none` is today's behaviour named explicitly — keys pass
    through, no metering, no wire routing — and stays the phase-1 default;
    `torve doctor` names it and says plainly that it leaves S-0001/D-13 unmet
    (S-0021/D-9). Under any other adapter a brokered tier names no credential
    (S-0021/D-1): the broker's provider table is the one channel.

    The two modes are S-0021/D-3's split: `endpoint` (the phase-1 default)
    closes custody and metering on the daemon's default bridge; `sealed`
    adds containment — the sandbox joins the user-defined internal Docker
    network named here, whose only host-side address is the broker, and
    every non-provider destination the run needs is declared under
    `pass_through` and CONNECTed without inspection. The same network is
    named in `runtime.network` — egress policy (this block) and sandbox
    provisioning (`runtime`) are two views of one fact, and the runner
    validator refuses them to disagree (S-0021/D-11).

    An endpoint whose sandboxes are remote — an OpenSandbox server on
    another machine — configures `bind` and, across a NAT or a hostname
    split, `advertise`: the loopback/bridge derivation reaches nobody out
    there, and the remote broker answers non-provider requests only with
    a refusal naming the destination — the pass-through leg is a
    sealed-mode mechanism, authenticated by a topology a remote run does
    not have."""

    model_config = STRICT

    adapter: Literal["none", "local", "opensandbox"] = "none"
    """Which broker adapter is in force: `none` is today's behaviour named explicitly —
    keys pass through, no metering, no wire routing (S-0021/D-9)."""
    mode: Literal["endpoint", "sealed"] = "endpoint"
    """S-0021/D-3's split: `endpoint` closes custody and metering on the daemon's default
    bridge; `sealed` adds containment on an internal Docker network whose only host-side
    address is the broker."""
    bind: str = ""
    """Remote endpoint mode (S-0041/D-6): the host:port the broker thread listens on
    instead of the loopback/bridge-gateway derivation — a port is mandatory, because the
    sandbox-side composition of this address happens with no channel to the broker and
    cannot learn an ephemeral one. Endpoint-only: sealed mode's address is the internal
    network's gateway and the name-derived port, a topology, not a configured address. The
    wire is plaintext http — prompts and diffs cross it exposed unless TLS or a private
    network is the operator's deployment; the engine ships plaintext-capable and says so
    plainly rather than pretend-default."""
    advertise: str = ""
    """Remote endpoint mode (S-0041/D-6): the host[:port] sandboxes are pointed at, for the
    NAT/hostname split; its port falls back to bind's, and an unset advertise speaks bind
    verbatim. Endpoint-only, like `bind`."""
    network: str = ""
    """The user-defined --internal Docker network sealed mode joins; the broker attaches to
    it at its gateway, the sandboxes join it, and nothing on it is reachable except the
    broker (S-0021/D-3). Empty in endpoint mode; must equal runtime.network when
    sealed."""
    providers: dict[str, BrokerProvider] = Field(default_factory=dict)
    """provider -> wire facts; the run's routing is a dispatch-checked subset (S-0021/D-4:
    the broker exposes one loopback route per routed provider). Projected from the
    provider records at load and not written in the configuration (S-0064/D-1)."""
    pass_through: list[str] = Field(default_factory=list)
    """Sealed mode only: named hosts the broker will CONNECT to without inspection — a
    package index, the forge. An undeclared destination is refused loudly, and the run
    fails rather than succeed through a path nobody meant to leave open (S-0021/D-3)."""
    cost_tolerance: float = 0.25
    """A broker-measured cost that diverges from the adapter's self-report by more than
    this fraction is an engine event (S-0021/D-5)."""

    # ....................... #

    @model_validator(mode="after")
    def _this_phase_supports_only_what_is_built(self) -> BrokerConfig:
        if self.adapter == "opensandbox":
            raise ValueError(
                "broker adapter 'opensandbox' is not built — it is condition-gated "
                "on a live server and arrives as an adapter, never a prerequisite (S-0021/D-2)"
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
                    "(S-0021/D-4's wire enforcement would be bypassable)"
                )

            if self.bind or self.advertise:
                raise ValueError(
                    "sealed mode's broker address is the internal network's gateway at "
                    "a name-derived port — broker.bind and broker.advertise are "
                    "endpoint-mode knobs; a configured address would have a sealed run "
                    "pretending a topology it does not have"
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

            if self.bind:
                if self.adapter == "none":
                    raise ValueError(
                        "broker.bind is the address the broker thread listens on; "
                        "adapter 'none' has no thread to bind"
                    )

                bind_host, bind_port = _validate_host_port_shape(
                    self.bind, f"broker.bind {self.bind!r}"
                )

                if bind_port is None:
                    raise ValueError(
                        f"broker.bind {self.bind!r} names no port — a sandbox learns "
                        "this address with no channel to the broker, so the port must "
                        "be configured, never ephemeral; set broker.bind to host:port"
                    )

                if bind_host == WILDCARD_BIND_HOST and not self.advertise:
                    raise ValueError(
                        "broker.bind '0.0.0.0' listens on every interface but names no "
                        "reachable destination — an in-sandbox client dialing it dials "
                        "itself; set broker.advertise to the address sandboxes reach"
                    )

            if self.advertise:
                if not self.bind:
                    raise ValueError(
                        "broker.advertise publishes where broker.bind listens — set "
                        "broker.bind too; advertising an address nothing binds is a "
                        "destination to nowhere"
                    )

                _validate_host_port_shape(self.advertise, f"broker.advertise {self.advertise!r}")

                # The bind guard above exists because an in-sandbox client
                # dialing 0.0.0.0 dials itself. Advertising the wildcard
                # publishes exactly that address to every provider route,
                # so copying bind into advertise walked straight past the
                # refusal it satisfies (T-0277).
                if split_host_port(self.advertise)[0] == WILDCARD_BIND_HOST:
                    raise ValueError(
                        "broker.advertise '0.0.0.0' is the address sandboxes cannot "
                        "reach — it is where the broker listens, not somewhere to "
                        "dial; name the host or IP a sandbox reaches the broker on"
                    )

        return self


# ....................... #


class RuntimeConfig(BaseModel):
    model_config = STRICT

    adapter: str = "docker"
    """Which runtime adapter creates the run's sandboxes: docker or opensandbox."""
    image: str = "python:3.13-slim"
    """The sandbox image every tier that names none of its own runs in (`image_for`)."""
    sandbox_timeout: float = 1800
    """Platform-enforced lifecycle bound, seconds; a tier may name its own
    (S-0035/D-6)."""
    agent_timeout: float = 1200
    """Hard cap per agent attempt, on top of cooperative asks; a tier may name its own
    (S-0035/D-6). Strictly inside `sandbox_timeout`: the sandbox is the outer bound, so an
    agent clock at or past it can never fire and the attempt is booked as an infrastructure
    failure rather than as an agent that ran out of time."""

    @model_validator(mode="after")
    def _the_agent_clock_fits_inside_the_sandbox(self) -> RuntimeConfig:
        """An agent clock the sandbox outlives is a clock, and one the sandbox
        does not is a decoration.

        Measured: `agent_timeout` was raised to 2200 while `sandbox_timeout`
        stayed at 1800, and the next long attempt died at 1800.2s as
        `agent_error` — an infrastructure failure where the truth was a slow
        model. The two numbers are not independent and the engine should not
        pretend they are.
        """

        if self.agent_timeout >= self.sandbox_timeout:
            raise ValueError(
                f"agent_timeout {self.agent_timeout} is not inside sandbox_timeout "
                f"{self.sandbox_timeout} — the sandbox is the outer bound, so an agent "
                "clock at or past it never fires and its attempt is booked as an "
                "infrastructure failure rather than as an agent that ran out of time"
            )

        return self

    network: str = ""
    """Docker network mode ("" = the daemon's default bridge). "host" shares the host's
    network stack, which is what lets a sandbox reach a proxy or VPN listening on the
    host's loopback — the operator trades network isolation for the host's egress path,
    knowingly. Docker-only; the OpenSandbox server owns its own egress model
    (S-0003/runtime)."""
    docker: Literal["", "socket"] = ""
    """Docker inside the sandbox (S-0017/docker-inside-the-sandbox, S-0017/D-9). "socket"
    mounts the host daemon's socket into every sandbox of the run — attempt and gates
    alike — and the image supplies the docker CLI. Host-equivalent capability, granted
    knowingly per repository (S-0017/D-10): a container started over the host socket can
    mount any host path. The nested daemon is the named, deferred stronger mode.
    OpenSandbox refuses any value here."""
    opensandbox: OpenSandboxConfig = Field(default_factory=OpenSandboxConfig)
    """The OpenSandbox server the runtime talks to when the adapter is opensandbox."""


# ....................... #


class StoreConfig(BaseModel):
    """The durable run store (S-0001/D-14, S-0001/D-15): mock for tests and simulation,
    Postgres for real runs (S-0003/D-6). The mock is in-process, so cross-process
    guarantees — a reaper seeing another runner's leases — need Postgres.

    `dsn_env` names the environment variable holding the DSN; the value never
    enters a committed file."""

    model_config = STRICT

    adapter: str = "mock"
    """Which store adapter backs the durable run: mock or postgres."""
    dsn_env: str = "TORVE_PG_DSN"
    """The name of the environment variable holding the DSN; the value never enters a
    committed file."""
    schema_name: str = "public"
    """The Postgres schema the run relation lives in."""
    run_relation: str = "torve_durable_run"
    """The Postgres table the durable run rows are written to."""
    lease_for: float = 60.0
    """Lease duration; cancel asks ride back on renewal."""
    heartbeat_divisor: int = 3
    """How many heartbeats fit in one lease — the renewal interval is `lease_for` divided
    by this."""
    max_run_duration: float = 7200.0
    """Hard cap on one durable body."""


# ....................... #


# The two skills this engine ships, against the roles that would load them — a
# sample the suite builds a `SkillsConfig` from, and nothing a repository gets.
# `torve init` mints no profile any more (S-0062/A-6): equipment is what a
# repository asked for, never what the engine assumed.
#
# ponytail: lives here because four test modules import it; it belongs in
# tests/conftest.py, and moves the next time those files are in scope.
ROLE_SKILLS: dict[str, list[str]] = {
    "implement": ["flag-dont-flip", "ratchet-what-you-build"],
    "review": ["ratchet-what-you-build"],
    "revert": ["flag-dont-flip"],
}


# ....................... #


class SkillsConfig(BaseModel):
    """Role-scoped skill sets (S-0009/trigger-collision-is-the-real-cost, S-0009/D-1)
    materialized into the sandbox from package data at dispatch (S-0009/A-1, S-0009/D-7).

    Nobody writes this any more (S-0061/D-11): the set a role loads is the profile
    named for that role, `.torve/agents/<role>.yaml`, and the loader fills this in from
    those files so every reader keeps the shape it had. A default set and a named set
    were two mechanisms answering one question in two files.
    """

    model_config = STRICT

    sets: dict[str, list[str]] = Field(default_factory=dict)
    """The skill names each dispatchable role loads, keyed by role — resolved from the
    role profiles, never written here. A seat whose own profile names skills overrides
    its role's entry wholesale (`effective_skill_sets`)."""


# ....................... #


class SpecsConfig(BaseModel):
    """Where the specification corpus lives (0013 S-0013/A-1, S-0013/D-7; S-0057
    S-0057/D-3): one path, never a list or a glob — numbering is continuous
    across a corpus, and two roots mean two counters and a colliding
    identifier at the first merge (S-0016/D-23). The archive and the schemas are
    its siblings. Read from the runner's configuration per S-0013/D-3, never
    from the repository under work."""

    model_config = STRICT

    path: str = layout.SPECS_DIR
    """The one path the specification corpus lives at, relative to the repository root —
    never a list or a glob (S-0013/A-1, S-0013/D-7, S-0057/D-3)."""


# ....................... #


class ReapConfig(BaseModel):
    model_config = STRICT

    stale_after: float = 600
    """A non-terminal state with a heartbeat older than this, in seconds, is orphaned."""


# ....................... #


class TracesConfig(BaseModel):
    """Retention bounds for the durable trace store (S-0039/retention, S-0039/D-3):
    the reaper's pass sheds traces oldest-first past either bound. Both
    knobs are operator's — trace volume is a property of the fleet, not the
    engine — and the defaults are the drafting values, pending the owner's
    read of dogfood volume. A bound set to zero is kept literal, not taken
    as "disabled": zero days or zero megabytes means keep nothing, and the
    pass reports every trace it shed."""

    model_config = STRICT

    keep_days: int = 30
    """The age bound: the reaper's pass sheds traces older than this many days,
    oldest-first. Zero is kept literal — keep nothing — not taken as "disabled"."""
    max_mb: int = 512
    """The size bound: the reaper's pass sheds traces oldest-first until the store fits in
    this many megabytes. Zero is kept literal — keep nothing — not taken as
    "disabled"."""


# ....................... #


class VcsConfig(BaseModel):
    """Local git at the runner boundary (S-0010/signing). The signing key is a
    path to an SSH private key the RUNNER holds — it is never mounted into a
    sandbox, and the signature attests "Torve produced this under its task",
    never that a human reviewed it. Unset means unsigned."""

    model_config = STRICT

    signing_key: str | None = None
    """The path to an SSH private key the runner holds and signs its commits with; unset
    means unsigned (S-0010/signing)."""


# ....................... #


class ScmConfig(BaseModel):
    """The remote forge (S-0010/two-ports-deliberately-separate). The credential is named, never held:
    `token_env` is the NAME of the environment variable the runner reads at
    push/PR time (S-0001/D-13) — the value stays in the runner's process and no
    sandbox ever sees it. `repo` is owner/name on the forge."""

    model_config = STRICT

    open_pr: bool = False
    """Whether a landed attempt opens a pull request on the forge; flip per repository once
    a remote exists."""
    repo: str | None = None
    """owner/name on the forge — the repository pushes, pull requests and the CI check read
    against."""
    token_env: str | None = None
    """The NAME of the environment variable the runner reads at push/PR time (S-0001/D-13);
    the value stays in the runner's process and no sandbox ever sees it."""


# ....................... #


class ReviewConfig(BaseModel):
    """Review triggers (S-0005/triggers). Off by default — a blocker stopping
    the run is configuration deciding a consequence (S-0001/D-10), and configuring
    nothing decides nothing. `task_gated` is board-driven; the pull-request
    triggers admit `torve review pr` as the forge's event delivery.
    `skip_authors` is §4's author skip rule; draft and zero-changed-files
    pull requests always skip. `blocker_revisions` (S-0043, S-0001/D-10's framing:
    configuration decides the consequence, never the model) bounds how many
    in-run attempts a surviving blocker earns before escalating
    `blocker_finding`; 0 escalates on the first surviving blocker, today's
    behavior exactly."""

    model_config = STRICT
    on: list[str] = Field(default_factory=list)
    """The review triggers in force (S-0005/triggers), from `task_gated`, `pr_opened` and
    `pr_synchronized`. Empty is off: configuring nothing decides nothing."""
    skip_authors: list[str] = Field(default_factory=list)
    """§4's author skip rule: pull requests by these authors are not reviewed; draft and
    zero-changed-files pull requests always skip."""

    blocker_revisions: int = 1
    """The blocker revision budget (S-0043 S-0043/D-1/D-43.3): in-run attempts, same
    worktree, a surviving blocker spends before escalating. Counted per run, spent only by
    surviving blockers — a clean review costs nothing against it. 0 escalates on the first
    surviving blocker, today's behavior exactly."""

    blocks_at: Literal["blocker", "major"] = "major"
    """The severity that stops a promotion. Three consecutive reviews of this engine's own
    work found a real defect apiece — a leg that shipped the next phase's deliverable, a
    leg whose construction took the manager down, a decode error that abandoned every
    candidate in a lane pass — and graded all three `major`, so all three promoted. The
    reviewer uses `major` for "this must be fixed" and reserves `blocker` for something
    that has not once occurred in this repository. The grade is the reviewer's reading and
    stays what it recorded; what stops a promotion is configuration's to decide
    (S-0001/D-10), which is what this is."""

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
    """Landing policy (S-0006/promotion). The operator's `torve merge` is always
    the recorded approval; `auto_merge` is the opt-in that lets a manager
    pass invoke the same lane on the same terms — it arms the pass's landing
    leg, and every refusal below still applies to it unchanged.

    `require_ci` is §3's `ci: green_on_current_head` requirement: the lane
    refuses to land a candidate whose branch tip is not green on the
    configured remote's CI (`scm.repo`), polled with backoff against the
    lightweight runs endpoint (§1). A rebased tree is additionally judged
    by the local battery re-run — the remote verdict covers the tip the
    remote actually saw."""

    model_config = STRICT
    auto_merge: bool = False
    """S-0052/D-2: S-0006/D-2's opt-in, restored with its original default of false. Off, a
    manager pass never lands and behaves exactly as it did before the landing leg existed —
    landing stays a human act. On, the pass runs the same `process_lane` `torve merge`
    runs, with the same arguments, so every refusal below refuses a pass's leg exactly as
    it refuses the manual verb."""
    require_ci: bool = False
    """§3's `ci: green_on_current_head` requirement: the lane refuses to land a candidate
    whose branch tip is not green on the configured remote's CI (`scm.repo`)."""
    require_review: bool = False
    """§3's review criterion (S-0006/D-14, S-0006/A-3): the lane lands only a candidate
    whose producing run recorded a concluded review (`reviewed_by` on the run state) — the
    unconfigured-review bridge never satisfies it."""
    approvals: int = 0
    """§3's approvals requirement (T-0060): the lane lands only a candidate with this many
    recorded approvals of its CURRENT branch tip — an approval that predates the last push
    approves nothing (S-0006/D-3). Zero requires none: configuring nothing decides
    nothing."""
    quiet_window: int = 0
    """§3's quiet window, in seconds: a landing whose branch tip is younger than this
    refuses — pushing resets the window. Zero disables it."""


# ....................... #


class LoopConfig(BaseModel):
    """The manager pass's knobs. Named for the standing loop that is gone
    (S-0019/A-8); the keys stay because a configuration file that has to be
    rewritten to keep working is a worse cost than a section named after
    its ancestor."""

    model_config = STRICT

    pause_escalations: int = 1
    """A pass mints nothing while this root's escalation queue holds this many, counting
    both carriers (S-0048/D-5). The queue may drain during a pause; it may not grow."""
    tick_budget: int = 3600
    """Seconds; an adoption lock older than this is stale and broken loudly."""
    standing_max_per_tick: int = 1
    """S-0023 S-0023/D-6: instantiations across every standing job in one pass, so spend
    per unit time stays cadence times a known bound."""


# ....................... #


class IntakeConfig(BaseModel):
    """The drafting run's knobs (S-0020). `max_drafts` is S-0020/D-8's
    decomposition ceiling — how many contracts one request may yield;
    `iterations` bounds the draft-lint loop like any attempt budget.
    `document_threshold` is the document-threshold rule's starting point —
    the number of distinct documents whose settled ground a scope must
    cross before the work needs one of its own (S-0030 S-0030/D-3), a
    calibration knob rather than a truth."""

    model_config = STRICT

    max_drafts: int = 4
    """S-0020/D-8's decomposition ceiling — how many contracts one request may yield."""
    iterations: int = 3
    """The draft-lint loop's budget, bounded like any attempt budget."""
    document_threshold: int = 2
    """The document-threshold rule's starting point — the number of distinct documents
    whose settled ground a scope must cross before the work needs one of its own (S-0030
    S-0030/D-3), a calibration knob rather than a truth."""


# ....................... #


# ....................... #
# The seat's four refusals (S-0064/D-4, S-0064/D-5). Free functions rather than
# methods: they are about a seat and a record together, and neither owns the pair.


def _where(seat: str) -> str:
    return f"tier {seat!r}"


def merge_records(tiers: dict[str, Any], records: dict[str, Provider]) -> None:
    """Fold each seat's provider record onto the seat, in place (S-0064/D-1).

    The same shape the harness manifest and the agent profile already use: a
    reader receives one object carrying every field, because three objects at
    every call site would be the split leaking out of the files it belongs in.
    Run after `resolve_seats` so a seat cannot write any of these itself — they
    are the record's, and a seat that could restate one could disagree with it.
    """

    for entry in tiers.values():
        if not isinstance(entry, dict):
            continue

        record = records.get(str(entry.get("provider") or ""))

        if record is None:
            continue

        model = record.models.get(str(entry.get("model") or ""))
        dialect = str(entry.get("dialect") or "")
        route = record.routes.get(dialect) if dialect else None

        if route is None and len(record.routes) == 1:
            (route,) = record.routes.values()

        entry.update(
            {
                "route": route_name(str(entry.get("provider") or ""), dialect),
                "key_env": record.key_env,
                "request_timeout_s": record.request_timeout_s,
                "stream_idle_timeout_s": record.stream_idle_timeout_s,
            }
        )

        if route is not None:
            entry["base_url"] = route.base_url

        if model is not None:
            entry.update(
                {
                    "model_id": model.id or str(entry.get("model") or ""),
                    "context_window": model.context_window,
                    "max_tokens": model.max_tokens,
                }
            )


# ....................... #


def credential_names(config: RunnerConfig, tier: TierConfig) -> tuple[str, ...]:
    """The environment variables a sandbox on this seat is given (S-0064/D-9).

    A credential is a property of the provider, so the name comes off the
    record rather than off the harness that dials it. Under a broker it comes
    off nothing: the run-scoped token is the only credential that reaches a
    sandbox, and the leak the old `api_key_env` refusal guarded is closed by
    there being no second place to name one.
    """

    if tier.adapter == "fake" or not tier.provider or config.broker.adapter != "none":
        return ()

    record = config.provider_records.get(tier.provider)

    return (record.key_env,) if record is not None and record.key_env else ()


# ....................... #


def _dialect(seat: str, tier: TierConfig, record: Provider) -> None:
    """The harness and the provider must share a dialect (S-0064/D-4)."""

    if not tier.api:
        raise ValueError(
            f"{_where(seat)} names harness dialects nowhere — its manifest owes `api`, "
            f"one or more of {', '.join(APIS)}, naming what the harness speaks"
        )

    shared = sorted(set(tier.api) & set(record.routes))

    if not shared:
        raise ValueError(
            f"{_where(seat)}: its harness speaks {', '.join(sorted(tier.api))} and provider "
            f"{tier.provider!r} serves {', '.join(sorted(record.routes))} — a seat whose "
            "harness cannot speak to its provider would dial a route nothing answers on. "
            f"Either the manifest names another dialect or .torve/providers/{tier.provider}"
            ".yaml serves one"
        )


def _model(seat: str, tier: TierConfig, record: Provider) -> None:
    """The roster is what a seat may reach, and the effort is what the model has
    (S-0064/D-5). A model the record does not list is refused rather than passed
    through: an undeclared model reaches the provider under whatever name was
    typed, and the regime hash records a model nobody wrote down."""

    entry = record.models.get(tier.model)

    if entry is None:
        listed = ", ".join(sorted(record.models)) or "no model at all"
        raise ValueError(
            f"{_where(seat)} names model {tier.model!r}, and provider {tier.provider!r} "
            f"declares {listed} — a seat reaches a model its provider's record lists, "
            f"never one it does not. Add it to .torve/providers/{tier.provider}.yaml or "
            "name one that is there"
        )

    if not tier.reasoning:
        return

    if not entry.reasoning:
        raise ValueError(
            f"{_where(seat)} asks reasoning {tier.reasoning!r} and model {tier.model!r} "
            "declares no reasoning levels — a model that does not reason cannot be asked "
            "to think harder"
        )

    if tier.reasoning not in entry.reasoning:
        raise ValueError(
            f"{_where(seat)} asks reasoning {tier.reasoning!r} and model {tier.model!r} "
            f"has {', '.join(entry.reasoning)} — the endpoint would refuse this per "
            "request, after a sandbox exists; this refuses it before one does"
        )


# ....................... #


class RunnerConfig(BaseModel):
    model_config = STRICT

    schema_version: int = SCHEMA_VERSION
    """The engine's shape version this configuration is read under."""
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    """The sandbox runtime: which adapter creates sandboxes, in what image, under what
    clocks."""
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    """Review triggers and what a review's findings cost (S-0005/triggers)."""
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)
    """Landing policy: what a candidate must satisfy before the lane lands it
    (S-0006/promotion)."""
    store: StoreConfig = Field(default_factory=StoreConfig)
    """The durable run store (S-0001/D-14, S-0001/D-15)."""
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    """The role-scoped skill sets materialized into the sandbox at dispatch
    (S-0009/D-1)."""
    poison_ceiling: int = 3
    """How many times a task may be dispatched before it is escalated; checked before
    dispatch, and a reached ceiling escalates, never retries."""
    base: str | None = None
    """The base ref for worktrees; None means the first of origin/main and main that
    exists."""
    reap: ReapConfig = Field(default_factory=ReapConfig)
    """When the reaper treats a non-terminal run as orphaned."""
    traces: TracesConfig = Field(default_factory=TracesConfig)
    """Retention bounds for the durable trace store (S-0039/retention, S-0039/D-3)."""
    vcs: VcsConfig = Field(default_factory=VcsConfig)
    """Local git at the runner boundary — the commit signing key (S-0010/signing)."""
    scm: ScmConfig = Field(default_factory=ScmConfig)
    """The remote forge: the repository, the named credential and whether attempts open
    pull requests (S-0010/two-ports-deliberately-separate)."""
    specs: SpecsConfig = Field(default_factory=SpecsConfig)
    """Where the specification corpus lives (S-0013/D-7, S-0057/D-3)."""

    @model_validator(mode="before")
    @classmethod
    def _specs_not_rfcs(cls, data: Any) -> Any:
        # S-0057/D-3: the old key is refused naming the new one, never mapped.
        if isinstance(data, dict) and "rfcs" in data:
            raise ValueError("`rfcs` is `specs` since S-0057 (S-0057/D-3): rename the key")

        return data

    tiers: dict[str, TierConfig] = Field(default_factory=_default_tiers)
    """The adapter mapping a task's `tier` resolves against (S-0004/adapters), keyed by the
    seat literal or the dotted `seat.variant` (S-0027/D-3)."""
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    """Which providers a repository's contents may reach, enforced at dispatch
    (S-0004/D-8)."""
    provider_records: dict[str, Provider] = Field(default_factory=dict)
    """Every provider record under `.torve/providers/`, resolved at load and never written
    in this file (S-0064/D-1) — what one credential buys: the clocks, the routes and the
    model roster. `providers` above is the policy over these; this is the facts."""
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    """The egress broker: which adapter is in force and what it is fed (S-0021/the-port)."""
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    """Where an interrupt-class escalation is delivered (S-0051)."""
    loop: LoopConfig = Field(default_factory=LoopConfig)
    """The manager pass's knobs (S-0019/A-8)."""
    intake: IntakeConfig = Field(default_factory=IntakeConfig)
    """The drafting run's knobs (S-0020)."""
    worker_slot: int = 0
    """Names this worker's auth volume (S-0004/D-2); slots are stable, tasks are not."""

    unmeasured_images: Literal["refuse", "allow"] = "refuse"
    """Whether a tier may dispatch under an image no paired replay verdict has measured
    (S-0027 S-0027/D-7, as amended). `refuse` is the rule as written: a definition edit
    must not quietly change what a seat runs under. `allow` is for a repository rebuilding
    its own engine, where the images change faster than verdicts can be recorded — the
    dispatch still records the unmeasured digest as an engine event, so the reading stays
    honest and the measurement stays owed."""

    # ....................... #

    @model_validator(mode="after")
    def _seats_resolve_against_the_roster(self) -> RunnerConfig:
        """Every seat reaches a model its provider declares, over a dialect its
        harness speaks, at an effort that model has (S-0064/D-4, S-0064/D-5).

        Four failures that were discovered by an attempt become configuration
        errors named before a sandbox exists. Each names both files, because
        which of them is wrong is the reader's call: the provider offered
        something, the manifest says it cannot take it, and either could be the
        one to change.

        `api_key_env` no longer exists to refuse (S-0064/D-9), and the leak it
        guarded is closed structurally rather than by a check: a credential is
        a property of the provider now, so there is no second place for a seat
        to name one.
        """

        for name, tier in sorted(self.tiers.items()):
            if tier.adapter == "fake" or not tier.provider:
                continue

            record = self.provider_records.get(tier.provider)

            if record is None:
                continue  # the routing check already refuses this, with better words

            _dialect(name, tier, record)
            _model(name, tier, record)

        return self

    # ....................... #

    @model_validator(mode="after")
    def _retry_variant_names_a_configured_tier(self) -> RunnerConfig:
        """S-0027/D-11: a rung to nowhere is a configuration error at load time,
        not a dispatch-time surprise after the first gate-red. S-0034/D-6's
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
    def _character_routing_names_a_configured_tier(self) -> RunnerConfig:
        """S-0034/D-3: a character routed to a variant that is not configured is
        a load-time refusal, the same rung `retry_variant` already stands
        on — never a dispatch-time surprise."""

        offenders: set[tuple[str, str, str]] = set()

        for name, tier in self.tiers.items():
            for character, variant in tier.character_routing.items():
                target = f"{name}.{variant}"

                if target not in self.tiers:
                    offenders.add((name, character, target))

        if offenders:
            named = ", ".join(
                f"{name!r} character {character!r} -> {target!r}"
                for name, character, target in sorted(offenders)
            )

            raise ValueError(f"character_routing names no configured tier: {named}")

        return self

    # ....................... #

    @model_validator(mode="after")
    def _sealed_runtime_holds_the_network(self) -> RunnerConfig:
        """S-0021/D-3's containment is a property of the run's wiring, so the
        two halves must agree: the sandbox joins the internal network the
        broker attaches to (S-0021/D-11 — egress policy in the broker block,
        sandbox provisioning in runtime, one fact). Sealed mode also needs
        the one runtime that has user-defined internal networks, and
        refuses the host daemon socket — a socket is host-equivalent
        capability (S-0017/D-10), which is exactly the trust sealed mode is
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
                "(S-0017/D-10), the exact trust sealed containment exists to remove"
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

    @model_validator(mode="after")
    def _remote_broker_shares_its_advertised_address(self) -> RunnerConfig:
        """The one resolution point for remote endpoint mode: the address
        `broker.bind`/`broker.advertise` name is decided once here and
        published on the opensandbox config the composition hands the
        runtime, which composes the sandbox's proxy env from it — the
        broker thread and the runtime never talk, they read the one
        resolved fact (the sealed name-derived-port doctrine with a
        configured address in place of the gateway)."""

        self.runtime.opensandbox.publish_remote_broker_proxy(remote_broker_proxy(self.broker))

        return self


# ....................... #


def route_name(provider: str, dialect: str) -> str:
    """What the broker calls one route (S-0064/D-2).

    Provider and dialect together, because a provider may serve two and they are
    different upstreams that answer differently. It was the provider alone while
    every record served one dialect; the first seat to want the other found the
    refusal this replaces.
    """

    return f"{provider}.{dialect}" if dialect else provider


def _resolve_dialects(
    tiers: dict[str, Any], manifests: dict[str, list[str]], records: dict[str, Provider]
) -> None:
    """Write each seat's resolved dialect onto it, in place.

    A seat may name one; a seat that does not gets the single dialect its
    harness and its provider share. Resolved here rather than left implied,
    because everything downstream — the broker's route, the seam's `TORVE_API`,
    the doctor's line — needs the answer and none of them should re-derive it.
    Where the choice is real and unmade this leaves it empty, and the seat's own
    refusal says so in better words than a guess would.
    """

    for entry in tiers.values():
        if not isinstance(entry, dict):
            continue

        record = records.get(str(entry.get("provider") or ""))
        speaks = manifests.get(str(entry.get("harness") or ""), [])

        if record is None or not speaks or entry.get("dialect"):
            continue

        shared = sorted(set(speaks) & set(record.routes))

        if len(shared) == 1:
            entry["dialect"] = shared[0]


def _wire_facts(record: Provider, dialect: str) -> dict[str, Any]:
    """One route of one provider as the broker's three wire facts (S-0064/D-1).

    The broker forwards; it needs one upstream per route and nothing about the
    roster. A record serving two dialects is two entries on one credential,
    because they are two upstreams that answer differently — the same reason a
    route owns its compat facts rather than the provider.
    """

    route = record.routes[dialect]

    return {"upstream": route.base_url, "key_env": record.key_env, "via_proxy": record.via_proxy}


# ....................... #


def load_runner_config(root: Path, path: Path | None = None) -> RunnerConfig:
    """Explicit `path` is a flag-level override (S-0013/D-4); otherwise the file
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
    named: dict[str, tuple[str, str]] = {}

    if isinstance(tiers, dict):
        try:
            named = resolve_seats(cast("dict[str, Any]", tiers), root)

        except AgentError as exc:
            raise ValueError(str(exc)) from None

    # S-0061/D-11: the role default is a profile that declares the role, so the sets a
    # repository once wrote under `skills:` are read off `.torve/agents/`. A
    # `skills:` key in the configuration is refused by `SkillsConfig` itself.
    roles = role_skills(root)

    if roles:
        config.setdefault("skills", {})["sets"] = roles

    # S-0064/D-1: a provider's facts live in its own record. The two keys below
    # are read from `.torve/providers/`, so writing either here is refused
    # naming where the values went rather than silently losing to the loader.
    if "provider_records" in config:
        raise ValueError(
            f"{resolved}: `provider_records` is read from .torve/providers/<name>.yaml "
            "and never written in this file"
        )

    broker_block = config.get("broker")

    if isinstance(broker_block, dict) and "providers" in broker_block:
        raise ValueError(
            f"{resolved}: `broker.providers` moved to .torve/providers/<name>.yaml — "
            "a provider's credential and base URL now sit beside its model roster and "
            "its clocks, and `broker:` keeps adapter, mode and cost_tolerance"
        )

    try:
        records = load_providers(root)

    except ProviderError as exc:
        raise ValueError(str(exc)) from None

    if records:
        config["provider_records"] = {name: record.model_dump() for name, record in records.items()}
        # Order matters: a seat's dialect is what its route is named after, so it
        # is resolved before the record's facts fold onto the seat.
        tiers = config.get("tiers") or {}
        speaks = {name: list(manifest.api) for name, manifest in all_harnesses(root).items()}
        _resolve_dialects(tiers, speaks, records)
        merge_records(tiers, records)

        if broker_block is None:
            broker_block = {}
            config["broker"] = broker_block

        if isinstance(broker_block, dict):
            # Anything else is a malformed `broker:` and stays that way — the
            # model's own refusal names it better than this injection could.
            # A route no seat reaches gets no entry. The broker forwards what
            # seats actually use; `providers.default` is the policy of what the
            # repository's contents *may* reach, which is a different question and
            # answered in a different place.
            reached = {
                (str(entry.get("provider") or ""), str(entry.get("dialect") or ""))
                for entry in tiers.values()
                if isinstance(entry, dict) and entry.get("provider") and entry.get("dialect")
            }
            broker_block["providers"] = {
                route_name(provider, dialect): _wire_facts(records[provider], dialect)
                for provider, dialect in sorted(reached)
                if provider in records and dialect in records[provider].routes
            }

    try:
        return RunnerConfig.model_validate(config)
    except ValidationError as exc:
        # S-0028/D-3's fourth refusal class: a merged result invalid enough that
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
                and error["loc"][1] in named
            }
        )

        if not offenders:
            raise

        where = "; ".join(
            f"tier {seat!r} via harness {named[seat][0]!r}"
            + (f" and profile {named[seat][1]!r}" if named[seat][1] else "")
            for seat in offenders
        )

        raise ValueError(f"{where}: invalid merged tier configuration — {exc}") from exc
