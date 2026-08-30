"""`torve fleet tick` and `torve fleet status`'s mechanism (RFC 0024 §5.2,
§5.4): survey every root's escalation queue, decide the pause once for the
fleet, check each root's own configuration against its manifest trust class
(§5.3, D-24.6) before ticking it in the manifest's deterministic order under
its own lock with the pause decision passed down, and read every root's
queue into one table ordered by age. No fleet lock (D-24.7) — the per-root
lock inside `run_tick` is the only mutual exclusion. No fleet store (D-24.3):
every function here reads roots and writes to roots, never to a shared
artefact of its own.

`tick` is injected (a `TickRunner`) rather than built here: wiring one
root's `TickDeps` needs adapters, and `torve.application` may not import
`torve.adapters` (RFC 0015 §6) — that construction is `torve.cli.fleet`'s
job, exactly as `torve.cli.tick` builds it for a solo tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from torve.application.loop import TickReport, escalated_count
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config.fleet import FleetManifest, FleetRepository, TrustRefused, enforce_trust
from torve.config.runconfig import load_runner_config
from torve.domain.states import TaskState

# ----------------------- #

# One root's worth of tick, given the fleet's pause decision — built by the
# CLI, which alone may wire the adapters a real tick needs.
TickRunner = Callable[[FleetRepository, bool], TickReport]


# ....................... #


@dataclass
class RootOutcome:
    root: str
    trust: str
    escalated: int
    outcome: str  # "ticked" | "locked out" | "error: <detail>"
    noop: bool


# ....................... #


@dataclass
class FleetReport:
    escalated_total: int
    paused: bool
    outcomes: list[RootOutcome]


# ....................... #


def survey(manifest: FleetManifest) -> dict[str, int]:
    """Leg 1 (§5.2): each root's escalation queue, read the same way
    `torve status` and the tick itself already do (`escalated_count`) — no
    new source, and the count a fleet decision is based on is the same
    count each root's own tick will report."""

    return {repo.root: escalated_count(repo.path) for repo in manifest.ticking_order()}


# ....................... #


def decide_pause(manifest: FleetManifest, counts: dict[str, int]) -> tuple[int, bool]:
    """Leg 2 (§5.2, D-24.2): the pause is decided once, for the fleet
    total — never per root."""

    total = sum(counts.values())

    return total, total >= manifest.attention.pause_escalations


# ....................... #


def fleet_tick(manifest: FleetManifest, tick: TickRunner) -> FleetReport:
    """Legs 3 and 4: for each root in the manifest's order, its own runner
    configuration is checked against its trust class (§5.3, D-24.6) before
    the tick — a refusal is recorded and the pass continues, exactly like a
    locked-out or failing root (D-24.5), and never reaches `tick`. The pause
    decision is passed down to every root that is ticked. One fleet event —
    the queue total, the pause decision, and every root's outcome — appended
    to each ticked root's own telemetry (D-24.11: no fleet-side stream exists
    to hold it instead, per D-24.3)."""

    counts = survey(manifest)
    total, paused = decide_pause(manifest, counts)
    outcomes: list[RootOutcome] = []

    for repo in manifest.ticking_order():
        escalated = counts[repo.root]

        try:
            enforce_trust(repo, load_runner_config(repo.path))
            report = tick(repo, paused)
            outcome = "locked out" if report.locked_out else "ticked"
            noop = report.noop

        except TrustRefused as exc:  # D-24.6: refused before the root is ticked
            outcome, noop = f"refused: {exc}", True

        except Exception as exc:  # D-24.5: recorded, the pass continues
            outcome, noop = f"error: {exc}", True

        outcomes.append(
            RootOutcome(
                root=repo.root, trust=repo.trust, escalated=escalated, outcome=outcome, noop=noop
            )
        )

    event = {
        "escalated_total": total,
        "paused": paused,
        "roots": [asdict(o) for o in outcomes],
    }

    for repo in manifest.ticking_order():
        try:
            engine_event(repo.path, "fleet_tick", event)

        except OSError:
            continue  # this root's telemetry write failed; the pass already ran

    return FleetReport(escalated_total=total, paused=paused, outcomes=outcomes)


# ....................... #


@dataclass
class EscalationRow:
    root: str
    task_id: str
    reason: str
    detail: str
    age_s: float


# ....................... #


def fleet_escalations(manifest: FleetManifest) -> list[EscalationRow]:
    """`torve fleet status` (§5.4, D-24.8): every root's escalation queue in
    one table, oldest first — RFC 0006's primary alert (D-6.8) given its
    fleet form. Read-only over roots (D-24.3): nothing here writes."""

    rows: list[EscalationRow] = []

    for repo in manifest.repositories:
        for state in RunState.load_all(repo.path / naming.WORKTREE_DIR):
            if state.state is not TaskState.ESCALATED or state.escalation is None:
                continue

            rows.append(
                EscalationRow(
                    root=repo.root,
                    task_id=state.task_id,
                    reason=state.escalation.reason,
                    detail=state.escalation.detail,
                    age_s=state.heartbeat_age_s(),
                )
            )

    return sorted(rows, key=lambda row: row.age_s, reverse=True)
