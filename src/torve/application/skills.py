"""Role-scoped skill materialization (A-3, D-9.7): the runner writes the
role's skill set into the sandbox from package data at dispatch time. Nothing
is installed into consuming repositories, so nothing can drift, and the skill
version is the Torve version by construction.

The source of truth is the repository's `skills/` directory, shipped inside
the wheel as `torve/_skills` package data; a development checkout resolves the
repository copy directly.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

# ----------------------- #


def skills_root() -> Path:
    packaged = Path(str(resources.files("torve"))) / "_skills"
    if packaged.is_dir():
        return packaged
    development = Path(__file__).resolve().parents[3] / "skills"
    if development.is_dir():
        return development
    raise RuntimeError("torve ships no skills data — broken installation")


def available() -> list[str]:
    return sorted(p.name for p in skills_root().iterdir()
                  if p.is_dir() and (p / "SKILL.md").is_file())


def materialize(role: str, dest: Path, sets: dict[str, list[str]]) -> list[str]:
    """Write the role's skill set under *dest* (one directory per skill) and
    return the names written. An unknown skill in a set is a configuration
    error, not a silent skip — a skill that quietly stops applying makes the
    telemetry lie (D-9.2)."""
    names = sets.get(role, [])
    root = skills_root()
    written: list[str] = []
    for name in names:
        source = root / name
        if not (source / "SKILL.md").is_file():
            raise RuntimeError(
                f"skill set for role {role!r} names {name!r}, which torve does not ship "
                f"(available: {', '.join(available())})"
            )
        target = dest / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        written.append(name)
    return written
