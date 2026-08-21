"""`torve reap` — convention-driven, not tracked (RFC 0003 §4.2): enumerate by
label and prefix, cross-reference live runs, destroy anything without one.
This survives a crash of the runner itself, which PID tracking does not.

Liveness in v1 is the state-file heartbeat (real leases arrive in T-0004): a
non-terminal run whose heartbeat went stale is an orphan. Its sandbox is
destroyed and the run is escalated as `lease_expired` — the same verdict the
durable store's recovery will hand out later. Worktrees are removed only for
terminal runs; a crashed run's worktree may hold un-committed agent output,
which is triage evidence, not garbage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from torve import naming
from torve.domain import EscalationReason, TaskState
from torve.ports import Runtime, WorkspacePort
from torve.runconfig import RunnerConfig
from torve.runstate import RunState

ACTIVE = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})


@dataclass
class ReapReport:
    sandboxes_destroyed: list[str] = field(default_factory=list)
    worktrees_removed: list[str] = field(default_factory=list)
    runs_expired: list[str] = field(default_factory=list)


def reap(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    workspace: WorkspacePort,
    force: bool = False,
) -> ReapReport:
    report = ReapReport()
    states = RunState.load_all(root / naming.WORKTREE_DIR)
    by_task = {s.task_id: s for s in states}

    # Expire orphaned runs first, so the sandbox sweep below sees them dead.
    for state in states:
        stale = state.heartbeat_age_s() > config.reap.stale_after
        if state.state in ACTIVE and (stale or force):
            state.escalate(EscalationReason.LEASE_EXPIRED,
                           f"heartbeat stale ({state.heartbeat_age_s():.0f}s) at reap")
            report.runs_expired.append(state.task_id)

    live_runs = {s.run_id for s in states
                 if s.state in ACTIVE and s.heartbeat_age_s() <= config.reap.stale_after}

    for sandbox in runtime.list_torve_sandboxes():
        if sandbox.labels.get(naming.LABEL_RUN) not in live_runs:
            runtime.destroy_by_id(sandbox.id)
            report.sandboxes_destroyed.append(sandbox.name)

    for task_id, _path in workspace.list_worktrees():
        state = by_task.get(task_id)
        if state is not None and state.state in (TaskState.READY, TaskState.ABANDONED):
            workspace.remove(task_id)
            report.worktrees_removed.append(task_id)
        elif state is None:
            # A worktree with no state file at all is pure convention debris.
            workspace.remove(task_id)
            report.worktrees_removed.append(task_id)

    return report
