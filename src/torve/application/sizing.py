"""Pre-dispatch size estimate (RFC 0002 §6b, D-2.9): file-count in allow,
number of acceptance commands, presence of more than one module.

Rules of thumb, and a function rather than a policy object (A-49) — the
`HistoricalPercentile` arm the protocol was shaped for needs the attempt
store and retrospective calibration, and neither exists. Until it does,
observations are carried by the telemetry records themselves.
"""

from __future__ import annotations

from torve.domain.attempt import SizeVerdict
from torve.domain.task import Task

# ----------------------- #

MAX_ALLOW_GLOBS = 10
MAX_ACCEPTANCE = 6
MAX_MODULES = 1


# ....................... #


def estimate(task: Task) -> SizeVerdict:
    reasons: list[str] = []
    if len(task.scope.allow) > MAX_ALLOW_GLOBS:
        reasons.append(f"{len(task.scope.allow)} allow globs (threshold {MAX_ALLOW_GLOBS})")
    if len(task.acceptance) > MAX_ACCEPTANCE:
        reasons.append(f"{len(task.acceptance)} acceptance commands (threshold {MAX_ACCEPTANCE})")
    modules = {glob.split("/", 1)[0] for glob in task.scope.allow if "/" in glob}
    if len(modules) > MAX_MODULES:
        listed = ", ".join(sorted(modules))
        reasons.append(f"touches {len(modules)} top-level modules: {listed}")
    if reasons:
        return SizeVerdict(size="too_large", reasons=reasons)
    if not task.scope.allow and not task.acceptance:
        return SizeVerdict(
            size="too_small",
            reasons=["no scope and no acceptance — not worth the machinery"],
        )
    return SizeVerdict(size="ok")
