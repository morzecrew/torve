"""The fleet manifest (RFC 0024 §5.1, D-24.1) — the one artefact that is
*about* repositories rather than living in one, and the reason it cannot:
D-13.3 says the repository under work configures nothing about the engine
that works on it, and a repository declaring its own trust class is that
failure in its purest form. Read from the operator's machine, never from a
root the fleet ticks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

# ----------------------- #


class FleetRepository(BaseModel):
    """One manifest entry. `trust` names the capability class a repository
    is granted (§5.3) — never defaulted, since a class nobody wrote down is
    a grant nobody reviewed."""

    model_config = ConfigDict(extra="forbid")
    root: str
    trust: Literal["own", "reviewed", "untrusted"]

    # ....................... #

    @property
    def path(self) -> Path:
        return Path(self.root).expanduser().resolve()


# ....................... #


class FleetAttention(BaseModel):
    """The shared budget (§5.1, D-24.2): triage debt measured once, across
    every repository, because the operator triaging it exists once."""

    model_config = ConfigDict(extra="forbid")
    pause_escalations: int = 1


# ....................... #


class FleetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repositories: list[FleetRepository] = Field(default_factory=list)
    attention: FleetAttention = Field(default_factory=FleetAttention)
    # Deterministic, never a priority field (D-24.4): a fleet that ticks
    # roots in a chosen order is one config change from being a scheduler
    # with opinions.
    order: Literal["manifest", "alphabetical"] = "manifest"

    # ....................... #

    def ticking_order(self) -> list[FleetRepository]:
        if self.order == "alphabetical":
            return sorted(self.repositories, key=lambda repo: repo.root)

        return list(self.repositories)


# ....................... #


def default_manifest_path() -> Path:
    """`~/.config/torve/fleet.yaml` (§5.1) — XDG_CONFIG_HOME when set, the
    convention every other XDG-aware tool on the operator's machine already
    follows. An explicit `--manifest` flag is the only override (D-13.4)."""

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")

    return Path(base) / "torve" / "fleet.yaml"


# ....................... #


def load_fleet_manifest(path: Path) -> FleetManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if raw is None:
        return FleetManifest()

    if not isinstance(raw, dict):
        raise ValueError(f"{path}: fleet manifest must be a mapping")

    return FleetManifest.model_validate(raw)
