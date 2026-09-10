"""The fleet manifest (S-0024/the-manifest-lives-with-the-operator, S-0024/D-1) — the one artefact that is
*about* repositories rather than living in one, and the reason it cannot:
S-0013/D-3 says the repository under work configures nothing about the engine
that works on it, and a repository declaring its own trust class is that
failure in its purest form. Read from the operator's machine, never from a
root the fleet ticks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from torve.base.model import STRICT
from torve.config.runconfig import RunnerConfig

# ----------------------- #

# The fleet manifest's own shape version (T-0321).
SCHEMA_VERSION = 1


class FleetRepository(BaseModel):
    """One manifest entry. `trust` names the capability class a repository
    is granted (§5.3) — never defaulted, since a class nobody wrote down is
    a grant nobody reviewed."""

    model_config = STRICT
    root: str
    """Where the repository sits on this machine; `path` resolves it."""
    trust: Literal["own", "reviewed", "untrusted"]
    """The capability class this repository is granted (§5.3), never defaulted — a class
    nobody wrote down is a grant nobody reviewed."""

    partition: str = ""
    """Which board this root's contracts are minted onto (S-0048/D-1). Declared here and
    never derived: a partition read off the git remote is convenient and wrong for a
    repository with no remote, with two, or with one that changed; declared *here*
    rather than in the root for S-0013/D-3's reason, since a repository choosing its own
    partition could mint onto a board it was never given. Empty is legal because
    `torve fleet tick` neither reads nor needs it, and the resident loop refuses an
    empty one before the root is served (S-0048/D-2)."""

    # ....................... #

    @property
    def path(self) -> Path:
        return Path(self.root).expanduser().resolve()


# ....................... #


class FleetAttention(BaseModel):
    """The shared budget (§5.1, S-0024/D-2): triage debt measured once, across
    every repository, because the operator triaging it exists once."""

    model_config = STRICT
    pause_escalations: int = 1
    """How many open escalations pause the fleet — one budget across every repository,
    because the operator triaging them exists once."""


# ....................... #


class FleetManifest(BaseModel):
    """The operator's own file (S-0024): which roots this machine ticks, how
    much attention each may consume, and in what order. It stays on the
    machine because that is what genuinely varies by machine — the repository
    under work never gets to argue with it."""

    model_config = STRICT
    schema_version: int = SCHEMA_VERSION
    """The manifest's own shape version (T-0321) — the fleet declared none at all, so
    a reader had nothing to refuse an older file by."""
    repositories: list[FleetRepository] = Field(default_factory=list)
    """The roots this machine ticks, in the order they are written unless `order` says
    otherwise."""
    attention: FleetAttention = Field(default_factory=FleetAttention)
    """The triage budget shared across every root."""
    order: Literal["manifest", "alphabetical"] = "manifest"
    """The ticking order — deterministic, never a priority field (S-0024/D-4): a fleet
    that ticks roots in a chosen order is one configuration change away from being a
    scheduler with opinions."""

    # ....................... #

    def ticking_order(self) -> list[FleetRepository]:
        if self.order == "alphabetical":
            return sorted(self.repositories, key=lambda repo: repo.root)

        return list(self.repositories)


# ....................... #


class TrustRefused(ValueError):
    """A root's own configuration asks for more than its trust class allows
    (§5.3, S-0024/D-6) — refused before the root is ticked, naming the class and
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
    naming the class and the setting, which is what turns S-0017/D-10 from a
    remembered sentence into a read refusal.
    """

    if repo.trust == "own":
        return

    if config.runtime.docker == "socket":
        raise TrustRefused(
            f"root {repo.root!r} is trust class {repo.trust!r}, which permits no "
            "runtime.docker: socket — host-equivalent capability is granted to "
            "'own' repositories only (S-0017 S-0017/D-10)"
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
                f"broker.mode: sealed (S-0021) — got broker.mode: {config.broker.mode!r}"
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
    follows. An explicit `--manifest` flag is the only override (S-0013/D-4)."""

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
