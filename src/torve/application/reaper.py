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

A terminal run's state file is swept with its worktree — the durable
record of what happened is the task log and telemetry, and a state
file that outlives the sweep shows up in `torve status` forever. What
remains in `.wt/` after a reap is only live and escalated runs. The
state sweep is driven by the state files themselves, not the worktree
listing, so a footprint whose worktree is already gone is still
collected. Traces are gone from that sweep (D-39.1): they live in the
retention-capped store under `.torve/traces/`, and the only deletion
there is this reaper's retention pass, enforcing the `traces:` bounds
oldest-first (D-39.3). The store is local — a sweep deletes files on
this host and never moves, copies or sends one (D-39.2).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from torve.application.ports import Runtime, StoreFactory, WorkspacePort
from torve.application.runstate import RunState
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig
from torve.domain.states import EscalationReason, TaskState

# Answers "has this task landed on the base?" — wired by the caller from
# the vcs (D-19.10); None means the reaper cannot know and keeps.
LandedOracle = Callable[[str], bool]

# ----------------------- #

ACTIVE = frozenset({TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED})
TERMINAL = frozenset({TaskState.READY, TaskState.ABANDONED})


# ....................... #


@dataclass
class ReapReport:
    sandboxes_destroyed: list[str] = field(default_factory=list)
    worktrees_removed: list[str] = field(default_factory=list)
    runs_expired: list[str] = field(default_factory=list)
    states_removed: list[str] = field(default_factory=list)
    traces_removed: list[str] = field(default_factory=list)


# ....................... #


def _sweep_worktrees(
    workspace: WorkspacePort,
    by_task: dict[str, RunState],
    report: ReapReport,
    dry_run: bool,
) -> None:
    for name, _path in workspace.list_worktrees():
        # An intake/decompose drafting run's worktree carries a distinct
        # suffix (naming.INTAKE_SUFFIX) from the state file it is named
        # after — unstripped, every live drafting run reads as convention
        # debris and a concurrent tick destroys it mid-run.
        task_id = name.removesuffix(naming.INTAKE_SUFFIX)
        state = by_task.get(task_id)

        if state is None or state.state in TERMINAL:
            # No state file at all is pure convention debris; terminal runs
            # left their commits on the task branch.
            if not dry_run:
                workspace.remove(name)

            report.worktrees_removed.append(name)


# ....................... #


def _lane_input(root: Path, state: RunState, landed: LandedOracle | None) -> bool:
    """D-19.10 (A-28, narrowing D-3.23): a READY implement or revert run
    whose task has not landed on the base is the lane's input, not debris —
    its state file survives the sweep. Without a landed oracle the answer
    is conservative: keep. Review-role READY states never land and stay
    sweepable; so does a READY state with no contract to land. A READY
    draft run is kept unconditionally (RFC 0020, D-20.10): its landing is
    adoption, which disposes of the state itself — the lab's first live
    drafting run was swept one tick after going green, orphaning the
    adoption it awaited."""

    if state.state is not TaskState.READY:
        return False

    contract = layout.task_file(root, state.task_id)

    if not contract.is_file():
        return False

    try:
        from torve.gates.context import load_task

        role = load_task(contract).role

    except ValueError:
        return False

    if role == "draft":
        return True

    if role not in ("implement", "revert"):
        return False

    return landed is None or not landed(state.task_id)


# ....................... #


def _sweep_states(
    root: Path,
    states: list[RunState],
    report: ReapReport,
    dry_run: bool,
    landed: LandedOracle | None = None,
    escalated: bool = False,
) -> None:
    """A terminal run's remaining footprint: the state file, named by
    convention (D-3.4) beside the worktree. Driven by the state files, not
    the worktree listing — the worktree may already be gone. Traces are not
    part of this sweep (D-39.1): they live in the durable store, and the
    retention pass below is their only remover."""

    for state in states:
        collectable = state.state in TERMINAL or (
            # --escalated (A-70): the operator's explicit triage-discard for
            # an escalation already dealt with outside the state machine —
            # an infra failure, a hand landing. Never swept by default: an
            # escalation exists to be looked at.
            escalated and str(state.state) == "escalated"
        )

        if not collectable:
            continue

        if _lane_input(root, state, landed):
            continue

        if not dry_run:
            state.path.unlink(missing_ok=True)

        report.states_removed.append(state.task_id)


# ....................... #


def _retain_traces(root: Path, config: RunnerConfig, report: ReapReport, dry_run: bool) -> None:
    """The trace store's retention (D-39.3): past either bound of the
    `traces:` block — `keep_days` of age, `max_mb` of size — the store sheds
    oldest first. This is the store's only remover (D-39.1): the terminal
    sweep leaves traces to this pass. Age is measured by modification time;
    a trace deleted here while its `trace_ref` still names it is the defined
    outcome of retention, which every reader renders as a reaped pointer,
    never an error. The store is local: this deletes, and nothing else —
    no copy, no upload, no transmission of a trace (D-39.2)."""

    store = naming.traces_dir(root)

    stamped: list[tuple[float, int, Path]] = []

    for trace in store.glob("*.trace.log"):
        if not trace.is_file():
            continue  # only the helpers' own files are the store's contents

        try:
            stat = trace.stat()
        except OSError:
            continue  # vanished between glob and stat: already gone is retained

        stamped.append((stat.st_mtime, stat.st_size, trace))

    stamped.sort(key=lambda entry: entry[0])  # oldest first, both bounds

    horizon = time.time() - config.traces.keep_days * 86_400
    budget = config.traces.max_mb * 1_048_576
    total = sum(size for _mtime, size, _trace in stamped)

    for mtime, size, trace in stamped:
        if mtime >= horizon and total <= budget:
            break  # this one and everything after it (younger) is inside both bounds

        total -= size

        if not dry_run:
            trace.unlink(missing_ok=True)

        report.traces_removed.append(trace.name)


# ....................... #


def _escalate_if_active(state: RunState, reason: EscalationReason, detail: str) -> bool:
    if state.state in ACTIVE:
        state.escalate(reason, detail)
        return True

    return False


# ....................... #


def _heartbeat_reap(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    workspace: WorkspacePort,
    force: bool,
    dry_run: bool,
    landed: LandedOracle | None = None,
    escalated: bool = False,
) -> ReapReport:
    report = ReapReport()
    states = RunState.load_all(root / naming.WORKTREE_DIR)

    for state in states:
        stale = state.heartbeat_age_s() > config.reap.stale_after

        if state.state in ACTIVE and (stale or force):
            if not dry_run:
                state.escalate(
                    EscalationReason.LEASE_EXPIRED,
                    f"heartbeat stale ({state.heartbeat_age_s():.0f}s) at reap",
                )

            report.runs_expired.append(state.task_id)

    live_runs = {
        s.run_id
        for s in states
        if s.state in ACTIVE and s.heartbeat_age_s() <= config.reap.stale_after
    }

    own = naming.root_key(root)

    for sandbox in runtime.list_torve_sandboxes():
        # The reap keeps to its root (D-3.25, A-38): another engine's
        # sandbox on the shared daemon is not ours to judge; an unlabelled
        # one is a pre-A-38 stray and stays reapable by anyone.
        owner = sandbox.labels.get(naming.LABEL_ROOT)

        if owner is not None and owner != own:
            continue

        if sandbox.labels.get(naming.LABEL_RUN) not in live_runs:
            if not dry_run:
                runtime.destroy_by_id(sandbox.id)

            report.sandboxes_destroyed.append(sandbox.name)

    _sweep_worktrees(workspace, {s.task_id: s for s in states}, report, dry_run)
    _sweep_states(root, states, report, dry_run, landed, escalated=escalated)
    _retain_traces(root, config, report, dry_run)

    return report


# ....................... #


async def _durable_reap(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    workspace: WorkspacePort,
    force: bool,
    dry_run: bool,
    store: StoreFactory,
    landed: LandedOracle | None = None,
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

    # Shadow liveness is host truth (D-4.18, A-57): replays register no
    # durable record by design, so a run named by a host state file with a
    # fresh heartbeat is live too — a crashed shadow's heartbeat goes stale
    # and it reaps; an orphan with no state file reaps immediately.
    live_engine_runs |= {
        s.run_id
        for s in states
        if s.state in ACTIVE and s.heartbeat_age_s() <= config.reap.stale_after
    }

    # The same liveness set settles escalation, not only sandbox teardown
    # (one shared check, no guard per caller): an ACTIVE state whose run
    # never registered a durable record at all — a stale shadow replay, or
    # a task the engine minted and claimed at intake/decompose whose
    # drafting run then died before it wrote anything further — is
    # invisible to `expire_abandoned` above and would otherwise sit in .wt
    # forever, blocking dispatch through the overlap gate. Reported even on
    # a dry run: unlike lease expiry, membership in `live_engine_runs` is
    # read-only, nothing here needs claiming to predict.
    for state in states:
        if state.state in ACTIVE and state.run_id not in live_engine_runs:
            if not dry_run:
                state.escalate(reason, f"no live engine run for {state.run_id[:8]} at reap")

            report.runs_expired.append(state.task_id)

    own = naming.root_key(root)

    for sandbox in runtime.list_torve_sandboxes():
        # D-3.25 (A-38): same root fence as the local-regime reap.
        owner = sandbox.labels.get(naming.LABEL_ROOT)

        if owner is not None and owner != own:
            continue

        if sandbox.labels.get(naming.LABEL_RUN) not in live_engine_runs:
            if not dry_run:
                runtime.destroy_by_id(sandbox.id)

            report.sandboxes_destroyed.append(sandbox.name)

    _sweep_worktrees(workspace, by_task, report, dry_run)
    _sweep_states(root, states, report, dry_run, landed)
    _retain_traces(root, config, report, dry_run)

    return report


# ....................... #


def reap(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    workspace: WorkspacePort,
    force: bool = False,
    dry_run: bool = False,
    store: StoreFactory | None = None,
    landed: LandedOracle | None = None,
    escalated: bool = False,
) -> ReapReport:
    if config.store.adapter == "postgres":
        if store is None:
            raise RuntimeError("a postgres reap needs a store factory injected by the caller")

        return asyncio.run(
            _durable_reap(root, config, runtime, workspace, force, dry_run, store, landed)
        )

    return _heartbeat_reap(
        root, config, runtime, workspace, force, dry_run, landed, escalated=escalated
    )
