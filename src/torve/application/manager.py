"""The manager's view of a partition (S-0044/the-manager, S-0044/D-5, S-0044/D-7).

Everything here is a fold over the event log. A task's state is not a field
somebody maintains; it is what the recorded facts add up to, which is why a
manager can be killed at any moment and rebuild exactly what it knew by
reading the log again — the restart transparency S-0044/D-5 asks for is a
property of the data, not machinery.

The dispatch rules are v1's, unchanged, because they were never about how
the loop was hosted: a dependency is satisfied only by a landing (S-0019/A-3,
S-0019/A-4), tasks in flight together must not share scope (S-0019/A-6), and landings
serialize within a partition while partitions run independently (S-0044/D-7).
What changes is only where the state they read comes from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from torve.application.planner import scopes_clash
from torve.application.sizing import estimate
from torve.base.clock import parse
from torve.domain.events import EventKind, NightClosed, NightOpened, SubjectType
from torve.domain.spec import document_id
from torve.domain.states import TaskState
from torve.domain.task import DISPATCHABLE_ROLES, Task

if TYPE_CHECKING:
    from torve.domain.events import EventRecord

# ----------------------- #

# A task the engine is still holding: claimed or anywhere inside an attempt.
# These are what a new dispatch must stay scope-disjoint from (S-0019/A-6).
IN_FLIGHT = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})

# How long a claim stands with nothing happening before the manager may
# hand the task to someone else. The manager's rule, not the worker's: the
# worker holding a lease is exactly the process that cannot be trusted to
# decide when it has stopped.
LEASE_SECONDS = 900


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
    # S-0045 S-0045/D-4: when this task last burned a seat, and what it has
    # burned. Liveness is read from here and never from the agent — an
    # attempt with no recent burn is not working, whatever it would say
    # about itself, and it is not asked.
    last_burn: datetime | None = None
    burned_usd: float = 0.0
    # When the claim was taken, and when anything last happened. A worker
    # that dies holds its task until the first of these goes stale — which
    # is the whole cost of a kill (S-0044/D-6), and the only thing that makes it
    # bounded rather than permanent.
    claimed_at: datetime | None = None
    last_event_at: datetime | None = None
    # The contract this task was minted with (S-0049 S-0049/D-1), re-minted
    # whenever the repository's differs. None for a mint written before
    # S-0044/A-8, or for one whose payload no longer validates as a `Task` — both
    # read as "the record does not hold this task's contract", which is
    # undispatchable and visible rather than an error (S-0049/D-5, S-0049/D-6).
    contract: Task | None = None


# ....................... #


@dataclass(frozen=True)
class Board:
    """Every task a partition's log has ever mentioned."""

    tasks: dict[str, TaskView] = field(default_factory=dict)

    # ....................... #

    def in_flight(self) -> list[TaskView]:
        return [view for view in self.tasks.values() if view.state in IN_FLIGHT]

    # ....................... #

    def escalated(self) -> set[str]:
        """The tasks waiting on a person. What a fleet's attention budget is
        measured from (S-0024 S-0024/D-2) for a partition the manager serves —
        the run-state files answer for whatever v1 still holds, and the two
        are unioned rather than summed (S-0048/D-5)."""

        return {view.task_id for view in self.tasks.values() if view.state is TaskState.ESCALATED}

    # ....................... #

    def landed(self) -> set[str]:
        return {view.task_id for view in self.tasks.values() if view.state is TaskState.READY}


# ....................... #

# What each recorded fact means for the task it is about. Kinds absent here
# say nothing about state — a divergence, a burn event or a message is a
# fact about the work, not a transition (S-0044/D-1: routing keys on outcomes).
_TRANSITIONS: dict[EventKind, TaskState] = {
    EventKind.TASK_MINTED: TaskState.QUEUED,
    EventKind.TASK_CLAIMED: TaskState.CLAIMED,
    EventKind.TASK_RELEASED: TaskState.QUEUED,
    EventKind.TASK_RETURNED: TaskState.QUEUED,
    EventKind.ATTEMPT_STARTED: TaskState.RUNNING,
    EventKind.GATES_EVALUATED: TaskState.GATED,
    EventKind.REVIEW_RECORDED: TaskState.REVIEWED,
    EventKind.LANDING_RECORDED: TaskState.READY,
    EventKind.ESCALATION_RAISED: TaskState.ESCALATED,
}


# ....................... #


def minted_contract(payload: Mapping[str, Any]) -> Task | None:
    """The contract a mint carried, or None when it carried none.

    None covers two cases the board must survive rather than raise on: a
    mint written before S-0044/A-8, and a payload that no longer validates as a
    `Task` because the model moved under it. Both mean the same thing to
    every reader — the record does not hold this task's contract — and one
    unreadable payload must not take a whole board down (S-0049/D-5).
    """

    raw = payload.get("contract")

    if not isinstance(raw, dict) or not raw:
        return None

    try:
        return Task.model_validate(current_shape(raw))

    except ValidationError:
        return None


def current_shape(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A recorded contract as the model reads it today (S-0059/D-3): a mint
    written before S-0059 carries `rfc`, the document's path; it folds as
    `spec`, the document's identifier, and the record is never rewritten."""

    if "rfc" not in raw:
        return dict(raw)

    modern = {key: value for key, value in raw.items() if key != "rfc"}

    try:
        modern["spec"] = document_id(str(raw["rfc"] or ""))
    except ValueError:
        modern["spec"] = None

    return modern


# ....................... #


def project(events: Iterable[EventRecord]) -> Board:
    """Fold the log into a board. Replaying the same events must produce the
    same board — that equality is what lets a manager restart without
    handing anything off."""

    tasks: dict[str, TaskView] = {}

    for event in events:
        if event.subject_type is not SubjectType.TASK:
            continue

        known = event.subject_id in tasks
        view = tasks.get(event.subject_id, TaskView(task_id=event.subject_id))
        view = replace(view, partition=event.partition, last_event_at=event.created_at)
        payload = event.payload

        # A re-mint records a contract and nothing else (S-0049/D-2): a manager
        # that could re-queue an escalated task by noticing an edited file
        # would be writing an `escalation.resolved` it has no authority to
        # write, under another name.
        remint = event.kind is EventKind.TASK_MINTED and known
        state = _TRANSITIONS.get(event.kind)

        if state is not None and not remint:
            view = replace(view, state=state)

        if event.kind is EventKind.TASK_MINTED:
            view = replace(view, contract=minted_contract(payload) or view.contract)
        elif event.kind is EventKind.TASK_CLAIMED:
            view = replace(
                view, claimed_by=str(payload.get("worker") or ""), claimed_at=event.created_at
            )
        elif event.kind is EventKind.TASK_RELEASED:
            view = replace(view, claimed_by=None, claimed_at=None)
        elif event.kind is EventKind.TASK_RETURNED:
            # The candidate's commit is no longer the answer, and a queued
            # row still showing a landing reads as a landing (S-0044/A-13).
            view = replace(view, claimed_by=None, claimed_at=None, landed_sha=None)
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
    satisfied by a landing and by nothing else (S-0019/A-3, S-0019/A-4): a run that
    reached `ready` without one has told the board nothing it can act on."""

    landed = board.landed()

    return [one for one in task.depends_on if one not in landed]


# ....................... #


def overlaps(task: Task, board: Board) -> list[str]:
    """Tasks in flight whose scope this one shares (S-0019/A-6). The rule is the
    standing loop's own, shared rather than restated: conservative about
    overlap, and refusing outright for an unconstrained allow-set, because
    a task that may touch anything can prove itself disjoint from nothing.
    Two agents editing one file is a conflict the engine cannot resolve
    afterwards.

    The scopes come off the board's own contracts (S-0049/D-1). A task in flight
    whose contract the record does not hold is skipped rather than assumed
    disjoint — the same silence the scan produced when it could not read a
    contract file."""

    shared: list[str] = []

    for view in board.in_flight():
        if view.task_id == task.id or view.contract is None:
            continue

        if scopes_clash(task.scope.allow, view.contract.scope.allow):
            shared.append(view.task_id)

    return sorted(shared)


# ....................... #


def decomposed(task: Task, board: Board) -> bool:
    """Whether some other contract on this board carries this task as its
    parent (S-0026 S-0026/D-6) — true once a decomposition of it has been
    adopted, at which point it is the integration task and its own
    `too_large` verdict has already routed once and does not route again.

    The repository answered this by globbing the task directory. The board
    carries every contract since S-0049/A-1, so the same question is a fold, and
    a rule that reads the record cannot disagree with the board it gates.
    """

    return any(
        view.contract is not None and view.contract.parent == task.id
        for view in board.tasks.values()
    )


# ....................... #


def dispatchable(board: Board, partition: str) -> list[str]:
    """What this partition could start right now, in id order.

    A task qualifies when this partition's board carries it as queued with a
    contract a worker may take, its size is one a worker may finish, its
    dependencies have landed, and nothing sharing its scope is in flight. Everything else is somebody's turn: an
    escalated task waits on a human, a claimed one on its worker, a landed
    one on nobody.

    The role guard is what lets the scan mint every contract (S-0049/A-1). A
    review or draft contract is recorded because the planning projections
    read the record, and it is offered to nobody because the run that
    minted it is the only thing that ever executes it.

    Answered from the board alone (S-0049/D-1). Minting is what places a task on
    a partition (S-0044/D-7), so an unminted contract belongs to nobody and a
    contract minted elsewhere belongs to that partition's manager — and a
    task whose contract the record does not hold is not dispatchable
    (S-0049/D-6), because there is nothing to check its scope or dependencies
    against.
    """

    ready: list[str] = []

    for task_id in sorted(board.tasks):
        view = board.tasks[task_id]
        task = view.contract

        if task is None or view.partition != partition:
            continue

        if task.role not in DISPATCHABLE_ROLES:
            continue

        if view.state is not TaskState.QUEUED:
            continue

        # S-0026/D-7: a contract this large that nobody has decomposed awaits a
        # decomposition, not a worker. The scan refused it too; refusing it
        # here is what lets the scan go.
        if estimate(task).size == "too_large" and not decomposed(task, board):
            continue

        if blocked_by(task, board) or overlaps(task, board):
            continue

        ready.append(task_id)

    return ready


# ....................... #

# How long an in-flight task may burn nothing before the board calls it
# stalled. Long enough that a slow model, a long gate pass or a sandbox
# build is not an accusation; short enough that a wedged attempt is visible
# within one coffee break. Whether a stall should also *end* the attempt is
# S-0045/D-8, and deliberately unanswered until there is recorded burn to argue
# from — this reading surfaces it, and stops nothing.
STALL_AFTER = timedelta(minutes=20)


# ....................... #


def stalled(view: TaskView, *, now: datetime | None = None, after: timedelta = STALL_AFTER) -> bool:
    """Whether this task looks wedged, from the burn stream alone (S-0045/D-4).

    A task nobody is running cannot be stalled, and a task that has burned
    nothing at all is not yet evidence of anything: a sandbox is still being
    built, or the tier is a fake that never calls a provider. What counts is
    an attempt that burned and then stopped.
    """

    if view.state not in IN_FLIGHT or view.last_burn is None:
        return False

    return (now or datetime.now(UTC)) - view.last_burn > after


# ....................... #


def expired(
    board: Board, *, now: datetime | None = None, lease: timedelta | None = None
) -> list[TaskView]:
    """Tasks whose holder has gone silent past its lease (S-0044 S-0044/D-6).

    A worker holds nothing but a lease, and a worker that dies holds it
    until it runs out — so something has to notice. What counts as activity
    is any recorded fact about the task, not a heartbeat the holder sends:
    a process that can report itself alive can report itself alive while
    wedged, and the facts are what the work actually produced.

    Nothing here acts. Releasing is the manager's, and it records why.
    """

    moment = now or datetime.now(UTC)
    window = lease if lease is not None else timedelta(seconds=LEASE_SECONDS)

    return sorted(
        (
            view
            for view in board.tasks.values()
            if view.state in IN_FLIGHT
            and view.claimed_at is not None
            and moment - (view.last_event_at or view.claimed_at) > window
        ),
        key=lambda one: one.task_id,
    )


# ....................... #

# What the lane records about a pull request (S-0080/D-7, S-0080/D-8,
# S-0080/D-9). A merge is `lane_landed` in the mode it landed under, because
# the record shape is the local lane's and the mode is the field that says
# which act produced it; a conflict is the one the disposal starts from, so
# the requeue that follows it is not the same candidate counted twice.
_PR_EVENTS = {
    "lane_pr_opened": "opened",
    "lane_conflict": "conflicted",
    "lane_pr_closed": "closed",
}


@dataclass(frozen=True)
class PullRequests:
    """What a night left on the forge (S-0080/D-14): four counts and no
    field prose can occupy, because in `pull_request` mode what is waiting
    on a person is almost entirely there."""

    opened: int = 0
    merged: int = 0
    conflicted: int = 0
    closed: int = 0


def pull_requests(
    rows: Iterable[Mapping[str, Any]], *, since: datetime, until: datetime | None = None
) -> PullRequests:
    """Fold the window's recorded facts into the four counts.

    A fold like every other column the report holds: the rows are the
    engine's own stream, membership is a time comparison against the
    night's bounds, and a row whose instant does not read counts nowhere
    rather than taking the fold down.
    """

    counts = dict.fromkeys(("opened", "merged", "conflicted", "closed"), 0)

    for row in rows:
        try:
            moment = parse(str(row.get("at") or ""))

        except ValueError:
            continue

        if moment < since or (until is not None and moment > until):
            continue

        event = str(row.get("event") or "")

        if event == "lane_landed":
            if str(row.get("mode") or "") == "pull-request":
                counts["merged"] += 1

        elif event in _PR_EVENTS:
            counts[_PR_EVENTS[event]] += 1

    return PullRequests(**counts)


# ----------------------- #

# The morning report (S-0079/D-3, S-0079/D-4). Four lists folded out of the
# window between an open and its close, computed on every call and stored
# nowhere: a stored summary is the one artefact nothing can check against
# anything, and the board beside it would be free to disagree.
#
# Every field below is a recorded one — a task id, a sha, a gate name, a
# value from a closed vocabulary, an instant. There is deliberately nowhere
# for a sentence to go, which is what keeps a model's account of its own
# night out of the report a person reads when they were not there.


@dataclass(frozen=True)
class Landing:
    """A candidate the night landed."""

    task_id: str
    sha: str
    at: datetime


# ....................... #


@dataclass(frozen=True)
class Conviction:
    """One gate that judged an attempt red: what ran, and how it came out."""

    task_id: str
    attempt: int
    gate: str
    outcome: str
    at: datetime


# ....................... #


@dataclass(frozen=True)
class Ending:
    """A task and the reason it stopped — an attempt the engine ended, or an
    escalation still waiting on a person. The same triple either way, because
    both are a task, a reason from `EscalationReason` and an instant."""

    task_id: str
    reason: str
    at: datetime


# ....................... #

# A gate that ran and did not hold. `flaky`, `skipped` and `bypassed` are
# outcomes a battery reports without convicting anybody.
_CONVICTING = frozenset({"fail", "error"})


@dataclass(frozen=True)
class NightReport:
    """One night's window, as the log's own facts leave it."""

    night_id: str
    opened_at: datetime
    terms: NightOpened
    closed_at: datetime | None = None
    close: NightClosed | None = None
    landed: tuple[Landing, ...] = ()
    convicted: tuple[Conviction, ...] = ()
    ended: tuple[Ending, ...] = ()
    waiting: tuple[Ending, ...] = ()

    # ....................... #

    @property
    def unfinished(self) -> bool:
        """A night whose manager never wrote a close — killed, or still
        running. The open is the only thing it needed to have written, so
        this reads as unfinished rather than as lost (S-0079/D-1)."""

        return self.close is None


# ....................... #


def night_report(events: Iterable[EventRecord], *, night_id: str = "") -> NightReport | None:
    """Fold one night's window, or None when the log holds no such night.

    Named or, by default, the most recent open — which is what somebody at
    breakfast wants and what a script can pin with an identifier.

    Membership is a time comparison and nothing else (S-0079/D-3): the night
    stamps no event of its own, so a fact recorded in the second between two
    nights belongs to whichever window holds its instant. Both boundaries are
    in the log, so that ambiguity is resolvable by reading.
    """

    records = list(events)
    opens = [
        record
        for record in records
        if record.kind is EventKind.NIGHT_OPENED and (not night_id or record.subject_id == night_id)
    ]

    if not opens:
        return None

    opened = opens[-1]
    close = next(
        (
            record
            for record in records
            if record.kind is EventKind.NIGHT_CLOSED and record.subject_id == opened.subject_id
        ),
        None,
    )
    until = close.created_at if close is not None else None
    landed: list[Landing] = []
    convicted: list[Conviction] = []
    ended: list[Ending] = []
    waiting: list[Ending] = []
    resolved: set[str] = set()

    for record in records:
        if record.subject_type is not SubjectType.TASK or record.created_at < opened.created_at:
            continue

        if until is not None and record.created_at > until:
            continue

        task_id, payload, at = record.subject_id, record.payload, record.created_at

        if record.kind is EventKind.LANDING_RECORDED:
            landed.append(Landing(task_id, str(payload.get("sha") or ""), at))

        elif record.kind is EventKind.GATES_EVALUATED:
            attempt = int(payload.get("attempt") or 0)
            convicted.extend(
                Conviction(task_id, attempt, str(result.get("name") or ""), outcome, at)
                for result in payload.get("results") or []
                if (outcome := str(result.get("outcome") or "")) in _CONVICTING
            )

        elif record.kind is EventKind.ATTEMPT_FINISHED:
            # An attempt with an escalation reason is one the engine ended;
            # one that went on to a gate pass has no ending of its own.
            if reason := str(payload.get("escalation") or ""):
                ended.append(Ending(task_id, reason, at))

        elif record.kind is EventKind.ESCALATION_RAISED:
            waiting.append(Ending(task_id, str(payload.get("reason") or ""), at))

        elif record.kind is EventKind.ESCALATION_RESOLVED:
            resolved.add(task_id)

    return NightReport(
        night_id=opened.subject_id,
        opened_at=opened.created_at,
        terms=NightOpened.model_validate(opened.payload),
        closed_at=until,
        close=NightClosed.model_validate(close.payload) if close is not None else None,
        landed=tuple(landed),
        convicted=tuple(convicted),
        ended=tuple(ended),
        # What a person closed inside the night is no longer waiting for one.
        waiting=tuple(one for one in waiting if one.task_id not in resolved),
    )
