"""`torve run` — one task, synchronously, exit code is the outcome (S-0003/D-1).

The attempt loop is one durable function executed through the TaskStore
facade (S-0001/D-14): forze's runner owns the lease heartbeat, cancel observation and
fenced terminal writes; this module owns what the loop *means* — transitions
executed from facts, the poison ceiling checked before dispatch, gates
outside the agent session (S-0001/D-11; shell gates in a fresh sandbox, a
decision logged in T-0003).

`drive_attempts` is the pure core, driven by hooks: `torve run` supplies the
real sandbox/gates/landing hooks, and the DST simulation supplies simulated
ones — one loop, two harnesses, so the invariants exercise the code that
ships (S-0003/tests).

What the real hooks *do* is steps over a `Dispatch` (S-0046): the agent
session and the revert leg in `session`, the review in `review`, and the
three that stay here beside the loop that sequences them — the gate pass,
the landing and the budget checkpoint. `real_hooks` binds them.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from forze.application.contracts.durable.function import (
    DurableRunStatus,
    current_durable_run,
)
from forze.application.execution import ExecutionContext
from forze.base.primitives import JsonDict

from torve.application import decisions
from torve.application.dispatch import (
    BARE,
    CONFIGURED,
    Dispatch,
    GatePass,
    RunDeps,
    attempt_row,
    cache_volumes,
    close_dispatch,
    emit,
    open_broker,
    open_dispatch,
    review_gated,
)
from torve.application.forge import compose_pr
from torve.application.manager import in_flight_fences, project
from torve.application.planner import change_type_of
from torve.application.ports import (
    AgentResult,
    JudgePort,
    NestedDocker,
    Runtime,
    SandboxHandle,
    SandboxSpec,
    judge_ref,
)
from torve.application.review import review_step, reviewer_for
from torve.application.runstate import Escalation, RunState
from torve.application.session import (
    AXIS_SEVERITY,
    RevertConflict,
    halted,
    revert_leg,
    run_agent_session,
)
from torve.application.sizing import has_children
from torve.application.taskstore import TaskStore
from torve.application.telemetry import (
    append_record,
    build_record,
    config_hash,
    engine_event,
    record_payload,
    record_row,
)
from torve.base import naming
from torve.base.clock import parse
from torve.config import layout
from torve.config.manifest import UNLABELED_AXIS, Gate, Manifest, load_manifest
from torve.config.runconfig import RunnerConfig
from torve.config.spec import TRACKED_LIST, write_tracked_files
from torve.domain.attempt import GateResult
from torve.domain.events import ActorKind, EventKind, HoldoutFile, SubjectType, VerifyHoldout
from torve.domain.states import BlockedDispatch, EscalationReason, TaskState
from torve.domain.task import Task
from torve.gates.context import GateContext, GitError, build_context, git, resolve_base
from torve.gates.contract import spec
from torve.gates.runner import RunReport, run_gates

# ----------------------- #


@dataclass
class AttemptHooks:
    """What one attempt does, one gate pass does, and one landing does. The
    loop below owns the states and facts; the hooks own the mechanism."""

    attempt: Callable[[RunState], Awaitable[AgentResult]]
    # After an attempt: the escalation a halted divergence entry asks for
    # (S-0092/D-3), or None when nothing halted.
    halted: Callable[[int], EscalationReason | None]
    gates: Callable[[RunState], Awaitable[tuple[int, str, str]]]  # exit, summary, config hash
    land: Callable[[RunState, str], Awaitable[str]]  # returns the recorded fact
    # Right after an attempt that actually ran, before it is judged: what the
    # host records about it (S-0116/D-9). None for a run that records nothing.
    after_attempt: Callable[[RunState], None] | None = None
    # After green gates, before landing (S-0005, S-0005/D-11): returns the fact
    # for the reviewed transition, or None when the review escalated the
    # target. Absent -> the review-not-configured bridge.
    review: Callable[[RunState], Awaitable[str | None]] | None = None
    # One close per run, whatever path the loop exits by: the broker's
    # revocation and final usage land here (S-0021/the-port).
    close: Callable[[], None] | None = None
    # Called once, only when the run ends on budget exhaustion — wallclock or
    # tokens (S-0026 S-0026/D-8/9): commits whatever the worktree holds so the
    # next dispatch has a candidate tip to continue from. Never called on a
    # convicted escalation — that restarts from base unchanged.
    checkpoint: Callable[[RunState], None] | None = None
    # Called once, when the review stage escalates the target or the attempt
    # halts on a locked row (S-0086/D-2, S-0086/A-1): commits the tree on the
    # task's branch under the checkpoint trailer, so work the attempt did
    # right is not lost to an escalation that is not a conviction.
    reviewed_tree: Callable[[RunState], None] | None = None


# ....................... #


# Continuation eligibility's shared marker (S-0026 S-0026/D-8): every detail
# string this module writes for a wallclock-caused budget_exhausted
# escalation starts with it, and `_exhausted` is the only reader — so
# generation and detection can never drift apart.
_WALLCLOCK_MARKER = "wallclock budget exhausted"

# The subjects `_commit_reviewed_tree` writes for the escalations a
# continuation resumes from (S-0090/D-2, S-0096/D-1), and the only thing
# `_checkpointed_for_resume` reads off a branch tip.
_BLOCKER_MARKER = "escalated from review on a blocker"
_HALTED_MARKER = "escalated halted on a locked row"
_UNDERSPECIFIED_MARKER = "escalated halted on a spec gap"


def _exhausted(escalation: Escalation) -> bool:
    """S-0026/D-8: budget exhaustion — wallclock or tokens. `iterations`
    exhaustion (like `poison_ceiling`) is excluded on purpose: repeated red
    gates is a judgement on the work, not a clock running out. These are the
    escalations the budget checkpoint commits for (S-0026/D-9)."""

    if escalation.reason == str(EscalationReason.COST_ANOMALY):
        return True

    return escalation.reason == str(
        EscalationReason.BUDGET_EXHAUSTED
    ) and escalation.detail.startswith(_WALLCLOCK_MARKER)


def _continuable(escalation: Escalation) -> bool:
    """The escalations the next dispatch resumes from: budget exhaustion
    (S-0026/D-8), and a review blocker or a halt (S-0090/D-2, S-0096/D-1) —
    a review that asks for one file's change costs that change, and a halt
    answered by an amendment resumes where it stopped, whether it cites a
    LOCKED row or is `underspecified`. A gate conviction still restarts from
    base."""

    return _exhausted(escalation) or escalation.reason in (
        str(EscalationReason.BLOCKER_FINDING),
        str(EscalationReason.LOCKED_CONFLICT),
        str(EscalationReason.UNDERSPECIFIED),
    )


# ....................... #


def _elapsed_minutes(state: RunState) -> float:
    started = parse(state.history[0]["at"])

    return (datetime.now(UTC) - started).total_seconds() / 60


# ....................... #


async def drive_attempts(
    state: RunState, task: Task, config: RunnerConfig, hooks: AttemptHooks
) -> RunState:
    ceiling = config.poison_ceiling
    iterations = task.budget.iterations
    wallclock_minutes = task.budget.wallclock_minutes

    try:
        result = await _attempt_loop(state, task, hooks, ceiling, iterations, wallclock_minutes)

        # S-0026/D-9: the checkpoint is what gives the next dispatch a candidate
        # tip to cut from — taken once, right where the loop actually ended,
        # never for a convicted escalation.
        if (
            hooks.checkpoint is not None
            and result.escalation is not None
            and _exhausted(result.escalation)
        ):
            hooks.checkpoint(result)

        return result

    finally:
        # One close per run, whatever path the loop exits by — a green
        # landing, an escalation, a cancellation. The broker's token is
        # revoked and its final usage recorded here (S-0021/the-port).
        if hooks.close is not None:
            hooks.close()


# ....................... #


async def _attempt_loop(
    state: RunState,
    task: Task,
    hooks: AttemptHooks,
    ceiling: int,
    iterations: int | None,
    wallclock_minutes: int | None = None,
) -> RunState:
    # S-0099/D-8: the state is seeded with every attempt the task has already
    # had, so the number an attempt carries is the task's whole sequence. The
    # ceiling and the budget bound what *this* dispatch makes, not the
    # lifetime — a requeue continues the count without inheriting a spent
    # budget.
    start = state.attempts

    while True:
        made = state.attempts - start

        # Poison ceiling is checked before dispatch, never after (S-0001/state-machine).
        if made >= ceiling:
            state.escalate(EscalationReason.POISON_CEILING, f"{made} attempts, ceiling {ceiling}")

            return state

        if iterations is not None and made >= iterations:
            state.escalate(
                EscalationReason.BUDGET_EXHAUSTED, f"{made} attempts, budget {iterations}"
            )

            return state

        if (
            wallclock_minutes is not None
            and (elapsed := _elapsed_minutes(state)) >= wallclock_minutes
        ):
            state.escalate(
                EscalationReason.BUDGET_EXHAUSTED,
                f"{_WALLCLOCK_MARKER}: {elapsed:.1f}m elapsed, budget {wallclock_minutes}m",
            )

            return state

        state.transition(TaskState.RUNNING, f"attempt {state.attempts + 1} dispatched")
        state.save()
        result = await hooks.attempt(state)

        # An attempt hook that escalated (the broker refusing the run's
        # budget, S-0021/D-6) stops the loop: continuing would burn attempts
        # against a refusal that will not lift.
        if state.escalation is not None:
            return state

        if result.refused is not None:
            # S-0089/D-2: the identical dispatch is refused again, so this was
            # no attempt a model made — uncounted, unjudged, not checkpointed.
            state.attempts -= 1
            state.escalate(EscalationReason.SEAT_REFUSED, result.refused)

            return state

        # S-0116/D-9: a verify attempt's held-out properties are read off the
        # worktree and recorded here, before any verdict — what the verifier
        # wrote is a fact about the attempt whether or not its gates go green.
        if hooks.after_attempt is not None:
            hooks.after_attempt(state)

        if halt := hooks.halted(state.attempts):
            # Terminal by design, not an error: the one case where a task
            # stops on working code (S-0001/state-machine). The entry picks
            # the escalation — a LOCKED row or, otherwise, underspecified
            # (S-0096/D-1).
            state.escalate(
                halt,
                f"halted divergence entry in the {task.id} execution log",
            )

            # S-0086/A-1: working code that stopped on a row is the tree a
            # person amends the row for; it goes on the branch, not the floor
            # (bloomery T-0020, 2026-09-19).
            if hooks.reviewed_tree is not None:
                hooks.reviewed_tree(state)

            return state

        if result.timed_out or result.exit_code != 0:
            fact = (
                "agent hit the hard timeout"
                if result.timed_out
                else f"agent exited {result.exit_code}"
            ) + "; gates not run"

            state.transition(TaskState.GATED, fact)
            state.save()
            continue

        state.transition(TaskState.GATED, "agent exited 0; gates running")
        state.save()

        try:
            exit_code, summary, digest = await hooks.gates(state)

        except BlockedDispatch:
            # A contingent write an in-flight run holds (S-0104/D-3): the
            # attempt goes back to the queue, never to a person, so the
            # refusal propagates to whoever claimed it rather than becoming
            # an infrastructure escalation.
            raise

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))
            return state

        if exit_code != 0:
            state.history.append(
                {
                    "at": state.heartbeat,
                    "from": str(state.state),
                    "to": str(state.state),
                    "fact": f"gates red: {summary}",
                }
            )

            state.save()
            continue

        outcome = await _apply_review(hooks, state)

        if outcome is None:
            continue  # S-0043/D-1: blocker revision budget spent, retry in place

        if not outcome:
            return state  # a surviving blocker escalated the target

        fact = await hooks.land(state, digest)
        state.transition(TaskState.READY, fact)
        state.save()

        return state


# ....................... #


async def _apply_review(hooks: AttemptHooks, state: RunState) -> bool | None:
    """Runs the configured review hook and transitions to REVIEWED. True
    lands; False means the target escalated (a surviving blocker's spent
    budget, an unparseable verdict, or a broker budget refusal) — the caller
    stops the loop without landing; None means a surviving blocker spent
    revision budget (S-0043 S-0043/D-1) without escalating — the caller
    retries the same worktree. The review-not-configured bridge always
    returns True."""

    if hooks.review is None:
        state.transition(TaskState.REVIEWED, "gates green; review not configured")
        return True

    review_fact = await hooks.review(state)

    if review_fact is None:
        if state.escalation is None:
            return None

        # S-0086/D-2: the tree that passed the battery is committed before the
        # escalation leaves this stage — a blocker, an unreadable verdict and a
        # broker refusal alike.
        if hooks.reviewed_tree is not None:
            hooks.reviewed_tree(state)

        return False

    state.transition(TaskState.REVIEWED, review_fact)
    return True


# ....................... #


class _SandboxExecutor:
    """ExecuteOnce over a fresh sandbox, created lazily so gate passes with no
    shell gates cost nothing, destroyed by the caller when the pass ends."""

    def __init__(self, runtime: Runtime, spec: SandboxSpec, workspace: Path) -> None:
        self.runtime, self.spec, self.workspace = runtime, spec, workspace
        self.handle: SandboxHandle | None = None

    # ....................... #

    def __call__(self, command: str, timeout: float) -> tuple[int | None, str]:
        if self.handle is None:
            self.handle = self.runtime.create(self.spec, self.workspace)

        result = self.runtime.exec(self.handle, command, timeout)

        return result.exit_code, result.output

    # ....................... #

    def close(self) -> None:
        if self.handle is not None:
            self.runtime.sync_out(self.handle, self.workspace)
            self.runtime.destroy(self.handle)
            self.handle = None


# ....................... #


def _write_attempt_record(run: Dispatch, record: dict[str, Any], attempt: int) -> None:
    """One record, both carriers (S-0044/A-4). The stream is written from the
    record and the event is the same record with the envelope's fields
    removed, so a projection reading either one is reading the same
    numbers."""

    payload = record_payload(record, attempt)
    manifest_rel = load_manifest(layout.gates_file(run.worktree)).telemetry

    append_record(
        run.root / manifest_rel, record_row(payload, task_id=record["task_id"], at=record["at"])
    )
    emit(run, "gates_evaluated", attempt, **payload)


# ....................... #


def _promoted_paths(task: Task, manifest: Manifest, paths: list[str]) -> list[str]:
    """The changed paths the task was right to reach beyond its declared
    globs (S-0104/D-1, D-5): a literal in the contract's `contingent` block,
    and a file riding with an in-scope source through the manifest's
    `scope.derived` map, applied both directions. A path the declared globs
    already admit is not promoted — nothing was widened.

    An empty allow-set is unconstrained (S-0002/scope-in-detail): every path is already in
    scope, so nothing is named."""

    if not task.scope.allow:
        return []

    allow = spec(task.scope.allow)
    contingent = set(task.scope.contingent)

    def in_scope(path: str) -> bool:
        return allow.match_file(path) or path in contingent

    beyond = set(contingent)

    for source, riders in manifest.scope.derived.items():
        if in_scope(source):
            beyond.update(riders)

        if any(in_scope(rider) for rider in riders):
            beyond.add(source)

    return sorted(
        {path for path in paths if path and not allow.match_file(path) and path in beyond}
    )


def _recorded_promotions(state: RunState) -> list[str]:
    """The contingent paths a run has promoted, read off its own history —
    the host-local half of the fence the board folds from `scope.promoted`
    (S-0104/D-2). A run whose history holds none reads as none."""

    marker = "promoted "

    return [
        str(row.get("fact", ""))[len(marker) :]
        for row in state.history
        if str(row.get("fact", "")).startswith(marker)
    ]


def _in_flight(root: Path, skip_id: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """(task id, declared allow, promoted paths) for every run in flight on
    this host — the fence `_blocking_overlap` and the write-time refusal
    read. A review claims no fence (S-0005/D-2): it writes nothing by
    construction, so its empty allow-set is never "unconstrained". A run
    whose contract the host does not hold is skipped rather than assumed
    disjoint, exactly as the scan has always read it."""

    from torve.gates.context import load_task

    active = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}

    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        if state.task_id == skip_id or state.state not in active:
            continue

        contract = root / layout.TORVE_DIR / "tasks" / state.task_id / "contract.yaml"

        if not contract.is_file():
            continue

        other = load_task(contract)

        if other.role == "review":
            continue

        yield state.task_id, list(other.scope.allow), _recorded_promotions(state)


def _held_contingent(
    root: Path, task: Task, paths: list[str], fences: Mapping[str, list[str]] | None = None
) -> tuple[str, str] | None:
    """The first of *paths* an in-flight task holds, with the holder's id
    (S-0104/D-2, D-3). The fence is read from both halves: the run states on
    this host, which the dispatch scan also reads, and the board — every
    in-flight task of the partition, wherever it is running, so a task
    another host claimed is a holder here too. A fence is the task's declared
    allow plus every contingent path it promoted; an unconstrained task holds
    everything."""

    held: dict[str, tuple[list[str], list[str]]] = {
        holder: (allow, promoted) for holder, allow, promoted in _in_flight(root, task.id)
    }

    for holder, fence in (fences or {}).items():
        if holder != task.id and holder not in held:
            held[holder] = (fence, [])

    for holder, (allow, promoted) in held.items():
        if not allow:
            return holder, paths[0]

        for path in paths:
            if _scope_overlap([path], [*allow, *promoted]) is not None:
                return holder, path

    return None


def _board_fences(run: Dispatch) -> dict[str, list[str]]:
    """S-0104/D-2: the board's in-flight fence as the run reads it at the
    write — every active task's declared allow plus the paths it promoted,
    wherever the task is running. The channel is the run's own route into the
    record; without one — a hand dispatch, a test — the fence is host-local,
    exactly as before."""

    from torve.application.eventlog import RunLogChannel

    channel = run.deps.channel

    if not isinstance(channel, RunLogChannel):
        return {}

    events = asyncio.run_coroutine_threadsafe(
        channel.log.since(partition=channel.partition), channel.loop
    ).result(channel.timeout_s)

    return in_flight_fences(project(events))


def _record_promotions(run: Dispatch, state: RunState, ctx: GateContext) -> None:
    """S-0104/D-2: a contingent path this attempt actually wrote joins the
    task's fence. Recorded from the diff, so the widening is read off what
    the tree holds rather than believed from the plan — and recorded on the
    board, so a task sharing the path waits on this one instead of writing
    it at the same time.

    S-0104/D-3: a written contingent path an in-flight run holds is refused
    at the write, with the holder's id. The attempt goes back to the queue
    the way a `blocked_by_overlap` dispatch does — its run state is dropped
    so the next pass may claim it again, and nothing escalates, because a
    collision costs a wait and not a person."""

    if run.shadow or ctx.task is None:
        return

    written = [entry.path for entry in ctx.diff if entry.path]
    promoted = _promoted_paths(ctx.task, ctx.manifest, written)

    if not promoted:
        return

    held = _held_contingent(run.root, ctx.task, promoted, _board_fences(run))

    if held is not None:
        holder, path = held

        engine_event(
            run.root, "blocked_dispatch", {"task": run.task.id, "blocked_by": holder, "path": path}
        )
        state.path.unlink(missing_ok=True)

        raise BlockedDispatch(f"blocked_by_overlap: {holder} on {path}")

    for path in promoted:
        emit(run, "scope_promoted", state.attempts, path=path)
        state.history.append(
            {
                "at": state.heartbeat,
                "from": str(state.state),
                "to": str(state.state),
                "fact": f"promoted {path}",
            }
        )

    state.save()


# ....................... #


# ----------------------- #
# The verifier's held-out properties (S-0116/D-9, D-10). A verify attempt
# leaves them under `.torve/tmp/holdout/` of its worktree; the host reads
# them, records them once on the implement task of the entry the verifier
# serves, and deletes them, so they never reach the verify candidate's
# commit and never reach the implementer's sandbox.

# The worktree-relative directory a verify attempt writes its held-out
# properties under (S-0116/D-6), and the ceiling one event may carry.
HOLDOUT_REL = f"{layout.TORVE_DIR}/tmp/holdout"
HOLDOUT_LIMIT = 64 * 1024


# ....................... #


def _holdout_dir(worktree: Path) -> Path:
    return worktree / HOLDOUT_REL


def holdout_files(worktree: Path) -> list[HoldoutFile]:
    """The held-out files a verify attempt left, as names and texts, bounded
    by `HOLDOUT_LIMIT` (S-0116/D-9). Empty when it wrote none, which is still
    a record: the latest event is the held-out set, and it replaces every
    earlier one. Sorted, so the same tree reads the same record."""

    directory = _holdout_dir(worktree)

    # A symlink is never a property: following one would read a host file the
    # verifier named into the record and a gate checkout. `rglob` does not
    # descend through a linked directory, so the directory and each yielded
    # path are all there is to refuse.
    if directory.is_symlink() or not directory.is_dir():
        return []

    files: list[HoldoutFile] = []
    budget = HOLDOUT_LIMIT

    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue

        text = path.read_text(encoding="utf-8", errors="replace")

        if len(text) > budget:
            text = text[:budget]

        files.append(HoldoutFile(name=path.relative_to(directory).as_posix(), text=text))
        budget -= len(text)

        if budget <= 0:
            break

    return files


# ....................... #


def _log_channel(run: Dispatch) -> Any:
    """The run's route into the record, when it holds one — a hand dispatch
    and a test have none, and then there is nowhere to record."""

    from torve.application.eventlog import RunLogChannel

    channel = run.deps.channel

    return channel if isinstance(channel, RunLogChannel) else None


# ....................... #


def _record_worker_event(
    run: Dispatch, kind: EventKind, subject_id: str, payload: dict[str, Any]
) -> None:
    """Record one fact the worker owns about a subject that may not be this
    run's own task — the held-out set lands on the entry's implement task
    (S-0116/D-9). The append blocks on the loop that owns the store, so a
    battery that reads it next sees it."""

    channel = _log_channel(run)

    if channel is None:
        return

    asyncio.run_coroutine_threadsafe(
        channel.log.record(
            kind,
            partition=channel.partition,
            subject_type=SubjectType.TASK,
            subject_id=subject_id,
            actor_kind=ActorKind.WORKER,
            actor_id=channel.seat,
            payload=payload,
        ),
        channel.loop,
    ).result(channel.timeout_s)


# ....................... #


def _entry_implement_task(run: Dispatch) -> str | None:
    """The implement task of the verify task's phasing entry (S-0116/D-9),
    read off the worktree's corpus by the link D-5 mints at plan time: the
    implement contract that depends on this verify task. Several phasing
    entries share a document and a phase (D-4), so spec and phase alone name
    the wrong task — every entry's implement task does, and the held-out set
    would land on a foreign one. None when the corpus holds none.

    The root holds every minted contract where the files are the record; the
    worktree holds only this task's own copy, because `.torve/tasks/` is
    ignored. With a store, the board holds them (S-0056/D-9)."""

    from torve.gates.context import load_task

    for corpus in (run.root, run.worktree):
        tasks = corpus / layout.TORVE_DIR / "tasks"

        if not tasks.is_dir():
            continue

        for path in sorted(tasks.glob("*/contract.yaml")):
            try:
                other = load_task(path)

            except ValueError:
                continue

            if other.role == "implement" and run.task.id in other.depends_on:
                return other.id

    return _board_implement_task(run)


# ....................... #


def _board_implement_task(run: Dispatch) -> str | None:
    """The entry's implement task off the board the run's record projects —
    the store-backed half of `_entry_implement_task`. None without a record."""

    from torve.application.manager import project

    channel = _log_channel(run)

    if channel is None:
        return None

    records = asyncio.run_coroutine_threadsafe(
        channel.log.of_subject_type(SubjectType.TASK, partition=channel.partition), channel.loop
    ).result(channel.timeout_s)

    for view in sorted(project(records).tasks.values(), key=lambda one: one.task_id):
        contract = view.contract

        if contract is not None and contract.role == "implement":
            if run.task.id in contract.depends_on:
                return view.task_id

    return None


# ....................... #


def _holdout_subject(run: Dispatch) -> str | None:
    """Where this task's held-out set is recorded and read (S-0116/D-9): a
    verify task's lives on its entry's implement task; every other task's is
    its own."""

    if run.task.role == "verify":
        return _entry_implement_task(run)

    return run.task.id


# ....................... #


def _record_holdout(run: Dispatch, state: RunState) -> None:
    """After a verify attempt, record its held-out properties and delete them
    from the worktree (S-0116/D-9). A no-op for every other role and for a
    shadow replay, which has no record of its own to write to."""

    if run.shadow or run.task.role != "verify":
        return

    files = holdout_files(run.worktree)
    shutil.rmtree(_holdout_dir(run.worktree), ignore_errors=True)

    subject = _holdout_subject(run)

    if subject is None:
        return

    _record_worker_event(
        run,
        EventKind.VERIFY_HOLDOUT,
        subject,
        VerifyHoldout(files=files).model_dump(mode="json"),
    )


# ....................... #


def _latest_holdout(run: Dispatch, subject_id: str) -> list[HoldoutFile]:
    """The held-out files of the latest `verify.holdout` event on *subject_id*
    (S-0116/D-9, D-10). Empty when there is none — the ordinary task."""

    channel = _log_channel(run)

    if channel is None:
        return []

    events = asyncio.run_coroutine_threadsafe(
        channel.log.history(subject_id, partition=channel.partition), channel.loop
    ).result(channel.timeout_s)

    latest = [event for event in events if event.kind is EventKind.VERIFY_HOLDOUT]

    if not latest:
        return []

    return VerifyHoldout.model_validate(latest[-1].payload).files


# ....................... #


def _restore_holdout(run: Dispatch) -> None:
    """Before a verify attempt, put its entry's latest recorded held-out set
    back where the verifier writes it (S-0116/D-9). `_record_holdout` removes
    the files after every attempt and the latest event replaces every earlier
    one, so a retry that rewrote only its visible test would record an empty
    set over the real one. A name that leaves the directory is skipped."""

    subject = _holdout_subject(run)

    if subject is None:
        return

    directory = _holdout_dir(run.worktree)

    for one in _latest_holdout(run, subject):
        target = directory / one.name

        if not target.resolve().is_relative_to(directory.resolve()):
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(one.text, encoding="utf-8")


async def _verify_attempt(
    run: Dispatch, attempt: Callable[[RunState], Awaitable[AgentResult]], state: RunState
) -> AgentResult:
    _restore_holdout(run)

    return await attempt(state)


# ....................... #


def _attach_holdout(run: Dispatch, ctx: GateContext) -> None:
    """Hand a battery the held-out properties of the task's latest event, and
    the directory a verifier's tests live under (S-0116/D-10, D-12): a caller
    that injected a set on the deps — the verifier eval's candidate arm, whose
    replay writes no event — is handed that set instead."""

    if run.deps.holdout:
        ctx.holdout = list(run.deps.holdout)
    else:
        subject = _holdout_subject(run)
        ctx.holdout = _latest_holdout(run, subject) if subject is not None else []

    ctx.tests_dir = run.config.verify.tests_dir


# ....................... #


def _nested_docker(run: Dispatch, role: str) -> NestedDocker | None:
    """The daemon a sandbox of *role* gets, or None (S-0111/D-1, D-4). The
    configuration resolves role membership (`nests`); the runner composes the
    adapter's fact from it, slot-suffixing the docker-state volume like every
    other per-slot volume so two workers never share a daemon's state."""

    if not run.config.runtime.nests(role):
        return None

    nested = run.config.runtime.nested

    return NestedDocker(
        image=nested.image,
        # A shadow run starts cold (S-0035/D-3): no slot volume, so its daemon
        # gets a throwaway one that leaves with the side container.
        volume="" if run.shadow else f"{nested.cache_volume}-{run.config.worker_slot}",
        registry_mirrors=tuple(nested.registry_mirrors),
        ready_s=nested.ready_s,
    )


# ....................... #


def _gates_spec(run: Dispatch, state: RunState) -> SandboxSpec:
    """The battery's sandbox spec (S-0003/D-8, S-0035/D-3, S-0111/D-4): the same
    image and derived cache the attempt ran under, and — when the configuration
    nests the gates role — a daemon of its own in a side container."""

    return SandboxSpec(
        name=naming.sandbox_name(run.task.id, state.run_id) + "-gates",
        # Shell gates run over the same image the agent ran under
        # (S-0003/D-8) — an image swap between attempt and gates would be
        # its own regime change.
        image=run.image or run.config.runtime.image,
        labels=naming.labels(run.task.id, state.run_id, run.root),
        timeout_s=run.config.runtime.sandbox_timeout,
        # The battery pays the toolchain cold tax (mypy, ruff, uv all
        # run under these gates), so the live gates sandbox carries the
        # same derived-cache mount the attempt did — an empty dict here
        # is the cold pass, shadow's the exclusion (S-0035/D-3).
        volumes=cache_volumes(run),
        # The gates sandbox gets the nested daemon first (S-0111/D-4).
        nested=_nested_docker(run, "gates"),
    )


# ....................... #

# The toolchain caches an attempt's own run leaves behind (S-0114/D-4): never
# an ignored write, whichever path they sit under.
_IGNORED_CACHES = frozenset(
    {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
)


def _attempt_started(run: Dispatch) -> float | None:
    """When the attempt's sandbox started, as a POSIX timestamp — the line
    between an ignored file the attempt left and one already there. None
    when the run stamped nothing (a test pass, a replay)."""

    raw = run.meta.get("started_at")

    if not raw:
        return None

    try:
        return parse(str(raw)).timestamp()

    except ValueError:
        return None


def _canonical_tracked(worktree: Path) -> str:
    """What the engine's own `write_tracked_files` would leave in the
    worktree's list right now — the content an engine-written file is
    compared against (S-0114/D-4)."""

    try:
        return git(worktree, "ls-files", "-z")

    except GitError:
        return ""


def _ignored_writes(run: Dispatch) -> list[str]:
    """The ignored files the attempt left in the worktree, by path
    (S-0114/D-4): the toolchain caches its own run wrote are not writes, and
    a file the engine wrote there counts only once its content differs from
    what the engine wrote. An empty list is an attempt that wrote nothing
    outside its commit.

    A file the host cannot stat — a broken symlink, a path that vanished
    between the listing and the read — is left out rather than reported."""

    worktree = run.worktree

    try:
        listed = git(worktree, "ls-files", "--others", "--ignored", "--exclude-standard")

    except GitError:
        return []

    started = _attempt_started(run)
    engine = _task_bookkeeping(run.task.id)
    canonical: str | None = None
    found: list[str] = []

    for name in listed.splitlines():
        name = name.strip()

        if not name or name in engine:
            continue

        if any(part in _IGNORED_CACHES for part in PurePosixPath(name).parts):
            continue

        path = worktree / name

        try:
            if not path.is_file():
                continue

            if name == TRACKED_LIST:
                if canonical is None:
                    canonical = _canonical_tracked(worktree)

                if path.read_text(encoding="utf-8", errors="replace") == canonical:
                    continue

            # The instant's resolution is the second (S-0058/D-7), so a file
            # the engine wrote in the attempt's starting second reads as
            # before it: the engine writes the pack and the tracked list in
            # that window, and an attempt's own run outlives it.
            if started is not None and int(path.stat().st_mtime) <= int(started):
                continue

        except OSError:
            continue

        found.append(name)

    return sorted(found)


def _ignored_write_result(names: list[str]) -> GateResult:
    """The attempt's ignored writes as a shadow result (S-0114/D-4): it
    changes no verdict and the reviewer never reads it (S-0054/D-13) — the
    record, `torve why` and the next attempt's feedback carry it."""

    return GateResult(
        name="ignored-writes",
        outcome="fail",
        state="shadow",
        sha="",
        output="the attempt wrote ignored files outside its commit:\n" + "\n".join(names),
    )


def _with_ignored_writes(report: RunReport, names: list[str]) -> RunReport:
    """The battery as the record carries it: the same results, with the
    ignored-writes fact beside them when there is one to record."""

    if not names:
        return report

    return RunReport(
        results=[*report.results, _ignored_write_result(names)], exit_code=report.exit_code
    )


def _deepen_judge(judge: Path, root: Path, task_id: str) -> None:
    """Hand the checkout its ancestry, the attempt's base among it
    (S-0114/D-1): the snapshot fetch cuts the history at the snapshot, and a
    gate that diffs against `{base}` or reads a file as the base tree holds
    it needs the commit the snapshot came from. Every object this fetches is
    one the snapshot reaches."""

    if not (judge / ".git" / "shallow").exists():
        return

    git(judge, "fetch", "--unshallow", "--no-tags", str(root), judge_ref(task_id))


# ....................... #


def run_gate_pass(run: Dispatch, state: RunState) -> tuple[int, str, str, list[GateResult], str]:
    """(exit_code, summary, config_hash, results, patch) — the results and
    the patch feed the review's input when one is configured. Raises on
    infrastructure failure.

    The battery runs in a sandbox of its own over the same image the agent
    ran under (S-0003/D-8) and the same derived cache (S-0035/D-3), because a pass
    that judged a different regime than the attempt ran in is judging
    something else.

    What it judges is the judge checkout (S-0114/D-2): the snapshot of what
    the commit would carry, mounted in place of the worktree, so nothing the
    attempt left outside its commit reaches a verdict. The checkout is
    removed when the battery ends, green or red (S-0114/D-5).

    The attempt record is built once here and written to both carriers from
    that one object (S-0044/A-4): the telemetry row every projection reads, and
    the event the board folds. The row is written whether or not anything
    is observing — the record exists before either carrier does."""

    manifest_path = layout.gates_file(run.worktree)

    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no gate manifest in the worktree ({manifest_path}) — gates are fail-closed"
        )

    manifest = load_manifest(manifest_path)

    task_file = layout.task_file(run.worktree, run.task.id)
    source = layout.task_file(run.root, run.task.id)

    if not task_file.is_file() and source.is_file():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, task_file)

    # S-0114/D-4: the attempt is over and the gate pass has not written into
    # the worktree again — the ignored files it left there are listed once,
    # here, and recorded as a shadow fact beside the battery.
    ignored = _ignored_writes(run)

    opener = run.judge or run.deps.workspace
    judge = opener.judge_checkout(run.task.id)

    try:
        # A replay's checkout is fetched whole from its clone, whose history
        # already ends at the replayed parent; the root holds no ref of it.
        if not run.shadow:
            _deepen_judge(judge, run.root, run.task.id)

        # The engine's own files for the task — its contract, the agent's log —
        # live under an ignored directory, so no snapshot carries them. The
        # battery reads them, and without them judged the attempt as no task's.
        for rel in _task_bookkeeping(run.task.id):
            held, carried = run.worktree / rel, judge / rel

            if held.is_file() and not carried.exists():
                carried.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(held, carried)

        # The base is resolved on the host and named to the checkout by the sha
        # of its merge base with the attempt (S-0114/D-1): an ancestor of the
        # snapshot, so the checkout holds it even when the base ref has moved
        # since the cut. The gates read only the merge base, `{base}` included.
        host_base = resolve_base(run.worktree, run.gates_base or run.config.base)
        base = (
            git(run.worktree, "merge-base", host_base, "HEAD").strip()
            if host_base is not None
            else None
        )

        executor = _SandboxExecutor(run.deps.runtime, _gates_spec(run, state), judge)

        try:
            judge_task = layout.task_file(judge, run.task.id)

            ctx = build_context(
                judge,
                manifest,
                base=base,
                task_path=judge_task if judge_task.is_file() else None,
                specs=run.config.specs.path,
            )

            ctx.execute = executor
            # S-0116/D-10: the held-out properties of the task's latest event
            # reach the battery here, before any gate runs.
            _attach_holdout(run, ctx)
            # S-0106/D-6: a phase of a refactor document is judged by whether
            # behaviour held, so the battery is told the type `red-on-base` re-aims
            # on. Read here, not in the gate: the corpus is the planner's (S-0015/A-1).
            ctx.change_type = change_type_of(judge / run.config.specs.path, run.task)

            if _is_empty_implement_diff(ctx, run.root):
                # T-0172: an implement attempt that changed nothing against the
                # merge base is a no-op — refusing it here makes the loop read a
                # red gates fact and retry toward the poison ceiling, instead of
                # blessing an unchanged tree green. The attempt already spent
                # (S-0004/telemetry-staged), so the red record carries that cost and names
                # the refusal; the battery itself would only repeat the same
                # verdicts over a tree the agent never touched.
                digest = config_hash(
                    manifest_path, run.worktree, run.config, image_digest=run.image_digest
                )
                summary = "empty diff against base — no changes produced"
                # Named in the results, so the record and the next attempt's
                # feedback carry the refusal rather than a red with no gate
                # (bloomery T-0006 repeated the same no-op against a verdict it
                # could not read).
                refusal = GateResult(
                    name="empty-diff",
                    outcome="fail",
                    state="blocking",
                    exit_code=1,
                    sha=ctx.head_sha,
                    output=summary,
                )
                record = build_record(
                    ctx,
                    _with_ignored_writes(RunReport(results=[refusal], exit_code=1), ignored),
                    digest,
                    agent=run.meta,
                )
                _write_attempt_record(run, record, state.attempts)

                return 1, summary, digest, [refusal], ctx.patch

            # Before the battery: a written contingent path an in-flight run
            # holds refuses the attempt here (S-0104/D-3), and judging a tree
            # about to be handed back to the queue buys nothing.
            _record_promotions(run, state, ctx)

            # S-0113/D-2: the battery runs inside a sandbox that has no git, so
            # the host leaves the checkout's tracked files where the citation
            # scan reads them — current as of the attempt that just ran.
            write_tracked_files(judge)

            report = run_gates(ctx)

        finally:
            executor.close()

    finally:
        # S-0114/D-5: a judge checkout outlives no battery. The record is
        # built below from the context, so this runs after the battery ends,
        # green or red.
        opener.remove_judge(run.task.id)

    digest = config_hash(manifest_path, run.worktree, run.config, image_digest=run.image_digest)
    record = build_record(ctx, _with_ignored_writes(report, ignored), digest, agent=run.meta)
    _write_attempt_record(run, record, state.attempts)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)

    return report.exit_code, summary, digest, report.results, ctx.patch


def _task_bookkeeping(task_id: str) -> set[str]:
    """The engine's own files for one task, canonical and legacy layouts —
    the same set the scope gate skips (gates/scope.py). They are not
    candidate work: an attempt whose only trace is its own contract copy
    changed nothing an operator would review. Keep the two lists in step."""

    return {
        f"{layout.TORVE_DIR}/tasks/{task_id}/contract.yaml",
        f"{layout.TORVE_DIR}/tasks/{task_id}/log.yaml",
        f"{layout.TORVE_DIR}/logs/{task_id}.yaml",
        f"{layout.TORVE_DIR}/tasks/{task_id}.yaml",
        f"logs/{task_id}.yaml",
        f"tasks/{task_id}.yaml",
    }


def _is_empty_implement_diff(ctx: GateContext, root: Path) -> bool:
    """An implement attempt whose candidate changed nothing must read as a
    red attempt (T-0172) — a green verdict over an unchanged tree is exactly
    how a silent no-op promoted. The candidate's diff is judged excluding
    the task's own contract and log: a worktree cut at base before the
    mint carries no contract, so the gate pass copies it in as an untracked
    file — the engine's bookkeeping, implicitly in scope, and no more
    candidate work than the tree's gitignored artefacts. The integration
    task's empty diff stays legal (S-0026/D-6: at adoption the parent becomes
    the integration task, the battery over the composed tree is the point,
    and its landing is the decomposition's completion) — but only the
    adoption makes it one: a task is the integration task exactly when some
    contract under the engine's .torve/tasks names it as parent, not when
    it merely carries `depends_on`, which every phase-sequenced implement
    task does. Revert and review roles are untouched. No base resolved
    means there is nothing to diff against, so nothing to refuse."""

    task = ctx.task

    if task is None or task.role != "implement" or ctx.merge_base is None:
        return False

    # T-0177: the engine's record of adoption is S-0026/D-5's parent field on
    # the children (the adopted.json marker sits beside the decomposition
    # run's contract, not the parent's, so it cannot discriminate at the
    # parent's id). `depends_on` is the wrong discriminator — it exempts
    # every ordinary phase-sequenced implement task.
    if has_children(root, task.id):
        return False

    bookkeeping = _task_bookkeeping(task.id)
    only_bookkeeping = all(
        entry.path in bookkeeping and (entry.old_path is None or entry.old_path in bookkeeping)
        for entry in ctx.diff
    )

    # A no-op that says why is not silent: an attempt whose log decides one
    # of the contract's own rows — "this phase is not needed, and here is the
    # evidence" — has done the phase's work, and the execution record the
    # landing writes is its diff (bloomery T-0006, 2026-09-19: a phase its
    # document said may be dropped was refused twice for dropping it).
    return only_bookkeeping and not _decides_a_row(ctx)


def _decides_a_row(ctx: GateContext) -> bool:
    """Whether the attempt's log carries a `decided` entry for a row the
    contract inherited, by either spelling of the row's id."""

    from torve.gates.decisions_reported import parse_log

    if ctx.task is None or not ctx.log_text:
        return False

    document, _ = parse_log(ctx.log_text)
    entries = document.get("entries") if isinstance(document, dict) else None

    if not isinstance(entries, list):
        return False

    rows = {row.id for row in ctx.task.decisions} | {
        row.id.rsplit("/", 1)[-1] for row in ctx.task.decisions
    }

    def answers(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False

        if entry.get("action") == "decided" and str(entry.get("decision", "")) in rows:
            return True

        # A review round whose every claim is rejected with an entry has done
        # its work (S-0084/D-13): the contradiction is the reply the engine
        # posts, and the tree is right to be unchanged. bloomery's first live
        # round (T-0028) rejected a claim already fixed on the branch three
        # times and was refused as an empty diff each time.
        return entry.get("decision") == "unlisted" and entry.get("kind") == "contradicted"

    return any(answers(entry) for entry in entries)


# ....................... #


def _agent_identity(meta: dict[str, Any]) -> str:
    """The commit author, and the `agent` a landing records
    (S-0010/branches-and-commits): adapter/model@model_version, degrading
    gracefully — a fake or mechanical attempt is named for what it is, never
    invented."""

    adapter = str(meta.get("adapter") or "unknown")
    model = meta.get("model")
    ident = f"{adapter}/{model}" if model else adapter
    version = meta.get("model_version")

    return f"{ident}@{version}" if version else ident


# ....................... #


def _work_message(task: Task, attempts: int) -> str:
    """The work commit's message, composed from the contract and nothing
    else (S-0010/D-6): the subject carries the intent's head, so a history
    is readable without opening the task.

    No `Torve-` trailer rides here (S-0059/D-10). The landing file that
    follows in its own commit carries the task, the attempt, the agent,
    the base, this commit and the rows the contract inherited — everything
    the five trailers said, in the place the corpus reads."""

    # S-0007/A-1: the contract's short title names the landing; the intent's
    # first line is the fallback for contracts minted before it existed.
    head = task.title.strip() or (
        task.intent.strip().splitlines()[0].strip() if task.intent.strip() else ""
    )

    if len(head) > 46:
        head = head[:45].rstrip() + "…"

    what = f" {head} —" if head else ""

    return f"torve({task.id}):{what} attempt {attempts} green"


# ....................... #


async def judge(run: Dispatch, state: RunState) -> tuple[int, str, str]:
    """One gate pass over the tree the attempt just left, and the facts the
    rest of the run reads off it: the convictions retry selection uses
    (S-0034/D-5), the pass the reviewer is handed, and — on a red — whether
    the next attempt is routed as a repair."""

    # Deliberately not guarded: a sync that fails leaves the battery judging
    # a log nobody vouched for, and the gates are fail-closed. The raise
    # lands as GATE_INFRASTRUCTURE_FAILURE, which is what this is.
    if run.deps.journal is not None:
        await asyncio.to_thread(run.deps.journal, run.worktree)

    try:
        exit_code, summary, digest, results, patch = await asyncio.to_thread(
            run_gate_pass, run, state
        )

    except BlockedDispatch:
        # S-0104/D-3: a contingent write an in-flight task holds is a wait,
        # not a failure — the attempt returns to the queue and nothing
        # escalates. The catch-all below would record
        # GATE_INFRASTRUCTURE_FAILURE for a refusal the task says escalates
        # nothing, so the refusal passes through untouched.
        raise

    except Exception:
        # S-0038/D-1: the gates step raising is an attempt ending with no gate
        # record — the loop escalates GATE_INFRASTRUCTURE_FAILURE from here
        # and stops, so the row lands before the exception travels. The
        # agent exited 0 by construction (the loop calls this no other way)
        # and the gate report never completed: exit_code carries the
        # agent's 0, the row says gates_run false.
        attempt_row(
            run,
            "gate_infrastructure",
            exit_code=0,
            timed_out=False,
            escalation=str(EscalationReason.GATE_INFRASTRUCTURE_FAILURE),
        )
        raise

    run.convictions = list(results)
    run.last_pass = GatePass(results=list(results), patch=patch, digest=digest)

    if exit_code != 0:
        await asyncio.to_thread(_route_repair, run, state)

    return exit_code, summary, digest


# ....................... #


async def _bare_gates(run: Dispatch, _state: RunState) -> tuple[int, str, str]:
    """The bare arm's gate pass is no pass at all (S-0074/D-3): the battery's
    removal is a property of the replay — this hook, chosen by the bare flag —
    never an edit to the gate manifest, so the regime digest still names the
    unchanged manifest and the loop reads green with nothing judged. The
    manifest every other attempt is judged by is never the thing that
    changed; a manifest with gates removed would be a different regime, and
    the digest would be right to say so."""

    manifest_path = layout.gates_file(run.worktree)

    digest = (
        config_hash(manifest_path, run.worktree, run.config, image_digest=run.image_digest)
        if manifest_path.is_file()
        else ""
    )

    return 0, "battery removed — bare arm", digest


# ....................... #

# S-0069/D-5, settled at this phase: the qualifying set for a repair starts at
# the gates whose checks are pure functions of the tree. The evidence that
# settled it is `.torve/specs/S-0069/document.yaml`'s motivation — the same
# gate convicted the next attempt of the same task 196 times across 28 tasks:
# `acceptance` 52, `layering` 41, `user-facing-text` 39, `decisions-reported`
# 26, `scope` 14. The four gates here are those checks: a red from one names a
# property of the tree, not a verdict on the approach, and the tree a
# mechanical conviction names is repaired rather than rebuilt. `acceptance` —
# the largest class — stays deliberately outside the set: a suite that fails
# twice may be a tree worth keeping or an approach worth abandoning, and only
# the ledger's per-gate repeat counts can tell those apart. Widening this set
# is a decision with those counts behind it (S-0069/D-5's own consequence);
# it does not happen by editing this line.
#
# `red-on-base` is the first widening, argued rather than edited in
# (S-0081/D-7): its check is the same kind of pure function of the tree — a
# test that is green against base is a property of the two trees, not a
# verdict on the approach — and the repair is the one the conviction names,
# writing the test the change had to make green. It qualifies only once it
# blocks, which is the rule every name here already reads below: while the
# gate stands at `shadow` (S-0081/D-1) its failure is a fact and routes
# where it routes today.
REPAIR_GATES: frozenset[str] = frozenset(
    {"layering", "scope", "user-facing-text", "decisions-reported", "red-on-base"}
)


# ....................... #


def _repair_command(gate: Gate) -> str:
    """The convicting gate's own command, as the repair's acceptance names it
    (S-0069/D-3). A shell gate declares the command it runs and gains it
    verbatim; a builtin is declared by reference (`@scope` is not a command),
    so its repair command is the battery's own per-gate verb — what the
    attempt can run to see exactly the check that convicted it."""

    return f"torve gates run --only {gate.name}" if gate.run.startswith("@") else gate.run


# ....................... #


def _severity_rank(axis: str) -> int:
    return AXIS_SEVERITY.index(axis) if axis in AXIS_SEVERITY else len(AXIS_SEVERITY)


def _repair_gate(run: Dispatch) -> tuple[str, str] | None:
    """The gate the next attempt is a repair of, with its repair command, or
    None when this red routes where it routes today. A conviction qualifies
    when it blocks on a gate in `REPAIR_GATES` — a shadow or quarantined
    failure is a fact, not a conviction, the same rule the ladder reads — and
    only for as long as that gate has not already routed one: a repair is
    attempted once per conviction (S-0069/D-6), and a second conviction on the
    same gate must not let the repair read its own failure as its input. When
    several gates qualify, the most severe axis present is repaired first —
    the ladder's own order."""

    try:
        resolved = load_manifest(layout.gates_file(run.worktree)).resolved_gates()

    except (OSError, ValueError, yaml.YAMLError):
        # Nothing classified, nothing qualified: an unreadable manifest
        # convicts, but it cannot vouch for what a repair would be for.
        return None

    declared = {gate.name: gate for gate in resolved}

    convicted = sorted(
        {
            result.name
            for result in run.convictions
            if result.name in REPAIR_GATES
            and result.name in declared
            and result.outcome in ("fail", "error")
            and result.state == "blocking"
        },
        key=lambda name: (_severity_rank(declared[name].axis or UNLABELED_AXIS), name),
    )

    return next(
        (
            (name, _repair_command(declared[name]))
            for name in convicted
            if name not in run.repaired_gates
        ),
        None,
    )


# ....................... #


def _route_repair(run: Dispatch, state: RunState) -> None:
    """A conviction on a qualifying gate routes the next attempt as a repair
    (S-0069/D-3, D-4, D-6): the tree it starts from is the convicted one, its
    acceptance is the gate's own command beside everything the contract
    already declared, and one gate earns one repair per dispatch. Every other
    red routes where it routed before: the contract's own acceptance, and the
    ladder untouched — what tier runs next stays outcome and axis alone
    (S-0054/D-12).

    The contract does not change: the battery judges the task file on disk,
    and this mutates only the in-memory copy the attempt is prompted from.

    The stamp names the attempt it repairs beside the gate (S-0081/D-6): the
    repair row's `repair_of_attempt` is the `attempt` of the row that carries
    the conviction, so whether repairing beat retrying from base is a join
    over the record rather than a reading of a transcript. Additive — a row
    carrying `repair` and no `repair_of_attempt` was written before this key
    and still reads as a repair, one whose conviction is unknown."""

    picked = _repair_gate(run)

    if picked is None:
        run.meta.pop("repair", None)
        run.meta.pop("repair_of_attempt", None)
        run.task = run.task.model_copy(update={"acceptance": list(run.contract_acceptance)})
        return

    gate, command = picked
    run.repaired_gates.add(gate)
    run.meta["repair"] = gate
    run.meta["repair_of_attempt"] = state.attempts
    run.task = run.task.model_copy(update={"acceptance": [*run.contract_acceptance, command]})
    _commit_convicted_tree(run, state, gate)


# ....................... #


def _commit_convicted_tree(run: Dispatch, state: RunState, gate: str) -> None:
    """A repair starts from the convicted attempt's tree, not from base
    (S-0069/D-4): the work that was right survives the mistake. The loop
    carries the worktree into the next attempt either way; the commit is what
    makes the convicted tree the repair's base at the seam — the repair
    attempt's worktree contains the convicted attempt's commits, and an
    ordinary retry's does not — and it leaves the tree on the task's branch
    whatever the rest of the run does.

    Like the budget checkpoint it is kin to (S-0026/D-9), this commit writes
    no landing, and carries the same trailer so nothing mistakes it for a
    landed candidate (S-0059/D-12). A failed commit does not undo the
    routing: the tree is the same one the repair would carry anyway, the
    commit is only its record, and the conviction must not be replaced by an
    infrastructure escalation."""

    message = (
        f"torve({run.task.id}): attempt {state.attempts} convicted by {gate}\n\n"
        f"Torve-Checkpoint: {run.task.id} attempt {state.attempts}"
    )
    author = f"{_agent_identity(run.meta)} <agents@torve.local>"

    try:
        run.deps.vcs.commit_all(run.worktree, message, author, run.config.vcs.signing_key)

    except Exception as exc:
        engine_event(
            run.root,
            "repair_tree_uncommitted",
            {"task": run.task.id, "attempt": state.attempts, "gate": gate, "error": repr(exc)},
        )


# ....................... #


def _commit_reviewed_tree(run: Dispatch, state: RunState) -> None:
    """An escalation from the review stage leaves the attempt's tree only in
    the worktree — the candidate is committed at `reviewed → ready`, after the
    review — so a green-gated tree was lost to a blocker, an unreadable verdict
    or a broker refusal. S-0086/D-2 commits it on the task's branch here, kin to
    the budget checkpoint (S-0026/D-9) and the convicted tree (S-0069/D-4): the
    same trailer, so nothing mistakes it for a landed candidate, and no landing
    is written (S-0059/D-12).

    A failed commit leaves the escalation as it was: the reason the run stops
    is the review's, and an infrastructure failure here must not replace it."""

    why = "escalated from review"
    reason = state.escalation.reason if state.escalation is not None else None

    if reason == str(EscalationReason.LOCKED_CONFLICT):
        why = _HALTED_MARKER

    elif reason == str(EscalationReason.BLOCKER_FINDING):
        why = _BLOCKER_MARKER

    elif reason == str(EscalationReason.UNDERSPECIFIED):
        why = _UNDERSPECIFIED_MARKER

    message = (
        f"torve({run.task.id}): attempt {state.attempts} {why}\n\n"
        f"Torve-Checkpoint: {run.task.id} attempt {state.attempts}"
    )
    author = f"{_agent_identity(run.meta)} <agents@torve.local>"

    try:
        run.deps.vcs.commit_all(run.worktree, message, author, run.config.vcs.signing_key)

    except Exception as exc:
        engine_event(
            run.root,
            "reviewed_tree_uncommitted",
            {"task": run.task.id, "attempt": state.attempts, "error": repr(exc)},
        )


# ....................... #


async def land(run: Dispatch, state: RunState, digest: str) -> str:
    """The candidate commit, and its branch and pull request where the forge
    leg is on. The commit is the runner's artefact (S-0010/D-1), composed here
    where the attempt's model_version is already known: author is the agent
    identity (S-0010/D-2), trailers complete (S-0010/D-4), one commit per attempt
    (S-0010/D-8), signed outside the sandbox when a key is configured
    (S-0010/D-3)."""

    deps, config, task, worktree = run.deps, run.config, run.task, run.worktree

    author = f"{_agent_identity(run.meta)} <agents@torve.local>"

    sha = await asyncio.to_thread(
        deps.vcs.commit_all,
        worktree,
        _work_message(task, state.attempts),
        author,
        config.vcs.signing_key,
    )

    # S-0059/D-9: the landing follows the work commit and names it, in a
    # commit of its own — a file cannot name the commit it rides in, and an
    # amended commit is a different sha. Two commits per attempt, departing
    # S-0010/D-8. What this attempt found goes beside the rows it cites
    # (S-0057/D-7), or under `.torve/execution/` when the contract names no
    # document (S-0059/D-11).
    landing_sha: str | None = None

    try:
        execution = decisions.land(
            worktree,
            worktree / config.specs.path,
            task,
            attempt=state.attempts,
            agent=_agent_identity(run.meta),
            commit=sha or "",
        )
        landed = f"execution {execution.relative_to(worktree)}"

        landing_sha = await asyncio.to_thread(
            deps.vcs.commit_all,
            worktree,
            f"torve({task.id}): landing of attempt {state.attempts}",
            author,
            config.vcs.signing_key,
        )
    except ValueError as exc:
        landed = f"no execution file — {exc}"

    # A later attempt whose work was already committed — the convicted tree
    # of the attempt before it is checkpointed on the branch (S-0069) — has
    # nothing new to commit and is still a candidate: the tip the landing
    # commit leaves. Without this the worker read "no landing" and released
    # the task, and the next claim recut the branch over a green candidate
    # (bloomery T-0007, 2026-09-19).
    sha = sha or landing_sha

    # The credential is resolved by NAME here, at the runner boundary
    # (S-0001/D-13): the value lives only in this process and the subprocess
    # environments the adapters compose.
    token = os.environ.get(config.scm.token_env) if config.scm.token_env else None

    # Under `promotion.unit: document` the pull request is the document's and the
    # lane's to open (S-0083/D-1): the task branch stays local, because a task
    # pull request beside the document's is the one-per-task unit by another door.
    publishes = config.scm.open_pr and not (
        config.promotion.landing == "pull_request" and config.promotion.unit == "document"
    )
    pushed = (
        await asyncio.to_thread(
            # supersede (S-0010/D-10, S-0010/A-1): the attempt owns the task's
            # persistent branch — a prior candidate there is superseded
            # under lease, its feedback captured at the requeue.
            deps.vcs.push,
            worktree,
            naming.branch(task.id),
            token,
            True,
        )
        # Publication follows the forge leg (S-0010/D-11, S-0010/A-2): with open_pr
        # off the candidate stays local — pushing a branch is publishing,
        # and on a repository whose base was never pushed it publishes the
        # entire history.
        if sha and publishes
        else False
    )

    pr_url = ""

    if pushed and publishes:
        title, pr_body = compose_pr(
            task,
            state.attempts,
            digest,
            run.meta,
            list(run.last_pass.results),
            worktree,
            changed=deps.vcs.changed_names(worktree),
        )

        pr_url = await asyncio.to_thread(
            deps.scm.open_pr, worktree, naming.branch(task.id), title, pr_body
        )

    state.landed_sha = sha or None
    fact = f"committed {sha[:10]}" if sha else "nothing to commit"
    fact += f"; pushed={pushed}" + (f"; pr={pr_url}" if pr_url else "; pr deferred")
    fact += f"; {landed}"

    return fact


# ....................... #


def checkpoint(run: Dispatch, final: RunState) -> None:
    """Commit whatever the worktree holds when a run ends on budget
    exhaustion, so the next dispatch has a candidate tip to cut from
    (S-0026/D-9). Local only: the branch already lives in this repository, and
    publishing a WIP tip is the eventual `land`'s job, unchanged.

    A trailer of its own keeps this commit from ever being mistaken for a
    landed candidate: it writes no landing, and a landing is what a landed
    candidate is (S-0059/D-12)."""

    message = (
        f"torve checkpoint {run.task.id}: attempt {final.attempts} exhausted its budget"
        f"\n\nTorve-Checkpoint: {run.task.id} attempt {final.attempts}"
    )
    author = f"{_agent_identity(run.meta)} <agents@torve.local>"
    run.deps.vcs.commit_all(run.worktree, message, author, run.config.vcs.signing_key)


# ....................... #


def real_hooks(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    worktree: Path,
    shadow: bool = False,
    gates_base: str | None = None,
    resume: bool = False,
    bare: bool = False,
    arm: str | None = None,
    judges: JudgePort | None = None,
) -> AttemptHooks:
    """Bind one dispatch's steps into the hooks the loop drives (S-0046).

    The dispatch settles the regime and refuses what must be refused; this
    picks the attempt leg the role calls for and the review leg the
    configuration calls for, and opens the broker last — after every step
    above it that can still fail (S-0046/D-4).

    `arm` is the arm axis (S-0074/D-1, S-0082/D-1): the dispatch carries which
    arm is running, and both removals are read from it — the battery's swaps
    the gate pass for `_bare_gates`, a pass that runs nothing, and the
    prompt's is the attempt leg's (S-0082/D-1). Neither is ever an edit to the
    manifest or the contract. `bare` is the older spelling of `arm=BARE` and
    names the same arm.

    Every arm but `configured` is a replay's flag: an arm run that would land
    its work is refused here, because an arm cannot land anything
    (S-0074/D-3)."""

    arm = arm or (BARE if bare else CONFIGURED)

    if arm != CONFIGURED and not shadow:
        raise ValueError(
            f"a {arm} run is a replay — removing the apparatus it names is a "
            "property of the replay, never of a live dispatch that would land "
            "its work"
        )

    run = open_dispatch(
        root,
        task,
        config,
        deps,
        worktree,
        shadow=shadow,
        gates_base=gates_base,
        resume=resume,
        arm=arm,
        judge=judges,
    )

    # Revert is mechanical (S-0010/revert-as-a-role, S-0010/D-7): no agent, no attempt
    # sandbox — but the gates still run in theirs and the landing carries
    # the revert's own provenance. Its targets resolve now, so an
    # unresolvable one fails here rather than at attempt three.
    attempt = revert_leg(run) if task.role == "revert" else partial(run_agent_session, run)

    if task.role == "verify" and not shadow:
        attempt = partial(_verify_attempt, run, attempt)

    # Review follows execution (S-0005/D-11): minted by the run, never by the
    # planner, and never for a shadow replay — a replay measures the
    # harness, not the reviewer. Resolving the reviewer here is the last
    # thing that can fail before the broker opens.
    review = None

    if review_gated(config, task, shadow):
        reviewer_for(run)  # raises here, not after an attempt has been paid for
        review = partial(review_step, run)

    open_broker(run)

    return AttemptHooks(
        attempt=attempt,
        halted=partial(halted, run),
        after_attempt=partial(_record_holdout, run),
        gates=partial(_bare_gates, run) if run.battery_removed else partial(judge, run),
        land=partial(land, run),
        review=review,
        close=partial(close_dispatch, run),
        checkpoint=partial(checkpoint, run),
        reviewed_tree=partial(_commit_reviewed_tree, run),
    )


# ....................... #


def _cut_from(root: Path, task: Task, config: RunnerConfig) -> tuple[str | None, bool, str | None]:
    """(base_ref, fetch, gates_base) — where a task's worktree is cut from,
    whether the remote's refs are updated first, and the base the battery
    judges the attempt against.

    Under `promotion.unit: document` (S-0083/D-9) a phase starts on the tree
    the previous phase's landing produced — the document branch's tip — so a
    night runs a whole document with nobody in the middle, and the battery
    judges against that same tip so a phase's diff is its own work rather than
    everything the branch carries. Before the branch is cut, the base is the
    remote's main after a fetch rather than a local copy stale from the first
    merge onward. Every other unit, a contract naming no document (S-0083/D-4)
    and a local landing (S-0083/D-2) cut exactly as they cut today.

    The document branch is read as the remote holds it (S-0091/D-1), the copy
    the lane works from — never a local ref the remote may have deleted."""

    promotion = config.promotion

    if promotion.landing != "pull_request" or promotion.unit != "document" or task.spec is None:
        return resolve_base(root, config.base), False, None

    try:
        document = resolve_base(root, f"origin/{naming.document_branch(task.spec)}")

    except GitError:
        return resolve_base(root, config.base), True, None

    return document, False, document


# ....................... #


async def _run_task_async(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    state: RunState,
    resume: bool = False,
) -> RunState:
    base, fetch, gates_base = _cut_from(root, task, config)

    # The keyword is passed only where it is on, so a workspace that never
    # fetches is called exactly as it was called before the port carried one.
    worktree = (
        deps.workspace.create(task.id, base, resume=resume, fetch=True)
        if fetch
        else deps.workspace.create(task.id, base, resume=resume)
    )
    state.worktree = str(worktree)
    state.save()

    if resume:
        _refresh_contract(root, task, worktree)

    hooks = real_hooks(root, task, config, deps, worktree, resume=resume, gates_base=gates_base)

    async def body(_fctx: ExecutionContext, _input_json: JsonDict | None) -> JsonDict:
        bound = current_durable_run()

        if bound is not None:
            state.durable_run_id = bound.run_id
            state.save()

        try:
            final = await drive_attempts(state, task, config, hooks)

        except RevertConflict as exc:
            # S-0010/revert-as-a-role: a dependent-commit conflict escalates as
            # merge_conflict — the revert aborted and the worktree is clean.
            state.escalate(EscalationReason.MERGE_CONFLICT, str(exc))
            final = state

        except asyncio.CancelledError:
            if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
                state.escalate(
                    EscalationReason.KILLED, "cancellation observed via the lease heartbeat"
                )

            raise

        except BlockedDispatch:
            # A wait, not a failure: the gate pass already removed the state
            # file (S-0104/D-3), and escalating would write it back.
            raise

        except Exception as exc:
            # A hook that raises ends the run. Left running, the state file
            # refused the task's requeued dispatch until a reap (T-0655,
            # 2026-10-19, a seat image that would not start); escalated, the
            # requeue that resolves it clears the file (S-0094/D-5).
            if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
                state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))

            raise

        return {
            "task_id": task.id,
            "state": str(final.state),
            "attempts": final.attempts,
            "escalation": final.escalation.reason if final.escalation else None,
        }

    store = await deps.store(config.store)
    taskstore = TaskStore(store, config.store)
    taskstore.register(body)

    record = await taskstore.run_now(
        {"task_id": task.id, "engine_run_id": state.run_id},
        idempotency_key=f"{task.id}:{state.run_id}",
    )

    if (
        record.status is DurableRunStatus.TIMED_OUT
        and state.escalation is None
        and state.state not in (TaskState.READY, TaskState.ABANDONED)
    ):
        state.escalate(
            EscalationReason.BUDGET_EXHAUSTED,
            f"{_WALLCLOCK_MARKER}: max run duration reached (store watchdog)",
        )

    if record.status is DurableRunStatus.FAILED:
        raise RuntimeError(f"durable run failed: {record.error}")

    return state


# ....................... #


# ....................... #


def _scope_overlap(mine: list[str], theirs: list[str]) -> str | None:
    """The first of `theirs` that intersects `mine`, in the same scan order
    the nested loop used — which contended path gets reported must not
    change."""

    from torve.base.globs import overlap

    for mine_glob in mine:
        for their_glob in theirs:
            if overlap([mine_glob], [their_glob]):
                return their_glob

    return None


# ....................... #


def _blocking_overlap(root: Path, task: Task) -> tuple[str, str] | None:
    """(blocking task id, contended path) when an active run's fence holds
    this task's declared scope; an empty allow-set is unconstrained and
    contends with everything. An active review claims no fence — it
    writes nothing by construction (S-0005/D-2), so no dispatch is refused
    against it. A review dispatch never reaches the fence at all: the
    front door refuses it (T-0183), so the exemption's assumption holds
    by construction.

    An in-flight run's fence is its declared allow plus every contingent
    path it promoted (S-0104/D-2), so a task declaring a path another run
    widened into is refused here too. A task's own contingent paths are not
    a claim at dispatch (S-0104/D-3): they are refused at the write, where
    the attempt's diff shows one was actually made."""

    for holder, allow, promoted in _in_flight(root, task.id):
        if not task.scope.allow or not allow:
            return holder, "unconstrained scope"

        overlap = _scope_overlap(task.scope.allow, [*allow, *promoted])

        if overlap is not None:
            return holder, overlap

    return None


# ....................... #


def _should_resume(previous: RunState) -> bool:
    """A continuation is legible only immediately off its own escalation
    (S-0026/D-9): `escalation` is never cleared by design (S-0001/state-machine's history
    is append-only), so without this the field would resurrect a long-dead
    budget exhaustion on an unrelated re-queue — a lane conflict auto-requeue
    or a review `revise` both land on QUEUED too. Requiring the immediately
    preceding transition to be `escalated` pins the field to the same event."""

    if previous.state is not TaskState.QUEUED or previous.escalation is None:
        return False

    if len(previous.history) < 2 or previous.history[-2].get("to") != str(TaskState.ESCALATED):
        return False

    return _continuable(previous.escalation)


def _checkpointed_for_resume(root: Path, task: Task) -> bool:
    """S-0090/D-2: a task whose run state was reaped still resumes when its
    branch tip is the checkpoint a review blocker or a halt left — the tree is
    on the branch either way, and the reaper removing the state file is not an
    answer to the escalation."""

    try:
        message = git(root, "log", "-1", "--format=%B", naming.branch(task.id))

    except GitError:
        return False

    subject = message.splitlines()[0] if message else ""

    return f"Torve-Checkpoint: {task.id} " in message and subject.endswith(
        (_BLOCKER_MARKER, _HALTED_MARKER, _UNDERSPECIFIED_MARKER)
    )


# ....................... #


def _refresh_contract(root: Path, task: Task, worktree: Path) -> None:
    """S-0090/D-3: the tree carries over from the checkpoint, the terms do not.
    The checkpoint committed the contract the stopped attempt read; the copy is
    dropped so the contract as it stands at this dispatch — the refreshed one
    after `plan --refresh` — is the one the gates and the log verbs read. With
    no contract file at the root, the board's projection writes it later."""

    copy = layout.task_file(worktree, task.id)
    source = layout.task_file(root, task.id)
    copy.unlink(missing_ok=True)

    if source.is_file():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, copy)


# ....................... #


class RoleNotDispatchable(ValueError):
    """Direct dispatch of a role the generic attempt path does not implement
    the isolation contract for (T-0183): review (S-0005/D-2) and draft (S-0020/D-2)
    each run through a runner-minted path with a read-only workspace, and
    this path would mount the workspace writable. A ValueError, so the run
    CLI's configuration-error catch already handles it."""


def check_dispatch_role(task: Task) -> None:
    """The front door's role guard (T-0183): refuse direct dispatch of any
    role the generic attempt path does not implement the isolation contract
    for. Implement and revert are the only roles this path isolates; review
    (S-0005/D-2) and draft (S-0020/D-2) are each refused with the path that owns
    them. Refusing here — before the fence or any claim — makes the review
    exemption's "writes nothing" assumption true by construction: the fence
    never sees a review dispatch."""

    if task.role == "review":
        # S-0005/D-2 (T-0183): a direct dispatch would mount the workspace
        # writable, so the door points at the runner-minted path instead.
        target = task.targets[0] if task.targets else "its target"
        raise RoleNotDispatchable(
            f"{task.id} is a review-role task — reviews are not run "
            f"directly: a review follows execution, and the runner mints "
            f"and drives it once its target's gates go green. Run "
            f"{target} and its review follows."
        )

    if task.role == "draft":
        # S-0020 S-0020/D-2 (T-0183): the drafting path owns the read-only
        # workspace; the generic attempt path would mount it writable.
        raise RoleNotDispatchable(
            f"{task.id} is a draft-role task — drafting runs are driven "
            "by `torve intake`, never by `torve run`: the drafter works "
            "in a read-only workspace and its gate is the contract lint."
        )


def run_task(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    *,
    attempt_base: int = 0,
) -> RunState:
    # T-0183: review and draft reach this generic path only through a
    # bypass — each has a runner-minted path with a read-only workspace
    # (S-0005/D-2, S-0020/D-2), and this path mounts the workspace writable. Refuse
    # at the door, before state, the fence or any claim exists; implement
    # and revert are the only roles this path implements the isolation
    # contract for.
    check_dispatch_role(task)

    state_path = naming.state_file(root, task.id)
    resume = False
    # S-0099/D-8: how many attempts the task has already had. The record's
    # count is handed in; the host's own run record is the fallback when a
    # dispatched run has no observer, and the larger wins so neither a
    # surviving state file nor a reaped one can drop the sequence.
    recorded = attempt_base

    if state_path.exists():
        previous = RunState.load(state_path)

        # QUEUED is a board re-queue (T-0059): the human act already
        # happened, and dispatch is exactly what it asked for.
        if previous.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.QUEUED):
            raise RuntimeError(
                f"{task.id} has an existing run in state {previous.state} "
                f"(run {previous.run_id[:8]}); triage it or `torve reap` first"
            )

        resume = _should_resume(previous)
        recorded = max(recorded, previous.attempts)

    else:
        resume = _checkpointed_for_resume(root, task)

    # S-0090/D-2: a landed task has nothing left to continue.
    if resume and task.id in decisions.landed_task_ids(root, root / config.specs.path):
        resume = False

    blocked = _blocking_overlap(root, task)

    if blocked is not None:
        blocker, path = blocked

        engine_event(
            root, "blocked_dispatch", {"task": task.id, "blocked_by": blocker, "path": path}
        )

        raise BlockedDispatch(f"blocked_by_overlap: {blocker} on {path}")

    state = RunState(task_id=task.id, path=state_path, attempts=recorded)
    fact = "torve run: single synchronous claim"
    state.transition(TaskState.CLAIMED, f"{fact} (continuation)" if resume else fact)

    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state, resume=resume))

    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")

        return state
