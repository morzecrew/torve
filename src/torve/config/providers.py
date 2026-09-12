""".torve/providers/<name>.yaml — what one credential buys (S-0064/D-1).

Three strata, and the point of the file is that each value sits in exactly
one of them.

The **provider** holds what one credential buys: the key's variable name,
whether the runner's own upstream leg tunnels through a proxy, and the
clocks. The clocks are here rather than per route because a wedged stream is
a property of the network path to a vendor, and the incident that produced
this repository's two numbers — a trickling stream that idled two attempts to
the full agent cap — did not care which URL it was trickling from.

A **route** holds what a dialect changes (S-0064/D-2). `routes` is keyed by api
name, each carrying its own `base_url` and its own compat facts: measured
against ModelStudio on 2026-09-11, `/compatible-mode` returns thinking as
`reasoning_content` while `/apps/anthropic` returns native `thinking` blocks,
on one credential and one model. A value torve has no name for rides as the
route's `extra`, verbatim and unvalidated, and visibly so (S-0064/D-10) — a
schema torve invents is one it has to keep up with.

A **model** holds what is true of the model wherever it is reached. `reasoning`
is a list rather than a boolean so a seat's choice has something to be refused
against (S-0064/D-5), and `price` is optional because a subscription seat has no
per-token cost and must say so rather than report zero (S-0064/D-12).

This module validates the record and nothing else. Which seat may reach which
model, and which dialect a harness speaks, are the next phase's refusals; what
lands here is the file the engine has never been able to read.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field, model_validator

from torve.base.model import STRICT
from torve.config import layout

# ----------------------- #

# The record's own shape version, like every other file torve reads (T-0321).
SCHEMA_VERSION = 1

PROVIDERS_DIR = "providers"

# torve's own dialect keys (S-0064/D-2), never a harness's spelling: `openai`
# means Chat Completions, and a provider serving the Responses API would earn
# a third key rather than an overloaded first one. The set is not closed —
# nothing about it has to be decided now.
APIS = ("openai", "anthropic")

# Prices are written per million tokens, which is how every vendor's billing
# page states them; the arithmetic divides once, here.
PER_TOKENS = 1_000_000

# The record keys the token counts ride under in the agent block. Named here
# rather than imported, because `config` may not import `application`
# (S-0015) and the counts are what an attempt's cost is computed from.
INPUT_TOKENS = "input_tokens"
OUTPUT_TOKENS = "output_tokens"
CACHE_READ_TOKENS = "cache_read_tokens"
CACHE_CREATION_TOKENS = "cache_creation_tokens"


class ProviderError(ValueError):
    """A provider record that could not be read, or that named something the
    engine refuses to guess at."""


# ....................... #


class Price(BaseModel):
    """What a model costs, in US dollars per million tokens (S-0064/D-12).

    The engine computes an attempt's cost from this and the token counts
    rather than believing a harness that may not recognise the model it was
    pointed at — measured, claude emits `unrecognized_model` for
    qwen3.8-flash and then prices the attempt off its own Anthropic table.
    """

    model_config = STRICT

    input: float = 0.0
    """US dollars per million input tokens."""
    output: float = 0.0
    """US dollars per million output tokens."""
    cache_read: float | None = None
    """US dollars per million tokens read from the prompt cache; absent bills them at the
    input rate rather than inventing a discount."""
    cache_write: float | None = None
    """US dollars per million tokens written to the prompt cache; absent bills them at the
    input rate rather than inventing a surcharge."""

    # ....................... #

    def cost(self, counts: Mapping[str, Any]) -> float | None:
        """What `counts` cost at these rates, or None when the attempt
        reported no counts at all.

        Absent stays absent (S-0004/D-6): an adapter that reported nothing is
        unreported, never zero. A count that is reported is billed, and one
        that is not contributes nothing — the two are different facts and
        this is the only place that can still tell them apart.
        """

        rates = {
            INPUT_TOKENS: self.input,
            OUTPUT_TOKENS: self.output,
            CACHE_READ_TOKENS: self.input if self.cache_read is None else self.cache_read,
            CACHE_CREATION_TOKENS: self.input if self.cache_write is None else self.cache_write,
        }
        reported = {
            name: int(counts[name])
            for name, _rate in rates.items()
            if isinstance(counts.get(name), int)
        }

        if not reported:
            return None

        return round(sum(rates[name] * count for name, count in reported.items()) / PER_TOKENS, 6)


# ....................... #


class Compat(BaseModel):
    """What one route's endpoint does differently, measured and dated in the
    record rather than probed at dispatch (S-0064/D-2).

    Narrow on purpose: a fact torve can name is validated, and everything
    else rides as the route's `extra` (S-0064/D-10). The `developer` role is
    deliberately not here — torve never builds a request, so the client that
    would send it decides it (S-0064/D-11).
    """

    model_config = STRICT

    thinking_format: str = ""
    """How this route returns a model's thinking when it is not the native Anthropic
    block — `deepseek` for an endpoint that returns `reasoning_content`. Empty means the
    route's own dialect form, whatever that is."""


# ....................... #


class Route(BaseModel):
    """One API dialect of one provider (S-0064/D-2): a provider serving two
    dialects is two routes on one credential."""

    model_config = STRICT

    base_url: str = ""
    """Where this dialect is served (http:// or https://). Named `base_url` because that
    is what every client calls it; it was `upstream` while it lived on the broker."""
    compat: Compat = Field(default_factory=Compat)
    """The quirks of this route that torve has a name for."""
    extra: dict[str, Any] = Field(default_factory=dict)
    """Values torve has no name for, handed to the image verbatim and unvalidated. A
    quirk nobody has modelled yet does not block a seat, and it stays obvious in review
    which values the engine stands behind and which it only carries."""

    # ....................... #

    @model_validator(mode="after")
    def _a_route_is_somewhere_to_dial(self) -> Route:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"route base_url {self.base_url!r} must be an http(s) base URL — "
                "a route is where a dialect is served, and a name nothing can dial "
                "is not one"
            )

        return self


# ....................... #


class Model(BaseModel):
    """One entry in a provider's roster: what is true of the model wherever it
    is reached.

    Everything here is optional because a record is written as it is measured,
    and a value nobody has measured is better absent than guessed. What is
    absent is unstated, never zero.
    """

    model_config = STRICT

    id: str = ""
    """What reaches the provider, and what the regime hash records; empty means the roster
    key is its own id (S-0064/D-3). A slug awkward to type or to use as a path segment gets
    a local shorthand, and renaming that shorthand cannot move a regime digest."""
    context_window: int = 0
    """How many tokens this model's context holds; 0 is unstated."""
    max_tokens: int = 0
    """The cap on one response's tokens; 0 is unstated."""
    reasoning: list[str] = Field(default_factory=list)
    """The reasoning levels this model has, as words a seat names one of. A list rather
    than a flag so a seat's choice has something to be refused against; empty is a model
    that does not reason."""
    price: Price | None = None
    """What a token costs here. Absent for a seat that genuinely has no per-token cost — a
    subscription — and then the attempt's cost stays unreported rather than invented."""


# ....................... #


class Provider(BaseModel):
    """One provider, one credential, one file (S-0064/D-1).

    `broker.providers.<name>` folded in whole, leaving `broker:` with
    `adapter`, `mode` and `cost_tolerance` — the three things that are about
    the broker rather than about a provider. A provider's facts are now
    validated, hashed and reviewed like every other file under `.torve/`,
    instead of being a string the engine sets and never reads.
    """

    model_config = STRICT

    schema_version: int = SCHEMA_VERSION
    """The record's own shape version."""
    name: str = ""
    """What a seat resolves this provider by; empty is the filename stem, the same rule
    the agent profile and the harness manifest follow."""
    key_env: str = ""
    """The name of the environment variable holding this provider's credential — a name,
    never a value: configuration is committed and credentials are not."""
    via_proxy: bool = False
    """The runner's own upstream leg to this provider tunnels through the host's
    https_proxy — for endpoints unreachable from the host directly (region gating). A
    sandbox never sees a proxy either way; this is the host's egress, not the run's."""
    request_timeout_s: float | None = None
    """How long one request to this provider may take, in seconds; absent leaves the
    harness's own default in force."""
    stream_idle_timeout_s: float | None = None
    """How long a started stream may go silent before it is abandoned, in seconds — the
    backstop for a wedged-but-trickling stream; absent leaves the harness's own default."""
    routes: dict[str, Route] = Field(default_factory=dict)
    """This provider's API dialects, keyed by api name, each with its own base URL and its
    own compat facts."""
    models: dict[str, Model] = Field(default_factory=dict)
    """The roster, keyed by what a seat writes. An empty roster is a provider nothing is
    seated on yet, which is a configuration, not an error."""

    # ....................... #

    @model_validator(mode="after")
    def _a_provider_is_a_credential_and_somewhere_to_spend_it(self) -> Provider:
        if not self.key_env:
            raise ValueError(
                "key_env must name the environment variable holding the credential"
            )

        if not self.routes:
            raise ValueError(
                "a provider serves at least one route — name the api dialect under "
                f"`routes` ({', '.join(APIS)}) with the base URL it is served at"
            )

        unknown = sorted(name for name in self.routes if name not in APIS)

        if unknown:
            raise ValueError(
                f"routes name {', '.join(unknown)}, which is no api dialect this engine "
                f"knows — the names are {', '.join(APIS)}"
            )

        return self

    # ....................... #

    def id_for(self, model: str) -> str:
        """What reaches the provider when a seat writes `model` (S-0064/D-3): the
        roster entry's `id`, or the key itself when it declares none. A key
        the roster does not list is its own id too — resolving a seat against
        the roster is the next phase's refusal, and this must not start
        performing it early."""

        entry = self.models.get(model)

        return (entry.id or model) if entry is not None else model


# ....................... #


def providers_dir(root: Path) -> Path:
    """Where a repository's provider records live — beside `config.yaml` in the
    tree the runner was launched from, never in the repository under work
    (S-0013/D-3)."""

    return root / layout.TORVE_DIR / PROVIDERS_DIR


# ....................... #


def _body(path: Path) -> dict[str, Any]:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, yaml.YAMLError) as exc:
        raise ProviderError(f"{path}: could not be read as a provider record — {exc}") from exc

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise ProviderError(f"{path}: a provider record must be a mapping")

    return cast("dict[str, Any]", raw)


# ....................... #


def load_providers(root: Path) -> dict[str, Provider]:
    """Every provider record under `.torve/providers/`, by the name it answers
    to: its own `name` key, or its filename stem.

    Two files claiming one name are refused naming both — the half a directory
    cannot enforce, exactly as for the two files a seat names (S-0061/D-12). A
    missing directory is no providers, which is a repository that has not
    written any down yet.
    """

    found: dict[str, Provider] = {}
    where: dict[str, Path] = {}
    directory = providers_dir(root)

    for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
        body = _body(path)
        name = str(body.get("name") or path.stem)

        if name in where:
            raise ProviderError(
                f"two provider records are both named {name!r}: {where[name]} and {path} — "
                "a name is what a seat resolves, so one of them has to change"
            )

        try:
            found[name] = Provider.model_validate(body)

        except ValueError as exc:
            raise ProviderError(f"{path}: {exc}") from None

        where[name] = path

    return found


# ....................... #


def load_provider(root: Path, name: str) -> Provider:
    """The record that answers to `name`, or a refusal naming what does."""

    records = load_providers(root)
    record = records.get(name)

    if record is None:
        raise ProviderError(
            f"no provider record named {name!r} in {providers_dir(root)}; "
            f"present: {', '.join(sorted(records)) or 'none'}"
        )

    return record
