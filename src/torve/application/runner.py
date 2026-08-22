"""`torve run` — one task, synchronously, exit code is the outcome (D-3.1).

The attempt loop is one durable function executed through the TaskStore
facade (D-5): forze's runner owns the lease heartbeat, cancel observation and
fenced terminal writes; this module owns what the loop *means* — transitions
executed from facts, the poison ceiling checked before dispatch, gates
outside the agent session (D-3; shell gates in a fresh sandbox, a
decision logged in T-0003).

`drive_attempts` is the pure core, driven by hooks: `torve run` supplies the
real sandbox/gates/landing hooks, and the DST simulation supplies simulated
ones — one loop, two harnesses, so the invariants exercise the code that
ships (RFC 0003 §6).
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from forze.application.contracts.durable.function import (
    DurableRunStatus,
    current_durable_run,
)
from forze.application.execution import ExecutionContext
from forze.base.primitives import JsonDict
from pathspec import GitIgnoreSpec

from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Runtime,
    SandboxHandle,
    SandboxSpec,
    Scm,
    StoreFactory,
    Vcs,
    WorkspacePort,
)
from torve.application.runstate import RunState
from torve.application.skills import materialize
from torve.application.taskstore import TaskStore
from torve.application.telemetry import append_record, build_record, config_hash
from torve.base import naming
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.config.runconfig import RunnerConfig, TierConfig, tier_for
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import Task
from torve.gates.context import build_context, resolve_base
from torve.gates.runner import run_gates

# ----------------------- #

# A LOCKED conflict is written to the log as a halted entry; the runner reads
# the fact from the file, the agent cannot cause the transition directly.
# The A-1 YAML log is parsed, not pattern-matched.


@dataclass
class RunDeps:
    workspace: WorkspacePort
    runtime: Runtime
    agent: Agent
    vcs: Vcs
    scm: Scm
    store: StoreFactory


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
                           f"halted divergence entry in the {task.id} execution log")
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
        except Exception as exc:
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
    log = layout.log_file(worktree, task_id)
    if not log.is_file():
        return False
    try:
        document = yaml.safe_load(log.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return False  # an unreadable log is the decisions-reported gate's finding
    if not isinstance(document, dict):
        return False
    entries: Any = cast(dict[str, Any], document).get("entries")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(e, dict) and str(cast(dict[str, Any], e).get("action", "")) == "halted"
        for e in cast(list[object], entries)
    )


def _withhold_never_send(worktree: Path, globs: list[str]) -> dict[Path, bytes]:
    """Lift `never_send` files out of the worktree for the attempt (RFC 0004
    §6b): the sandbox mounts the worktree, so anything present may reach the
    provider. A worktree's `.git` is a host-side pointer the sandbox cannot
    follow, so removal here is removal from the sandbox's world. Contents are
    restored from memory after `sync_out`; an agent edit to a withheld path is
    discarded — the policy protects the file in both directions."""
    if not globs:
        return {}
    spec = GitIgnoreSpec.from_lines(globs)
    withheld: dict[Path, bytes] = {}
    for path in sorted(worktree.rglob("*")):
        rel = path.relative_to(worktree)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if path.is_file() and spec.match_file(str(rel)):
            withheld[path] = path.read_bytes()
            path.unlink()
    return withheld


def _restore_never_send(withheld: dict[Path, bytes]) -> None:
    for path, content in withheld.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _sandbox_auth(tier: TierConfig, worker_slot: int) -> tuple[tuple[str, ...], dict[str, str]]:
    """(env_passthrough, volumes) for the tier's authentication route (RFC
    0004 §1): key names for api and harness, a per-slot volume for
    subscription (D-4.2), nothing for fake."""
    if tier.adapter in ("api", "harness"):
        return tuple(tier.api_key_env), {}
    if tier.adapter == "subscription":
        return (), {f"{tier.auth_volume}-{worker_slot}": tier.auth_mount}
    return (), {}


class _SandboxExecutor:
    """ExecuteOnce over a fresh sandbox, created lazily so gate passes with no
    shell gates cost nothing, destroyed by the caller when the pass ends."""

    def __init__(self, runtime: Runtime, spec: SandboxSpec, workspace: Path) -> None:
        self.runtime, self.spec, self.workspace = runtime, spec, workspace
        self.handle: SandboxHandle | None = None

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
    run_id: str, root: Path, agent_meta: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    """(exit_code, summary, config_hash). Raises on infrastructure failure."""
    manifest_path = layout.gates_file(worktree)
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no gate manifest in the worktree ({manifest_path}) — gates are fail-closed"
        )
    manifest = load_manifest(manifest_path)

    task_file = layout.task_file(worktree, task_id)
    source = layout.task_file(root, task_id)
    if not task_file.is_file() and source.is_file():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, task_file)

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
            base=resolve_base(worktree, config.base),
            task_path=task_file if task_file.is_file() else None,
        )
        ctx.execute = executor
        report = run_gates(ctx)
    finally:
        executor.close()

    digest = config_hash(manifest_path, worktree, config)
    record = build_record(ctx, report, digest, agent=agent_meta)
    append_record(root / manifest.telemetry, record)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)
    return report.exit_code, summary, digest


def _real_hooks(
    root: Path, task: Task, config: RunnerConfig, deps: RunDeps, worktree: Path
) -> AttemptHooks:
    tier = tier_for(config, task.tier)
    # What actually runs, not what the tier configured — an --agent fake
    # override must not masquerade as a model in the telemetry.
    kind = getattr(deps.agent, "kind", tier.adapter)
    real = kind != "fake"
    # Denormalised into every record this run appends (RFC 0004 §6): which
    # adapter and model did the work cannot be reconstructed later.
    agent_meta: dict[str, Any] = {
        "tier": task.tier, "adapter": kind,
        "provider": (tier.provider or None) if real else None,
        "model": (tier.model or None) if real else None,
        "model_version": None, "cost_usd": None, "trace_ref": None,
    }

    async def attempt(state: RunState) -> AgentResult:
        # The runner composes the sandbox's context: the role's skill set is
        # written from package data at dispatch (A-3) — the agent does not
        # "have skills installed", and nothing is checked into the repository.
        materialize(task.role, worktree / ".torve" / "skills", config.skills.sets)
        env_passthrough, volumes = (
            _sandbox_auth(tier, config.worker_slot) if real else ((), {})
        )
        spec = SandboxSpec(
            name=naming.sandbox_name(task.id, state.run_id) + f"-a{state.attempts}",
            image=config.runtime.image,
            labels=naming.labels(task.id, state.run_id),
            timeout_s=config.runtime.sandbox_timeout,
            env_passthrough=env_passthrough,
            volumes=volumes,
        )
        withheld = _withhold_never_send(worktree, config.providers.never_send)
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
            agent_meta.update(model_version=result.model_version,
                              cost_usd=result.cost_usd, trace_ref=result.trace_ref)
            return result
        finally:
            # Synchronous on purpose: a cancelled task cannot await its own
            # cleanup, and the sandbox must die regardless (D-4).
            deps.runtime.destroy(handle)
            _restore_never_send(withheld)
            state.sandbox_id = None
            state.save()

    def halted() -> bool:
        return _log_has_halted_entry(worktree, task.id)

    async def gates(state: RunState) -> tuple[int, str, str]:
        return await asyncio.to_thread(
            _run_gates_in_worktree, worktree, task.id, config, deps.runtime,
            state.run_id, root, agent_meta,
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
    worktree = deps.workspace.create(task.id, resolve_base(root, config.base))
    state.worktree = str(worktree)
    state.save()
    hooks = _real_hooks(root, task, config, deps, worktree)

    async def body(_fctx: ExecutionContext, _input_json: JsonDict | None) -> JsonDict:
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

    store = await deps.store(config.store)
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
