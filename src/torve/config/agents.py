"""The agent profile and the harness manifest (S-0061/D-1, S-0061/D-2).

Three questions used to share one seventeen-field tier body, and the file
meant to name a persona was validated against that whole body — so a file
called `heavy.yaml` could legally decide where a conviction routes. They are
three files now, one question each:

- `.torve/agents/<name>.yaml` — what the agent *is*: its skills, its plugins,
  the working rules it appends. Nothing about how it runs (S-0061/D-1).
- `.torve/harnesses/<name>.yaml` — how a model is *reached*: the adapter, the
  command that runs in the sandbox, the image that is the harness's identity
  (S-0017/D-4), and how auth arrives. No model (S-0061/D-2).
- the seat in `config.yaml` — which run gets which, plus what varies per run:
  the model, the routing, the clocks (S-0061/D-3).

Both files are committed under `.torve/` rather than kept on the operator's
machine (S-0061/D-10): their bodies join the regime hash, and a hash over a
file that exists on one laptop reproduces nowhere. This is not S-0013/D-3
reopened — they sit beside `config.yaml` in the repository the runner was
launched from, never in the repository under work.

What this module resolves is the *declaration*. The seat a reader receives is
still one object carrying every field, because three objects at every call
site would be the split leaking out of the files it belongs in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field

from torve.base.model import STRICT
from torve.config import layout

# ----------------------- #

# The profile and the manifest carry their own shape version (T-0321).
SCHEMA_VERSION = 1

AGENTS_DIR = "agents"
HARNESSES_DIR = "harnesses"


class Plugin(BaseModel):
    """One plugin the harness loads (S-0061/D-5).

    A source and a ref, and nothing else: the ref is whatever the source's own
    vocabulary pins with — a tag, a branch, a commit — and torve neither
    resolves nor verifies it. Pinning is the source's job; torve's is to stop
    the Dockerfile, the cache path and the installed-plugins file disagreeing
    about what was pinned.
    """

    model_config = STRICT
    source: str
    """Where the plugin comes from, in the source's own spelling — `github:owner/repo`."""
    ref: str = ""
    """What the source pins it at; empty takes whatever the harness's installer resolves."""


class AgentProfile(BaseModel):
    """What the agent is (S-0061/D-1) — never how it runs.

    A seat naming no profile resolves the profile named for the task's role
    (S-0061/D-11), so `implement`, `review` and `revert` are the names that
    carry a repository's defaults.
    """

    model_config = STRICT
    schema_version: int = SCHEMA_VERSION
    """The profile's own shape version."""
    skills: list[str] = Field(default_factory=list)
    """The skills materialized into this agent's sandbox, by name (S-0009/D-1). Names
    resolve against package data and the vendored directory exactly as before; only
    where the set is declared has moved."""
    plugins: list[Plugin] = Field(default_factory=list)
    """The plugins the harness loads, rendered into its own seeding format at dispatch
    (S-0061/D-6)."""
    prompt_extras: list[str] = Field(default_factory=list)
    """Working rules appended after the charter's base rules — never before, never
    replacing them (S-0061/D-4)."""


class HarnessManifest(BaseModel):
    """How a model is reached (S-0061/D-2) — and never which model.

    One image runs several models, so choosing among them is the seat's; what
    belongs here is everything that would be identical whichever model the
    seat picks.
    """

    model_config = STRICT
    schema_version: int = SCHEMA_VERSION
    """The manifest's own shape version."""
    adapter: str = "fake"
    """Which agent adapter this harness drives: fake, api, harness or subscription."""
    command: str = ""
    """The command line run inside the sandbox; `{prompt}` and `{model}` are
    substituted. The engine never links a harness SDK — it shells a line into a
    container it created (S-0004/D-1)."""
    image: str = ""
    """The sandbox image, which is what harness identity actually is (S-0017/D-4).
    Empty falls back to `runtime.image`."""
    api_key_env: list[str] = Field(default_factory=list)
    """The names of the variables the runtime forwards from its own environment —
    names, never values, so a secret never transits a spec (S-0001/D-13)."""
    auth_volume: str = "torve-auth"
    """The subscription route's volume; one per worker slot, `-<slot>` appended."""
    auth_mount: str = "/auth"
    """Where that volume is mounted, read-write because token refresh writes."""


# ....................... #

# Which file each key lives in. One home per key is the whole point (S-0061/I-2),
# so a key written in the wrong file is refused by name with the right one named,
# rather than silently ignored by a model that does not know it.
PROFILE_KEYS = frozenset(AgentProfile.model_fields) - {"schema_version"}
HARNESS_KEYS = frozenset(HarnessManifest.model_fields) - {"schema_version"}
SEAT_KEYS = frozenset(
    {
        "harness",
        "profile",
        "model",
        "provider",
        "retry_variant",
        "retry_variants",
        "character_routing",
        "agent_timeout",
        "sandbox_timeout",
        "cache_volume",
    }
)

HOME: dict[str, str] = {
    **dict.fromkeys(PROFILE_KEYS, "the agent profile, `.torve/agents/<name>.yaml`"),
    **dict.fromkeys(HARNESS_KEYS, "the harness manifest, `.torve/harnesses/<name>.yaml`"),
    **dict.fromkeys(SEAT_KEYS, "the seat, `tiers:` in .torve/config.yaml"),
}


class AgentError(ValueError):
    """A profile, a harness manifest or a seat that names it cannot be read."""


# ....................... #


def agents_dir(root: Path) -> Path:
    return root / layout.TORVE_DIR / AGENTS_DIR


def harnesses_dir(root: Path) -> Path:
    return root / layout.TORVE_DIR / HARNESSES_DIR


# ....................... #


def _refuse_foreign(path: Path, body: dict[str, Any], own: frozenset[str]) -> None:
    """Every key that belongs somewhere else, named with where (S-0061/I-2).

    `prompt` gets its own refusal (S-0061/D-4): it is not a key of any file,
    and a reader who tried it deserves the reason rather than a list of
    valid keys.
    """

    if "prompt" in body:
        raise AgentError(
            f"{path}: `prompt` is not a profile key — the working rules an attempt is "
            "gated against are the engine's, and a profile appends to them with "
            "`prompt_extras`"
        )

    for key in sorted(set(body) - own - {"schema_version"}):
        where = HOME.get(key)

        if where is None:
            raise AgentError(f"{path}: unknown key {key!r}")

        raise AgentError(f"{path}: `{key}` belongs in {where}, not here")


def _body(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        present = sorted(p.stem for p in path.parent.glob("*.yaml")) if path.parent.is_dir() else []
        raise AgentError(f"no {label} at {path}; present: {', '.join(present) or 'none'}")

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, yaml.YAMLError) as exc:
        raise AgentError(f"{path}: could not be read as a {label} — {exc}") from exc

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise AgentError(f"{path}: a {label} must be a mapping")

    return cast("dict[str, Any]", raw)


# ....................... #


def load_profile(root: Path, name: str) -> AgentProfile:
    path = agents_dir(root) / f"{name}.yaml"
    body = _body(path, "agent profile")
    _refuse_foreign(path, body, PROFILE_KEYS)

    try:
        return AgentProfile.model_validate(body)

    except ValueError as exc:
        raise AgentError(f"{path}: {exc}") from None


def load_harness(root: Path, name: str) -> HarnessManifest:
    path = harnesses_dir(root) / f"{name}.yaml"
    body = _body(path, "harness manifest")
    _refuse_foreign(path, body, HARNESS_KEYS)

    try:
        return HarnessManifest.model_validate(body)

    except ValueError as exc:
        raise AgentError(f"{path}: {exc}") from None


# ....................... #


def role_profiles(root: Path) -> dict[str, list[str]]:
    """The skill set each role's own profile declares (S-0061/D-11).

    The role default is a profile named for the role, so a repository writes
    its defaults where every other piece of equipment is written instead of in
    a second mapping under `skills:`. A role with no profile file contributes
    nothing — this is a lookup, not a requirement.
    """

    found: dict[str, list[str]] = {}
    directory = agents_dir(root)

    if not directory.is_dir():
        return found

    for path in sorted(directory.glob("*.yaml")):
        found[path.stem] = list(load_profile(root, path.stem).skills)

    return found


# ....................... #


def resolve_seats(tiers: dict[str, Any], root: Path) -> dict[str, tuple[str, str]]:
    """Merge each seat's harness and profile into its raw mapping, before
    `TierConfig` ever validates (the shape S-0028/D-2 already used).

    One merge level (S-0061/D-8): a seat merges its harness and its profile,
    and neither references another of its kind, so the file a refusal names is
    the file carrying the bad key. Order is harness, then profile, then the
    seat's own keys — but the seat may only carry seat keys, so nothing it
    writes can overwrite either file (S-0061/D-9). Returns the (harness,
    profile) names per seat for whoever wants to say where a value came from.
    """

    named: dict[str, tuple[str, str]] = {}

    for key, raw_entry in tiers.items():
        if not isinstance(raw_entry, dict):
            continue

        entry = cast("dict[str, Any]", raw_entry)
        harness_name = str(entry.get("harness") or "")
        profile_name = str(entry.get("profile") or "")

        for foreign in sorted(set(entry) - SEAT_KEYS):
            where = HOME.get(foreign)
            raise AgentError(
                f"tier {key!r}: `{foreign}` belongs in "
                f"{where or 'no file the engine reads'}, not on the seat"
                if where
                else f"tier {key!r}: unknown key {foreign!r}"
            )

        if not harness_name:
            raise AgentError(
                f"tier {key!r} names no harness — a seat says which harness reaches its "
                f"model, and the manifests live in {harnesses_dir(root)}"
            )

        merged: dict[str, Any] = dict(
            load_harness(root, harness_name).model_dump(exclude={"schema_version"})
        )

        if profile_name:
            merged.update(load_profile(root, profile_name).model_dump(exclude={"schema_version"}))

        merged.update(entry)
        tiers[key] = merged
        named[key] = (harness_name, profile_name)

    return named
