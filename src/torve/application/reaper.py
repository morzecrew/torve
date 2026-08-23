"""`torve reap` — convention-driven, not tracked (RFC 0003 §4.2): enumerate by
label and prefix, cross-reference live runs, destroy anything without one.

With a Postgres store the lease is the liveness authority (D-3.10 retired):
`claim_abandoned` decides expiry — the store owns the lease clock — and a
reclaimed run is landed `lease_expired` under its own fence, the same verdict
recovery hands out. The engine's state file is escalated on the D-30 edge.

With the in-process mock store there is nothing durable to consult across
processes (D-3.6: Postgres for real runs), so the v1 heartbeat heuristic
stays as the fallback. Worktrees are removed only for terminal runs either
way; a crashed run's worktree is triage evidence, not garbage.

Containers a socket-mode sandbox starts (RFC 0017 §2a, D-17.11) carry no
torve labels and are invisible here by design: cleanup-by-convention must
not pretend to cover what it cannot see. The battery that starts them owns
their lifecycle, exactly as it does on an operator's machine.

A terminal run's state file and trace logs are swept with its worktree — the
durable record of what happened is the task log and telemetry, and a state
file that outlives the sweep shows up in `torve status` forever. What remains
in `.wt/` after a reap is only live and escalated runs. The state sweep is
driven by the state files themselves, not the worktree listing, so a footprint
whose worktree is already gone is still collected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from torve.application.ports import Runtime, StoreFactory, WorkspacePort
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #

ACTIVE = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})
TERMINAL = frozenset({TaskState.READY, TaskState.ABANDONED})


@dataclass
class ReapReport:
    sandboxes_destroyed: list[str] = field(default_factory=list)
    worktrees_removed: list[str] = field(default_factory=list)
    runs_expired: list[str] = field(default_factory=list)
    states_removed: list[str] = field(default_factory=list)


def _sweep_worktrees(
    workspace: WorkspacePort, by_task: dict[str, RunState], report: ReapReport,
    dry_run: bool,
) -> None:
    for task_id, _path in workspace.list_worktrees():
        state = by_task.get(task_id)
        if state is None or state.state in TERMINAL:
            # No state file at all is pure convention debris; terminal runs
            # left their commits on the task branch.
            if not dry_run:
                workspace.remove(task_id)
            report.worktrees_removed.append(task_id)


def _sweep_states(
    root: Path, states: list[RunState], report: ReapReport, dry_run: bool,
) -> None:
    """A terminal run's remaining footprint: state file and trace logs, named
    by convention (D-3.4) beside the worktree. Driven by the state files, not
    the worktree listing — the worktree may already be gone."""
    wt_dir = root / naming.WORKTREE_DIR
    for state in states:
        if state.state not in TERMINAL:
            continue
        if not dry_run:
            for trace in sorted(wt_dir.glob(f"{state.task_id}.a*.trace.log")):
                trace.unlink()
            state.path.unlink(missing_ok=True)
        report.states_removed.append(state.task_id)


def _escalate_if_active(state: RunState, reason: EscalationReason, detail: str) -> bool:
    if state.state in ACTIVE:
        state.escalate(reason, detail)
        return True
    return False


def _heartbeat_reap(
    root: Path, config: RunnerConfig, runtime: Runtime, workspace: WorkspacePort,
    force: bool, dry_run: bool,
) -> ReapReport:
    report = ReapReport()
    states = RunState.load_all(root / naming.WORKTREE_DIR)

    for state in states:
        stale = state.heartbeat_age_s() > config.reap.stale_after
        if state.state in ACTIVE and (stale or force):
            if not dry_run:
                state.escalate(EscalationReason.LEASE_EXPIRED,
                               f"heartbeat stale ({state.heartbeat_age_s():.0f}s) at reap")
            report.runs_expired.append(state.task_id)

    live_runs = {s.run_id for s in states
                 if s.state in ACTIVE and s.heartbeat_age_s() <= config.reap.stale_after}
    for sandbox in runtime.list_torve_sandboxes():
        if sandbox.labels.get(naming.LABEL_RUN) not in live_runs:
            if not dry_run:
                runtime.destroy_by_id(sandbox.id)
            report.sandboxes_destroyed.append(sandbox.name)

    _sweep_worktrees(workspace, {s.task_id: s for s in states}, report, dry_run)
    _sweep_states(root, states, report, dry_run)
    return report


async def _durable_reap(
    root: Path, config: RunnerConfig, runtime: Runtime, workspace: WorkspacePort,
    force: bool, dry_run: bool, store: StoreFactory,
) -> ReapReport:
    from torve.application.taskstore import TaskStore

    report = ReapReport()
    states = RunState.load_all(root / naming.WORKTREE_DIR)
    by_task = {s.task_id: s for s in states}
    by_engine_run = {s.run_id: s for s in states}

    taskstore = TaskStore(await store(config.store), config.store)
    # A dry run cannot predict lease expiry without claiming — claim_abandoned
    # IS the mutation — so runs_expired stays empty and only the read-only
    # sandbox/worktree candidates are reported.
    expired = [] if dry_run else await taskstore.expire_abandoned()
    if force and not dry_run:
        expired += await taskstore.force_fail_running()

    reason = EscalationReason.KILLED if force else EscalationReason.LEASE_EXPIRED
    for record in expired:
        engine_run = (record.input_json or {}).get("engine_run_id", "")
        state = by_engine_run.get(str(engine_run))
        if state is not None and _escalate_if_active(
            state, reason, f"durable run {record.run_id[:8]} reclaimed at reap"
        ):
            report.runs_expired.append(state.task_id)

    live = await taskstore.live_records()
    live_engine_runs = {str((r.input_json or {}).get("engine_run_id", "")) for r in live}
    for sandbox in runtime.list_torve_sandboxes():
        if sandbox.labels.get(naming.LABEL_RUN) not in live_engine_runs:
            if not dry_run:
                runtime.destroy_by_id(sandbox.id)
            report.sandboxes_destroyed.append(sandbox.name)

    _sweep_worktrees(workspace, by_task, report, dry_run)
    _sweep_states(root, states, report, dry_run)
    return report


def reap(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    workspace: WorkspacePort,
    force: bool = False,
    dry_run: bool = False,
    store: StoreFactory | None = None,
) -> ReapReport:
    if config.store.adapter == "postgres":
        if store is None:
            raise RuntimeError("a postgres reap needs a store factory injected by the caller")
        return asyncio.run(_durable_reap(root, config, runtime, workspace, force, dry_run,
                                         store))
    return _heartbeat_reap(root, config, runtime, workspace, force, dry_run)
