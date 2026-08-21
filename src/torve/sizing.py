"""`SizePolicy` — pre-dispatch size estimate and post-hoc calibration
(RFC 0002 §6b, D-2.9). Static thresholds first; `HistoricalPercentile` over
telemetry arrives once there is telemetry to read.
"""

from __future__ import annotations

from typing import Protocol

from torve.models import SizeVerdict, Task


class SizePolicy(Protocol):
    def estimate(self, task: Task) -> SizeVerdict: ...

    def observe(self, attempt: dict[str, object]) -> None: ...


class StaticThresholds:
    """File-count in allow, number of acceptance commands, presence of more
    than one module. Rules of thumb until data exists (RFC 0002 §6b)."""

    def __init__(
        self,
        max_allow_globs: int = 10,
        max_acceptance: int = 6,
        max_modules: int = 1,
    ) -> None:
        self.max_allow_globs = max_allow_globs
        self.max_acceptance = max_acceptance
        self.max_modules = max_modules

    def estimate(self, task: Task) -> SizeVerdict:
        reasons = []
        if len(task.scope.allow) > self.max_allow_globs:
            reasons.append(
                f"{len(task.scope.allow)} allow globs (threshold {self.max_allow_globs})"
            )
        if len(task.acceptance) > self.max_acceptance:
            reasons.append(
                f"{len(task.acceptance)} acceptance commands (threshold {self.max_acceptance})"
            )
        modules = {glob.split("/", 1)[0] for glob in task.scope.allow if "/" in glob}
        if len(modules) > self.max_modules:
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

    def observe(self, attempt: dict[str, object]) -> None:
        """Calibration is retrospective (iterations-to-green) and needs the
        attempt store from RFC 0003; until then observations are carried by
        the telemetry records themselves."""
