"""The resident manager (S-0044/the-manager, S-0044/D-5): the loop that turns the
board into work.

Everything the loop needs to act it reads back from the log at the top of
each pass, and everything it decides it writes there before acting. That is
the whole of restart transparency — a manager killed mid-pass rebuilds its
view by reading, not by being told, and the only cost of the kill is the
lease of whatever it was holding.

The contracts themselves still live in the repository, which is not a
contradiction of "persistence holds the truth": the contract is the written
intent, and the log is what happened to it. Minting is where the two meet —
it is the act that places a contract on a partition (S-0044/D-7), and until a
mint exists no manager owns the task and no worker may claim it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from torve.application.manager import (
    IN_FLIGHT,
    LEASE_SECONDS,
    Board,
    TaskView,
    dispatchable,
    expired,
    project,
)
from torve.base import naming
from torve.config import layout
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import TaskState
from torve.domain.task import DISPATCHABLE_ROLES

if TYPE_CHECKING:
    from pathlib import Path

    from torve.application.eventlog import EventLog
    from torve.application.worker import Worker
    from torve.config.runconfig import NightConfig
    from torve.domain.task import Task

# ----------------------- #

# Whether a task already landed, and at which commit — the repository's own
# answer (S-0010/D-4's trailer), asked by the composition root because reaching
# git is an adapter's job and this is not one.
Landed = Callable[[str], str | None]

# Whether the host already has a record of this task having run — a
# run-state file or a telemetry row. Asked by the composition root for the
# same reason as `Landed`: it reads files, and this module decides.
Ran = Callable[[str], bool]

# Whether the operator's attention is spoken for right now, asked once a
# pass. A callable rather than a value because the queue changes while a
# resident manager runs (S-0048/A-1), and asked by the composition root because
# the answer joins a file carrier to a record one.
Pause = Callable[[], Awaitable[bool]]

# One drain of the undelivered notification queue (S-0051 S-0051/D-5),
# returning the task ids paged. Wired by the composition root because a
# destination is an adapter, and this module decides only *when*.
Relay = Callable[[], Awaitable[list[str]]]

# One pass of the serialized lane (S-0052/the-leg-and-where-it-sits), returning the task ids
# it landed. Wired by the composition root because landing is git, and
# this module decides only *when*. Unlike the relay, a pause stops it:
# landing advances the repository rather than delivering what is owed.
Lane = Callable[[], Awaitable[list[str]]]

# One evaluation of every committed standing job (S-0023/bounds-because-this-is-the-leg-that-can-grow), returning
# what it did and whether anything fired. Wired by the composition root
# because firing a predicate needs a sandbox, and this module decides only
# *when* it may run.
Standing = Callable[[], tuple[str, bool]]

# Whether a term of the night has been reached, asked once at the top of a
# pass and never inside one. Returns the reason to stop, or None to go round
# again. A callable because every term it reads — the spend, the queue, the
# clock — changes while the loop runs.
Stop = Callable[[], Awaitable[str | None]]

# How long an idle pass waits before looking again. Long enough that an idle
# manager costs nothing, short enough that a freshly adopted contract does
# not sit for a coffee break.
IDLE_SECONDS = 15.0

# S-0079/D-11: one task per pass. Recorded as a term of the night rather than
# left to be inferred from this module as of tonight's date, so a later night
# at another width is comparable against this one.
NIGHT_WIDTH = 1

# S-0079/D-1's kinds belong to this document's phase 1, which is not in this
# tree — `EventKind` carries no `night.*` member and `SubjectType` no `NIGHT`.
# Resolved by name so the open and the close land the moment phase 1 does,
# and skipped until then: what the night refuses, stops on and closes at is
# decided here and does not wait on the log's vocabulary.
NIGHT_OPENED = getattr(EventKind, "NIGHT_OPENED", None)
NIGHT_CLOSED = getattr(EventKind, "NIGHT_CLOSED", None)
NIGHT_SUBJECT = getattr(SubjectType, "NIGHT", None)


# ....................... #


def ran_here(root: Path) -> set[str]:
    """Every task this host has a record of having run: a run-state file, or
    a telemetry row.

    Built once per pass rather than asked per task. The scan asked it one
    contract at a time and re-read the whole stream for each, which on a
    repository with history is the same file parsed hundreds of times to
    answer one question about it.
    """

    from torve.application.projections import stream_rows

    found = {
        path.name.removesuffix(".state.json")
        for path in (root / naming.WORKTREE_DIR).glob("*.state.json")
    }

    return found | {str(row["task_id"]) for row in stream_rows(root) if row.get("task_id")}


# ....................... #


async def _leg(root: Path, name: str, call: Callable[[], object]) -> None:
    """One injected leg, whose failure is recorded and never fatal (S-0044/A-11).

    The retired tick wrapped every leg for this reason — "a bounded pass
    must reach its last leg so the record reflects whatever did happen" —
    and the manager's pass inherited the legs without the wrapper. It cost
    a live dispatch: a standing job that could not be instantiated took
    down the pass before it claimed anything, and the failure that mattered
    (a refused draft) was not the failure that showed (no work ran).

    The same argument as S-0024/D-5 one level down: a manager that stops
    serving because one leg is broken is worse than one that says so and
    carries on.
    """

    from torve.application.telemetry import engine_event

    try:
        outcome = call()

        if isinstance(outcome, Awaitable):
            await outcome

    except Exception as exc:
        engine_event(root, "leg_failed", {"leg": name, "error": str(exc)[:300]})


# ....................... #


def contracts(root: Path) -> dict[str, Task]:
    """Every contract the repository carries, by id.

    Every one, whatever its role (S-0049/A-1). A review or draft contract is
    nobody's to claim, and the board refuses to offer one — but it is a
    fact about the work, and the projections that read the record for a
    planning view need the whole population or they answer over a third of
    it. Filtering at the importer is what made the record a subset of the
    repository; filtering at dispatch is what keeps a worker honest.

    An unreadable contract is skipped rather than fatal: one malformed file
    is not a reason for a manager to stop managing the rest. Nothing else is
    filtered here — what a task's state is belongs to the board, and the
    board is what dispatch reads.
    """

    from torve.gates.context import load_task

    tasks: dict[str, Task] = {}

    for path in sorted((root / layout.TORVE_DIR / "tasks").glob("T-*/contract.yaml")):
        try:
            task = load_task(path)

        except ValueError:
            continue

        tasks[task.id] = task

    return tasks


# ....................... #


def _title(task: Task) -> str:
    """The name a board row shows (S-0007/A-1): the contract's own, its intent's
    first line, or the id — never empty, because a row nobody can read is a
    row nobody acts on."""

    first_line = task.intent.strip().splitlines()[0] if task.intent.strip() else ""

    return task.title.strip() or first_line or task.id


# ....................... #


def _remintable(view: TaskView, task: Task) -> bool:
    """Whether the repository's contract differs from the one on the board
    and may replace it (S-0049/D-3, S-0049/D-4).

    Compared as validated `Task` models rather than as raw payloads: a
    comparison that finds a difference where there is none re-mints on every
    pass and fills the log with a contract nobody changed.

    Never while the task is in flight. The contract an attempt is judged
    against is the one it started under, and a contract changing beneath a
    running attempt is the hazard the corpus rule already names, arriving
    from the other direction.
    """

    if view.state in IN_FLIGHT:
        return False

    return view.contract != task


# ....................... #


async def _record_mint(log: EventLog, task: Task, *, partition: str, actor_id: str) -> None:
    """One mint, first or re-mint — the same event either way (S-0044/A-8), since
    what makes the second one a version rather than a transition is the
    board's fold and not a different kind."""

    await log.record(
        EventKind.TASK_MINTED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task.id,
        actor_kind=ActorKind.MANAGER,
        actor_id=actor_id,
        payload={
            "title": _title(task),
            # S-0060/D-4: what asked, then whose rows govern, then the honest
            # absence — `operator` means nobody said, not that the operator did.
            "source_id": task.source or task.spec or "operator",
            # Copies of the contract's own fields, kept for the mints
            # written before S-0044/A-8 and pinned equal to it by test.
            "phase": task.phase,
            "depends_on": list(task.depends_on),
            "contract": task.model_dump(mode="json"),
        },
    )


# ....................... #


def _only_ever_minted(view: TaskView) -> bool:
    """Whether the record holds nothing about this task but its mint — no
    attempt, no landing, and still queued. The one state in which the
    repository may still tell the board something it does not know."""

    return view.state is TaskState.QUEUED and not view.attempts and not view.landed_sha


# ....................... #


async def _record_landing(
    log: EventLog, task_id: str, sha: str, *, partition: str, actor_id: str
) -> None:
    """A landing the repository proves and the record had not recorded.
    Attempt 0 because no attempt here produced it — the work landed
    somewhere this log was not watching."""

    await log.record(
        EventKind.LANDING_RECORDED,
        partition=partition,
        subject_type=SubjectType.TASK,
        subject_id=task_id,
        actor_kind=ActorKind.MANAGER,
        actor_id=actor_id,
        payload={"sha": sha, "attempt": 0},
    )


# ....................... #


async def mint(
    log: EventLog,
    tasks: dict[str, Task],
    *,
    partition: str,
    actor_id: str,
    landed: Landed | None = None,
    ran: Ran | None = None,
) -> list[str]:
    """Place contracts this partition has never seen onto its board.

    Minting is idempotent by reading rather than by remembering: a task the
    board already carries with the contract the repository holds is already
    minted, whatever state it has since reached, so a restart re-mints
    nothing and a re-adopted contract is not duplicated.

    A contract that *changed* is re-minted (S-0049/D-4), which is how the
    operator's recourse after an escalation works: fix the contract, resolve
    the escalation, and the next pass records the change and puts it in
    force. A re-mint carries the contract and transitions nothing (S-0049/D-2),
    and never happens while the task is in flight (S-0049/D-3).

    A contract that already landed is minted **and** recorded as landed, in
    that order, from the repository's own trailer. The repository outranks
    the host here (S-0019/A-3): a partition's first pass sees every contract the
    tree carries, including years of finished work, and a board that called
    those queued would hand a worker a task somebody finished long ago.
    Recording the landing rather than skipping the mint is what keeps the
    dependency rule honest — a task waiting on landed work must be able to
    find that landing on the board.

    The host's own run record only decides whether a contract is *minted*.
    Once a task is on the board its state is the record's, and a task a
    human requeued after an escalation is queued however many times it has
    run before — the board is what dispatch reads, and the board is what a
    person acts on.
    """

    board = project(await log.since(partition=partition))
    minted: list[str] = []

    for task_id, task in sorted(tasks.items()):
        view = board.tasks.get(task_id)
        sha = landed(task_id) if landed is not None else None

        if view is not None:
            if _remintable(view, task):
                await _record_mint(log, task, partition=partition, actor_id=actor_id)
                minted.append(task_id)

            if sha and _only_ever_minted(view):
                # The landing the first mint would have recorded, for a row
                # minted before this partition could see it (S-0049/A-2). Guarded
                # to a task the record has only ever *minted*: once it has
                # run here the board outranks the repository, and a human
                # who requeued a landed task is not overruled by a scan.
                await _record_landing(log, task_id, sha, partition=partition, actor_id=actor_id)

            continue

        offerable = task.role in DISPATCHABLE_ROLES

        if offerable and not sha and ran is not None and ran(task_id):
            # It ran on this host and did not land. Whatever it produced,
            # placing it on the board as queued would offer it to a worker
            # again — and unlike a landing there is nothing to record about
            # it that is true. It stays off the board until a human puts it
            # there.
            #
            # The guard is about being offered, so it applies only to what
            # can be (S-0049/A-1): a review that ran and landed nothing is still
            # a fact the planning projections read, and no worker will ever
            # be handed it.
            continue

        await _record_mint(log, task, partition=partition, actor_id=actor_id)
        minted.append(task_id)

        if sha:
            await _record_landing(log, task_id, sha, partition=partition, actor_id=actor_id)

    return minted


# ....................... #


async def reclaim(
    log: EventLog, *, partition: str, actor_id: str, lease: timedelta | None = None
) -> list[str]:
    """Return tasks whose holder has gone silent past its lease.

    This is the other half of "a killed worker loses nothing but its lease"
    (S-0044/D-6): something has to be the lease running out, and it is the
    manager, because the process that died cannot release itself. The
    release is a recorded fact with its reason, so a task that came back to
    the queue can always be told from one that never left.
    """

    board = project(await log.since(partition=partition))
    released: list[str] = []

    for view in expired(board, lease=lease):
        await log.record(
            EventKind.TASK_RELEASED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=view.task_id,
            actor_kind=ActorKind.MANAGER,
            actor_id=actor_id,
            payload={"reason": f"lease expired; last held by {view.claimed_by or 'nobody'}"},
        )
        released.append(view.task_id)

    return released


# ....................... #


async def once(
    log: EventLog,
    worker: Worker,
    root: Path,
    partition: str,
    *,
    lease: timedelta | None = None,
    landed: Landed | None = None,
    ran: Ran | None = None,
    only: str | None = None,
    paused: bool = False,
    dispatch: bool = True,
    standing: Standing | None = None,
    relay: Relay | None = None,
    lane: Lane | None = None,
) -> str | None:
    """One pass: reclaim what expired, mint what is new, then let the worker
    take at most one task. Returns the task id it handled, or None when the
    pass was idle.

    Reclaiming comes first because a task nobody is running is a task this
    pass could start, and the alternative is waiting a whole idle interval
    to notice.

    The scan is an importer, not a reader (S-0049/D-1): it is how a contract the
    repository gained reaches the record, and the mint is its only consumer.
    What a worker claims and runs comes off the board.

    `dispatch=False` is the other half: import and reclaim, claim nothing.
    It is what a re-mint after a contract-shape change is run under (S-0049/A-1) —
    the scan must reach the record without a worker taking the first thing
    it finds there, which on a repository with a queue is a real agent and
    real money.

    The relay runs whatever `paused` says, for the reason in S-0051/D-5: it
    delivers what is already owed rather than creating anything. The lane
    does not, for the reason in S-0052/D-3: landing is not delivering what is
    owed but advancing the repository, and a pause says nobody has capacity
    to look at what advancing produces.

    `paused` skips the legs that can grow the queue or advance the
    repository, and nothing else (S-0048/D-4, S-0023/D-6): the queue may drain
    during a pause, it may not grow. A pause is a statement about the
    operator's capacity to triage, never about the safety of what is
    already running, so an attempt in flight is not interrupted and a task
    already on the board is still claimed.
    """

    if relay is not None:
        # S-0051 S-0051/D-5: after reclaim, before the mint. What a pass does
        # first is the work already owed, and a page for an escalation
        # raised an hour ago is owed more than a contract nobody has minted
        # yet. Unaffected by `paused` — a pause is a statement that nobody
        # can triage more work, which is exactly when the queue most needs
        # draining.
        await _leg(root, "relay", relay)

    if lane is not None and not paused:
        # S-0052/the-leg-and-where-it-sits (S-0052/D-3): after the relay, before the mint. The same
        # argument one leg further: a candidate that went green an hour ago
        # is owed its landing more than a contract nobody has minted is owed
        # its board row — and landing first means the mint that follows sees
        # a base that already moved, which is the state the dependency rule
        # reads. The pause stops it where the relay's does not: landing is
        # advancing the repository, not delivering what is already owed.
        await _leg(root, "lane", lane)

    if standing is not None and not paused:
        # S-0023/bounds-because-this-is-the-leg-that-can-grow: standing before the scan, so a contract this pass
        # mints is on the board this pass. S-0023/D-6's first bound is the
        # caller's, and this is that caller: a paused pass evaluates no
        # predicate, because a predicate that fires creates work and a
        # pause is a statement that nobody has capacity to triage it.
        await _leg(root, "standing", standing)

    tasks = contracts(root)

    if only is not None:
        # An operator naming one task means that task and no other: the
        # board's own order is the right default and the wrong answer when
        # somebody is standing there asking for something specific.
        tasks = {task_id: task for task_id, task in tasks.items() if task_id == only}

    await reclaim(log, partition=partition, actor_id=worker.name, lease=lease)

    if not paused:
        await mint(log, tasks, partition=partition, actor_id=worker.name, landed=landed, ran=ran)

    return await worker.once(partition) if dispatch else None


# ....................... #


async def serve(
    log: EventLog,
    worker: Worker,
    root: Path,
    partition: str,
    *,
    idle_seconds: float = IDLE_SECONDS,
    passes: int | None = None,
    lease: timedelta | None = None,
    landed: Landed | None = None,
    ran: Ran | None = None,
    only: str | None = None,
    paused: Pause | bool = False,
    dispatch: bool = True,
    standing: Standing | None = None,
    relay: Relay | None = None,
    lane: Lane | None = None,
    stop: Stop | None = None,
) -> int:
    """Run passes until cancelled, or until *passes* of them have run.

    An idle pass sleeps; a productive one goes straight round again, because
    a partition that just landed something may have unblocked the next
    thing. Returns how many tasks were handled — the bounded form is what
    tests and a `--passes` dispatch use, and the unbounded one is the
    resident process S-0044/D-5 asks for.

    `paused` may be a callable, and for a resident process it must be: the
    escalation queue changes while the manager runs, and a pause decided
    once at startup stops meaning anything an hour later (S-0048/A-1).

    `stop` is asked at the top of each pass and nowhere else. A term reached
    mid-pass is honoured at the next one, which is what makes every bound a
    night carries soft: the attempt in flight when the clock ran out
    finishes (S-0079/D-12), and a loop that could interrupt one would be
    throwing away exactly the work the night was run to get.
    """

    handled = 0
    seen = 0

    while passes is None or seen < passes:
        if stop is not None and await stop() is not None:
            return handled

        seen += 1
        task_id = await once(
            log,
            worker,
            root,
            partition,
            lease=lease,
            landed=landed,
            ran=ran,
            only=only,
            paused=(await paused()) if callable(paused) else paused,
            dispatch=dispatch,
            standing=standing,
            relay=relay,
            lane=lane,
        )

        if task_id is not None:
            handled += 1
            continue

        if passes is None or seen < passes:
            # Cancellation lands here in an idle manager, which is where a
            # kill is free: nothing is claimed, so nothing is left leased.
            await asyncio.sleep(idle_seconds)

    return handled


# ----------------------- #


class NightRefused(Exception):
    """The night was refused at the open, before the first pass.

    An exception rather than a return value because there is exactly one
    caller and exactly one thing it can do: say so at the terminal while the
    operator who typed the command is still standing there. Raised only
    before `night.opened` — once the night is open nothing refuses it, and a
    queue that drains later closes it (S-0079/D-6).
    """


# ....................... #


@dataclass(frozen=True)
class Night:
    """What one served night was started with (S-0079/D-2).

    Read once at the open and never re-read, so the night's record says what
    it was started with even after the configuration was edited while it
    ran, and two nights are comparable because their terms are recorded
    rather than reconstructed from whatever the file says afterwards.
    """

    night_id: str
    opened_at: datetime
    queue: tuple[str, ...]
    budget_usd: float
    budget_attempts: int
    minutes: int
    stop_on: tuple[str, ...]
    lease_seconds: float
    width: int = NIGHT_WIDTH
    # The board as it stood at the open, which is what every budget axis is
    # measured against: the log carries a partition's whole history, so a
    # night that read the totals rather than the deltas would stop on the
    # spend of every night before it.
    spent_usd: float = 0.0
    attempts: int = 0
    escalated: frozenset[str] = field(default_factory=frozenset)

    # ....................... #

    @property
    def ends_at(self) -> datetime:
        """The wall-clock end, measured from the open (S-0079/D-12). From the
        open rather than from the command, because the open is the first
        instant recorded and a reader with the log alone can compute it."""

        return self.opened_at + timedelta(minutes=self.minutes)

    # ....................... #

    def terms(self) -> dict[str, object]:
        """The terms whole, as `night.opened` carries them."""

        return {
            "queue": list(self.queue),
            "width": self.width,
            "budget_usd": self.budget_usd,
            "budget_attempts": self.budget_attempts,
            "minutes": self.minutes,
            "stop_on": list(self.stop_on),
            "lease_seconds": self.lease_seconds,
        }


# ....................... #


def _spend(board: Board) -> tuple[float, int]:
    """What this partition has burned and how many attempts it has started,
    over its whole recorded life. A night's own figures are the difference
    between two of these."""

    return (
        sum(view.burned_usd for view in board.tasks.values()),
        sum(view.attempts for view in board.tasks.values()),
    )


# ....................... #


async def open_night(
    log: EventLog,
    partition: str,
    *,
    config: NightConfig,
    actor_id: str,
    lease: timedelta | None = None,
    now: datetime | None = None,
) -> Night:
    """Read the night's terms off the board and the configuration, refuse an
    empty ready queue, and record the open.

    The refusal is S-0079/D-6, and it is worth the special case: a night with
    nothing to start costs eight hours of sleeping and says so at the last
    moment the operator is present to hear it. It is a refusal only *here* —
    the same empty queue an hour later is a night that finished its work,
    and closing is the right end for that.
    """

    opened_at = now or datetime.now(UTC)
    board = project(await log.since(partition=partition))
    queue = dispatchable(board, partition)

    if not queue:
        raise NightRefused(
            "no task on this board can be started right now — a night would sleep "
            "through to morning having claimed nothing"
        )

    spent_usd, attempts = _spend(board)
    window = lease if lease is not None else timedelta(seconds=LEASE_SECONDS)
    night = Night(
        night_id=opened_at.strftime("%Y%m%dT%H%M%SZ"),
        opened_at=opened_at,
        queue=tuple(queue),
        budget_usd=config.budget_usd,
        budget_attempts=config.budget_attempts,
        minutes=config.minutes,
        stop_on=tuple(config.stop_on),
        lease_seconds=window.total_seconds(),
        spent_usd=spent_usd,
        attempts=attempts,
        escalated=frozenset(board.escalated()),
    )

    if NIGHT_OPENED is not None and NIGHT_SUBJECT is not None:
        await log.record(
            NIGHT_OPENED,
            partition=partition,
            subject_type=NIGHT_SUBJECT,
            subject_id=night.night_id,
            actor_kind=ActorKind.MANAGER,
            actor_id=actor_id,
            payload=night.terms(),
        )

    return night


# ....................... #


async def close_night(
    log: EventLog,
    partition: str,
    night: Night,
    *,
    reason: str,
    handled: int,
    actor_id: str,
    now: datetime | None = None,
) -> str:
    """Record the close and return the reason the night stopped.

    Written even when the end fell inside a pass (S-0079/D-12), which is why
    the payload carries how far past the end the close landed: a night that
    closed eleven minutes late has to read as one that honoured a soft bound
    rather than as one that ignored its terms.
    """

    closed_at = now or datetime.now(UTC)
    # `reached` names an escalation stop as `escalation:<class>`; the record
    # holds the bound in `reason` and the class in `detail`.
    bound, _, detail = reason.partition(":")

    if NIGHT_CLOSED is not None and NIGHT_SUBJECT is not None:
        await log.record(
            NIGHT_CLOSED,
            partition=partition,
            subject_type=NIGHT_SUBJECT,
            subject_id=night.night_id,
            actor_kind=ActorKind.MANAGER,
            actor_id=actor_id,
            payload={
                "reason": bound,
                "detail": detail,
                "handled": handled,
                "overran_seconds": max(0.0, (closed_at - night.ends_at).total_seconds()),
            },
        )

    return reason


# ....................... #


async def reached(
    log: EventLog, partition: str, night: Night, *, now: datetime | None = None
) -> str | None:
    """Which of the night's terms has been reached, or None.

    The escalation classes come first because they are the only term an
    operator named for themselves: "wake me for a locked conflict, keep
    going on a merge conflict" (S-0079/D-7). Only a task that was not already
    escalated at the open counts — last night's unresolved escalation is not
    tonight's reason to stop — and `loop.pause_escalations` is untouched,
    still counting a queue rather than reading its classes.
    """

    moment = now or datetime.now(UTC)
    board = project(await log.since(partition=partition))

    if night.stop_on:
        for task_id in sorted(board.tasks):
            view = board.tasks[task_id]

            if task_id in night.escalated or view.state is not TaskState.ESCALATED:
                continue

            if view.escalation in night.stop_on:
                return f"escalation:{view.escalation}"

    spent_usd, attempts = _spend(board)

    if night.budget_usd > 0 and spent_usd - night.spent_usd >= night.budget_usd:
        return "budget_usd"

    if night.budget_attempts > 0 and attempts - night.attempts >= night.budget_attempts:
        return "budget_attempts"

    if moment >= night.ends_at:
        return "wall_clock"

    if not dispatchable(board, partition) and not board.in_flight():
        # S-0079/D-6's other half: a queue that drains closes the night. It is
        # not an error and it is not the refusal — the work is done.
        return "drained"

    return None
