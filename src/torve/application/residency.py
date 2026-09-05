"""The resident manager (RFC 0044 §5.4, D-44.5): the loop that turns the
board into work.

Everything the loop needs to act it reads back from the log at the top of
each pass, and everything it decides it writes there before acting. That is
the whole of restart transparency — a manager killed mid-pass rebuilds its
view by reading, not by being told, and the only cost of the kill is the
lease of whatever it was holding.

The contracts themselves still live in the repository, which is not a
contradiction of "persistence holds the truth": the contract is the written
intent, and the log is what happened to it. Minting is where the two meet —
it is the act that places a contract on a partition (D-44.7), and until a
mint exists no manager owns the task and no worker may claim it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from torve.application.manager import IN_FLIGHT, TaskView, expired, project
from torve.base import naming
from torve.config import layout
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import TaskState
from torve.domain.task import DISPATCHABLE_ROLES

if TYPE_CHECKING:
    from datetime import timedelta
    from pathlib import Path

    from torve.application.eventlog import EventLog
    from torve.application.worker import Worker
    from torve.domain.task import Task

# ----------------------- #

# Whether a task already landed, and at which commit — the repository's own
# answer (D-10.4's trailer), asked by the composition root because reaching
# git is an adapter's job and this is not one.
Landed = Callable[[str], str | None]

# Whether the host already has a record of this task having run — a
# run-state file or a telemetry row. Asked by the composition root for the
# same reason as `Landed`: it reads files, and this module decides.
Ran = Callable[[str], bool]

# One evaluation of every committed standing job (RFC 0023 §5.4), returning
# what it did and whether anything fired. Wired by the composition root
# because firing a predicate needs a sandbox, and this module decides only
# *when* it may run.
Standing = Callable[[], tuple[str, bool]]

# How long an idle pass waits before looking again. Long enough that an idle
# manager costs nothing, short enough that a freshly adopted contract does
# not sit for a coffee break.
IDLE_SECONDS = 15.0


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


def contracts(root: Path) -> dict[str, Task]:
    """Every contract the repository carries, by id.

    Every one, whatever its role (A-96). A review or draft contract is
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
    """The name a board row shows (A-69): the contract's own, its intent's
    first line, or the id — never empty, because a row nobody can read is a
    row nobody acts on."""

    first_line = task.intent.strip().splitlines()[0] if task.intent.strip() else ""

    return task.title.strip() or first_line or task.id


# ....................... #


def _remintable(view: TaskView, task: Task) -> bool:
    """Whether the repository's contract differs from the one on the board
    and may replace it (D-49.3, D-49.4).

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
    """One mint, first or re-mint — the same event either way (A-91), since
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
            "source_id": task.rfc or "operator",
            # Copies of the contract's own fields, kept for the mints
            # written before A-91 and pinned equal to it by test.
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

    A contract that *changed* is re-minted (D-49.4), which is how the
    operator's recourse after an escalation works: fix the contract, resolve
    the escalation, and the next pass records the change and puts it in
    force. A re-mint carries the contract and transitions nothing (D-49.2),
    and never happens while the task is in flight (D-49.3).

    A contract that already landed is minted **and** recorded as landed, in
    that order, from the repository's own trailer. The repository outranks
    the host here (A-29): a partition's first pass sees every contract the
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
                # minted before this partition could see it (A-97). Guarded
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
            # can be (A-96): a review that ran and landed nothing is still
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
    (D-44.6): something has to be the lease running out, and it is the
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
) -> str | None:
    """One pass: reclaim what expired, mint what is new, then let the worker
    take at most one task. Returns the task id it handled, or None when the
    pass was idle.

    Reclaiming comes first because a task nobody is running is a task this
    pass could start, and the alternative is waiting a whole idle interval
    to notice.

    The scan is an importer, not a reader (D-49.1): it is how a contract the
    repository gained reaches the record, and the mint is its only consumer.
    What a worker claims and runs comes off the board.

    `dispatch=False` is the other half: import and reclaim, claim nothing.
    It is what a re-mint after a contract-shape change is run under (A-96) —
    the scan must reach the record without a worker taking the first thing
    it finds there, which on a repository with a queue is a real agent and
    real money.

    `paused` skips the two legs that can grow the queue and nothing else
    (D-48.4, D-23.6): the queue may drain during a pause, it may not grow.
    A pause is a statement about the operator's capacity to triage, never
    about the safety of what is already running, so an attempt in flight is
    not interrupted and a task already on the board is still claimed.
    """

    if standing is not None and not paused:
        # RFC 0023 §5.4: standing before the scan, so a contract this pass
        # mints is on the board this pass. D-23.6's first bound is the
        # caller's, and this is that caller: a paused pass evaluates no
        # predicate, because a predicate that fires creates work and a
        # pause is a statement that nobody has capacity to triage it.
        standing()

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
    paused: bool = False,
    dispatch: bool = True,
    standing: Standing | None = None,
) -> int:
    """Run passes until cancelled, or until *passes* of them have run.

    An idle pass sleeps; a productive one goes straight round again, because
    a partition that just landed something may have unblocked the next
    thing. Returns how many tasks were handled — the bounded form is what
    tests and a `--passes` dispatch use, and the unbounded one is the
    resident process D-44.5 asks for.
    """

    handled = 0
    seen = 0

    while passes is None or seen < passes:
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
            paused=paused,
            dispatch=dispatch,
            standing=standing,
        )

        if task_id is not None:
            handled += 1
            continue

        if passes is None or seen < passes:
            # Cancellation lands here in an idle manager, which is where a
            # kill is free: nothing is claimed, so nothing is left leased.
            await asyncio.sleep(idle_seconds)

    return handled
