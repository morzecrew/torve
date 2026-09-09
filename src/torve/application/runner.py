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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from forze.application.contracts.durable.function import (
    DurableRunStatus,
    current_durable_run,
)
from forze.application.execution import ExecutionContext
from forze.base.primitives import JsonDict

from torve.application import decisions
from torve.application.dispatch import (
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
from torve.application.ports import (
    AgentResult,
    Runtime,
    SandboxHandle,
    SandboxSpec,
)
from torve.application.review import review_step, reviewer_for
from torve.application.runstate import Escalation, RunState
from torve.application.session import (
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
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.config.runconfig import RunnerConfig
from torve.domain.attempt import GateResult
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import Task
from torve.gates.context import GateContext, build_context, resolve_base
from torve.gates.runner import RunReport, run_gates

# ----------------------- #


@dataclass
class AttemptHooks:
    """What one attempt does, one gate pass does, and one landing does. The
    loop below owns the states and facts; the hooks own the mechanism."""

    attempt: Callable[[RunState], Awaitable[AgentResult]]
    halted: Callable[[], bool]  # locked-conflict detection after an attempt
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


# ....................... #


# Continuation eligibility's shared marker (S-0026 S-0026/D-8): every detail
# string this module writes for a wallclock-caused budget_exhausted
# escalation starts with it, and `_continuable` is the only reader — so
# generation and detection can never drift apart.
_WALLCLOCK_MARKER = "wallclock budget exhausted"


def _continuable(escalation: Escalation) -> bool:
    """S-0026/D-8 (LOCKED): continuation fires only on budget exhaustion —
    wallclock or tokens — never on a gate conviction, review blocker or any
    judged escalation. `iterations` exhaustion (like `poison_ceiling`) is
    excluded on purpose: repeated red gates is a judgement on the work, not
    a clock running out, so it restarts from base like any conviction."""

    if escalation.reason == str(EscalationReason.COST_ANOMALY):
        return True

    return escalation.reason == str(
        EscalationReason.BUDGET_EXHAUSTED
    ) and escalation.detail.startswith(_WALLCLOCK_MARKER)


# ....................... #


def _elapsed_minutes(state: RunState) -> float:
    started = datetime.strptime(state.history[0]["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)

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
            and _continuable(result.escalation)
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
    while True:
        # Poison ceiling is checked before dispatch, never after (S-0001/state-machine).
        if state.attempts >= ceiling:
            state.escalate(
                EscalationReason.POISON_CEILING, f"{state.attempts} attempts, ceiling {ceiling}"
            )

            return state

        if iterations is not None and state.attempts >= iterations:
            state.escalate(
                EscalationReason.BUDGET_EXHAUSTED, f"{state.attempts} attempts, budget {iterations}"
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

        if hooks.halted():
            # Terminal by design, not an error: the one case where a task
            # stops on working code (S-0001/state-machine).
            state.escalate(
                EscalationReason.LOCKED_CONFLICT,
                f"halted divergence entry in the {task.id} execution log",
            )

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
        return None if state.escalation is None else False

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


def run_gate_pass(run: Dispatch, state: RunState) -> tuple[int, str, str, list[GateResult], str]:
    """(exit_code, summary, config_hash, results, patch) — the results and
    the patch feed the review's input when one is configured. Raises on
    infrastructure failure.

    The battery runs in a sandbox of its own over the same image the agent
    ran under (S-0003/D-8) and the same derived cache (S-0035/D-3), because a pass
    that judged a different regime than the attempt ran in is judging
    something else.

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

    executor = _SandboxExecutor(
        run.deps.runtime,
        SandboxSpec(
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
        ),
        run.worktree,
    )

    try:
        ctx = build_context(
            run.worktree,
            manifest,
            base=resolve_base(run.worktree, run.gates_base or run.config.base),
            task_path=task_file if task_file.is_file() else None,
        )

        ctx.execute = executor

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
            record = build_record(ctx, RunReport(exit_code=1), digest, agent=run.meta)
            _write_attempt_record(run, record, state.attempts)
            summary = "empty diff against base — no changes produced"

            return 1, summary, digest, [], ctx.patch

        report = run_gates(ctx)

    finally:
        executor.close()

    digest = config_hash(manifest_path, run.worktree, run.config, image_digest=run.image_digest)
    record = build_record(ctx, report, digest, agent=run.meta)
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

    return all(
        entry.path in bookkeeping and (entry.old_path is None or entry.old_path in bookkeeping)
        for entry in ctx.diff
    )


# ....................... #


def _agent_identity(meta: dict[str, Any]) -> str:
    """The commit author and Torve-Agent trailer value (S-0010/branches-and-commits):
    adapter/model@model_version, degrading gracefully — a fake or mechanical
    attempt is named for what it is, never invented."""

    adapter = str(meta.get("adapter") or "unknown")
    model = meta.get("model")
    ident = f"{adapter}/{model}" if model else adapter
    version = meta.get("model_version")

    return f"{ident}@{version}" if version else ident


# ....................... #


def _provenance_message(task: Task, attempts: int, digest: str, meta: dict[str, Any]) -> str:
    """The full trailer set (S-0010/D-4): enough that `git log --grep`
    reconstructs a task's history with the store offline. The subject
    carries the intent's head (S-0010/D-6: composed from the contract, never
    the agent's prose) — a history readable without opening the task."""

    # S-0007/A-1: the contract's short title names the landing; the intent's
    # first line is the fallback for contracts minted before it existed.
    head = task.title.strip() or (
        task.intent.strip().splitlines()[0].strip() if task.intent.strip() else ""
    )

    if len(head) > 46:
        head = head[:45].rstrip() + "…"

    what = f" {head} —" if head else ""

    lines = [
        f"torve({task.id}):{what} attempt {attempts} green",
        "",
        f"Torve-Task: {task.id}",
        f"Torve-Attempt: {attempts}",
        f"Torve-Agent: {_agent_identity(meta)}",
        f"Torve-Config: {digest}",
    ]

    if task.decisions:
        graded = " ".join(f"{d.id}({d.grade})" for d in task.decisions)
        lines.append(f"Torve-Decisions: {graded}")

    return "\n".join(lines)


# ....................... #


async def judge(run: Dispatch, state: RunState) -> tuple[int, str, str]:
    """One gate pass over the tree the attempt just left, and the two facts
    the rest of the run reads off it: the convictions retry selection uses
    (S-0034/D-5) and the pass the reviewer is handed."""

    # Deliberately not guarded: a sync that fails leaves the battery judging
    # a log nobody vouched for, and the gates are fail-closed. The raise
    # lands as GATE_INFRASTRUCTURE_FAILURE, which is what this is.
    if run.deps.journal is not None:
        await asyncio.to_thread(run.deps.journal, run.worktree)

    try:
        exit_code, summary, digest, results, patch = await asyncio.to_thread(
            run_gate_pass, run, state
        )

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

    return exit_code, summary, digest


# ....................... #


async def land(run: Dispatch, state: RunState, digest: str) -> str:
    """The candidate commit, and its branch and pull request where the forge
    leg is on. The commit is the runner's artefact (S-0010/D-1), composed here
    where the attempt's model_version is already known: author is the agent
    identity (S-0010/D-2), trailers complete (S-0010/D-4), one commit per attempt
    (S-0010/D-8), signed outside the sandbox when a key is configured
    (S-0010/D-3)."""

    deps, config, task, worktree = run.deps, run.config, run.task, run.worktree

    # S-0057/D-7: what this attempt found goes beside the rows it cites, in the
    # candidate commit; the commit field stays empty — the candidate's own
    # trailers name the task — and a contract naming no document lands
    # nowhere, which is a fact, not a failure.
    try:
        execution = decisions.land(
            worktree,
            worktree / config.specs.path,
            task,
            attempt=state.attempts,
            agent=_agent_identity(run.meta),
        )
        landed = f"execution {execution.relative_to(worktree)}"
    except ValueError as exc:
        landed = f"no execution file — {exc}"

    message = _provenance_message(task, state.attempts, digest, run.meta)
    author = f"{_agent_identity(run.meta)} <agents@torve.local>"

    sha = await asyncio.to_thread(
        deps.vcs.commit_all, worktree, message, author, config.vcs.signing_key
    )

    # The credential is resolved by NAME here, at the runner boundary
    # (S-0001/D-13): the value lives only in this process and the subprocess
    # environments the adapters compose.
    token = os.environ.get(config.scm.token_env) if config.scm.token_env else None

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
        if sha and config.scm.open_pr
        else False
    )

    pr_url = ""

    if pushed and config.scm.open_pr:
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

    A trailer of its own (never Torve-Task) keeps this commit from ever
    being mistaken for a landed candidate (S-0010/D-4's grep, the revert leg's
    `landed_shas`)."""

    message = (
        f"torve checkpoint {run.task.id}: attempt {final.attempts} exhausted its budget"
        f"\n\nTorve-Checkpoint: {run.task.id}\nTorve-Attempt: {final.attempts}"
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
) -> AttemptHooks:
    """Bind one dispatch's steps into the hooks the loop drives (S-0046).

    The dispatch settles the regime and refuses what must be refused; this
    picks the attempt leg the role calls for and the review leg the
    configuration calls for, and opens the broker last — after every step
    above it that can still fail (S-0046/D-4).
    """

    run = open_dispatch(
        root,
        task,
        config,
        deps,
        worktree,
        shadow=shadow,
        gates_base=gates_base,
        resume=resume,
    )

    # Revert is mechanical (S-0010/revert-as-a-role, S-0010/D-7): no agent, no attempt
    # sandbox — but the gates still run in theirs and the landing carries
    # the revert's own provenance. Its targets resolve now, so an
    # unresolvable one fails here rather than at attempt three.
    attempt = revert_leg(run) if task.role == "revert" else partial(run_agent_session, run)

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
        gates=partial(judge, run),
        land=partial(land, run),
        review=review,
        close=partial(close_dispatch, run),
        checkpoint=partial(checkpoint, run),
    )


# ....................... #


async def _run_task_async(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    state: RunState,
    resume: bool = False,
) -> RunState:
    worktree = deps.workspace.create(task.id, resolve_base(root, config.base), resume=resume)
    state.worktree = str(worktree)
    state.save()
    hooks = real_hooks(root, task, config, deps, worktree, resume=resume)

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


class BlockedDispatch(RuntimeError):
    """Dispatch refused: another active run's scope intersects this task's
    (S-0006/prevention-beats-ordering — prevention beats ordering). Never a silent wait: the
    cause is in the message and counted in telemetry (S-0006/D-6)."""


# ....................... #


def _scope_overlap(mine: list[str], theirs: list[str]) -> str | None:
    """The first of `theirs` that intersects `mine`, in the same scan order
    the nested loop used — which contended path gets reported must not
    change."""

    from torve.application.planner import globs_intersect

    for mine_glob in mine:
        for their_glob in theirs:
            if globs_intersect([mine_glob], [their_glob]):
                return their_glob

    return None


# ....................... #


def _blocking_overlap(root: Path, task: Task) -> tuple[str, str] | None:
    """(blocking task id, contended path) when an active run's allow-set
    intersects this task's; an empty allow-set is unconstrained and
    contends with everything. An active review claims no fence — it
    writes nothing by construction (S-0005/D-2), so no dispatch is refused
    against it. A review dispatch never reaches the fence at all: the
    front door refuses it (T-0183), so the exemption's assumption holds
    by construction."""

    from torve.gates.context import load_task

    active = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}

    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        if state.task_id == task.id or state.state not in active:
            continue

        contract = root / layout.TORVE_DIR / "tasks" / state.task_id / "contract.yaml"

        if not contract.is_file():
            continue

        other = load_task(contract)

        # An active review claims no fence (S-0005/D-2): its empty allow-set is
        # never "unconstrained", or an unrelated dispatch is refused by a
        # task that will write nothing.
        if other.role == "review":
            continue

        if not task.scope.allow or not other.scope.allow:
            return state.task_id, "unconstrained scope"

        overlap = _scope_overlap(task.scope.allow, other.scope.allow)

        if overlap is not None:
            return state.task_id, overlap

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


def run_task(root: Path, task: Task, config: RunnerConfig, deps: RunDeps) -> RunState:
    # T-0183: review and draft reach this generic path only through a
    # bypass — each has a runner-minted path with a read-only workspace
    # (S-0005/D-2, S-0020/D-2), and this path mounts the workspace writable. Refuse
    # at the door, before state, the fence or any claim exists; implement
    # and revert are the only roles this path implements the isolation
    # contract for.
    check_dispatch_role(task)

    state_path = naming.state_file(root, task.id)
    resume = False

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

    blocked = _blocking_overlap(root, task)

    if blocked is not None:
        blocker, path = blocked

        engine_event(
            root, "blocked_dispatch", {"task": task.id, "blocked_by": blocker, "path": path}
        )

        raise BlockedDispatch(f"blocked_by_overlap: {blocker} on {path}")

    state = RunState(task_id=task.id, path=state_path)
    fact = "torve run: single synchronous claim"
    state.transition(TaskState.CLAIMED, f"{fact} (continuation)" if resume else fact)

    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state, resume=resume))

    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")

        return state
