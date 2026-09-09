"""Gate execution (S-0002/the-gate-contract): cheapest first, every gate run, every
result persisted. A blocking-state gate's failure sets the exit code and
does not stop the battery — the ladder that picks a retry rung reads the
axis of each conviction, and short-circuiting handed it only the cheapest
one (T-0234). Shadow and quarantined gates never touch the exit code
(S-0002/states).

Gates execute here, outside any agent session (S-0001/D-11): outcomes are computed
from exit codes and prepared inputs, never reported by a model.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from torve.base.shell import run_command
from torve.config.manifest import SHELL_GATE_TIMEOUT, Gate
from torve.domain.attempt import BypassRecord, GateResult
from torve.domain.vocabulary import GateOutcome
from torve.gates import BUILTINS
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #


@dataclass
class RunReport:
    results: list[GateResult] = field(default_factory=list)
    exit_code: int = 0

    # ....................... #

    @property
    def bypass_count_by_gate(self) -> dict[str, int]:
        return {r.name: 1 for r in self.results if r.outcome == "bypassed"}

    # ....................... #

    @property
    def flaky_count_by_command(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        for result in self.results:
            for command in result.flaky_commands:
                counts[command] = counts.get(command, 0) + 1

        return counts


# ....................... #


def _substitute_base(gate: Gate, ctx: GateContext) -> str:
    """Hand the shell the base the battery itself computed (S-0036/D-1's judgment
    surface depends on it): every `{base}` in a shell gate's command is
    replaced with the merge-base the context was built from — the exact value
    every diff-input builtin judges against, so the battery's base and a
    gate's base cannot disagree, in live runs and replays alike. No gate ever
    resolves a ref in shell; if none was resolvable (a fresh repository), the
    command's `{base}` has nothing honest to stand for and the gate errors
    rather than inventing one."""

    if "{base}" not in gate.run:
        return gate.run

    if ctx.merge_base is None:
        raise ValueError(
            f"gate {gate.name!r}: its command asks for the battery's base, "
            "but no base is resolvable against this repository"
        )

    return gate.run.replace("{base}", ctx.merge_base)


# ....................... #


def decision_gates(ctx: GateContext) -> list[Gate]:
    """The contract's checkable rows as gates (S-0054 S-0054/D-2): one
    `decision:<id>` shell gate per inherited row with a `check`, under the
    compliance axis, at the row's `check_state` — `shadow` until an
    amendment promotes it (S-0054/D-4) — with the row's twin as its sabotage
    reference. Contract-borne: never written into the manifest, and gone
    with the contract. Read from the contract alone, never the corpus
    (S-0007/D-18)."""

    if ctx.task is None:
        return []

    gates: list[Gate] = []

    for row in ctx.task.decisions:
        if not row.check:
            continue

        gates.append(
            Gate(
                name=f"decision:{row.id}",
                run=row.check,
                state=row.check_state,
                origin=row.id,  # the row is the gate's origin (S-0059/D-4)
                axis="compliance",
                input="worktree",
                timeout=SHELL_GATE_TIMEOUT,
                sabotage=row.check_twin,
            )
        )

    return gates


# ....................... #


def _execute(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    builtin = gate.builtin

    if builtin is not None:
        return BUILTINS[builtin](gate, ctx)

    command = _substitute_base(gate, ctx)
    result = run_command(command, ctx.root, gate.timeout or 600.0, execute=ctx.execute)

    if result.exit_code == 0:
        outcome: GateOutcome = "flaky" if result.flaky else "pass"
        flaky = [gate.run] if result.flaky else []

        return BuiltinOutcome(outcome, result.output, exit_code=0, flaky_commands=flaky)

    return BuiltinOutcome("fail", result.output, exit_code=result.exit_code)


# ....................... #


def _find_bypass(gate: Gate, ctx: GateContext) -> BypassRecord | None:
    if gate.builtin == "secrets":
        return None  # S-0002/D-8: no bypass, ever

    for record in ctx.bypasses:
        if record.gate == gate.name:
            return record

    return None


# ....................... #


def _log_bypass(ctx: GateContext, record: BypassRecord) -> None:
    """Append the bypass to the task's `bypasses:` list (S-0002/D-7) — the same
    S-0001/A-1 YAML log, structurally appended: items are never removed or edited,
    which is what append-only means for a structured file."""

    if ctx.log_path is None:
        return

    import yaml

    document: dict[str, Any]

    if ctx.log_path.is_file():
        loaded: Any = yaml.safe_load(ctx.log_path.read_text(encoding="utf-8")) or {}

        if not isinstance(loaded, dict):
            return  # an unreadable log is the decisions-reported gate's finding

        document = cast(dict[str, Any], loaded)
    else:
        task_id = ctx.task.id if ctx.task else ""
        entries: list[Any] = []
        document = {"schema_version": 1, "task": task_id, "drift_count": 0, "entries": entries}

    fresh: list[dict[str, str]] = []

    cast(list[dict[str, str]], document.setdefault("bypasses", fresh)).append(
        {
            "gate": record.gate,
            "reason": record.reason,
            "author": record.author,
            "commit": record.commit,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)

    ctx.log_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


# ....................... #


def run_gates(
    ctx: GateContext, only: set[str] | None = None, progress: Callable[[str], None] | None = None
) -> RunReport:
    gates = [*ctx.manifest.resolved_gates(), *decision_gates(ctx)]

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

    # Every gate runs, including after a blocking one has failed (T-0234).
    # Short-circuiting there made the severity ladder inert: with the
    # cheapest-first order above, a 20-second form gate hid the functional
    # verdict behind it, `retry_rung_for` saw one axis where the ladder
    # assumes several, and S-0034/D-7's boundary masking could never fire from
    # under a lighter gate. Nothing new can block — the exit code is already
    # 1 — so what this buys is the axes, at the price a green attempt
    # already pays for the same battery.
    for _, gate in ordered:
        if progress is not None:
            # Presentation's window into the pass (S-0018/live-status-for-long-waits): the name of
            # the gate about to run, nothing more — the runner stays silent.
            progress(gate.name)

        started = time.monotonic()

        try:
            outcome = _execute(gate, ctx)

        except Exception as exc:
            outcome = BuiltinOutcome("error", f"gate infrastructure failure: {exc!r}")

        duration = time.monotonic() - started

        bypass = None

        if outcome.outcome in ("fail", "error") and gate.state == "blocking":
            # A bypass on a gate that cannot block would be a signature spent
            # on nothing; shadow failures are measurement, not obstacles.
            bypass = _find_bypass(gate, ctx)

        result = GateResult(
            name=gate.name,
            outcome="bypassed" if bypass else outcome.outcome,
            state=gate.state,
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

        if result.outcome in ("fail", "error") and gate.state == "blocking":
            blocking_failed = True

    report.exit_code = 1 if blocking_failed else 0

    return report
