"""Shared shapes and helpers for builtin gates."""

from __future__ import annotations

from dataclasses import dataclass, field

import pathspec

from torve.domain.attempt import GateOutcome

# ----------------------- #


@dataclass
class BuiltinOutcome:
    outcome: GateOutcome
    output: str = ""
    exit_code: int | None = None
    flaky_commands: list[str] = field(default_factory=list)
    quarantined_failures: list[str] = field(default_factory=list)


# ....................... #


def spec(patterns: list[str]) -> pathspec.GitIgnoreSpec:
    """The one pathspec dialect the gates match in: gitignore semantics, so a
    manifest glob means what the same glob means in `.gitignore`."""

    return pathspec.GitIgnoreSpec.from_lines(patterns)


# ....................... #

NO_TASK = BuiltinOutcome(
    outcome="skipped",
    output=(
        "no task contract on this run; recorded as skipped, not as green "
        "(degraded mode — the check is meaningless without a spec)"
    ),
)
