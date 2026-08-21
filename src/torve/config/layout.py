"""Where Torve's files live in a consuming repository (RFC 0013, D-13.1).

Everything sits under `.torve/` — the root stays clean. The root-level names
that predate the move (`gates.yaml`, `torve.yaml`, `tasks/`, `logs/`) resolve
as a fallback so migrating costs one move, not a flag day. When neither
location has the file, resolution returns the canonical `.torve/` path, so
error messages and new writes point at the layout a repository should have.

No layering (D-13.4): a lookup returns exactly one path, and overrides are
explicit CLI flags, never a second file merged over the first.
"""

from __future__ import annotations

from pathlib import Path

# ----------------------- #

TORVE_DIR = ".torve"


def _resolve(canonical: Path, legacy: Path) -> Path:
    if canonical.is_file():
        return canonical
    if legacy.is_file():
        return legacy
    return canonical


def gates_file(root: Path) -> Path:
    """The gate manifest — always local to the repository being checked,
    never inherited or merged from a parent (RFC 0013 §3)."""
    return _resolve(root / TORVE_DIR / "gates.yaml", root / "gates.yaml")


def config_file(root: Path) -> Path:
    """Runner configuration — read from where the runner was launched, never
    from the repository under work (D-13.3): a repository being operated on
    does not get to configure the engine operating on it."""
    return _resolve(root / TORVE_DIR / "config.yaml", root / "torve.yaml")


def task_file(root: Path, task_id: str) -> Path:
    return _resolve(
        root / TORVE_DIR / "tasks" / f"{task_id}.yaml",
        root / "tasks" / f"{task_id}.yaml",
    )


def log_file(root: Path, task_id: str) -> Path:
    return _resolve(
        root / TORVE_DIR / "logs" / f"{task_id}.yaml",
        root / "logs" / f"{task_id}.yaml",
    )
