"""`scope` — files outside `allow` or inside `deny` (S-0002/scope-in-detail).

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
    prefixes: set[str] = set()

    if ctx.task is not None:
        # Canonical and legacy locations alike (S-0013, S-0001/A-5): the gate
        # judges repositories on either side of the layout migrations.
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/contract.yaml")
        implicit.add(f"{layout.TORVE_DIR}/tasks/{ctx.task.id}/log.yaml")

        # S-0057/D-7, S-0058/D-6: the landing goes beside the rows it cites,
        # in the execution directory of the document the contract names —
        # written by the engine at landing, so it is the task's own like its
        # log. The directory is the identifier under the corpus the context
        # names (S-0059/D-2); no lookup.
        if ctx.task.spec:
            prefixes.add(f"{ctx.specs.rstrip('/')}/{ctx.task.spec}/execution/")
        else:
            # S-0059/D-11: a task naming no document lands here instead.
            prefixes.add(f"{layout.TORVE_DIR}/execution/")

        for prefix in (f"{layout.TORVE_DIR}/", ""):
            implicit.add(f"{prefix}logs/{ctx.task.id}.yaml")
            implicit.add(f"{prefix}tasks/{ctx.task.id}.yaml")

    allow = spec(scope.allow) if scope.allow else None
    deny = spec(scope.deny) if scope.deny else None

    denied: list[str] = []
    outside: list[str] = []

    for entry in ctx.diff:
        for path in filter(None, (entry.path, entry.old_path)):
            if path in implicit or any(path.startswith(prefix) for prefix in prefixes):
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
