"""What an agent has (S-0062/D-1) — one list, one item per thing it is given.

Four questions used to be four mechanisms. A plugin was a source and a ref, a
skill was a bare name resolving only against package data, and an MCP server or
a hook had no declaration at all — so a repository could pin a plugin and could
not say which skills it wanted, let alone where from. They are one shape now:

    kind    where the harness puts it — skill, plugin, mcp, hook, agent
    source  where it comes from, in one of three schemes
    ref     what the source pins it at, required for the one that fetches
    select  the subset of a source that holds several

Torve is not a package manager (S-0062/non_goals). It fetches what a
declaration names at the ref it names, through the source's own tool, and
neither resolves a range nor decides what a version means. What it does refuse
is a declaration that could not be reconstructed later: an unpinned fetch is a
regime nobody can rebuild, which is the defect S-0061/D-7 took out of the hash.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args
from urllib.parse import quote, unquote

from pydantic import BaseModel, Field, model_validator

from torve.base.model import STRICT

# ----------------------- #

Kind = Literal["skill", "plugin", "mcp", "hook", "agent"]
KINDS: tuple[str, ...] = get_args(Kind)

# `torve:` is package data, versioned with the engine (S-0062/D-6). `local:` is a
# path in the repository under work, which git has already versioned. `github:`
# is the one that reaches the network, and the only one a ref means anything for.
SCHEMES: tuple[str, ...] = ("torve", "local", "github")
FETCHED: tuple[str, ...] = ("github",)

# The schemes whose source can hold more than one item of its kind, so `select`
# has something to select from.
PLURAL: tuple[str, ...] = ("github", "local")


class EquipmentError(ValueError):
    """A declaration that names no source the engine can reach, or reaches one
    at a version nobody wrote down."""


class Equipment(BaseModel):
    """One thing an agent is given (S-0062/D-1)."""

    model_config = STRICT
    kind: Kind
    """Where the harness puts it. A kind the harness does not accept is refused
    against its manifest, not here (S-0062/D-2)."""
    source: str
    """`torve:<name>`, `local:<path>` or `github:<owner>/<repo>` (S-0062/D-3)."""
    ref: str = ""
    """What the source pins it at, in the source's own vocabulary — a tag, a branch,
    a commit. Required for a fetched source and refused for the two that carry a
    version already."""
    select: list[str] = Field(default_factory=list)
    """The subset of the source to take; empty takes everything it holds."""

    @model_validator(mode="after")
    def _reachable(self) -> Equipment:
        scheme, _, locator = self.source.partition(":")

        if not locator or scheme not in SCHEMES:
            raise EquipmentError(
                f"{self.source!r} names no source the engine can reach — a source is "
                f"{', '.join(f'`{name}:...`' for name in SCHEMES)}"
            )

        if scheme in FETCHED and not self.ref:
            raise EquipmentError(
                f"{self.source} names no ref — a fetched source is pinned or the regime "
                "it equips cannot be rebuilt"
            )

        if scheme not in FETCHED and self.ref:
            raise EquipmentError(
                f"{self.source} carries `ref: {self.ref}`, and "
                + (
                    "package data is versioned with the engine"
                    if scheme == "torve"
                    else "a tracked path is versioned by git"
                )
                + " — a second version could only disagree"
            )

        if self.select and scheme not in PLURAL:
            raise EquipmentError(
                f"{self.source} names one {self.kind} already, so `select` has nothing "
                "to select from"
            )

        return self

    @property
    def scheme(self) -> str:
        return self.source.partition(":")[0]

    @property
    def locator(self) -> str:
        return self.source.partition(":")[2]

    @property
    def key(self) -> str:
        """The cache key: the declaration itself, escaped into one path segment.

        Escaped rather than flattened, so it round-trips (S-0062/I-2) — a record
        naming a key names the source and the ref an operator wrote, and
        `parse_key` reads them back without the declaration being at hand.
        """

        return f"{quote(self.source, safe='')}@{quote(self.ref, safe='')}"


# ....................... #


def parse_key(key: str) -> tuple[str, str]:
    """The source and the ref a cache key was built from (S-0062/I-2)."""

    source, _, ref = key.rpartition("@")

    return unquote(source), unquote(ref)


def merge_equipment(role: Sequence[Equipment], seat: Sequence[Equipment]) -> list[Equipment]:
    """The role's equipment, then the seat's on top (S-0062/D-12).

    Appended and deduplicated by kind and source, the seat's item winning where
    both layers name one — the seat is the more specific statement. Two layers
    rather than one because the vocabularies differ: `executor` is a seat and
    `implement` is a role, and one executor seat runs tasks of several roles, so
    the role layer varies *within* a seat and cannot be collapsed into it.

    Without this a seat profile declaring one plugin would replace the role's
    skills wholesale, which is the S-0061/D-11 defect T-0325 fixed arriving
    through the door D-1 opens when `skills` folds into this list.
    """

    merged = {(item.kind, item.source): item for item in role}

    for item in seat:
        merged[item.kind, item.source] = item

    return list(merged.values())


def skill_names(items: Sequence[Equipment]) -> list[str]:
    """The package-data skill names in *items*, for `materialize` (S-0009/D-1).

    Only `torve:` sources: a name is what `materialize` resolves, and the names
    it resolves are the ones the engine ships (S-0062/D-6). A fetched skill
    reaches its agent through the harness's own capability map instead, which is
    S-0062 phase 2 — until then it is declarable and not yet delivered.
    """

    return [item.locator for item in items if item.kind == "skill" and item.scheme == "torve"]
