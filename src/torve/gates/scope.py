"""`scope` — files outside `allow` or inside `deny` (RFC 0002 §6).

deny wins over allow; an empty allow means unconstrained. The task's scope
governs when a task exists; the manifest's scope otherwise. The task's own
contract and log files are implicitly in scope — other gates require the log
to exist, so a scope that forbids writing it would deadlock the task.
"""

from __future__ import annotations

from torve.config import layout
from torve.config.manifest import Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome, spec

# ----------------------- #


def check_scope(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    scope = ctx.task.scope if ctx.task is not None else ctx.manifest.scope
    implicit: set[str] = set()
    if ctx.task is not None:
        # Canonical and legacy locations alike (RFC 0013, A-12): the gate
        # judges repositories on either side of the layout migrations.
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/contract.yaml")
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/log.yaml")
        for prefix in (f"{layout.TORVE_DIR}/", ""):
            implicit.add(f"{prefix}logs/{ctx.task.id}.yaml")
            implicit.add(f"{prefix}tasks/{ctx.task.id}.yaml")

    allow = spec(scope.allow) if scope.allow else None
    deny = spec(scope.deny) if scope.deny else None

    denied: list[str] = []
    outside: list[str] = []
    for entry in ctx.diff:
        for path in filter(None, (entry.path, entry.old_path)):
            if path in implicit:
                continue
            if deny is not None and deny.match_file(path):
                denied.append(path)
            elif allow is not None and not allow.match_file(path):
                outside.append(path)

    if not denied and not outside:
        source = "task scope" if ctx.task is not None else "manifest scope"
        constraint = "unconstrained" if not scope.allow and not scope.deny else "clean"
        return BuiltinOutcome("pass", f"{len(ctx.diff)} changed path(s), {constraint} ({source})")

    lines = [f"denied path: {p}" for p in sorted(set(denied))]
    lines += [f"outside allow: {p}" for p in sorted(set(outside))]
    return BuiltinOutcome("fail", "\n".join(lines))
