"""The manager's view of a partition (RFC 0044 §5.4, D-44.5, D-44.7).

Everything here is a fold over the event log. A task's state is not a field
somebody maintains; it is what the recorded facts add up to, which is why a
manager can be killed at any moment and rebuild exactly what it knew by
reading the log again — the restart transparency D-44.5 asks for is a
property of the data, not machinery.

The dispatch rules are v1's, unchanged, because they were never about how
the loop was hosted: a dependency is satisfied only by a landing (A-29,
A-31), tasks in flight together must not share scope (A-39), and landings
serialize within a partition while partitions run independently (D-44.7).
What changes is only where the state they read comes from.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from torve.application.planner import globs_intersect
from torve.domain.events import EventKind, SubjectType
from torve.domain.states import TaskState

if TYPE_CHECKING:
    from torve.domain.events import EventRecord
    from torve.domain.task import Task

# ----------------------- #

# A task the engine is still holding: claimed or anywhere inside an attempt.
# These are what a new dispatch must stay scope-disjoint from (A-39).
IN_FLIGHT = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})


# ....................... #


@dataclass(frozen=True)
class TaskView:
    """One task as its recorded facts leave it."""

    task_id: str
    partition: str = ""
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    claimed_by: str | None = None
    landed_sha: str | None = None
    escalation: str | None = None
    # RFC 0045 D-45.4: when this task last burned a seat, and what it has
    # burned. Liveness is read from here and never from the agent — an
    # attempt with no recent burn is not working, whatever it would say
    # about itself, and it is not asked.
    last_burn: datetime | None = None
    burned_usd: float = 0.0


# ....................... #


@dataclass(frozen=True)
class Board:
    """Every task a partition's log has ever mentioned."""

    tasks: dict[str, TaskView] = field(default_factory=dict)

    # ....................... #

    def in_flight(self) -> list[TaskView]:
        return [view for view in self.tasks.values() if view.state in IN_FLIGHT]

    # ....................... #

    def landed(self) -> set[str]:
        return {view.task_id for view in self.tasks.values() if view.state is TaskState.READY}


# ....................... #

# What each recorded fact means for the task it is about. Kinds absent here
# say nothing about state — a divergence, a burn event or a message is a
# fact about the work, not a transition (D-44.1: routing keys on outcomes).
_TRANSITIONS: dict[EventKind, TaskState] = {
    EventKind.TASK_MINTED: TaskState.QUEUED,
    EventKind.TASK_CLAIMED: TaskState.CLAIMED,
    EventKind.TASK_RELEASED: TaskState.QUEUED,
    EventKind.ATTEMPT_STARTED: TaskState.RUNNING,
    EventKind.GATES_EVALUATED: TaskState.GATED,
    EventKind.REVIEW_RECORDED: TaskState.REVIEWED,
    EventKind.LANDING_RECORDED: TaskState.READY,
    EventKind.ESCALATION_RAISED: TaskState.ESCALATED,
}


# ....................... #


def project(events: Iterable[EventRecord]) -> Board:
    """Fold the log into a board. Replaying the same events must produce the
    same board — that equality is what lets a manager restart without
    handing anything off."""

    tasks: dict[str, TaskView] = {}

    for event in events:
        if event.subject_type is not SubjectType.TASK:
            continue

        view = tasks.get(event.subject_id, TaskView(task_id=event.subject_id))
        view = replace(view, partition=event.partition)
        payload = event.payload

        if (state := _TRANSITIONS.get(event.kind)) is not None:
            view = replace(view, state=state)

        if event.kind is EventKind.TASK_CLAIMED:
            view = replace(view, claimed_by=str(payload.get("worker") or ""))
        elif event.kind is EventKind.TASK_RELEASED:
            view = replace(view, claimed_by=None)
        elif event.kind is EventKind.ATTEMPT_STARTED:
            view = replace(view, attempts=int(payload.get("attempt") or view.attempts + 1))
        elif event.kind is EventKind.LANDING_RECORDED:
            view = replace(view, landed_sha=str(payload.get("sha") or ""), claimed_by=None)
        elif event.kind is EventKind.ESCALATION_RAISED:
            view = replace(view, escalation=str(payload.get("reason") or ""), claimed_by=None)
        elif event.kind is EventKind.SEAT_CONSUMED:
            view = replace(
                view,
                last_burn=event.created_at,
                burned_usd=view.burned_usd + float(payload.get("cost_usd") or 0.0),
            )
        elif event.kind is EventKind.ESCALATION_RESOLVED:
            resolution = str(payload.get("resolution") or "")
            view = replace(
                view,
                state=(TaskState.ABANDONED if resolution == "abandoned" else TaskState.QUEUED),
                escalation=None,
            )

        tasks[event.subject_id] = view

    return Board(tasks=tasks)


# ....................... #


def blocked_by(task: Task, board: Board) -> list[str]:
    """The dependencies this task is still waiting on. A dependency is
    satisfied by a landing and by nothing else (A-29, A-31): a run that
    reached `ready` without one has told the board nothing it can act on."""

    landed = board.landed()

    return [one for one in task.depends_on if one not in landed]


# ....................... #


def overlaps(task: Task, board: Board, tasks: dict[str, Task]) -> list[str]:
    """Tasks in flight whose scope this one shares (A-39). Conservative by
    construction — `globs_intersect` refuses what is provably shared, never
    what is cleverly disjoint — because two agents editing one file is a
    conflict the engine cannot resolve afterwards."""

    shared: list[str] = []

    for view in board.in_flight():
        if view.task_id == task.id:
            continue

        other = tasks.get(view.task_id)

        if other is not None and globs_intersect(task.scope.allow, other.scope.allow):
            shared.append(view.task_id)

    return sorted(shared)


# ....................... #


def dispatchable(tasks: dict[str, Task], board: Board, partition: str) -> list[str]:
    """What this partition could start right now, in id order.

    A task qualifies when this partition's board carries it as queued, its
    dependencies have landed, and nothing sharing its scope is in flight.
    Everything else is somebody's turn: an escalated task waits on a human,
    a claimed one on its worker, a landed one on nobody.

    A contract the board has never seen is not dispatchable here, whatever
    the caller passed in. Minting is what places a task on a partition
    (D-44.7), so an unminted contract belongs to nobody and a contract
    minted elsewhere belongs to that partition's manager.
    """

    ready: list[str] = []

    for task_id, task in sorted(tasks.items()):
        view = board.tasks.get(task_id)

        if view is None or view.partition != partition:
            continue

        if view.state is not TaskState.QUEUED:
            continue

        if blocked_by(task, board) or overlaps(task, board, tasks):
            continue

        ready.append(task_id)

    return ready


# ....................... #

# How long an in-flight task may burn nothing before the board calls it
# stalled. Long enough that a slow model, a long gate pass or a sandbox
# build is not an accusation; short enough that a wedged attempt is visible
# within one coffee break. Whether a stall should also *end* the attempt is
# D-45.8, and deliberately unanswered until there is recorded burn to argue
# from — this reading surfaces it, and stops nothing.
STALL_AFTER = timedelta(minutes=20)


# ....................... #


def stalled(view: TaskView, *, now: datetime | None = None, after: timedelta = STALL_AFTER) -> bool:
    """Whether this task looks wedged, from the burn stream alone (D-45.4).

    A task nobody is running cannot be stalled, and a task that has burned
    nothing at all is not yet evidence of anything: a sandbox is still being
    built, or the tier is a fake that never calls a provider. What counts is
    an attempt that burned and then stopped.
    """

    if view.state not in IN_FLIGHT or view.last_burn is None:
        return False

    return (now or datetime.now(UTC)) - view.last_burn > after
