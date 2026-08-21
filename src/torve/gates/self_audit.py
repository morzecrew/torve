"""`self-audit` — author-side blind spots (RFC 0002 §4), non-blocking.

Until agents ship (RFC 0004) this is the deterministic core of the
discipline: a *written* log must carry the declared `drift_count` claim —
an absent claim and an honest zero must not read identically (D-2.10; YAML
per A-1). A missing or empty log is legal per A-13/D-3.21: the file is
created by writing, absence IS the empty log, and this gate does not demand
one into existence.
"""

from __future__ import annotations

from torve.config.manifest import Gate
from torve.gates.context import GateContext
from torve.gates.contract import NO_TASK, BuiltinOutcome
from torve.gates.decisions_reported import parse_log

# ----------------------- #


def check_self_audit(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK
    if ctx.log_text is None or not ctx.log_text.strip():
        return BuiltinOutcome(
            "pass", "no execution log — absence is an empty log (D-3.21), nothing to audit"
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
