"""The equipment cache: fetched host-side, mounted read-only (S-0062/D-4).

Nothing is fetched while an attempt is running. An item is fetched once, into a
directory named by its own declaration, and every attempt that declares it
mounts the same bytes — so two checkouts of one tree equip identically and a
cold runner pays the fetch once rather than once per attempt.

    ~/.cache/torve/equipment/<kind>/<source>@<ref>/     the item
    ~/.cache/torve/equipment/.mounts/<digest>/          what one seat mounts

The second is what `TORVE_EQUIPMENT` names: a directory holding `manifest.json`
and one entry per item (S-0063/D-12). It is built by hard link where the
filesystem allows it, so a seat's mount costs directory entries rather than a
copy of every skill it was given.

Deleting the whole thing costs wall clock and nothing else — it is derived
state, the property S-0035/D-1 already states for the toolchain cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from torve.config.equipment import Equipment

if TYPE_CHECKING:
    from collections.abc import Sequence

# ----------------------- #

CACHE_ENV = "TORVE_EQUIPMENT_CACHE"
"""Where the cache lives, for an operator who wants it somewhere else."""

MOUNTS_DIR = ".mounts"
MANIFEST = "manifest.json"

# What a fetch records beside the bytes, so `--check` needs no network.
PIN_FILE = ".torve-pin"

# Where the cache mounts inside a sandbox, and therefore what `TORVE_EQUIPMENT`
# names (S-0063/D-2). A constant rather than a per-seat path: the manifest
# inside the mount carries in-container paths, so the two would have to agree
# anyway, and one place to disagree is better than two.
EQUIPMENT_MOUNT = "/opt/torve/equipment"

# What the image reads at the root of its mount. Version it: the shape is a
# contract with three scripts torve does not own the release cycle of.
MANIFEST_SCHEMA_VERSION = 1

# How long a fetch may take before it is the network's problem rather than a
# slow clone. An operator warming a cold cache by hand waits; an attempt never
# reaches this code at all.
FETCH_TIMEOUT_S = 600


class EquipmentError(RuntimeError):
    """An item could not be fetched, or was fetched into something unusable."""


# ....................... #


def cache_root() -> Path:
    """The cache directory, from the environment or the XDG default."""

    named = os.environ.get(CACHE_ENV)

    if named:
        return Path(named).expanduser()

    home = os.environ.get("XDG_CACHE_HOME") or "~/.cache"

    return Path(home).expanduser() / "torve" / "equipment"


def item_path(item: Equipment, root: Path | None = None) -> Path:
    """Where this item's bytes live: the declaration, escaped into one segment
    (S-0062/I-2), under the kind that says what to do with it."""

    return (root or cache_root()) / item.kind / item.key


# ....................... #


def _fetch_github(item: Equipment, into: Path) -> None:
    """A pinned clone, through git's own vocabulary.

    `gh skill install --pin` writes the same tree for a skill and records the
    pin in the SKILL.md frontmatter, which is what `--check` audits (S-0062/D-11);
    a clone is what every kind can be fetched by, so it is what this does.
    """

    into.parent.mkdir(parents=True, exist_ok=True)
    scratch = into.with_name(into.name + ".fetching")
    shutil.rmtree(scratch, ignore_errors=True)

    for args in (
        ["git", "clone", "--quiet", f"https://github.com/{item.locator}", str(scratch)],
        ["git", "-C", str(scratch), "checkout", "--quiet", item.ref],
    ):
        # argv, never a shell line: a source is an operator's string.
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=FETCH_TIMEOUT_S, check=False
        )

        if result.returncode != 0:
            shutil.rmtree(scratch, ignore_errors=True)

            raise EquipmentError(
                f"{item.source}@{item.ref}: {args[0]} failed — "
                f"{result.stderr.strip() or 'no error output'}"
            )

    # The pin travels with the bytes, so an audit needs no network (S-0062/D-11).
    (scratch / PIN_FILE).write_text(f"{item.source}@{item.ref}\n", encoding="utf-8")
    # Atomic: a half-fetched directory must never look like a warm one.
    scratch.rename(into)


def _fetch_local(item: Equipment, into: Path, *, root: Path) -> None:
    """A path in the repository under work, copied so the mount is read-only
    whatever the worktree does afterwards."""

    source = (root / item.locator).resolve()

    if not source.exists():
        raise EquipmentError(f"{item.source}: {source} does not exist in this repository")

    into.parent.mkdir(parents=True, exist_ok=True)
    scratch = into.with_name(into.name + ".fetching")
    shutil.rmtree(scratch, ignore_errors=True)

    if source.is_dir():
        shutil.copytree(source, scratch)
    else:
        scratch.mkdir(parents=True)
        shutil.copy2(source, scratch / source.name)

    shutil.rmtree(into, ignore_errors=True)
    scratch.rename(into)


def _fetch_torve(item: Equipment, into: Path) -> None:
    """Package data, versioned with the engine (S-0062/D-6)."""

    from torve.application.skills import skills_root

    source = skills_root() / item.locator

    if not source.is_dir():
        raise EquipmentError(
            f"{item.source}: this engine ships no {item.locator!r} — "
            "a `torve:` source names package data, which is versioned with the engine"
        )

    into.parent.mkdir(parents=True, exist_ok=True)
    scratch = into.with_name(into.name + ".fetching")
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(source, scratch)
    shutil.rmtree(into, ignore_errors=True)
    scratch.rename(into)


def warm(items: Sequence[Equipment], *, root: Path, cache: Path | None = None) -> list[Path]:
    """Fetch what is cold and return where each item landed, in order.

    Host-side and never inside an attempt (S-0062/D-4, S-0062/I-1): an attempt
    that has to reach the internet to be equipped is an attempt whose failures
    include the internet's.
    """

    cache = cache or cache_root()
    landed: list[Path] = []

    for item in items:
        where = item_path(item, cache)

        if not where.is_dir():
            if item.scheme == "github":
                _fetch_github(item, where)

            elif item.scheme == "local":
                _fetch_local(item, where, root=root)

            else:
                _fetch_torve(item, where)

        landed.append(where)

    return landed


# ....................... #


def mount_name(item: Equipment) -> str:
    """What this item is called under the mount.

    The last segment of its locator, which is what the thing is actually named
    — `house-voice`, `caveman` — reduced to the characters a harness will take.
    dsh refuses a skill whose directory is not a valid skill name, so the
    escaped cache key cannot be the name here even though it is the name in the
    cache.
    """

    tail = item.locator.rstrip("/").rsplit("/", 1)[-1]
    safe = "".join(character if character.isalnum() else "-" for character in tail).strip("-")

    return safe or item.kind


def _link_tree(source: Path, target: Path) -> None:
    """The item under the mount, by hard link where the filesystem allows it.

    A mount is per-seat and the cache is shared, so a copy would duplicate every
    skill for every attempt. Hard links cost a directory entry, survive a bind
    mount — unlike a symlink, whose target would have to be mounted too — and
    cannot be edited through the read-only mount anyway.
    """

    try:
        shutil.copytree(source, target, copy_function=os.link)

    except OSError:
        # A different filesystem, or one without hard links. Correctness first.
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target)


def mount_root(items: Sequence[Equipment], *, root: Path, cache: Path | None = None) -> Path | None:
    """The directory a seat mounts, or None when it was given nothing.

    Named by a digest of the declaration, so two seats declaring the same
    equipment share one — and a seat whose declaration changed gets a different
    directory rather than a stale one repaired in place.
    """

    if not items:
        return None

    cache = cache or cache_root()
    keys = [f"{item.kind}/{item.key}" for item in items]
    digest = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]
    where = cache / MOUNTS_DIR / digest

    if (where / MANIFEST).is_file():
        return where

    warm(items, root=root, cache=cache)
    scratch = where.with_name(where.name + ".building")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    entries = []

    taken: dict[str, int] = {}

    for item in items:
        # Named as the thing is named, not as the cache keys it. A directory
        # name is not private bookkeeping: dsh refuses a skill whose directory
        # is not a valid skill name, and claude puts this name in a flag an
        # operator reads. The key disambiguates only where two items would
        # otherwise collide.
        name = mount_name(item)
        taken[name] = taken.get(name, 0) + 1

        if taken[name] > 1:
            name = f"{name}-{hashlib.sha256(item.key.encode()).hexdigest()[:8]}"

        _link_tree(item_path(item, cache), scratch / name)
        entries.append(
            {
                "kind": item.kind,
                "source": item.source,
                "ref": item.ref,
                "select": list(item.select),
                # In-container, because the image reads this and nothing else
                # tells it where the mount landed.
                "path": f"{EQUIPMENT_MOUNT}/{name}",
            }
        )

    (scratch / MANIFEST).write_text(
        json.dumps(
            {"schema_version": MANIFEST_SCHEMA_VERSION, "items": entries},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(where, ignore_errors=True)
    scratch.rename(where)

    return where


def regime_keys(items: Sequence[Equipment]) -> list[str]:
    """What the regime hash reads: the keys, never the contents (S-0062/D-8).

    The key is the declaration, and the declaration is what an operator chose —
    hashing bytes would make a regime depend on when a fetch happened.
    """

    return sorted(f"{item.kind}/{item.key}" for item in items)
