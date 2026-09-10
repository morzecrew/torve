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
from pydantic import BaseModel, Field, model_validator

from torve.base.model import STRICT
from torve.config import layout
from torve.config.equipment import KINDS, Equipment, skill_names
from torve.domain.vocabulary import ROLES

# ----------------------- #

# The profile and the manifest carry their own shape version (T-0321).
SCHEMA_VERSION = 1

AGENTS_DIR = "agents"
HARNESSES_DIR = "harnesses"


class Plugin(BaseModel):
    """One plugin on a resolved seat, derived from the profile's equipment.

    S-0062/D-1 folded plugins into `equipment`, so nothing declares this shape
    any more — `resolve_seats` builds it from the items of kind `plugin` so the
    renderer and the sandbox spec keep the shape they had. Both retire with the
    renderer in S-0062 phase 4.

    Was (S-0061/D-5):

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
    equipment: list[Equipment] = Field(default_factory=list)
    """Everything this agent is given, one item per thing (S-0062/D-1). A kind the
    seat's harness does not accept is refused when the seat resolves (S-0062/D-2);
    the role's own profile contributes a layer under this one (S-0062/D-12)."""
    prepare: str = ""
    """A command run in the sandbox before the agent, on its own clock — an index
    built, a cache warmed. Its failure is an infrastructure failure and convicts
    nothing (S-0062/D-7); chained into the harness command it would be booked as a
    gate-red conviction instead."""
    prompt_extras: str = ""
    """Prose appended after the charter's base rules — never before, never replacing
    them (S-0061/D-4). One block, written as it should read: a list rendered one
    bullet per entry could add a rule and never a paragraph (S-0061/A-5)."""


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
    kinds: list[str] = Field(default_factory=list)
    """Which equipment kinds this harness accepts (S-0063/D-4). A kind a profile
    declares and this does not name is refused at load, naming both files; how each
    reaches the harness is the image's `equip` (S-0063/D-3), not a template here.
    A harness naming no kind takes no equipment, which is what `fake` is."""
    env: dict[str, str] = Field(default_factory=dict)
    """The knobs this harness's image reads — `{"CLAUDE_PERMISSION_MODE": "..."}`
    (S-0063/D-10). Torve sets them and never interprets them; a knob that is not here
    is a rebuild, because a flag that changes what an agent may do is a regime change
    the digest should carry."""
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

    @model_validator(mode="after")
    def _kinds(self) -> HarnessManifest:
        """A kind the engine has no name for is wrong in one visible line rather
        than in an attempt that ran without its equipment (S-0063/D-4)."""

        for kind in sorted(set(self.kinds)):
            if kind not in KINDS:
                raise ValueError(
                    f"`kinds` names {kind!r}, which is no equipment kind — "
                    f"the kinds are {', '.join(KINDS)}"
                )

        return self


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


# The keys equipment replaced (S-0062/D-1), each named with what to write instead.
# They were profile keys, so a list of valid keys would read as a typo rather
# than as a shape that moved.
FOLDED: dict[str, str] = {
    "skills": "`equipment` as items of kind `skill` — `{kind: skill, source: torve:<name>}`",
    "plugins": "`equipment` as items of kind `plugin` — the source and the ref unchanged",
    # S-0063/D-1: the shell that knows how to start a harness lives beside the
    # harness. A manifest names the image and the knobs its scripts read.
    "command": (
        "the image's own `/opt/torve/run`, which the engine invokes — a manifest names "
        "`image`, the `kinds` it takes and the `env` its scripts read, and never a shell line"
    ),
    # S-0063/D-4: the capability map keeps the refusal and loses the templates.
    "equips": "`kinds`, a list — the flag per kind is the image's `/opt/torve/equip`",
}


def _refuse_foreign(path: Path, body: dict[str, Any], own: frozenset[str]) -> None:
    """Every key that belongs somewhere else, named with where (S-0061/I-2).

    `prompt` gets its own refusal (S-0061/D-4): it is not a key of any file,
    and a reader who tried it deserves the reason rather than a list of
    valid keys. So do `skills` and `plugins`, which are not gone but folded
    (S-0062/D-1).
    """

    for folded in sorted(set(body) & set(FOLDED)):
        raise AgentError(f"{path}: `{folded}` is now {FOLDED[folded]}")

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


def _declared(
    path: Path, label: str, own: frozenset[str], model: type[BaseModel]
) -> dict[str, Any]:
    """The keys this file actually wrote, after the same validation and the
    same refusals its model performs.

    The body, never the model's dump: a merge over a dump cannot tell a key
    the file omitted from one it set to the model's default, and that
    ambiguity is what S-0028/D-2 mandated a raw-mapping merge to avoid. A
    profile naming only `plugins` used to write `skills: []` onto the seat
    and silently strip the role's set.
    """

    body = _body(path, label)
    _refuse_foreign(path, body, own)

    try:
        model.model_validate(body)

    except ValueError as exc:
        raise AgentError(f"{path}: {exc}") from None

    return {key: value for key, value in body.items() if key != "schema_version"}


def load_profile(root: Path, name: str) -> AgentProfile:
    path = agents_dir(root) / f"{name}.yaml"

    return AgentProfile.model_validate(_declared(path, "agent profile", PROFILE_KEYS, AgentProfile))


def load_harness(root: Path, name: str) -> HarnessManifest:
    path = harnesses_dir(root) / f"{name}.yaml"

    return HarnessManifest.model_validate(
        _declared(path, "harness manifest", HARNESS_KEYS, HarnessManifest)
    )


# ....................... #


def role_equipment(root: Path) -> dict[str, list[Equipment]]:
    """The equipment each role's own profile declares (S-0061/D-11, S-0062/D-12).

    The role default is a profile named for the role, so a repository writes
    its defaults where every other piece of equipment is written instead of in
    a second mapping under `skills:`. A role with no profile file contributes
    nothing — this is a lookup, not a requirement.

    This is the lower of the two layers: `merge_equipment` puts the seat's
    profile on top of it, per task, because the role varies within a seat.
    """

    found: dict[str, list[Equipment]] = {}
    directory = agents_dir(root)

    if not directory.is_dir():
        return found

    for path in sorted(directory.glob("*.yaml")):
        # Only a name the engine has a role for. Every other profile is a
        # seat's, and keying it here would put a role nothing dispatches into
        # the role sets and into the regime hash — which is what makes
        # `torve eval` refuse a skill as "in no role set" (S-0009/A-5).
        if path.stem in ROLES:
            found[path.stem] = list(load_profile(root, path.stem).equipment)

    return found


def role_skills(root: Path) -> dict[str, list[str]]:
    """The package-data skill names each role loads, which is what `materialize`
    resolves (S-0009/D-1) and what `skills.sets` carries for every reader that
    had it before equipment existed."""

    return {role: skill_names(items) for role, items in role_equipment(root).items()}


# ....................... #


def resolve_seats(tiers: dict[str, Any], root: Path) -> dict[str, tuple[str, str]]:
    """Merge each seat's harness and profile into its raw mapping, before
    `TierConfig` ever validates (the shape S-0028/D-2 already used).

    Each file contributes the keys it wrote and no others, so a profile that
    names only `plugins` leaves the seat's `skills` unset and the role's own
    profile answers (S-0061/D-11).

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

        merged: dict[str, Any] = _declared(
            harnesses_dir(root) / f"{harness_name}.yaml",
            "harness manifest",
            HARNESS_KEYS,
            HarnessManifest,
        )

        if profile_name:
            merged.update(
                _declared(
                    agents_dir(root) / f"{profile_name}.yaml",
                    "agent profile",
                    PROFILE_KEYS,
                    AgentProfile,
                )
            )

        merged.update(entry)
        _equipped(key, harness_name, profile_name, merged)
        tiers[key] = merged
        named[key] = (harness_name, profile_name)

    return named


def _equipped(seat: str, harness_name: str, profile_name: str, merged: dict[str, Any]) -> None:
    """Refuse a kind this harness cannot be given, then derive the shapes the
    readers still have (S-0062/D-2).

    The refusal is S-0061/D-6's generalised from plugins to every kind, and it
    exists for the same reason: an attempt quietly missing its equipment
    measures a regime nobody configured, and the record would say it ran with
    equipment it never had. Both files are named, because which of them is
    wrong is the reader's call — the profile asked for something, the manifest
    says it cannot take it, and either could be the one to change.

    `plugins` and `skills` are then written from the same list. Nothing declares
    them any more; the sandbox spec and the materializer still read them, and
    they retire with the renderer in phase 4.
    """

    try:
        items = [Equipment.model_validate(raw) for raw in merged.get("equipment", [])]

    except ValueError as exc:
        raise AgentError(f"tier {seat!r} via profile {profile_name!r}: {exc}") from None

    kinds: list[str] = merged.get("kinds", [])

    for item in items:
        # A package-data skill has a second channel every harness has: `materialize`
        # writes it into the worktree and the prompt names it (S-0062/D-10, which
        # only stands if a harness with no `skill` flag still receives skills). So
        # the refusal is "no way to deliver it", not "no flag for it" — every other
        # kind, and a skill that has to be fetched, has only the flag.
        if item.kind in kinds or (item.kind == "skill" and item.scheme == "torve"):
            continue

        takes = ", ".join(sorted(kinds)) or "no equipment at all"
        raise AgentError(
            f"tier {seat!r}: profile {profile_name!r} declares {item.source} of kind "
            f"{item.kind!r}, and harness {harness_name!r} takes {takes} — a kind the "
            "harness cannot be told about would go missing from an attempt that "
            "still ran"
        )

    merged["plugins"] = [
        {"source": item.source, "ref": item.ref} for item in items if item.kind == "plugin"
    ]
    names = skill_names(items)

    if names:
        merged["skills"] = names
