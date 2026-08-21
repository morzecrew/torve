"""`torve run` — one task, synchronously, exit code is the outcome (D-3.1).

The loop: claim -> worktree -> sandbox -> agent -> gates -> ready | escalated.
Transitions are executed here, from facts (exit codes, gate outcomes, log
entries); the agent reports observations and never causes a transition.

Gates execute outside the agent session (D-3): pure gates run in the engine,
and shell gates run in a *fresh* sandbox from the same image — never in the
sandbox the agent used, where a staged PATH shim could fake a test runner
(logged decision, logs/T-0003.md).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from torve import naming
from torve.context import _resolve_base, build_context
from torve.domain import EscalationReason, TaskState
from torve.manifest import config_hash, load_manifest
from torve.models import Task
from torve.ports import Agent, AgentContext, Runtime, SandboxSpec, Scm, Vcs, WorkspacePort
from torve.runconfig import RunnerConfig
from torve.runner import run_gates
from torve.runstate import RunState
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


def _log_has_halted_entry(worktree: Path, task_id: str) -> bool:
    log = worktree / "logs" / f"{task_id}.md"
    return log.is_file() and bool(HALTED_ENTRY.search(log.read_text(encoding="utf-8")))


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

    worktree = deps.workspace.create(task.id, _resolve_base(root, config.base))
    state.worktree = str(worktree)
    state.save()

    ceiling = config.poison_ceiling
    iterations = task.budget.iterations
    try:
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
                result = deps.agent.run(AgentContext(
                    task=task, attempt=state.attempts, workspace=worktree,
                    handle=handle, runtime=deps.runtime, workdir=spec.workdir,
                    timeout_s=config.runtime.agent_timeout,
                ))
                deps.runtime.sync_out(handle, worktree)
            finally:
                deps.runtime.destroy(handle)
                state.sandbox_id = None
                state.save()

            if _log_has_halted_entry(worktree, task.id):
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
                exit_code, summary, digest = _run_gates_in_worktree(
                    worktree, task.id, config, deps.runtime, state.run_id, root
                )
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
            message = (f"torve({task.id}): attempt {state.attempts} green\n\n"
                       f"Torve-Task: {task.id}\nTorve-Attempt: {state.attempts}\n"
                       f"Torve-Config: {digest}")
            sha = deps.vcs.commit_all(worktree, message)
            pushed = deps.vcs.push(worktree, naming.branch(task.id)) if sha else False
            pr_url = ""
            if pushed and config.scm.open_pr:
                pr_url = deps.scm.open_pr(worktree, naming.branch(task.id),
                                          f"{task.id}: {task.rfc or 'task'}", message)
            fact = (f"committed {sha[:10]}" if sha else "nothing to commit")
            fact += f"; pushed={pushed}" + (f"; pr={pr_url}" if pr_url else "; pr deferred")
            state.transition(TaskState.READY, fact)
            state.save()
            return state
    except KeyboardInterrupt:
        state.escalate(EscalationReason.KILLED, "interrupted by operator")
        return state
