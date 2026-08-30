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

from torve.config.runconfig import RunnerConfig

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


class TrustRefused(ValueError):
    """A root's own configuration asks for more than its trust class allows
    (§5.3, D-24.6) — refused before the root is ticked, naming the class and
    the offending setting. Raised from the operator's own file, which is
    deliberately where the repository under work cannot argue with it."""


# ....................... #


def enforce_trust(repo: FleetRepository, config: RunnerConfig) -> None:
    """Check a root's own runner configuration against its manifest trust
    class (§5.3):

    | class     | permits                          | requires                |
    | own       | runtime.docker: socket, any network | —                     |
    | reviewed  | no socket                        | providers.default empty |
    | untrusted | no socket, no network: host      | broker.mode: sealed     |

    `own` is unchecked by design — it is the class that already trusts the
    repository as its own shell. Every other class refuses with `TrustRefused`
    naming the class and the setting, which is what turns D-17.10 from a
    remembered sentence into a read refusal.
    """

    if repo.trust == "own":
        return

    if config.runtime.docker == "socket":
        raise TrustRefused(
            f"root {repo.root!r} is trust class {repo.trust!r}, which permits no "
            "runtime.docker: socket — host-equivalent capability is granted to "
            "'own' repositories only (RFC 0017 D-17.10)"
        )

    if repo.trust == "untrusted":
        if config.runtime.network == "host":
            raise TrustRefused(
                f"root {repo.root!r} is trust class 'untrusted', which permits no "
                "runtime.network: host"
            )

        if config.broker.mode != "sealed":
            raise TrustRefused(
                f"root {repo.root!r} is trust class 'untrusted', which requires "
                f"broker.mode: sealed (RFC 0021) — got broker.mode: {config.broker.mode!r}"
            )

        return

    # reviewed
    if config.providers.default:
        raise TrustRefused(
            f"root {repo.root!r} is trust class 'reviewed', which requires an "
            "explicit per-repository provider allowlist, not providers.default "
            f"— got providers.default: {config.providers.default!r}"
        )


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
