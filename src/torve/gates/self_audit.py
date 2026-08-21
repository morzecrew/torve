"""`self-audit` — author-side blind spots (RFC 0002 §4), non-blocking.

Until a runner and agents exist (RFC 0004) this is the deterministic core of
the discipline: the task's execution log must exist and carry a `Drift count`
audit line, per the flag-dont-flip convention — a log that only appears once
something goes wrong cannot tell a clean run from an unexamined one. Settled
with the charter owner 2026-08-21; departure from the RFC table's `worktree`
input is logged in logs/T-0002.md.
"""

from __future__ import annotations

import re

from torve.context import GateContext
from torve.gates.base import NO_TASK, BuiltinOutcome
from torve.models import Gate

DRIFT_COUNT = re.compile(r"^\*\*Drift count:\s*(\d+)", re.M)


def check_self_audit(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK
    if ctx.log_text is None:
        return BuiltinOutcome(
            "fail",
            f"no execution log at logs/{ctx.task.id}.md — a clean run still "
            "writes 'Drift count: 0'",
        )
    counts = DRIFT_COUNT.findall(ctx.log_text)
    if not counts:
        return BuiltinOutcome(
            "fail",
            "execution log has no '**Drift count: N**' line; an absent count "
            "and an honest zero must not read identically",
        )
    return BuiltinOutcome("pass", f"drift count declared: {counts[-1]} (last of {len(counts)})")
