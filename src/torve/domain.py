"""The task lifecycle (RFC 0001 §4), pure and above the ports — simulation
exercises handlers over ports, so anything derived below one is invisible to
it (RFC 0003 §6). Transitions are executed by the runner from facts, never by
a model; an agent reports observations, it never causes a transition.

The escalation vocabulary is deliberately closed (RFC 0001 §5.1): an
extensible enum makes telemetry incomparable across time. It is §4's list
plus `cost_anomaly` (§5.2) and `killed` (RFC 0006 §5a); any further addition
is an RFC amendment, not a code change.
"""

from __future__ import annotations

from enum import StrEnum


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


# gated -> running is the retry loop: red gates send the attempt counter back
# through running, where it increments and meets the poison ceiling.
# claimed -> escalated covers a runner that died between claim and first
# dispatch — the reaper's lease_expired verdict needs a legal exit from
# claimed (logged decision, logs/T-0003.md).
TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.CLAIMED}),
    TaskState.CLAIMED: frozenset({TaskState.RUNNING, TaskState.ESCALATED}),
    TaskState.RUNNING: frozenset({TaskState.GATED, TaskState.ESCALATED}),
    TaskState.GATED: frozenset({TaskState.REVIEWED, TaskState.RUNNING, TaskState.ESCALATED}),
    TaskState.REVIEWED: frozenset({TaskState.READY, TaskState.ESCALATED}),
    TaskState.READY: frozenset(),
    TaskState.ESCALATED: frozenset({TaskState.QUEUED, TaskState.ABANDONED}),
    TaskState.ABANDONED: frozenset(),
}

# `ready` is not `merged` (the engine stops at mergeable); `abandoned` is the
# human's terminal verdict. `escalated` is neither — it waits on a human.
TERMINAL = frozenset({TaskState.READY, TaskState.ABANDONED})


class IllegalTransition(Exception):
    def __init__(self, current: TaskState, to: TaskState) -> None:
        super().__init__(f"illegal transition {current} -> {to}; "
                         f"legal: {sorted(TRANSITIONS[current])}")
        self.current, self.to = current, to


def check_transition(current: TaskState, to: TaskState) -> None:
    if to not in TRANSITIONS[current]:
        raise IllegalTransition(current, to)
