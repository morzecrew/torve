"""Role-scoped skill materialization (S-0009/A-1, S-0009/D-7): the runner writes the
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


# ....................... #


def available() -> list[str]:
    return sorted(
        p.name for p in skills_root().iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )


# ....................... #


def skill_dir(name: str) -> Path:
    """A shipped skill's directory, or a refusal naming what does ship."""

    directory = skills_root() / name

    if not (directory / "SKILL.md").is_file():
        raise LookupError(f"no skill named {name!r} ships (available: {', '.join(available())})")

    return directory


# ....................... #


def description(name: str) -> str:
    """The skill's own one-line description, from its frontmatter."""

    for line in (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip()

    return ""


# ....................... #


def read_skill(name: str) -> str:
    """A shipped skill's `SKILL.md`, whole."""

    return (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")


# ....................... #


def read_reference(name: str, reference: str) -> str:
    """One file under a shipped skill's `references/` directory, by name with
    or without its `.md` suffix."""

    base = skill_dir(name) / "references"

    for candidate in (base / reference, base / f"{reference}.md"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")

    raise LookupError(f"skill {name!r} has no reference {reference!r}")


# ....................... #


def materialize(
    role: str, dest: Path, sets: dict[str, list[str]], vendor_root: Path | None = None
) -> list[str]:
    """Write the role's skill set under *dest* (one directory per skill) and
    return the names written. A name resolves against package data and the
    repository's vendored directory together (S-0009/vendored-skills, S-0009/D-11); a name
    present in both is refused in both directions (S-0009/D-12) — a vendored
    variant of a parsed skill drifts against its gate — and a name unknown
    to both is a configuration error, not a silent skip: a skill that
    quietly stops applying makes the telemetry lie (S-0009/D-2)."""

    names = sets.get(role, [])
    packaged = skills_root()
    written: list[str] = []

    for name in names:
        shipped = packaged / name
        vendored = vendor_root / name if vendor_root is not None else None
        has_shipped = (shipped / "SKILL.md").is_file()
        has_vendored = vendored is not None and (vendored / "SKILL.md").is_file()

        if has_shipped and has_vendored:
            raise RuntimeError(
                f"skill {name!r} is both shipped and vendored — refused, never "
                "shadowed in either direction: a vendored variant of a parsed "
                "skill drifts against the gate that reads its output"
            )

        if not (has_shipped or has_vendored):
            raise RuntimeError(
                f"skill set for role {role!r} names {name!r}, which is neither "
                f"shipped (available: {', '.join(available())}) nor vendored "
                "in this repository"
            )

        source = shipped if has_shipped else vendored
        assert source is not None
        target = dest / name

        if target.exists():
            shutil.rmtree(target)

        # The projections are torve's corpus, never the adopter's: a sandbox
        # copy carries the skill's text and nothing of the rows beside it
        # (S-0100/D-7).
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("AGENTS.md"))
        written.append(name)

    return written
