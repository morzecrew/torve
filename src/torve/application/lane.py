"""The serialized merge lane (RFC 0006 §1, D-6.1): ready is a lane, not a
set. One candidate at a time — a task branch whose base has not moved lands
exactly as it was measured (a rebase that changes nothing buys no new
signal); one whose base moved is rebased in a disposable worktree and its
gate battery re-runs over the rebased tree before landing, which is review
freshness against current head (D-6.3) in the local regime, where the
battery is current-head CI. A conflicted rebase aborts and escalates the
run — `ready -> escalated`, reason `merge_conflict` (charter A-26, D-6.10),
the one edge out of ready and the lane's alone — so the escalation queue's
age starts counting the moment a landing fails. The branch stays exactly
as measured; the engine never resolves a conflict, and the lane moves on
to the next candidate. Resolution is the standard escalated fork:
re-queue to re-run against the moved base, or abandon when a human
landed the work by hand.

The operator's invocation is the recorded approval; each outcome rides the
telemetry stream as an engine event (D-6.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from torve.application.ports import CiStatus, LaneVcs
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config import layout
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


@dataclass
class LaneResult:
    task: str
    branch: str
    action: str  # landed | conflict | gates red | already landed | no branch | would *
    detail: str = ""
    sha: str = ""

    @property
    def landed(self) -> bool:
        return self.action == "landed"


def ready_candidates(root: Path) -> list[RunState]:
    states = RunState.load_all(root / naming.WORKTREE_DIR)
    return sorted((s for s in states if s.state is TaskState.READY),
                  key=lambda s: s.task_id)


def _regate(workdir: Path, base_ref: str, task_id: str) -> tuple[int, str]:
    """The full battery over the rebased tree, exactly as `torve gates run`
    would judge it — fail-closed on a missing manifest."""
    from torve.gates.context import build_context, load_task, resolve_base
    from torve.gates.runner import run_gates

    manifest_path = layout.gates_file(workdir)
    if not manifest_path.is_file():
        return 1, "no gate manifest in the rebased tree"
    from torve.config.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    task_path = layout.task_file(workdir, task_id)
    ctx = build_context(
        workdir, manifest,
        base=resolve_base(workdir, base_ref),
        task_path=task_path if task_path.is_file() else None,
    )
    if task_path.is_file():
        ctx.task = load_task(task_path)
    report = run_gates(ctx)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)
    return report.exit_code, summary


def process_lane(
    root: Path, vcs: LaneVcs, dry_run: bool = False, only: str | None = None,
    ci: CiStatus | None = None,
) -> list[LaneResult]:
    base = vcs.current_branch(root)
    if not dry_run and not vcs.is_clean(root):
        raise RuntimeError(
            f"the working tree on {base!r} is not clean — the lane fast-forwards "
            "the checkout, commit or stash first"
        )
    approver = vcs.approver(root)
    results: list[LaneResult] = []
    for state in ready_candidates(root):
        if only is not None and state.task_id != only:
            continue
        task_id = state.task_id
        branch = naming.branch(task_id)
        branch_tip = vcs.tip(root, branch)
        if branch_tip is None:
            results.append(LaneResult(task_id, branch, "no branch",
                                      "ran outside the engine or already cleaned up"))
            continue
        if vcs.is_ancestor(root, branch_tip, base):
            results.append(LaneResult(task_id, branch, "already landed", sha=branch_tip))
            continue

        if ci is not None and not dry_run:
            # ci: green_on_current_head (RFC 0006 §3): the remote's verdict
            # for the tip the remote actually saw. Only "success" lands; a
            # rebased tree is additionally judged by the local battery below.
            verdict = ci.conclusion(branch_tip)
            if verdict != "success":
                engine_event(root, "lane_ci_not_green",
                             {"task": task_id, "sha": branch_tip, "verdict": verdict})
                results.append(LaneResult(
                    task_id, branch, "ci not green",
                    f"remote ci {verdict} on {branch_tip[:10]}"))
                continue

        base_tip = vcs.tip(root, base) or base
        if vcs.is_ancestor(root, base_tip, branch_tip):
            # The base has not moved under this branch: the tree that would
            # land is byte-identical to the one the gates measured.
            if dry_run:
                results.append(LaneResult(task_id, branch, "would land",
                                          "fast-forward, gates already measured"))
                continue
            sha = vcs.merge_ff(root, branch_tip)
            engine_event(root, "lane_landed", {
                "task": task_id, "mode": "fast-forward", "sha": sha,
                "approver": approver})
            results.append(LaneResult(task_id, branch, "landed", "fast-forward", sha))
            continue

        if dry_run:
            results.append(LaneResult(task_id, branch, "would rebase",
                                      "base moved; gates re-run before landing"))
            continue
        engine_wt = root / naming.WORKTREE_DIR / task_id
        if engine_wt.exists():
            # The run's own worktree still pins the branch, and git refuses
            # to check a branch out twice. A READY candidate's worktree is
            # disposable — the work lives on the branch, and the reap would
            # collect it anyway — so the lane releases it for the rebase.
            vcs.remove_worktree(root, engine_wt)
        workdir = root / naming.WORKTREE_DIR / f"lane-{task_id}"
        if not vcs.rebase_in_worktree(root, branch, base, workdir):
            engine_event(root, "lane_conflict", {"task": task_id, "base": base})
            state.escalate(
                EscalationReason.MERGE_CONFLICT,
                f"rebase onto {base!r} conflicts; branch untouched — "
                "re-queue or abandon")
            results.append(LaneResult(
                task_id, branch, "conflict",
                "merge_conflict: rebase aborted, branch untouched — run escalated"))
            continue
        try:
            exit_code, summary = _regate(workdir, base, task_id)
        finally:
            vcs.remove_worktree(root, workdir)
        if exit_code != 0:
            engine_event(root, "lane_gates_red", {"task": task_id, "gates": summary})
            results.append(LaneResult(task_id, branch, "gates red", summary))
            continue
        sha = vcs.merge_ff(root, branch)
        engine_event(root, "lane_landed", {
            "task": task_id, "mode": "rebased", "sha": sha, "approver": approver})
        results.append(LaneResult(task_id, branch, "landed", "rebased, gates green", sha))
    return results
