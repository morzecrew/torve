"""Gate execution (RFC 0002 §3): cheapest first, fail-fast on the first
blocking failure, non-blocking gates run regardless, every result persisted.

Gates execute here, outside any agent session (D-3): outcomes are computed
from exit codes and prepared inputs, never reported by a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from torve.context import GateContext
from torve.gates import BUILTINS
from torve.gates.base import BuiltinOutcome
from torve.models import BypassRecord, Gate, GateResult
from torve.shell import run_command


@dataclass
class RunReport:
    results: list[GateResult] = field(default_factory=list)
    exit_code: int = 0

    @property
    def bypass_count_by_gate(self) -> dict[str, int]:
        return {r.name: 1 for r in self.results if r.outcome == "bypassed"}

    @property
    def flaky_count_by_command(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            for command in result.flaky_commands:
                counts[command] = counts.get(command, 0) + 1
        return counts


def _execute(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    builtin = gate.builtin
    if builtin is not None:
        return BUILTINS[builtin](gate, ctx)
    result = run_command(gate.run, ctx.root, gate.timeout or 600.0)
    if result.exit_code == 0:
        outcome = "flaky" if result.flaky else "pass"
        flaky = [gate.run] if result.flaky else []
        return BuiltinOutcome(outcome, result.output, exit_code=0, flaky_commands=flaky)
    return BuiltinOutcome("fail", result.output, exit_code=result.exit_code)


def _find_bypass(gate: Gate, ctx: GateContext) -> BypassRecord | None:
    if gate.builtin == "secrets":
        return None  # D-2.8: no bypass, ever
    for record in ctx.bypasses:
        if record.gate == gate.name:
            return record
    return None


def _log_bypass(ctx: GateContext, record: BypassRecord) -> None:
    """Append the bypass to the task's execution log (D-2.7) — same
    append-only file; the log checker reads only ```divergence blocks and
    ignores this one, but a human triaging the task does not."""
    if ctx.log_path is None:
        return
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (
        f"\n```bypass\ngate: {record.gate}\nreason: {record.reason}\n"
        f"author: {record.author}\ncommit: {record.commit}\nat: {stamp}\n```\n"
    )
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.log_path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def run_gates(ctx: GateContext, only: set[str] | None = None) -> RunReport:
    gates = ctx.manifest.resolved_gates()
    if only is not None:
        unknown = only - {g.name for g in gates}
        if unknown:
            raise ValueError(f"unknown gate(s): {', '.join(sorted(unknown))}")
        gates = [g for g in gates if g.name in only]

    # Cheapest first, by declared timeout as the cost proxy; manifest order
    # breaks ties, so ordering stays deterministic and reviewable.
    ordered = sorted(enumerate(gates), key=lambda pair: (pair[1].timeout or 0.0, pair[0]))

    report = RunReport()
    blocking_failed = False
    for _, gate in ordered:
        if blocking_failed and gate.blocking:
            report.results.append(
                GateResult(
                    name=gate.name,
                    outcome="skipped",
                    blocking=True,
                    sha=ctx.head_sha,
                    output="not run: an earlier blocking gate failed",
                )
            )
            continue

        started = time.monotonic()
        try:
            outcome = _execute(gate, ctx)
        except Exception as exc:  # noqa: BLE001 — gate machinery broke, not the code under test
            outcome = BuiltinOutcome("error", f"gate infrastructure failure: {exc!r}")
        duration = time.monotonic() - started

        bypass = None
        if outcome.outcome in ("fail", "error"):
            bypass = _find_bypass(gate, ctx)
        result = GateResult(
            name=gate.name,
            outcome="bypassed" if bypass else outcome.outcome,
            blocking=gate.blocking,
            exit_code=outcome.exit_code,
            duration_s=round(duration, 3),
            sha=ctx.head_sha,
            output=outcome.output,
            log_ref=str(ctx.log_path.relative_to(ctx.root)) if ctx.log_path else None,
            bypass=bypass,
            flaky_commands=outcome.flaky_commands,
            quarantined_failures=outcome.quarantined_failures,
        )
        if bypass:
            _log_bypass(ctx, bypass)
        report.results.append(result)

        if result.outcome in ("fail", "error") and gate.blocking:
            blocking_failed = True

    report.exit_code = 1 if blocking_failed else 0
    return report
