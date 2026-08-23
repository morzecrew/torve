"""The task lifecycle (RFC 0001 §4), pure and above the ports — simulation
exercises handlers over ports, so anything derived below one is invisible to
it (RFC 0003 §6). Transitions are executed by the runner from facts, never by
a model; an agent reports observations, it never causes a transition.

The escalation vocabulary is deliberately closed (RFC 0001 §5.1): an
extensible enum makes telemetry incomparable across time. It is §4's list
plus `cost_anomaly` (§5.2), `killed` (RFC 0006 §5a), `underspecified`
(charter A-21) and `stale_inheritance` (charter A-22); any further addition
is an RFC amendment, not a code change.
"""

from __future__ import annotations

from enum import StrEnum

# ----------------------- #


class TaskState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    GATED = "gated"
    REVIEWED = "reviewed"
    READY = "ready"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


class EscalationReason(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    POISON_CEILING = "poison_ceiling"
    LOCKED_CONFLICT = "locked_conflict"
    MERGE_CONFLICT = "merge_conflict"
    BLOCKER_FINDING = "blocker_finding"
    GATE_INFRASTRUCTURE_FAILURE = "gate_infrastructure_failure"
    LEASE_EXPIRED = "lease_expired"
    COST_ANOMALY = "cost_anomaly"
    KILLED = "killed"
    # A contract needing three or more load-bearing decisions invented is a
    # specification defect (0003 A-18); the fix is an amendment and a
    # re-mint, never a retry.
    UNDERSPECIFIED = "underspecified"
    # A non-terminal task minted from a document that later became superseded
    # (0007 §3.3, charter A-22): its inherited decisions no longer stand.
    # Re-mint from the superseding document or abandon — never retry.
    STALE_INHERITANCE = "stale_inheritance"


# The escalation vocabulary projected onto exit codes (RFC 0011 §3, D-11.4).
# One taxonomy, two views: a new exit code requires a new escalation reason
# and vice versa, so the projection lives here beside the enum it projects.
# Codes above 5 stay unassigned — reserve, never reuse.
EXIT_OK = 0
EXIT_GATES_RED = 1
EXIT_ESCALATED = 2
EXIT_CONFIG = 3
EXIT_INFRASTRUCTURE = 4
EXIT_EXHAUSTED = 5

EXIT_BY_REASON: dict[EscalationReason, int] = {
    EscalationReason.BUDGET_EXHAUSTED: EXIT_EXHAUSTED,
    EscalationReason.POISON_CEILING: EXIT_EXHAUSTED,
    EscalationReason.COST_ANOMALY: EXIT_EXHAUSTED,
    EscalationReason.LOCKED_CONFLICT: EXIT_ESCALATED,
    EscalationReason.MERGE_CONFLICT: EXIT_ESCALATED,
    EscalationReason.BLOCKER_FINDING: EXIT_ESCALATED,
    EscalationReason.KILLED: EXIT_ESCALATED,
    EscalationReason.UNDERSPECIFIED: EXIT_ESCALATED,
    EscalationReason.STALE_INHERITANCE: EXIT_ESCALATED,
    EscalationReason.GATE_INFRASTRUCTURE_FAILURE: EXIT_INFRASTRUCTURE,
    EscalationReason.LEASE_EXPIRED: EXIT_INFRASTRUCTURE,
}


# gated -> running is the retry loop: red gates send the attempt counter back
# through running, where it increments and meets the poison ceiling.
# claimed -> escalated covers a runner that died between claim and first
# dispatch — the reaper's lease_expired verdict needs a legal exit from
# claimed (a decision logged in T-0003).
# ready -> escalated is the lane's conflict edge (charter A-26, RFC 0006
# D-6.10): a candidate whose rebase conflicts is handed back to a human
# with reason merge_conflict; no other actor takes this edge.
TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.CLAIMED}),
    TaskState.CLAIMED: frozenset({TaskState.RUNNING, TaskState.ESCALATED}),
    TaskState.RUNNING: frozenset({TaskState.GATED, TaskState.ESCALATED}),
    TaskState.GATED: frozenset({TaskState.REVIEWED, TaskState.RUNNING, TaskState.ESCALATED}),
    TaskState.REVIEWED: frozenset({TaskState.READY, TaskState.ESCALATED}),
    TaskState.READY: frozenset({TaskState.ESCALATED}),
    TaskState.ESCALATED: frozenset({TaskState.QUEUED, TaskState.ABANDONED}),
    TaskState.ABANDONED: frozenset(),
}

# `ready` is not `merged` (the engine stops at mergeable); `abandoned` is the
# human's terminal verdict. `escalated` is neither — it waits on a human.
# TERMINAL means terminal to the ENGINE — sweepable, kill-refused, never
# re-dispatched. `ready` keeps one exit anyway: the lane's conflict edge
# above, which is a landing failing, not the engine resuming.
TERMINAL = frozenset({TaskState.READY, TaskState.ABANDONED})


class IllegalTransition(Exception):
    def __init__(self, current: TaskState, to: TaskState) -> None:
        super().__init__(f"illegal transition {current} -> {to}; "
                         f"legal: {sorted(TRANSITIONS[current])}")
        self.current, self.to = current, to


def check_transition(current: TaskState, to: TaskState) -> None:
    if to not in TRANSITIONS[current]:
        raise IllegalTransition(current, to)
