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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, cast

from torve.base.clock import stamp
from torve.base.globs import matches
from torve.base.shell import run_command
from torve.config.manifest import SHELL_GATE_TIMEOUT, Gate
from torve.domain.attempt import BypassRecord, GateResult
from torve.domain.spec import Invariant
from torve.domain.task import InheritedDecision, Task
from torve.domain.vocabulary import GateOutcome
from torve.gates import BUILTINS
from torve.gates.acceptance import selected_nothing
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome
from torve.gates.decisions_reported import check_decisions_reported

# ----------------------- #

#: Every contract-borne gate's name carries this prefix (`decision:<id>`), so
#: the runner tells a row's check from a manifest entry by name alone.
DECISION_PREFIX = "decision:"
#: A standing invariant's check as a gate (`invariant:<id>`), likewise told
#: from a manifest entry by name alone (S-0106/D-2).
INVARIANT_PREFIX = "invariant:"


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


def row_gates(rows: Iterable[InheritedDecision]) -> list[Gate]:
    """Inherited rows as `decision:<id>` gates (S-0054/D-2): one shell gate per
    row with a `check`, under the compliance axis, at the row's `check_state`
    — `shadow` until an amendment promotes it (S-0054/D-4) — with the row's
    twin as its sabotage reference. One reader for a task's contract rows and
    a document's inherited ones (S-0106/D-2), so the two batteries judge a row
    the same way."""

    gates: list[Gate] = []

    for row in rows:
        if not row.check:
            continue

        gates.append(
            Gate(
                name=f"{DECISION_PREFIX}{row.id}",
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


def decision_gates(task: Task | None) -> list[Gate]:
    """The contract's checkable rows as gates (S-0054 S-0054/D-2).

    Contract-borne: never written into the manifest, and gone with the
    contract. Read from the contract alone, never the corpus (S-0007/D-18).
    Takes the contract rather than the run context: the same list answers
    `torve gates list`, which has no repository to build a context against."""

    if task is None:
        return []

    return row_gates(task.decisions)


def invariant_gates(invariants: Iterable[Invariant]) -> list[Gate]:
    """Standing invariants as gates (S-0106/D-2): one `invariant:<id>` shell
    gate per invariant, always `shadow` — an invariant is a statement with a
    check, never a grade to block under. Its paths are the gate's, so an
    invariant the diff does not reach is reported skipped rather than run for
    nothing."""

    return [
        Gate(
            name=f"{INVARIANT_PREFIX}{invariant.id}",
            run=invariant.check,
            state="shadow",
            origin=invariant.id,
            axis="compliance",
            input="worktree",
            timeout=SHELL_GATE_TIMEOUT,
            paths=list(invariant.paths),
        )
        for invariant in invariants
    ]


# ....................... #


def _execute(gate: Gate, ctx: GateContext, checks: dict[str, str] | None = None) -> BuiltinOutcome:
    builtin = gate.builtin

    if builtin == "decisions-reported":
        # The silence check reads a checked row's verdict from this pass's own
        # decision gates, so a failing check leaves its row owed (S-0106/D-1).
        return check_decisions_reported(gate, ctx, checks=checks)

    if builtin is not None:
        return BUILTINS[builtin](gate, ctx)

    command = _substitute_base(gate, ctx)
    result = run_command(command, ctx.root, gate.timeout or 600.0, execute=ctx.execute)

    # A contract-borne gate whose command selects no test proves nothing: for a
    # decision gate, nothing about its row (S-0106/D-1); for an invariant gate,
    # nothing about the rule, so a stale `-k` selection reads as stale rather
    # than as a broken rule (S-0113/D-6). pytest's exit 5, or a summary
    # reporting no test ran — the reading `acceptance` makes — is a failure by
    # its own name, under the gate's own state. Any other shell gate keeps the
    # plain exit-code verdict.
    if gate.name.startswith((DECISION_PREFIX, INVARIANT_PREFIX)) and selected_nothing(
        result.exit_code, result.output
    ):
        return BuiltinOutcome(
            "fail",
            f"the check selected nothing\n\n{result.output}",
            exit_code=result.exit_code,
        )

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
            "at": stamp(),
        }
    )

    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)

    ctx.log_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


# ....................... #


def _judges_anything(gate: Gate, ctx: GateContext) -> bool:
    """Does the attempt's diff touch what this gate judges (S-0071/D-6)?

    A gate's paths are read through the same glob reading a contract's scope
    is (S-0105/D-4), so one idea has one grammar: `src/**` is everything under
    a directory, and what is under a matched path matches with it.
    """

    changed = [*ctx.changed_paths, *ctx.untracked]

    return any(matches(path, gate.paths) for path in changed)


# ....................... #


def battery(ctx: GateContext, only: set[str] | None = None) -> list[Gate]:
    """The gates a pass over *ctx* runs, in the order it runs them."""

    gates = [
        *ctx.manifest.resolved_gates(),
        *decision_gates(ctx.task),
        *row_gates(ctx.inherited),
        *invariant_gates(ctx.invariants),
    ]

    if only is not None:
        unknown = only - {g.name for g in gates}

        if unknown:
            raise ValueError(f"unknown gate(s): {', '.join(sorted(unknown))}")

        gates = [g for g in gates if g.name in only]

    # The contract's decision gates run first, because `decisions-reported`
    # reads their verdicts to know whether a checked row is covered (S-0106/D-1):
    # a check that has not run yet cannot license a row's silence. The manifest's
    # own gates then run cheapest-first, by declared timeout as the cost proxy;
    # manifest order breaks ties, so ordering stays deterministic and reviewable.
    decision = [g for g in gates if g.name.startswith(DECISION_PREFIX)]
    rest = [g for g in gates if not g.name.startswith(DECISION_PREFIX)]

    return [*decision, *sorted(rest, key=lambda g: g.timeout or 0.0)]


def battery_budget(gates: Iterable[Gate], ctx: GateContext) -> float:
    """The longest a pass over *gates* can take, in seconds: an upper bound, not
    an estimate. The gates run one after another, every command a gate runs is
    bounded by its timeout, and a failed command runs once more (`run_command`);
    acceptance runs each of its commands. A gate whose paths the attempt did
    not touch is skipped, as `run_gates` skips it.

    A pass records nothing until its verdict, so this is the silence a lease
    must allow before it may call the pass's holder gone.
    """

    total = 0.0

    for gate in gates:
        if gate.paths and not _judges_anything(gate, ctx):
            continue

        commands = 1

        if gate.builtin == "acceptance":
            commands = len(ctx.task.acceptance if ctx.task is not None else gate.commands) or 1

        total += 2 * commands * (gate.timeout or SHELL_GATE_TIMEOUT)

    return total


def run_gates(
    ctx: GateContext, only: set[str] | None = None, progress: Callable[[str], None] | None = None
) -> RunReport:
    ordered = battery(ctx, only)
    report = RunReport()
    blocking_failed = False
    checks: dict[str, str] = {}

    # Every gate runs, including after a blocking one has failed (T-0234).
    # Short-circuiting there made the severity ladder inert: with the
    # cheapest-first order above, a 20-second form gate hid the functional
    # verdict behind it, `retry_rung_for` saw one axis where the ladder
    # assumes several, and S-0034/D-7's boundary masking could never fire from
    # under a lighter gate. Nothing new can block — the exit code is already
    # 1 — so what this buys is the axes, at the price a green attempt
    # already pays for the same battery.
    for gate in ordered:
        if gate.paths and not _judges_anything(gate, ctx):
            # Reported, not omitted (S-0071/D-6): a gate that did not run is a
            # fact about the attempt, and a record that shows a pass would say
            # the gate had an opinion.
            report.results.append(
                GateResult(
                    name=gate.name,
                    outcome="skipped",
                    state=gate.state,
                    sha=ctx.head_sha,
                    output=(
                        f"nothing the attempt changed is under {', '.join(gate.paths)},"
                        " which is what this gate judges"
                    ),
                )
            )
            continue

        if progress is not None:
            # Presentation's window into the pass (S-0018/live-status-for-long-waits): the name of
            # the gate about to run, nothing more — the runner stays silent.
            progress(gate.name)

        started = time.monotonic()

        try:
            outcome = _execute(gate, ctx, checks)

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

        if gate.name.startswith(DECISION_PREFIX):
            checks[gate.name[len(DECISION_PREFIX) :]] = result.outcome

        if result.outcome in ("fail", "error") and gate.state == "blocking":
            blocking_failed = True

    report.exit_code = 1 if blocking_failed else 0

    return report
