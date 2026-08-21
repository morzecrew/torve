"""`no-test-tampering` — tests edited where the task did not license it
(RFC 0002 §4).

A test edit is licensed when the file falls inside the task's `scope.allow`
(an empty allow licenses everything, and is already loud in task review).
Adding a brand-new test file is not tampering; modifying or deleting an
existing one is what weakens a suite. Without a task there is no licence to
check against, so the gate reports skipped, never green.
"""

from __future__ import annotations

from torve.context import GateContext
from torve.gates.base import NO_TASK, BuiltinOutcome, matches_any, spec
from torve.models import Gate


def check_no_test_tampering(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK

    patterns = ctx.manifest.tests.patterns
    allow = spec(ctx.task.scope.allow) if ctx.task.scope.allow else None

    unlicensed = []
    for entry in ctx.diff:
        if entry.status == "A":
            continue
        paths = [p for p in (entry.path, entry.old_path) if p]
        for path in paths:
            if not matches_any(path, patterns):
                continue
            if allow is not None and not allow.match_file(path):
                unlicensed.append(f"{entry.status} {path}")

    if not unlicensed:
        return BuiltinOutcome("pass", "no unlicensed edits to existing tests")
    lines = ["test files edited outside the task's scope.allow:"]
    lines += sorted(set(unlicensed))
    return BuiltinOutcome("fail", "\n".join(lines))
