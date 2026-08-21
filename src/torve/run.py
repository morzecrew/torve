"""`torve run` — one task, synchronously, exit code is the outcome (D-3.1).

The attempt loop is one durable function executed through the TaskStore
facade (D-5): forze's runner owns the lease heartbeat, cancel observation and
fenced terminal writes; this module owns what the loop *means* — transitions
executed from facts, the poison ceiling checked before dispatch, gates
outside the agent session (D-3; shell gates in a fresh sandbox, logged in
logs/T-0003.md).

`drive_attempts` is the pure core, driven by hooks: `torve run` supplies the
real sandbox/gates/landing hooks, and the DST simulation supplies simulated
ones — one loop, two harnesses, so the invariants exercise the code that
ships (RFC 0003 §6).
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from forze.application.contracts.durable.function import (
    DurableRunStatus,
    current_durable_run,
)

from torve import naming
from torve.adapters.durable_store import open_store
from torve.context import _resolve_base, build_context
from torve.domain import EscalationReason, TaskState
from torve.manifest import config_hash, load_manifest
from torve.models import Task
from torve.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Runtime,
    SandboxSpec,
    Scm,
    Vcs,
    WorkspacePort,
)
from torve.runconfig import RunnerConfig
from torve.runner import run_gates
from torve.runstate import RunState
from torve.taskstore import TaskStore
from torve.telemetry import append_record, build_record

# A LOCKED conflict is written to the log as a halted entry; the runner reads
# the fact from the file, the agent cannot cause the transition directly.
HALTED_ENTRY = re.compile(r"^action:[ \t]*halted[ \t]*$", re.M)


@dataclass
class RunDeps:
    workspace: WorkspacePort
    runtime: Runtime
    agent: Agent
    vcs: Vcs
    scm: Scm


@dataclass
class AttemptHooks:
    """What one attempt does, one gate pass does, and one landing does. The
    loop below owns the states and facts; the hooks own the mechanism."""

    attempt: Callable[[RunState], Awaitable[AgentResult]]
    halted: Callable[[], bool]  # locked-conflict detection after an attempt
    gates: Callable[[RunState], Awaitable[tuple[int, str, str]]]  # exit, summary, config hash
    land: Callable[[RunState, str], Awaitable[str]]  # returns the recorded fact


async def drive_attempts(
    state: RunState, task: Task, config: RunnerConfig, hooks: AttemptHooks
) -> RunState:
    ceiling = config.poison_ceiling
    iterations = task.budget.iterations
    while True:
        # Poison ceiling is checked before dispatch, never after (RFC 0001 §4).
        if state.attempts >= ceiling:
            state.escalate(EscalationReason.POISON_CEILING,
                           f"{state.attempts} attempts, ceiling {ceiling}")
            return state
        if iterations is not None and state.attempts >= iterations:
            state.escalate(EscalationReason.BUDGET_EXHAUSTED,
                           f"{state.attempts} attempts, budget {iterations}")
            return state

        state.transition(TaskState.RUNNING, f"attempt {state.attempts + 1} dispatched")
        state.save()
        result = await hooks.attempt(state)

        if hooks.halted():
            # Terminal by design, not an error: the one case where a task
            # stops on working code (RFC 0001 §4).
            state.escalate(EscalationReason.LOCKED_CONFLICT,
                           f"halted divergence entry in logs/{task.id}.md")
            return state

        if result.timed_out or result.exit_code != 0:
            fact = ("agent hit the hard timeout" if result.timed_out
                    else f"agent exited {result.exit_code}") + "; gates not run"
            state.transition(TaskState.GATED, fact)
            state.save()
            continue

        state.transition(TaskState.GATED, "agent exited 0; gates running")
        state.save()
        try:
            exit_code, summary, digest = await hooks.gates(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the gates broke, not the code
            state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))
            return state

        if exit_code != 0:
            state.history.append({"at": state.heartbeat, "from": str(state.state),
                                  "to": str(state.state), "fact": f"gates red: {summary}"})
            state.save()
            continue

        state.transition(TaskState.REVIEWED,
                         "gates green; review not configured (RFC 0005 pending)")
        fact = await hooks.land(state, digest)
        state.transition(TaskState.READY, fact)
        state.save()
        return state


def _log_has_halted_entry(worktree: Path, task_id: str) -> bool:
    log = worktree / "logs" / f"{task_id}.md"
    return log.is_file() and bool(HALTED_ENTRY.search(log.read_text(encoding="utf-8")))


class _SandboxExecutor:
    """ExecuteOnce over a fresh sandbox, created lazily so gate passes with no
    shell gates cost nothing, destroyed by the caller when the pass ends."""

    def __init__(self, runtime: Runtime, spec: SandboxSpec, workspace: Path) -> None:
        self.runtime, self.spec, self.workspace = runtime, spec, workspace
        self.handle = None

    def __call__(self, command: str, timeout: float) -> tuple[int | None, str]:
        if self.handle is None:
            self.handle = self.runtime.create(self.spec, self.workspace)
        result = self.runtime.exec(self.handle, command, timeout)
        return result.exit_code, result.output

    def close(self) -> None:
        if self.handle is not None:
            self.runtime.sync_out(self.handle, self.workspace)
            self.runtime.destroy(self.handle)
            self.handle = None


def _run_gates_in_worktree(
    worktree: Path, task_id: str, config: RunnerConfig, runtime: Runtime,
    run_id: str, root: Path,
) -> tuple[int, str, str]:
    """(exit_code, summary, config_hash). Raises on infrastructure failure."""
    manifest_path = worktree / "gates.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError("no gates.yaml in the worktree — gates are fail-closed")
    manifest = load_manifest(manifest_path)

    task_file = worktree / "tasks" / f"{task_id}.yaml"
    if not task_file.is_file() and (root / "tasks" / f"{task_id}.yaml").is_file():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / "tasks" / f"{task_id}.yaml", task_file)

    executor = _SandboxExecutor(
        runtime,
        SandboxSpec(
            name=naming.sandbox_name(task_id, run_id) + "-gates",
            image=config.runtime.image,
            labels=naming.labels(task_id, run_id),
            timeout_s=config.runtime.sandbox_timeout,
        ),
        worktree,
    )
    try:
        ctx = build_context(
            worktree, manifest,
            base=_resolve_base(worktree, config.base),
            task_path=task_file if task_file.is_file() else None,
        )
        ctx.execute = executor
        report = run_gates(ctx)
    finally:
        executor.close()

    digest = config_hash(manifest_path, worktree)
    record = build_record(ctx, report, digest)
    append_record(root / manifest.telemetry, record)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)
    return report.exit_code, summary, digest


def _real_hooks(
    root: Path, task: Task, config: RunnerConfig, deps: RunDeps, worktree: Path
) -> AttemptHooks:
    async def attempt(state: RunState) -> AgentResult:
        spec = SandboxSpec(
            name=naming.sandbox_name(task.id, state.run_id) + f"-a{state.attempts}",
            image=config.runtime.image,
            labels=naming.labels(task.id, state.run_id),
            timeout_s=config.runtime.sandbox_timeout,
        )
        handle = deps.runtime.create(spec, worktree)
        state.sandbox_id = handle.id
        state.save()
        try:
            result = await asyncio.to_thread(deps.agent.run, AgentContext(
                task=task, attempt=state.attempts, workspace=worktree,
                handle=handle, runtime=deps.runtime, workdir=spec.workdir,
                timeout_s=config.runtime.agent_timeout,
            ))
            deps.runtime.sync_out(handle, worktree)
            return result
        finally:
            # Synchronous on purpose: a cancelled task cannot await its own
            # cleanup, and the sandbox must die regardless (D-4).
            deps.runtime.destroy(handle)
            state.sandbox_id = None
            state.save()

    def halted() -> bool:
        return _log_has_halted_entry(worktree, task.id)

    async def gates(state: RunState) -> tuple[int, str, str]:
        return await asyncio.to_thread(
            _run_gates_in_worktree, worktree, task.id, config, deps.runtime,
            state.run_id, root,
        )

    async def land(state: RunState, digest: str) -> str:
        message = (f"torve({task.id}): attempt {state.attempts} green\n\n"
                   f"Torve-Task: {task.id}\nTorve-Attempt: {state.attempts}\n"
                   f"Torve-Config: {digest}")
        sha = await asyncio.to_thread(deps.vcs.commit_all, worktree, message)
        pushed = deps.vcs.push(worktree, naming.branch(task.id)) if sha else False
        pr_url = ""
        if pushed and config.scm.open_pr:
            pr_url = deps.scm.open_pr(worktree, naming.branch(task.id),
                                      f"{task.id}: {task.rfc or 'task'}", message)
        fact = (f"committed {sha[:10]}" if sha else "nothing to commit")
        fact += f"; pushed={pushed}" + (f"; pr={pr_url}" if pr_url else "; pr deferred")
        return fact

    return AttemptHooks(attempt=attempt, halted=halted, gates=gates, land=land)


async def _run_task_async(
    root: Path, task: Task, config: RunnerConfig, deps: RunDeps, state: RunState
) -> RunState:
    worktree = deps.workspace.create(task.id, _resolve_base(root, config.base))
    state.worktree = str(worktree)
    state.save()
    hooks = _real_hooks(root, task, config, deps, worktree)

    async def body(_fctx, _input_json):
        bound = current_durable_run()
        if bound is not None:
            state.durable_run_id = bound.run_id
            state.save()
        try:
            final = await drive_attempts(state, task, config, hooks)
        except asyncio.CancelledError:
            if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
                state.escalate(EscalationReason.KILLED,
                               "cancellation observed via the lease heartbeat")
            raise
        return {
            "task_id": task.id,
            "state": str(final.state),
            "attempts": final.attempts,
            "escalation": final.escalation.reason if final.escalation else None,
        }

    store = await open_store(config.store)
    taskstore = TaskStore(store, config.store)
    taskstore.register(body)
    record = await taskstore.run_now(
        {"task_id": task.id, "engine_run_id": state.run_id},
        idempotency_key=f"{task.id}:{state.run_id}",
    )

    if (record.status is DurableRunStatus.TIMED_OUT and state.escalation is None
            and state.state not in (TaskState.READY, TaskState.ABANDONED)):
        state.escalate(EscalationReason.BUDGET_EXHAUSTED,
                       "max run duration reached (store watchdog)")
    if record.status is DurableRunStatus.FAILED:
        raise RuntimeError(f"durable run failed: {record.error}")
    return state


def run_task(root: Path, task: Task, config: RunnerConfig, deps: RunDeps) -> RunState:
    state_path = naming.state_file(root, task.id)
    if state_path.exists():
        previous = RunState.load(state_path)
        if previous.state not in (TaskState.READY, TaskState.ABANDONED):
            raise RuntimeError(
                f"{task.id} has an existing run in state {previous.state} "
                f"(run {previous.run_id[:8]}); triage it or `torve reap` first"
            )

    state = RunState(task_id=task.id, path=state_path)
    state.transition(TaskState.CLAIMED, "torve run: single synchronous claim")
    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state))
    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")
        return state
