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
from typing import TYPE_CHECKING

from torve.application.manager import project
from torve.config import layout
from torve.domain.events import ActorKind, EventKind, SubjectType

if TYPE_CHECKING:
    from pathlib import Path

    from torve.application.eventlog import EventLog
    from torve.application.worker import Worker
    from torve.domain.task import Task

# ----------------------- #

# How long an idle pass waits before looking again. Long enough that an idle
# manager costs nothing, short enough that a freshly adopted contract does
# not sit for a coffee break.
IDLE_SECONDS = 15.0

# The roles a worker executes. Review and draft are runner-minted mid-run
# (D-5.2, D-20.2) and are nobody's to claim from the board.
DISPATCHABLE_ROLES = ("implement", "revert")


# ....................... #


def contracts(root: Path) -> dict[str, Task]:
    """Every executable contract the repository carries, by id. An
    unreadable contract is skipped rather than fatal: one malformed file is
    not a reason for a manager to stop managing the rest."""

    from torve.gates.context import load_task

    tasks: dict[str, Task] = {}

    for path in sorted((root / layout.TORVE_DIR / "tasks").glob("T-*/contract.yaml")):
        try:
            task = load_task(path)

        except ValueError:
            continue

        if task.role in DISPATCHABLE_ROLES:
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


async def mint(
    log: EventLog, tasks: dict[str, Task], *, partition: str, actor_id: str
) -> list[str]:
    """Place contracts this partition has never seen onto its board.

    Minting is idempotent by reading rather than by remembering: a task the
    board already carries is already minted, whatever state it has since
    reached, so a restart re-mints nothing and a re-adopted contract is not
    duplicated.
    """

    board = project(await log.since(partition=partition))
    minted: list[str] = []

    for task_id, task in sorted(tasks.items()):
        if task_id in board.tasks:
            continue

        await log.record(
            EventKind.TASK_MINTED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task_id,
            actor_kind=ActorKind.MANAGER,
            actor_id=actor_id,
            payload={
                "title": _title(task),
                "source_id": task.rfc or "operator",
                "phase": task.phase,
                "depends_on": list(task.depends_on),
            },
        )
        minted.append(task_id)

    return minted


# ....................... #


async def once(log: EventLog, worker: Worker, root: Path, partition: str) -> str | None:
    """One pass: mint what is new, then let the worker take at most one
    task. Returns the task id it handled, or None when the pass was idle."""

    tasks = contracts(root)
    await mint(log, tasks, partition=partition, actor_id=worker.name)

    return await worker.once(tasks, partition)


# ....................... #


async def serve(
    log: EventLog,
    worker: Worker,
    root: Path,
    partition: str,
    *,
    idle_seconds: float = IDLE_SECONDS,
    passes: int | None = None,
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
        task_id = await once(log, worker, root, partition)

        if task_id is not None:
            handled += 1
            continue

        if passes is None or seen < passes:
            # Cancellation lands here in an idle manager, which is where a
            # kill is free: nothing is claimed, so nothing is left leased.
            await asyncio.sleep(idle_seconds)

    return handled
