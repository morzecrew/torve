"""The worker (RFC 0044 §5.5, D-44.6): claim, execute, record, release.

A worker holds no assignment state. Everything it knows about the task it
is running is in the log before it acts and in the log after, so killing
one costs its lease and nothing else — there is no in-memory queue to
drain, no partial progress to recover, and nothing another worker needs
handed over.

Execution itself is injected. The worker's job is the lifecycle around an
attempt — claiming it, recording what happened, releasing it — and the
machinery that actually runs an agent in a sandbox is v1's, ported as a
library rather than rewritten (D-44.12). That also makes the lifecycle
testable without a container.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import attrs

from torve.application.manager import LEASE_SECONDS, dispatchable, project
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import EscalationReason

if TYPE_CHECKING:
    from torve.application.eventlog import EventLog
    from torve.domain.task import Task

# ----------------------- #

# ....................... #


@dataclass(frozen=True)
class Outcome:
    """How the run ended, for the worker's release decision and nothing
    else. What each attempt did is recorded by the attempt itself (D-44.3),
    so this carries only what the lifecycle branches on: landed, escalated,
    or neither."""

    attempt: int
    exit_code: int
    landed_sha: str | None = None
    escalation: EscalationReason | None = None
    detail: str = ""


Execute = Callable[["Task"], Awaitable[Outcome]]


# ....................... #


@attrs.define(slots=True, kw_only=True, frozen=True)
class Worker:
    """One claim-puller. Constructed per runtime with its ports resolved,
    the same shape every service here takes."""

    log: EventLog
    name: str
    execute: Execute

    # ....................... #

    async def claim(self, tasks: dict[str, Task], partition: str) -> Task | None:
        """Take the first task this partition could start, or nothing.

        The board is rebuilt from the log on every pass rather than carried
        between them: a worker that remembers what it saw last time is a
        worker whose memory can disagree with the record.
        """

        board = project(await self.log.since(partition=partition))
        ready = dispatchable(tasks, board, partition)

        if not ready:
            return None

        task = tasks[ready[0]]

        await self.log.record(
            EventKind.TASK_CLAIMED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task.id,
            actor_kind=ActorKind.MANAGER,
            actor_id=self.name,
            payload={"worker": self.name, "lease_seconds": LEASE_SECONDS},
        )

        return task

    # ....................... #

    async def run(self, task: Task, partition: str) -> Outcome:
        """Execute the task and hand back what it produced.

        Nothing about the attempts is recorded here, deliberately. One
        dispatch is up to `poison_ceiling` attempts, each possibly under a
        different tier and each with its own gate verdict, and the worker
        sees one outcome — so a record written from here would be a summary
        claiming to be a history. The attempts report themselves from where
        they happen (`executors.runner_execute`, D-44.3); this method owns
        the boundary around them and nothing inside it.
        """

        return await self.execute(task)

    # ....................... #

    async def release(self, task: Task, partition: str, outcome: Outcome) -> None:
        """Hand the task back to the board in the state its facts leave it:
        landed, escalated, or queued for another pass."""

        if outcome.escalation is not None:
            await self.log.record(
                EventKind.ESCALATION_RAISED,
                partition=partition,
                subject_type=SubjectType.TASK,
                subject_id=task.id,
                actor_kind=ActorKind.WORKER,
                actor_id=self.name,
                payload={"reason": outcome.escalation, "detail": outcome.detail[:300]},
            )

            return

        if outcome.landed_sha:
            await self.log.record(
                EventKind.LANDING_RECORDED,
                partition=partition,
                subject_type=SubjectType.TASK,
                subject_id=task.id,
                actor_kind=ActorKind.MANAGER,
                actor_id=self.name,
                payload={"sha": outcome.landed_sha, "attempt": outcome.attempt},
            )

            return

        await self.log.record(
            EventKind.TASK_RELEASED,
            partition=partition,
            subject_type=SubjectType.TASK,
            subject_id=task.id,
            actor_kind=ActorKind.MANAGER,
            actor_id=self.name,
            payload={"reason": outcome.detail or "attempt finished without a landing"},
        )

    # ....................... #

    async def once(self, tasks: dict[str, Task], partition: str) -> str | None:
        """One full pass: claim, run, release. Returns the task id it
        handled, or None when the partition had nothing to start."""

        task = await self.claim(tasks, partition)

        if task is None:
            return None

        outcome = await self.run(task, partition)
        await self.release(task, partition, outcome)

        return task.id
