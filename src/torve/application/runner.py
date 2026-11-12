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
import hashlib
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
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

from torve.application import decisions, feedback, selection, threads
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
    telemetry_target,
)
from torve.application.forge import compose_pr
from torve.application.manager import in_flight_fences, project
from torve.application.ports import (
    AgentResult,
    JudgePort,
    NestedDocker,
    Runtime,
    SandboxHandle,
    SandboxSpec,
    judge_ref,
)
from torve.application.reaper import STOPPED_MARKER
from torve.application.review import (
    is_blocker_revision_fact,
    mint_review_task,
    review_step,
    reviewer_for,
)
from torve.application.runstate import Escalation, RunState
from torve.application.session import (
    AXIS_SEVERITY,
    RevertConflict,
    advance_tier,
    candidate_suffix,
    halted,
    revert_leg,
    run_agent_session,
)
from torve.application.sizing import has_children
from torve.application.taskstore import TaskStore
from torve.application.telemetry import (
    TOKEN_FIELDS,
    append_record,
    build_record,
    config_hash,
    engine_event,
    priced,
    record_payload,
    record_row,
)
from torve.base import naming
from torve.base.clock import parse, stamp
from torve.base.shell import clip
from torve.config import layout
from torve.config.manifest import UNLABELED_AXIS, Gate, Manifest, load_manifest
from torve.config.providers import Price
from torve.config.runconfig import RunnerConfig, agent_timeout_for
from torve.config.spec import write_tracked_files
from torve.domain.attempt import GateResult
from torve.domain.states import BlockedDispatch, EscalationReason, TaskState
from torve.domain.task import Task
from torve.gates.context import GateContext, GitError, build_context, git, resolve_base
from torve.gates.contract import spec
from torve.gates.evidence_package import TAIL_BYTES, CommandRun, build_evidence, evidence_budget
from torve.gates.runner import RunReport, battery, battery_budget, run_gates

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
    # The unit's class (S-0127/D-9), read once per run from the repository
    # root's tree: under the critic a contract naming no clock runs under its
    # class's. Absent — the simulation's hooks — reads M, whose clock is the
    # one every class had before.
    unit_size: Callable[[RunState], str] | None = None


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

# The subject `_commit_reviewed_tree` writes when the poison ceiling stops a
# run whose history holds a revision round (S-0125). `_continuable` reads
# the escalation's detail for the same words, so the two can never disagree
# about whether the run stopped on a revision.
_CEILING_REVISION_MARKER = "escalated at the ceiling on a revision"
_ON_A_REVISION_MARKER = "on a revision"

# The subject `checkpoint` writes when a run spends its budget (S-0026/D-9).
# A requeue removes the state file `_should_resume` reads, so the branch tip
# is the only record left that the run was a continuation.
_EXHAUSTED_MARKER = "exhausted its budget"


def _exhausted(escalation: Escalation) -> bool:
    """S-0026/D-8: budget exhaustion — wallclock or tokens, the unit's dollar
    cap (S-0123/D-1) among them, since its spend is tokens priced. `iterations`
    exhaustion (like `poison_ceiling`) is excluded on purpose: repeated red
    gates is a judgement on the work, not a clock running out. These are the
    escalations the budget checkpoint commits for (S-0026/D-9)."""

    if escalation.reason == str(EscalationReason.COST_ANOMALY):
        return True

    return escalation.reason == str(
        EscalationReason.BUDGET_EXHAUSTED
    ) and escalation.detail.startswith((_WALLCLOCK_MARKER, selection.UNIT_BUDGET_MARKER))


def _on_a_revision(state: RunState) -> bool:
    """Whether this run's history holds a surviving blocker's revision fact —
    the `review blocker (revision k of n)` row a revision attempt follows
    (S-0125). It is the fact, not the escalation reason, that says the
    ceiling stopped work a review had already judged."""

    return any(is_blocker_revision_fact(str(entry.get("fact", ""))) for entry in state.history)


def _continuable(escalation: Escalation, state: RunState | None = None) -> bool:
    """The escalations the next dispatch resumes from: budget exhaustion
    (S-0026/D-8), and a review blocker, a halt (S-0090/D-2, S-0096/D-1) or a
    ceiling reached after a revision round (S-0125) — a review that asks
    for one file's change costs that change, a halt answered by an amendment
    resumes where it stopped, whether it cites a LOCKED row or is
    `underspecified`, and a revision the ceiling stopped resumes from its own
    tree. A gate conviction still restarts from base."""

    if escalation.reason == str(EscalationReason.POISON_CEILING):
        return (
            _on_a_revision(state)
            if state is not None
            else _ON_A_REVISION_MARKER in escalation.detail
        )

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

    # S-0123/D-2: under the critic a unit's contract may name no clock, and
    # then the unit gets its class's (S-0127/D-9). A contract that names its
    # own bound keeps it.
    if wallclock_minutes is None and config.review.mode == "critic":
        size = hooks.unit_size(state) if hooks.unit_size is not None else "M"
        wallclock_minutes = selection.UNIT_BUDGETS[size][1]

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
            on_revision = _on_a_revision(state)
            detail = f"{made} attempts, ceiling {ceiling}"

            if on_revision:
                detail = f"{detail}; {_CEILING_REVISION_MARKER}"

            state.escalate(EscalationReason.POISON_CEILING, detail)

            # S-0125: a ceiling reached after a revision round keeps the
            # revision's tree on the branch, as a review blocker's escalation
            # does (S-0086/D-2). A run whose attempts saw no revision fact
            # commits nothing and still restarts from base.
            if on_revision and hooks.reviewed_tree is not None:
                hooks.reviewed_tree(state)

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

        if halt := hooks.halted(state.attempts):
            # Terminal by design, not an error: the one case where a task
            # stops on working code (S-0001/state-machine). The entry picks
            # the escalation — a LOCKED row or, otherwise, underspecified
            # (S-0096/D-1) — and an urgent operator note is `killed`.
            state.escalate(
                halt,
                "an urgent operator note ended the attempt"
                if halt is EscalationReason.KILLED
                else f"halted divergence entry in the {task.id} execution log",
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


class SandboxExecutor:
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
    manifest_rel = load_manifest(run.manifest_file).telemetry

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


def _nested_docker(run: Dispatch, role: str) -> NestedDocker | None:
    """The daemon a sandbox of *role* gets, or None (S-0111/D-1, D-4). The
    configuration resolves role membership (`nests`); the runner composes the
    adapter's fact from it, slot-suffixing the docker-state volume like every
    other per-slot volume so two workers never share a daemon's state."""

    if not run.config.runtime.nests(role):
        return None

    nested = run.config.runtime.nested
    # The battery's daemon state is its own, like its cache (S-0122/D-3):
    # candidate code a gate ran must not leave images for the next attempt.
    suffix = "-gates" if role == "gates" else ""

    return NestedDocker(
        image=nested.image,
        # A shadow run starts cold (S-0035/D-3): no slot volume, so its daemon
        # gets a throwaway one that leaves with the side container.
        volume="" if run.shadow else f"{nested.cache_volume}-{run.config.worker_slot}{suffix}",
        registry_mirrors=tuple(nested.registry_mirrors),
        ready_s=nested.ready_s,
    )


# ....................... #


def _gates_spec(run: Dispatch, state: RunState) -> SandboxSpec:
    """The battery's sandbox spec (S-0003/D-8, S-0035/D-3, S-0111/D-4): the same
    image and derived cache the attempt ran under, and — when the configuration
    nests the gates role — a daemon of its own in a side container."""

    return SandboxSpec(
        name=naming.sandbox_name(run.task.id, state.run_id)
        + "-gates"
        + candidate_suffix(run.candidate),
        # Shell gates run over the same image the agent ran under
        # (S-0003/D-8) — an image swap between attempt and gates would be
        # its own regime change.
        image=run.image or run.config.runtime.image,
        labels=naming.labels(run.task.id, state.run_id, run.root),
        timeout_s=run.config.runtime.sandbox_timeout,
        # The battery pays the toolchain cold tax (mypy, ruff, uv all
        # run under these gates), so the live gates sandbox carries a
        # derived cache of its own — never the attempt's, which candidate
        # code run here could seed for the next attempt (S-0122/D-3). An
        # empty dict is the cold pass, shadow's the exclusion (S-0035/D-3).
        volumes=cache_volumes(run, "gates"),
        # The gates sandbox gets the nested daemon first (S-0111/D-4).
        nested=_nested_docker(run, "gates"),
    )


# ....................... #


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

    # A package belongs to the pass that measured it; a pass that measures
    # none leaves none from the attempt before.
    run.evidence = None

    manifest_path = run.manifest_file

    if not manifest_path.is_file():
        raise FileNotFoundError(f"no gate manifest at {manifest_path} — gates are fail-closed")

    manifest = load_manifest(manifest_path)

    task_file = layout.task_file(run.worktree, run.task.id)
    source = layout.task_file(run.root, run.task.id)

    if not task_file.is_file() and source.is_file():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, task_file)

    opener = run.judge or run.deps.workspace
    judge = opener.judge_checkout(run.workspace_key)

    try:
        # A replay's checkout is fetched whole from its clone, whose history
        # already ends at the replayed parent; the root holds no ref of it.
        if not run.shadow:
            _deepen_judge(judge, run.root, run.workspace_key)

        # The engine's own files for the task — its contract, the agent's log —
        # live under an ignored directory, so no snapshot carries them. The
        # battery reads them, and without them judged the attempt as no task's.
        for rel in _task_bookkeeping(run.task.id):
            held, carried = run.worktree / rel, judge / rel

            if held.is_file() and not carried.exists():
                carried.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(held, carried)

        # S-0121/D-3: the contract judged is the root's, over any copy the
        # agent could have edited in its worktree. With no file at the root
        # the store holds the contract, and the record dispatched is the one.
        if not run.shadow:
            from torve.application.planner import project_contract

            carried = layout.task_file(judge, run.task.id)
            carried.unlink(missing_ok=True)

            if source.is_file():
                carried.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(source, carried)
            else:
                # S-0127/D-7: with the contract's own acceptance, never what
                # the attempt is shown besides, so no gate runs twice.
                declared = run.declared_acceptance
                project_contract(
                    judge,
                    run.task
                    if declared is None
                    else run.task.model_copy(update={"acceptance": list(declared)}),
                )

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

        executor = SandboxExecutor(run.deps.runtime, _gates_spec(run, state), judge)

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

            if _is_empty_implement_diff(ctx, run.root, state.attempts):
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
                    RunReport(results=[refusal], exit_code=1),
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

            # The battery records nothing until its verdict, and a cold one can
            # outlast the lease: another worker's pass then released a task
            # this one was still judging (erp-backend T-0001, 21 minutes). The
            # fact says how long the silence may last, so the lease waits it out.
            budget = battery_budget(battery(ctx), ctx)
            emit(
                run,
                "gates_started",
                state.attempts,
                budget_s=budget + (evidence_budget(ctx) if _critic_reads(run) else 0.0),
            )
            report = run_gates(ctx)

            # S-0122/D-5: a green candidate the critic will judge is measured
            # here, over the same checkout, base and gates sandbox the battery
            # just used — the host and that sandbox write every reading in it
            # (S-0122/D-2). A red one is not: nothing reads its package yet.
            if report.exit_code == 0 and _critic_reads(run):
                run.evidence = build_evidence(ctx, report.results, candidate=run.task.id)

        finally:
            executor.close()

    finally:
        # S-0114/D-5: a judge checkout outlives no battery. The record is
        # built below from the context, so this runs after the battery ends,
        # green or red.
        opener.remove_judge(run.workspace_key)

    digest = config_hash(manifest_path, run.worktree, run.config, image_digest=run.image_digest)
    record = build_record(ctx, report, digest, agent=run.meta)
    _write_attempt_record(run, record, state.attempts)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)

    return report.exit_code, summary, digest, report.results, ctx.patch


# ....................... #
# S-0127/D-10: a finding's repro, re-run against the candidate. Nothing calls
# it until the critic's decision reads its exit codes (S-0127/D-1).

# Where the critic writes the probe a repro names — the frozen prompt's
# `probes/` — and so the one place a re-run writes one.
PROBE_DIR = "probes"

# What a command prints when the read-only checkout denied it a write. The
# candidate's code can print it too, so it only sends the command to one run
# over a writable mount, never refuses a repro by itself.
_READ_ONLY = "Read-only file system"

# The project environment, bytecode and the common tool caches a re-run
# writes, under the sandbox's own temporary directory and never in the
# checkout it reads.
_REPRO_TMP = "/tmp/torve-repro"
REPRO_ENV = {
    "UV_PROJECT_ENVIRONMENT": f"{_REPRO_TMP}/venv",
    "PYTHONPYCACHEPREFIX": f"{_REPRO_TMP}/pycache",
    "PYTEST_ADDOPTS": "-p no:cacheprovider",
    "MYPY_CACHE_DIR": f"{_REPRO_TMP}/mypy",
    "RUFF_CACHE_DIR": f"{_REPRO_TMP}/ruff",
}


def _repro_timeout(run: Dispatch) -> float:
    """What one re-run may take: a repro is a test command, so the timeout of
    the `acceptance` gate in the attempt's manifest, else the runtime's
    `sandbox_timeout` when the manifest holds no such gate."""

    try:
        gates = load_manifest(run.manifest_file).resolved_gates()

    except (OSError, ValueError, yaml.YAMLError):
        gates = []

    return next(
        (gate.timeout for gate in gates if gate.name == "acceptance" and gate.timeout is not None),
        run.config.runtime.sandbox_timeout,
    )


def _probe_refusal(checkout: Path, name: str) -> str | None:
    """Why probe *name* may not be written into *checkout*, or None: its path
    leaves `probes/`, a component of it is a link, or a file is already
    there, tracked or not."""

    path = PurePosixPath(name)
    parts = path.parts

    if path.is_absolute() or len(parts) < 2 or parts[0] != PROBE_DIR or ".." in parts:
        return f"probe {name} is not under {PROBE_DIR}/"

    for depth in range(1, len(parts) + 1):
        if checkout.joinpath(*parts[:depth]).is_symlink():
            return f"probe {name} passes through a link"

    if checkout.joinpath(*parts).exists():
        return f"probe {name} is not new to the checkout"

    return None


def _held_files(checkout: Path) -> dict[str, str]:
    """Every file *checkout* holds outside `.git`, by the hash of its bytes,
    read from the disk rather than the checkout's own index, which a writable
    run could rewrite."""

    return {
        path.relative_to(checkout).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(checkout.rglob("*"))
        if path.relative_to(checkout).parts[0] != ".git"
        and path.is_file()
        and not path.is_symlink()
    }


def reproduce(
    run: Dispatch,
    state: RunState,
    commands: Sequence[str],
    probes: Mapping[str, str | None],
) -> list[CommandRun]:
    """Each of *commands* re-run against the candidate (S-0127/D-10), in a
    fresh gates sandbox (`_gates_spec`: the battery's image and cache) over a
    fresh judge checkout, with each of *probes* — the path a finding's repro
    names, and the text the critic's copy kept of it or None — written at its
    path first.

    A probe the copy did not keep, or one at a path that is not new to the
    checkout, not under `probes/` or through a link, refuses the whole repro.
    Once the probes are written the checkout is mounted read-only, with the
    environment and caches pointed under the sandbox's own temporary
    directory, so no command can change the candidate. A command whose output
    reports a write the mount denied runs once more, in a sandbox of its own,
    over the checkout mounted writable, and that run's exit stands unless it
    changed or removed a file the checkout held, which refuses the repro: a
    denied write never blocks, and a candidate that prints the marker gains
    nothing by it.

    Each run gets the `acceptance` gate's timeout, and all of them together
    what is left of the unit's clock (S-0127/D-9); a command the clock leaves
    no time for, its writable run included, is not run. One `CommandRun` per
    command, its exit None on a timeout, when it was not run, or for every
    command of a refused repro. The checkout and the sandboxes are removed
    when this returns, whatever happened; a checkout or sandbox that fails to
    open raises, as the battery's does."""

    minutes = run.task.budget.wallclock_minutes

    if minutes is None and run.config.review.mode == "critic":
        minutes = selection.UNIT_BUDGETS[_unit_class(run, state)][1]

    deadline = (
        None if minutes is None else time.monotonic() + (minutes - _elapsed_minutes(state)) * 60.0
    )
    opener = run.judge or run.deps.workspace

    try:
        checkout = opener.judge_checkout(run.workspace_key)

        return reproduce_over(
            run.deps.runtime,
            _gates_spec(run, state),
            checkout,
            commands,
            probes,
            _repro_timeout(run),
            deadline,
        )

    finally:
        opener.remove_judge(run.workspace_key)


def reproduce_over(
    runtime: Runtime,
    gates: SandboxSpec,
    checkout: Path,
    commands: Sequence[str],
    probes: Mapping[str, str | None],
    timeout: float,
    deadline: float | None = None,
) -> list[CommandRun]:
    """Each of *commands* re-run over *checkout*, a fresh copy of the
    candidate its caller removes, in sandboxes of spec *gates* under the
    controls `reproduce` names (S-0127/D-10): the probe refusals, the
    read-only mount and `REPRO_ENV`, and the one writable run a denied write
    earns. The corpus replay reads its repros through this too, so a case
    measures one as a night would.

    Each run is bounded by *timeout* and, given a *deadline* on
    `time.monotonic()`, by what is left before it, the writable run's bound
    read again once the read-only run has spent its share; a command the
    clock leaves no time for is not run. The read-only sandbox is closed
    before a writable run opens, so no two are ever up at once, and every
    sandbox is removed when this returns, whatever happened."""

    def refused(why: str) -> list[CommandRun]:
        return [
            CommandRun(command=command, exit_code=None, tail=f"refused: {why}")
            for command in commands
        ]

    def bound() -> float | None:
        """The next run's timeout, or None when the unit's clock is spent."""

        if deadline is None:
            return timeout

        left = deadline - time.monotonic()

        return min(timeout, left) if left > 0 else None

    def unrun(command: str) -> CommandRun:
        return CommandRun(
            command=command, exit_code=None, tail="not run: the unit's clock is spent"
        )

    for name, text in probes.items():
        if text is None:
            return refused(f"probe {name} was not kept from the critic's copy")

        why = _probe_refusal(checkout, name)

        if why is not None:
            return refused(why)

        target = checkout / name

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

        except OSError as exc:
            return refused(f"probe {name} could not be written: {exc}")

    spec = replace(gates, env={**gates.env, **REPRO_ENV}, workspace_read_only=True)
    executor = SandboxExecutor(runtime, spec, checkout)
    runs: list[CommandRun] = []

    try:
        for command in commands:
            limit = bound()

            if limit is None:
                runs.append(unrun(command))
                continue

            code, output = executor(command, limit)

            if _READ_ONLY in output:
                limit = bound()

                if limit is None:
                    runs.append(unrun(command))
                    continue

                held = _held_files(checkout)
                # The read-only sandbox goes first, so the two never overlap —
                # a nested daemon's state volume is one per slot — and the next
                # command opens a fresh one. The retry still takes a name of
                # its own, a runtime holding one sandbox per name.
                executor.close()
                writable = SandboxExecutor(
                    runtime,
                    replace(spec, name=f"{spec.name}-rw", workspace_read_only=False),
                    checkout,
                )

                try:
                    code, output = writable(command, limit)

                finally:
                    writable.close()

                now = _held_files(checkout)
                changed = next(
                    (path for path, digest in held.items() if now.get(path) != digest), None
                )

                if changed is not None:
                    return refused(f"`{command}` changed {changed} in the checkout")

            runs.append(CommandRun(command=command, exit_code=code, tail=clip(output, TAIL_BYTES)))

        return runs

    finally:
        executor.close()


# ....................... #


def _critic_reads(run: Dispatch) -> bool:
    """Whether this run's gate-green candidate goes to the critic, which is
    the one reader of an evidence package so far (S-0122/D-6)."""

    return run.config.review.mode == "critic" and review_gated(run.config, run.task, run.shadow)


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


def _is_empty_implement_diff(ctx: GateContext, root: Path, attempt: int | None = None) -> bool:
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
    return only_bookkeeping and not _decides_a_row(ctx, attempt)


def _decides_a_row(ctx: GateContext, attempt: int | None = None) -> bool:
    """Whether the attempt's log answers a row the contract inherited, by
    either spelling of the row's id: a `departed` entry (the phase is not
    needed, with a proposal and evidence), or a `decided` one, which logs
    written before S-0122/D-14 still carry.

    A rejection answers for the attempt that recorded it alone: an earlier
    attempt's contradiction does not make a later empty diff a no-op. With no
    *attempt* named, the log's latest recorded attempt is the one read."""

    from torve.gates.log_entry import parse_log

    if ctx.task is None or not ctx.log_text:
        return False

    document, _ = parse_log(ctx.log_text)
    entries = document.get("entries") if isinstance(document, dict) else None

    if not isinstance(entries, list):
        return False

    rows = {row.id for row in ctx.task.decisions} | {
        row.id.rsplit("/", 1)[-1] for row in ctx.task.decisions
    }

    if attempt is None:
        recorded: list[int] = []

        for entry in entries:
            if not isinstance(entry, dict) or entry.get("attempt") is None:
                continue

            # A persisted attempt may be a numeric string, a legacy or
            # hand-edited value; it is read the way `threads.rejections` reads
            # it, so the latest attempt it names is not silently attempt 1.
            try:
                recorded.append(int(entry["attempt"]))

            except (TypeError, ValueError, OverflowError):
                continue

        attempt = max(recorded) if recorded else 1

    def answers(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False

        return (
            entry.get("action") in ("departed", "decided")
            and str(entry.get("decision", "")) in rows
        )

    # A review round whose every claim is rejected with an entry has done its
    # work (S-0084/D-13): the contradiction is the reply the engine posts, and
    # the tree is right to be unchanged. bloomery's first live round (T-0028)
    # rejected a claim already fixed on the branch three times and was refused
    # as an empty diff each time. Only the current attempt's rejections count.
    return any(answers(entry) for entry in entries) or bool(threads.rejections(entries, attempt))


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
    else (S-0010/D-6): the subject carries the contract's short title, so a
    history is readable without opening the task.

    No `Torve-` trailer rides here (S-0059/D-10). The landing file that
    follows in its own commit carries the task, the attempt, the agent,
    the base, this commit and the rows the contract inherited — everything
    the five trailers said, in the place the corpus reads."""

    # S-0007/A-1: the contract's short title names the landing; the intent's
    # first line is the fallback for contracts minted before it existed.
    head = task.title.strip() or (
        task.intent.strip().splitlines()[0].strip() if task.intent.strip() else ""
    )

    subject = _clipped(head, 46)
    what = f" {subject} —" if subject else ""
    message = f"torve({task.id}):{what} attempt {attempts} green"

    # A cut subject leaves the whole text to the body (#199).
    return message if subject == head else f"{message}\n\n{head}"


def _clipped(text: str, room: int) -> str:
    """*text* cut with `…` at the first space at or past *room* that sits
    outside a backticked name, so no word, path or name is split (#199); a
    text with no such space is kept whole."""

    if len(text) <= room:
        return text

    named = {at for span in re.finditer(r"`[^`]*`", text) for at in range(*span.span())}
    cut = next((at for at in range(room, len(text)) if text[at] == " " and at not in named), None)

    return text if cut is None else text[:cut].rstrip(",;:") + "…"


# ....................... #


async def judge(run: Dispatch, state: RunState) -> tuple[int, str, str]:
    """One gate pass over the tree the attempt just left, and the facts the
    rest of the run reads off it: the convictions retry selection uses
    (S-0034/D-5), the pass the reviewer is handed, and — on a red — whether
    the next attempt is routed as a repair."""

    # S-0122/D-7: selection judged the tree this attempt goes on with, and
    # stamped what the rest of the run reads; its verdict is this pass's.
    if run.preselected is not None:
        verdict, run.preselected = run.preselected, None
        return verdict

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
    run.last_pass = GatePass(
        results=list(results), patch=patch, digest=digest, evidence=run.evidence
    )

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
# 26, `scope` 14. The gates here are those checks still in the battery: a red
# from one names a property of the tree, not a verdict on the approach, so the
# next attempt is told the gate's own command (S-0127/D-6 starts it from the
# tree the convicted one started from).
# `acceptance` — the largest class — stays deliberately outside the set: a
# suite that fails twice may be a tree worth keeping or an approach worth
# abandoning, and only the ledger's per-gate repeat counts can tell those
# apart. Widening this set is a decision with those counts behind it
# (S-0069/D-5's own consequence); it does not happen by editing this line.
# `user-facing-text`, `decisions-reported` and `red-on-base` left with their
# gates (S-0122/D-17).
REPAIR_GATES: frozenset[str] = frozenset({"layering", "scope"})


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
        # The battery's own manifest, so the repair is for the gate that convicted.
        resolved = load_manifest(run.manifest_file).resolved_gates()

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
    (S-0069/D-3, D-6): its acceptance is the gate's own command beside
    everything the attempt is shown, and one gate earns one repair per
    dispatch. Every other red routes where it routed before: the shown
    acceptance alone, and the ladder untouched — what tier runs next stays
    outcome and axis alone (S-0054/D-12). No convicted tree is committed or
    carried: the next attempt starts from the tree the convicted one started
    from (S-0127/D-6), and is told what convicted it.

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


# ....................... #


def _commit_reviewed_tree(run: Dispatch, state: RunState) -> None:
    """An escalation from the review stage leaves the attempt's tree only in
    the worktree — the candidate is committed at `reviewed → ready`, after the
    review — so a green-gated tree was lost to a blocker, an unreadable verdict
    or a broker refusal. S-0086/D-2 commits it on the task's branch here, kin to
    the budget checkpoint (S-0026/D-9): the same trailer, so nothing mistakes
    it for a landed candidate, and no landing is written (S-0059/D-12).

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

    elif reason == str(EscalationReason.POISON_CEILING) and _on_a_revision(state):
        why = _CEILING_REVISION_MARKER

    elif reason == str(EscalationReason.KILLED):
        why = "escalated on an urgent note"

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

    # A later attempt whose work was already committed — a checkpoint on the
    # branch, or the snapshot a candidate's worktree was cut at (S-0122/D-7) —
    # has nothing new to commit and is still a candidate: the tip the landing
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
        f"torve checkpoint {run.task.id}: attempt {final.attempts} {_EXHAUSTED_MARKER}"
        f"\n\nTorve-Checkpoint: {run.task.id} attempt {final.attempts}"
    )
    author = f"{_agent_identity(run.meta)} <agents@torve.local>"
    run.deps.vcs.commit_all(run.worktree, message, author, run.config.vcs.signing_key)


# ....................... #


# ....................... #
# S-0122/D-7, S-0127/D-6: the attempt as a unit's candidates. The order and
# the choosing are `selection`'s; what one candidate is — a session in its own
# worktree, its gate pass, its critic call — is here.

# A history fact per critic call, which the unit's budget of calls counts.
CRITIC_MARKER = "critic reviewed candidate "

# A history fact per priced candidate or critic call, which the unit's
# notional-spend cap sums across its attempts (S-0123/D-1). The amount leads,
# so the sum reads the same number the record names.
SPEND_MARKER = "unit spend "


def _list_prices(config: RunnerConfig) -> dict[tuple[str, str], Price]:
    """The list prices the provider table carries, by provider and roster key
    — the two names an attempt's agent block records its seat by."""

    return {
        (name, key): model.list_price
        for name, record in config.provider_records.items()
        for key, model in record.models.items()
        if model.list_price is not None
    }


def _unit_spend(state: RunState) -> float:
    """The unit's notional spend across its attempts (S-0123/D-1): every
    priced candidate and critic call the run's history carries."""

    total = 0.0

    for entry in state.history:
        fact = str(entry.get("fact", ""))

        if not fact.startswith(SPEND_MARKER):
            continue

        try:
            total += float(fact[len(SPEND_MARKER) :].split()[0])

        except (ValueError, IndexError):
            continue

    return total


def _record_spend(state: RunState, spend: float, what: str) -> None:
    """Append one priced call's notional spend to the run's history, so the
    next attempt carries it as it carries the critic-call count."""

    state.history.append(
        {
            "at": state.heartbeat,
            "from": str(state.state),
            "to": str(state.state),
            "fact": f"{SPEND_MARKER}{spend:.6f} {what}",
        }
    )
    state.save()


def selects(run: Dispatch) -> bool:
    """Whether this dispatch's attempt runs through selection: a live
    implement run whose critic judges candidates, or whose seat runs a
    session, so a red attempt is followed by a fresh one from the tree it
    started from (S-0127/D-6)."""

    if run.shadow or run.arm != CONFIGURED or run.task.role != "implement":
        return False

    critic = run.config.review.mode == "critic" and review_gated(run.config, run.task, run.shadow)

    return critic or getattr(run.deps.agent, "follow_up", None) is not None


def _escalated(state: RunState) -> bool:
    """Whether the unit escalated once, which makes it class L (S-0122/D-7)."""

    return any(entry.get("to") == str(TaskState.ESCALATED) for entry in state.history)


def _width(run: Dispatch, state: RunState) -> tuple[int, str]:
    """The unit's width and class (S-0122/D-7, S-0124/D-1). Several candidates
    only when a critic judges the unit; a revision attempt — one whose
    task has a feedback record at the root — is class S and runs one. A review
    round runs one, and `review.max_width` caps the rest."""

    revision = feedback.feedback_file(run.root, run.task.id).is_file()
    size = selection.unit_class(
        run.task, run.worktree, revision=revision, escalated=_escalated(state)
    )
    critic = run.config.review.mode == "critic" and review_gated(run.config, run.task, run.shadow)
    width = selection.WIDTHS[size] if critic and run.task.round is None else 1
    cap = run.config.review.max_width

    return (width if cap is None else min(width, cap)), size


def _unit_class(run: Dispatch, state: RunState) -> str:
    """The class whose figures bound the unit's spend and clock (S-0127/D-9):
    `unit_class` over the contract against the repository root's tree, with
    the `escalated` flag `_width` reads and without the revision flag. Read
    once per run, so every attempt reads the same one, and a revision, class
    S for its width, keeps the unit's bounds."""

    if not run.unit_size:
        run.unit_size = selection.unit_class(run.task, run.root, escalated=_escalated(state))

    return run.unit_size


def _candidate_dispatch(run: Dispatch, start: Dispatch, index: int, base: str) -> Dispatch:
    """Candidate *index*'s own dispatch over its own worktree, copied from
    *start* — the attempt as it stood before any candidate ran (S-0124/D-6), so
    every candidate runs on the tier the attempt resolved, never on one an
    earlier candidate's facts moved it to (S-0069/D-3). The facts a gate pass
    stamps are its own."""

    return replace(
        start,
        worktree=run.deps.workspace.create_candidate(run.task.id, index, base),
        candidate=index,
        meta=dict(start.meta),
        convictions=[],
        last_pass=GatePass(),
        evidence=None,
        repaired_gates=set(start.repaired_gates),
        preselected=None,
        preselected_review=None,
    )


async def select_attempt(run: Dispatch, state: RunState) -> AgentResult:
    """One attempt as the unit's candidates (S-0122/D-7): they run side by
    side (S-0124/D-4) from the one tree the attempt started from, the first
    to finish green wins and the others are cut, and the critic reads it
    alone (S-0127/D-6). A red candidate gets no follow-up turn, and an attempt
    with no green candidate is followed by a fresh one from the same tree.
    The one chosen is left in the task's worktree with its gate pass and its
    verdict stamped on the run, which the loop's gates and review then read
    instead of running again; the rest are removed, and the unit's record is
    written."""

    from torve.application.review import _blocker_revisions_spent

    width, size = _width(run, state)
    critic = run.config.review.mode == "critic" and review_gated(run.config, run.task, run.shadow)
    prices = _list_prices(run.config) if critic else {}
    budget_spent = _unit_spend(state) if critic else 0.0
    # S-0127/D-9: the unit's cap and clock are its class's, read from the
    # root once per run, never the attempt's own class its width reads.
    budget_usd, unit_minutes = selection.UNIT_BUDGETS[_unit_class(run, state)]
    revision_attempt = feedback.feedback_file(run.root, run.task.id).is_file()
    attempt_started = time.monotonic()
    reviews: list[str] = []
    run.meta.pop("candidate", None)

    # S-0124/D-6: the attempt resolves its tier once, before any candidate
    # starts. Every candidate then runs on that resolution; a sibling's facts
    # can no longer move it to another seat.
    run.resolved_agent = advance_tier(run, state)
    agent_clock = agent_timeout_for(run.config, run.tier)
    start = replace(run, meta=dict(run.meta), repaired_gates=set(run.repaired_gates))

    # S-0127/D-6: the attempt after a red one is an independent sample — a
    # fresh session from the tree the red one started from, never the tree it
    # left. The task's worktree goes back to that tree before anything runs.
    if run.start_tree is not None:
        await asyncio.to_thread(run.deps.workspace.create_candidate, run.task.id, 1, run.start_tree)
        await asyncio.to_thread(run.deps.workspace.promote_candidate, run.task.id, 1)

    # The unit's one tree, taken before the first candidate changes it: every
    # candidate starts from it, and so does the attempt after a red one.
    base = await asyncio.to_thread(run.deps.workspace.snapshot, run.task.id)

    # What each launched candidate's dispatch is, so a cancelled one's
    # finished turns can still be priced (S-0124/D-7).
    dispatches: dict[int, Dispatch] = {}
    # The later candidates' own dispatches over their own worktrees, cut
    # before any candidate starts.
    cut_from_base: dict[int, Dispatch] = {}
    # Candidates whose spend this attempt already booked, so a cut that lands
    # after the booking prices them once (S-0123/D-1).
    booked: set[int] = set()

    async def launch(index: int, clock: float) -> selection.Candidate:
        crun = run if index == 1 else cut_from_base[index]
        dispatches[index] = crun
        crun.clock = clock if clock < agent_clock else None

        if width > 1:
            crun.meta["candidate"] = index

        # An attempt's own spend starts at nothing: a preceding attempt's
        # block must never price a candidate cut before its first turn
        # finished (S-0123/D-1, S-0124/D-7).
        if critic:
            crun.meta.pop("cost_usd", None)

            for stale in TOKEN_FIELDS:
                crun.meta.pop(stale, None)

        started = time.monotonic()
        # S-0124/D-4: the cut a sibling's green finish makes can land while a
        # gate pass is running. The shield lets that pass finish and its
        # verdict stand, but the candidate itself is cut.
        cut = [False]

        async def judged() -> tuple[int, str, str] | None:
            # The loop's own gates step, as it reads a pass that raised: the
            # escalation ends the unit, and a refusal to dispatch goes back.
            async def pass_() -> tuple[int, str, str] | None:
                try:
                    return await judge(crun, state)

                except (BlockedDispatch, asyncio.CancelledError):
                    raise

                except Exception as exc:
                    # Two candidates can fail their passes together; the
                    # first escalation is the run's, the second is the same
                    # ending and must not transition again.
                    if state.escalation is None:
                        state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))

                    return None

            # S-0124/D-4: a gate pass already running is never cancelled. The
            # cancel ends the candidate's agent turn; the pass it already
            # started is shielded, finishes, and its verdict stands.
            running = asyncio.ensure_future(pass_())

            try:
                return await asyncio.shield(running)

            except asyncio.CancelledError:
                # A sibling finished green first, not this pass: the pass
                # finishes and its verdict stands, but the candidate is cut
                # (S-0124/D-4, S-0127/D-6).
                cut[0] = True
                return await running

        # S-0127/D-6: one session and no follow-up turn — a red candidate's
        # tree is judged once and goes no further.
        result = await run_agent_session(crun, state)
        crun.clock = None

        candidate = selection.Candidate(index=index, result=result, run=crun)
        # S-0124/D-9: the seat the candidate ran on, for the unit record.
        candidate.tier = crun.tier_name
        candidate.model = crun.tier.model or ""

        if critic:
            # S-0123/D-1: what this candidate cost the unit. The block is the
            # one the record is built from, priced by the engine, so a
            # roster-priced seat's cost is torve's arithmetic and a seat with
            # none falls back to its tokens at the ledger's list price.
            candidate.spend = selection.notional_spend(priced(crun.meta), prices)

            if candidate.spend is not None:
                _record_spend(state, candidate.spend, f"candidate {index}")
                booked.add(index)

        if (
            state.escalation is None
            and result.refused is None
            and not result.timed_out
            and result.exit_code == 0
            and halted(crun, state.attempts) is None
        ):
            verdict = await judged()
            # A sibling's green finish cut this candidate while its pass ran:
            # it is recorded with that pass's real verdict, marked cut.
            candidate.cut = cut[0]

            if verdict is None:
                candidate.wall_s = time.monotonic() - started
                return candidate

            crun.preselected = verdict
            candidate.green = verdict[0] == 0
            candidate.patch = crun.last_pass.patch

        candidate.wall_s = time.monotonic() - started

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        from torve.application.critic import judge as critic_judge

        taken = [*(run.deps.taken() if run.deps.taken is not None else ()), *reviews]
        review_task = mint_review_task(run.root, run.task, taken=taken)
        reviews.append(review_task.id)
        verdict = await asyncio.to_thread(critic_judge, candidate.run, review_task, state)
        candidate.verdict = verdict

        if critic:
            # S-0123/D-1: the critic call costs the unit too, priced the same
            # way the candidate it read was.
            candidate.critic_spend = selection.notional_spend(
                {
                    "cost_usd": verdict.cost_usd,
                    "provider": verdict.provider,
                    "model": verdict.model,
                    **verdict.token_counts,
                },
                prices,
            )

            if candidate.critic_spend is not None:
                _record_spend(state, candidate.critic_spend, f"critic {candidate.index}")

        state.history.append(
            {
                "at": state.heartbeat,
                "from": str(state.state),
                "to": str(state.state),
                "fact": f"{CRITIC_MARKER}{candidate.index}: {verdict.review_id}",
            }
        )
        state.save()

        return not verdict.blockers and not verdict.unparseable and verdict.refusal is None

    spent = sum(str(entry.get("fact", "")).startswith(CRITIC_MARKER) for entry in state.history)
    chosen = 1

    def cancelled_spend(index: int) -> float | None:
        """What a candidate cut by a sibling's green finish still spent: the
        turns it finished before its turn was cancelled (S-0124/D-7). Recorded
        on the run's history too, so the next attempt's bound counts it."""

        crun = dispatches.get(index)

        if not critic or crun is None:
            return None

        spend = selection.notional_spend(priced(crun.meta), prices)

        if spend is not None and index not in booked:
            _record_spend(state, spend, f"candidate {index}")
            booked.add(index)

        return spend

    def close_session(index: int) -> None:
        """Close candidate *index*'s kept session, if it still has one
        (S-0124/D-8). Idempotent, so the ordinary path and a fallback can both
        ask without double-destroying a sandbox."""

        crun = dispatches.get(index)
        opened = getattr(crun, "open_session", None)

        if crun is not None and opened is not None:
            opened.close()
            crun.open_session = None

    def revision_reraised(member: selection.Candidate) -> bool:
        """Whether the critic repeated a blocker an earlier round recorded
        (S-0127/D-4), read from the decision `review_step` reads."""

        verdict = member.verdict

        if verdict is None or not verdict.blockers:
            return False

        return verdict.decision is not None and bool(verdict.decision.reraised)

    try:
        # S-0124/D-4, S-0127/D-6: every worktree is cut before any candidate
        # starts, so the first to finish green finished first on its work,
        # not on the head start a sibling's worktree cost that sibling.
        for index in range(2, width + 1):
            cut_from_base[index] = await asyncio.to_thread(
                _candidate_dispatch, run, start, index, base
            )

        chosen_round = await selection.select(
            width,
            launch,
            review if critic else None,
            agent_clock=agent_clock,
            seed=run.task.id,
            critic_budget=max(0, selection.CRITIC_CALLS - spent),
            stop=lambda: state.escalation is not None,
            spent=budget_spent,
            budget_usd=budget_usd,
            cancelled_spend=cancelled_spend,
        )

        winner = chosen_round.chosen if chosen_round.candidates else None
        # S-0124/D-8: the first green candidate the critic blocked may be
        # continued in its own open session, in place of the fresh attempt the
        # round would otherwise start. Its sandbox (and every other
        # candidate's) closes once the critic pass has decided.
        continue_index = (
            winner.index
            if winner is not None
            and critic
            and chosen_round.winner is None
            and not chosen_round.exhausted
            and run.config.review.revision == "session"
            and _blocker_revisions_spent(state) < run.config.review.resolved_blocker_revisions()
            and winner.reviewed
            and not winner.clean
            # A blocker the previous round already named stops the run in
            # `review_step` (S-0124/D-3); no turn is spent on it.
            and not revision_reraised(winner)
            # The turn's own re-review is a critic call too (S-0122/D-8): a
            # unit whose calls are spent starts no turn it cannot judge.
            and spent + chosen_round.critic_calls < selection.CRITIC_CALLS
            else None
        )

        for index in list(dispatches):
            if index != continue_index:
                close_session(index)

        session_revision: selection.Revision | None = None

        def close_unit() -> None:
            # S-0124/D-9: every critic-mode attempt writes it, whatever its
            # width, and an attempt a budget or clock stop ended too — one the
            # cap stopped before any candidate launched included, with no
            # candidates and no selection.
            if not critic:
                return

            chosen_round.revision = session_revision
            append_record(
                telemetry_target(run),
                selection.unit_record(
                    chosen_round,
                    task_id=run.task.id,
                    attempt=state.attempts,
                    width=width,
                    size=size,
                    at=stamp(),
                    revision=session_revision,
                ),
            )

        # S-0123/D-1: the class's cap stopped a candidate or critic call the
        # unit still needed (S-0127/D-9). There is no winner to land, so the
        # run escalates and names what it spent.
        if chosen_round.exhausted and chosen_round.winner is None:
            state.escalate(
                EscalationReason.BUDGET_EXHAUSTED,
                f"{selection.UNIT_BUDGET_MARKER}: ${chosen_round.spent_usd:.2f} spent of "
                f"${budget_usd:.2f}",
            )
            close_unit()

            return AgentResult(exit_code=1, output="")

        # A budget or clock stop inside the session rounds still leaves the
        # selected candidate in the task's worktree for the checkpoint.
        stopped = False

        # One turn per revision round, in the same open session, while the
        # rounds last and the critic names nothing the last round recorded.
        while continue_index is not None and winner is not None:
            member = winner
            opened = getattr(member.run, "open_session", None)
            minutes = run.task.budget.wallclock_minutes
            if minutes is None:
                minutes = unit_minutes

            elapsed = _elapsed_minutes(state)
            remaining = max(0.0, (minutes - elapsed) * 60.0)
            unit_spend = _unit_spend(state)
            verdict = member.verdict

            if unit_spend >= budget_usd:
                # S-0124/D-7: the bound is a stop before new work, checked
                # before the revision turn exactly as before a fresh attempt.
                close_session(continue_index)
                state.escalate(
                    EscalationReason.BUDGET_EXHAUSTED,
                    f"{selection.UNIT_BUDGET_MARKER}: ${unit_spend:.2f} spent of ${budget_usd:.2f}",
                )
                stopped = True
                break

            if remaining <= 0:
                close_session(continue_index)
                state.escalate(
                    EscalationReason.BUDGET_EXHAUSTED,
                    f"{_WALLCLOCK_MARKER}: {elapsed:.1f}m elapsed, budget {minutes}m",
                )
                stopped = True
                break

            if opened is None or verdict is None or not verdict.blockers:
                # The seat keeps no session, or there is nothing to revise:
                # the round falls back to `fresh`.
                close_session(continue_index)
                break

            else:
                from torve.application.feedback import render_feedback
                from torve.application.review import blocker_threads

                threads = (
                    verdict.threads
                    if verdict.threads is not None
                    else blocker_threads(verdict.blockers, verdict.review_id)
                )
                feedback_text = render_feedback(
                    run.task.id, member.patch, threads, verdict.measured
                )
                # The sandbox is the outer bound (S-0035/D-6): the session
                # turn gets what is left of the sandbox that kept it, never a
                # clock that outlives the container.
                turn_clock = min(
                    remaining,
                    agent_timeout_for(run.config, member.run.tier),
                    opened.sandbox_left_s(),
                )
                turn_started = time.monotonic()
                continued = await opened.follow_up(feedback_text, turn_clock)
                wall_s = time.monotonic() - turn_started
                round_no = _blocker_revisions_spent(state) + 1

                if continued is None:
                    # S-0124/D-8: the harness kept no session. The round is a
                    # fresh revision attempt, and the loop starts it below.
                    close_session(continue_index)
                    break

                else:
                    # The turn is the round `review_step` would otherwise
                    # spend on a fresh attempt (S-0124/D-3, D-8): it records
                    # the same revision fact with the anchors it revised, so
                    # the next verdict is judged as the round after it.
                    from torve.application.review import blocker_fact

                    state.history.append(
                        {
                            "at": state.heartbeat,
                            "from": str(state.state),
                            "to": str(state.state),
                            "fact": blocker_fact(
                                verdict.review_id,
                                round_no,
                                run.config.review.resolved_blocker_revisions(),
                                verdict.blockers,
                                verdict.head,
                            ),
                        }
                    )
                    state.save()
                    member.result = continued
                    # The turn stamped the session's cumulative block onto the
                    # candidate's, so it is priced the way the candidate was
                    # (S-0123/D-1), never at the adapter's claim.
                    after_cost = selection.notional_spend(priced(member.run.meta), prices)
                    turn_spend = (
                        None
                        if after_cost is None
                        else round(max(0.0, after_cost - (member.spend or 0.0)), 6)
                    )

                    if turn_spend is not None:
                        _record_spend(state, turn_spend, f"revision {round_no}")
                        member.spend = round((member.spend or 0.0) + turn_spend, 6)

                    crun = member.run
                    crun.preselected = None
                    # The turn left a new tree: the green and the verdict the
                    # candidate carried were the previous tree's (S-0124/D-9).
                    member.green = member.reviewed = member.clean = False
                    member.verdict = None

                    # A turn that escalated, was refused, halted, timed out or
                    # failed is judged by nobody, as a first turn is: the
                    # loop reads its ending off the result.
                    if (
                        state.escalation is not None
                        or continued.refused is not None
                        or continued.timed_out
                        or continued.exit_code != 0
                        or halted(crun, state.attempts) is not None
                    ):
                        session_revision = selection.Revision(
                            kind="session",
                            round=round_no,
                            rereview="failed",
                            spend_usd=turn_spend,
                            wall_s=round(wall_s, 3),
                        )
                        close_session(continue_index)
                        break

                    crun.last_pass = GatePass()
                    exit_code = 1

                    try:
                        # The loop's gates step reads this verdict rather
                        # than judging the same tree again (S-0122/D-7).
                        gate_verdict = await judge(crun, state)
                        exit_code = gate_verdict[0]
                        crun.preselected = gate_verdict

                    except (BlockedDispatch, asyncio.CancelledError):
                        raise

                    except Exception as exc:
                        if state.escalation is None:
                            state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))

                    # A tree whose gates are red is read by no critic.
                    rereview = "failed"
                    rereviewed = False

                    if (
                        state.escalation is None
                        and exit_code == 0
                        and halted(crun, state.attempts) is None
                    ):
                        member.green = True
                        member.patch = crun.last_pass.patch
                        # The critic call is new work too (S-0124/D-7).
                        if _unit_spend(state) >= budget_usd:
                            state.escalate(
                                EscalationReason.BUDGET_EXHAUSTED,
                                f"{selection.UNIT_BUDGET_MARKER}: ${_unit_spend(state):.2f} spent of "
                                f"${budget_usd:.2f}",
                            )
                            # The turn ran and spent; only its re-review did not.
                            session_revision = selection.Revision(
                                kind="session",
                                round=round_no,
                                rereview="stopped",
                                spend_usd=turn_spend,
                                wall_s=round(wall_s, 3),
                            )
                            close_session(continue_index)
                            stopped = True
                            break

                        member.clean = await review(member)
                        chosen_round.critic_calls += 1
                        chosen_round.spent_usd = max(chosen_round.spent_usd, _unit_spend(state))
                        member.reviewed = True
                        rereviewed = True
                        rereview = "clean" if member.clean else "blocked"

                        if member.clean:
                            chosen_round.winner = member

                    session_revision = selection.Revision(
                        kind="session",
                        round=round_no,
                        rereview=rereview,
                        reraised=revision_reraised(member),
                        spend_usd=turn_spend,
                        wall_s=round(wall_s, 3),
                    )

                    # Another round in this session only when the critic read
                    # this turn's tree and blocked it anew, with rounds and
                    # critic calls left; otherwise `review_step` decides.
                    if not (
                        rereviewed
                        and not member.clean
                        and state.escalation is None
                        and not session_revision.reraised
                        and _blocker_revisions_spent(state)
                        < run.config.review.resolved_blocker_revisions()
                        and spent + chosen_round.critic_calls < selection.CRITIC_CALLS
                    ):
                        close_session(continue_index)
                        break

        winner = chosen_round.chosen
        chosen = winner.index
        # S-0127/D-6: a green candidate's tree goes on — to land, or to the
        # attempt a blocker sends it to; any other ending sends the next
        # attempt back to the tree this one started from.
        run.start_tree = None if winner.green else base

        if chosen > 1:
            await asyncio.to_thread(run.deps.workspace.promote_candidate, run.task.id, chosen)
            picked: Dispatch = winner.run
            run.task, run.repaired_gates = picked.task, picked.repaired_gates
            run.last_pass, run.convictions = picked.last_pass, picked.convictions
            run.evidence, run.preselected = picked.evidence, picked.preselected
            run.meta.clear()
            run.meta.update(picked.meta)

        run.preselected_review = winner.verdict if winner.reviewed else None

        if stopped:
            close_unit()

            return AgentResult(exit_code=1, output="")

        # S-0124/D-9: a fresh revision attempt records the round it was, so
        # the arm's records can be compared without joining attempts by hand.
        if session_revision is None and revision_attempt and critic:
            session_revision = selection.Revision(
                kind="fresh",
                round=_blocker_revisions_spent(state) or 1,
                # A revision whose agent or gates failed was read by no critic.
                rereview=(
                    "clean"
                    if chosen_round.winner is not None
                    else "blocked"
                    if winner.reviewed
                    else "failed"
                ),
                reraised=(revision_reraised(winner) if chosen_round.winner is None else False),
                spend_usd=round(max(0.0, chosen_round.spent_usd - budget_spent), 6),
                wall_s=round(time.monotonic() - attempt_started, 3),
            )

        close_unit()

        result: AgentResult = winner.result

        return result

    finally:
        # A session the ordinary path did not close — an exception while a
        # revision turn ran — still dies here (S-0124/D-8).
        for index in list(dispatches):
            close_session(index)

        # Every candidate's worktree, including one whose cut failed half way,
        # is removed unless it is the one that landed.
        for index in range(2, width + 1):
            if index != chosen:
                await asyncio.to_thread(run.deps.workspace.remove, f"{run.task.id}~{index}")


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
    attempt = (
        revert_leg(run)
        if task.role == "revert"
        else partial(select_attempt, run)
        if selects(run)
        else partial(run_agent_session, run)
    )

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
        gates=partial(_bare_gates, run) if run.battery_removed else partial(judge, run),
        land=partial(land, run),
        review=review,
        close=partial(close_dispatch, run),
        checkpoint=partial(checkpoint, run),
        reviewed_tree=partial(_commit_reviewed_tree, run),
        unit_size=partial(_unit_class, run),
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

    branch = naming.document_branch(task.spec)

    try:
        document = resolve_base(root, f"origin/{branch}")

    except GitError:
        return resolve_base(root, config.base), True, None

    # A landing held for its wave (S-0097/D-6) is on this checkout's copy only,
    # and the lane lands onto that copy: a phase cut without it conflicts with
    # it on every attempt, and the requeue cut it again from the same place.
    if document is not None and _holds_landings(root, branch, document):
        document = branch

    return document, False, document


def _holds_landings(root: Path, branch: str, remote: str) -> bool:
    """Whether this checkout's *branch* carries landings the remote lacks —
    the lane's `_unpublished`, read through `git` because the runner holds no
    `LaneVcs`. The lane keeps such a branch, diverged from the remote or not,
    and lands onto it, so the cut follows the same rule. A branch absent here,
    or one the remote already contains, is not one."""

    try:
        local = git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")

    except GitError:
        return False

    try:
        git(root, "merge-base", "--is-ancestor", local.strip(), remote)

    except GitError:
        return True

    return False


def _stale_checkpoint(root: Path, task_id: str, base: str) -> bool:
    """Whether the task's branch exists and lacks *base*: a checkpoint cut
    before the base moved. A branch absent here is not one."""

    branch = f"refs/heads/{naming.branch(task_id)}"

    try:
        git(root, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")

    except GitError:
        return False

    try:
        git(root, "merge-base", "--is-ancestor", base, branch)

    except GitError:
        return True

    return False


def _fork_point(root: Path, document: str, tip: str) -> str | None:
    """Where *tip* was cut from *document*, read off the branch's reflog — the
    remote's copy first, then this checkout's — or None when neither reflog
    names it, as in a fresh clone."""

    for ref in (f"refs/remotes/origin/{document}", f"refs/heads/{document}"):
        try:
            return git(root, "merge-base", "--fork-point", ref, tip).strip()

        except GitError:
            continue

    return None


def _merge_tree(root: Path, old: str, base: str, tip: str) -> str:
    """The tree a three-way merge of *base* and *tip* over *old* writes.

    `merge-tree --merge-base` arrived in git 2.40, and the battery runs on the
    agent's image, whose git is older (`dsh-sandbox:0.9.0` ships 2.39.5). There
    *base*'s tree is anchored under *old* so a plain two-commit merge-tree
    computes *old* as the merge base: the same merge, with one dangling commit
    left behind. A conflict raises, as it does on the newer form."""

    try:
        return git(root, "merge-tree", "--write-tree", f"--merge-base={old}", base, tip).strip()

    except GitError as exc:
        if "unknown option" not in str(exc):
            raise  # a conflict, not an old git

    anchor = git(
        root,
        "-c",
        "user.name=Torve",
        "-c",
        "user.email=torve@local",
        "commit-tree",
        f"{base}^{{tree}}",
        "-p",
        old,
        "-m",
        "torve: checkpoint merge anchor",
    ).strip()

    return git(root, "merge-tree", "--write-tree", anchor, tip).strip()


def _carry_checkpoint(root: Path, task_id: str, base: str, document: str | None = None) -> bool:
    """A stale checkpoint moved onto *base*: the commits only the checkpoint
    carries, merged three-way onto *base* and committed under the tip's
    message, with the old tip kept under `refs/torve/checkpoints/`. False,
    and the branch untouched, when the merge conflicts or git refuses."""

    branch = f"refs/heads/{naming.branch(task_id)}"

    try:
        tip = git(root, "rev-parse", f"{branch}^{{commit}}").strip()
        # The lane amends the last landing after its rebase, rewriting the
        # `commit:` its execution record names, so that landing's patch no
        # longer matches and `cherry` reads it as the checkpoint's own; the
        # merge then conflicted on the record and T-0809 was recut. The
        # document branch's reflog still names the tip the checkpoint was cut
        # from.
        old = _fork_point(root, document, tip) if document else None

        if old is None:
            # Oldest first: `-` where *base* already holds the patch (a landing
            # the lane rebased), `+` where only the checkpoint does.
            marks = [line.split() for line in git(root, "cherry", base, tip).splitlines()]
            upstream = [i for i, (mark, _sha) in enumerate(marks) if mark == "-"]
            own = marks[upstream[-1] + 1 :] if upstream else marks

            if not own:
                return False

            old = git(root, "rev-parse", f"{own[0][1]}^").strip()

        elif old == tip:
            return False

        # Exit 1 on a conflict, which `git` raises as a GitError.
        tree = _merge_tree(root, old, base, tip)
        message = git(root, "log", "-1", "--format=%B", tip).strip()
        carried = git(
            root,
            "-c",
            "user.name=Torve",
            "-c",
            "user.email=torve@local",
            "commit-tree",
            tree,
            "-p",
            base,
            "-m",
            message,
        ).strip()
        git(root, "update-ref", f"refs/torve/checkpoints/{task_id}/{tip[:12]}", tip)
        git(root, "update-ref", branch, carried, tip)

    except GitError:
        return False

    return True


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

    # A continuation resumes its checkpoint (S-0026/D-9), but under the document
    # unit the battery judges against the branch's tip as it stands now. A
    # checkpoint cut before that tip moved reads as reverting whatever landed
    # since: T-0695 ran six attempts on one past a hand fix, each red on the
    # fix's absence. So it is carried onto the tip, whose tree then holds what
    # landed since, and recut only when it does not merge; the workspace keeps
    # it under a ref either way. Every engine merge rebases the document
    # branch, and a recut there threw away T-0811's attempt 5.
    if (
        resume
        and gates_base is not None
        and _stale_checkpoint(root, task.id, gates_base)
        and not _carry_checkpoint(
            root,
            task.id,
            gates_base,
            naming.document_branch(task.spec) if task.spec is not None else None,
        )
    ):
        resume = False

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

    return _continuable(previous.escalation, previous)


def _checkpointed_for_resume(root: Path, task: Task) -> bool:
    """S-0090/D-2: a task whose run state was reaped still resumes when its
    branch tip is the checkpoint a review blocker, a halt, a revision the
    ceiling stopped (S-0125), a spent budget or a stopped run's requeue
    (`reaper.clear_escalated`) left — the tree is on the branch either way,
    and the reaper removing the state file is not an answer to the
    escalation."""

    try:
        message = git(root, "log", "-1", "--format=%B", naming.branch(task.id))

    except GitError:
        return False

    subject = message.splitlines()[0] if message else ""

    return f"Torve-Checkpoint: {task.id} " in message and subject.endswith(
        (
            _BLOCKER_MARKER,
            _HALTED_MARKER,
            _UNDERSPECIFIED_MARKER,
            _CEILING_REVISION_MARKER,
            _EXHAUSTED_MARKER,
            STOPPED_MARKER,
        )
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
    # S-0006/D-12: the lane re-queues a conflict once per base tip, and the
    # tip it re-queued against must outlive the re-dispatch. A fresh state
    # forgot it, and T-0004 (platform-images S-0002) re-queued three times
    # against one unmoved tip until the poison ceiling stopped it.
    conflict_base = None

    if state_path.exists():
        previous = RunState.load(state_path)
        conflict_base = previous.conflict_base

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

    state = RunState(
        task_id=task.id, path=state_path, attempts=recorded, conflict_base=conflict_base
    )
    fact = "torve run: single synchronous claim"
    state.transition(TaskState.CLAIMED, f"{fact} (continuation)" if resume else fact)

    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state, resume=resume))

    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")

        return state
