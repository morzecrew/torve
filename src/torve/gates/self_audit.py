"""`self-audit` — author-side blind spots (RFC 0002 §4), non-blocking.

Until agents ship (RFC 0004) this is the deterministic core of the
discipline: the task's execution log must exist and carry the declared
`drift_count` claim — a log that only appears once something goes wrong
cannot tell a clean run from an unexamined one (D-2.10; YAML per A-1).
"""

from __future__ import annotations

from torve.context import GateContext
from torve.gates.base import NO_TASK, BuiltinOutcome
from torve.gates.decisions_reported import parse_log
from torve.models import Gate

# ----------------------- #


def check_self_audit(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK
    if ctx.log_text is None:
        return BuiltinOutcome(
            "fail",
            f"no execution log at logs/{ctx.task.id}.yaml — a clean run still "
            "declares 'drift_count: 0'",
        )
    document, parse_error = parse_log(ctx.log_text)
    if document is None:
        return BuiltinOutcome("fail", parse_error or "log did not parse")
    declared = document.get("drift_count")
    if not isinstance(declared, int):
        return BuiltinOutcome(
            "fail",
            "execution log declares no 'drift_count'; an absent claim and an "
            "honest zero must not read identically",
        )
    return BuiltinOutcome("pass", f"drift count declared: {declared}")
