"""Where Torve's files live in a consuming repository (RFC 0013, D-13.1;
one directory per task per charter A-12, D-A.13).

Everything sits under `.torve/` — the root stays clean — and each task owns
one directory, `.torve/tasks/T-nnnn/`, holding `contract.yaml` and (once
anything was written) `log.yaml`, so retention is "remove the directory".
The layouts that predate the moves — flat `.torve/tasks/T-nnnn.yaml` with
`.torve/logs/`, and the root-level `gates.yaml`/`torve.yaml`/`tasks/`/`logs/`
— resolve as fallbacks so migrating costs one move, not a flag day. When no
location has the file, resolution returns the canonical path, so error
messages and new writes point at the layout a repository should have.

No layering (D-13.4): a lookup returns exactly one path, and overrides are
explicit CLI flags, never a second file merged over the first.
"""

from __future__ import annotations

from pathlib import Path

# ----------------------- #

TORVE_DIR = ".torve"


def _resolve(*candidates: Path) -> Path:
    """First existing candidate, else the first (canonical) one."""
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def gates_file(root: Path) -> Path:
    """The gate manifest — always local to the repository being checked,
    never inherited or merged from a parent (RFC 0013 §3)."""
    return _resolve(root / TORVE_DIR / "gates.yaml", root / "gates.yaml")


def config_file(root: Path) -> Path:
    """Runner configuration — read from where the runner was launched, never
    from the repository under work (D-13.3): a repository being operated on
    does not get to configure the engine operating on it."""
    return _resolve(root / TORVE_DIR / "config.yaml", root / "torve.yaml")


def skills_vendor_dir(root: Path) -> Path:
    """Vendored skills (RFC 0009 §4a, D-9.11): committed, reviewed
    directories resolving beside shipped skills at materialization."""
    return root / TORVE_DIR / "skills-vendor"


def task_dir(root: Path, task_id: str) -> Path:
    """One directory per task (A-12, D-A.13) — the unit retention removes."""
    return root / TORVE_DIR / "tasks" / task_id


def task_file(root: Path, task_id: str) -> Path:
    return _resolve(
        task_dir(root, task_id) / "contract.yaml",
        root / TORVE_DIR / "tasks" / f"{task_id}.yaml",
        root / "tasks" / f"{task_id}.yaml",
    )


def feedback_file(root: Path) -> Path:
    """ReviewFeedback records (RFC 0004 §6) — generated data, gitignored like
    the telemetry stream it sits beside."""
    return root / TORVE_DIR / "feedback.jsonl"


def log_file(root: Path, task_id: str) -> Path:
    return _resolve(
        task_dir(root, task_id) / "log.yaml",
        root / TORVE_DIR / "logs" / f"{task_id}.yaml",
        root / "logs" / f"{task_id}.yaml",
    )
