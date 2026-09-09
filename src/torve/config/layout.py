"""Where Torve's files live in a consuming repository (RFC 0013, D-13.1;
one directory per task per charter A-12, D-A.13).

Everything sits under `.torve/` — the root stays clean — the specification
corpus included (RFC 0057 D-57.3): `specs/` holds the documents, `archive/`
what once stood, `schemas/` what `torve init` derives from the models; the
corpus path is `specs.path` in the runner's configuration and its siblings
follow it. Each task owns
one directory, `.torve/tasks/T-nnnn/`, holding `contract.yaml` and (once
anything was written) `log.yaml`. With a store configured the directory is
a projection (RFC 0056 D-56.9): the board holds the task, `torve plan`
writes no file, and dispatch writes the contract into the worktree for the
attempt that reads it; without a store the files are the record.

One layout, no fallbacks (A-48): the pre-`.torve/` layouts resolved here
until the package had a released version to be compatible with, and a
lookup that searches is a lookup whose answer depends on what happens to
exist. A lookup returns exactly one path, and overrides are explicit CLI
flags, never a second file merged over the first (D-13.4).
"""

from __future__ import annotations

from pathlib import Path

# ----------------------- #

TORVE_DIR = ".torve"
SPECS_DIR = f"{TORVE_DIR}/specs"  # the default of `specs.path` (D-57.3)


# ....................... #


def gates_file(root: Path) -> Path:
    """The gate manifest — always local to the repository being checked,
    never inherited or merged from a parent (RFC 0013 §3)."""

    return root / TORVE_DIR / "gates.yaml"


# ....................... #


def config_file(root: Path) -> Path:
    """Runner configuration — read from where the runner was launched, never
    from the repository under work (D-13.3): a repository being operated on
    does not get to configure the engine operating on it."""

    return root / TORVE_DIR / "config.yaml"


# ....................... #


def skills_vendor_dir(root: Path) -> Path:
    """Vendored skills (RFC 0009 §4a, D-9.11): committed, reviewed
    directories resolving beside shipped skills at materialization."""

    return root / TORVE_DIR / "skills-vendor"


# ....................... #


def standing_dir(root: Path) -> Path:
    """Standing contracts (RFC 0023 D-23.1): one committed, reviewed file
    per recurring job. No enabled flag — an empty directory is off and
    deleting a file is the disable (D-23.7)."""

    return root / TORVE_DIR / "standing"


# ....................... #


def task_dir(root: Path, task_id: str) -> Path:
    """One directory per task (A-12, D-A.13); a projection when a store holds
    the task (D-56.9), the record itself when none does."""

    return root / TORVE_DIR / "tasks" / task_id


# ....................... #


def task_file(root: Path, task_id: str) -> Path:
    return task_dir(root, task_id) / "contract.yaml"


# ....................... #


def feedback_file(root: Path) -> Path:
    """ReviewFeedback records (RFC 0004 §6) — generated data, gitignored like
    the telemetry stream it sits beside."""

    return root / TORVE_DIR / "feedback.jsonl"


# ....................... #


def log_file(root: Path, task_id: str) -> Path:
    return task_dir(root, task_id) / "log.yaml"
