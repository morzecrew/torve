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

from collections.abc import Callable
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

    # ....................... #

    @property
    def landed(self) -> bool:
        return self.action == "landed"


# ....................... #


def ready_candidates(root: Path) -> list[RunState]:
    states = RunState.load_all(root / naming.WORKTREE_DIR)

    return sorted(
        (s for s in states if s.state is TaskState.READY and not _awaits_adoption(root, s.task_id)),
        key=lambda s: s.task_id,
    )


# ....................... #


def _awaits_adoption(root: Path, task_id: str) -> bool:
    """A READY draft run is intake's output, not the lane's input
    (RFC 0020, D-20.1): it has no branch and nothing to land — adoption
    consumes it. Anything unreadable stays a candidate; the lane's own
    no-branch handling reports it rather than hiding it."""

    contract = layout.task_file(root, task_id)

    if not contract.is_file():
        return False

    try:
        from torve.gates.context import load_task

        return load_task(contract).role == "draft"

    except ValueError:
        return False


# ....................... #


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
        workdir,
        manifest,
        base=resolve_base(workdir, base_ref),
        task_path=task_path if task_path.is_file() else None,
    )

    if task_path.is_file():
        ctx.task = load_task(task_path)

    report = run_gates(ctx)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)

    return report.exit_code, summary


# ....................... #


def _engine_record(root: Path, rel: str) -> bool:
    """The store's files are records, not landed content: the landing is
    measured from the candidate's committed tree, never composed from the
    checkout's engine state, so engine-authored dirt — minted task
    contracts, telemetry appends, the outbox pair — must not demand an
    operator commit before every landing."""

    from torve.application.evals import EVAL_LEDGER
    from torve.application.intake import INTAKE_LEDGER
    from torve.application.loop import LOCK
    from torve.application.outbox import LEDGER, OUTBOX
    from torve.application.review import PR_LEDGER
    from torve.config.manifest import Manifest, load_manifest

    if rel.startswith(f"{layout.TORVE_DIR}/tasks/"):
        return True

    manifest_path = layout.gates_file(root)

    telemetry_rel = (
        load_manifest(manifest_path).telemetry
        if manifest_path.is_file()
        else Manifest(gates=[]).telemetry
    )

    return rel in {
        telemetry_rel,
        f"{layout.TORVE_DIR}/{OUTBOX}",
        f"{layout.TORVE_DIR}/{LEDGER}",
        # The tick's own lock (RFC 0019) must not dirty the lane
        # leg running inside the tick that holds it; the
        # pr-reviews ledger is the same class of record.
        f"{layout.TORVE_DIR}/{LOCK}",
        f"{layout.TORVE_DIR}/{PR_LEDGER}",
        f"{layout.TORVE_DIR}/{EVAL_LEDGER}",
        # The intake ledger (RFC 0020 §5.4): the claim writes
        # it inside the tick, and the lane leg follows in the
        # same pass — found live when a claimed request blocked
        # an approved landing.
        f"{layout.TORVE_DIR}/{INTAKE_LEDGER}",
    }


# ....................... #


def record_approval(root: Path, task_id: str, actor: str, sha: str) -> bool:
    """One sha-bound approval (RFC 0006 §3, T-0060): recorded on the run
    state, deduped by (actor, sha) — approving the same tip twice is one
    approval, and an approval of a superseded tip stays in the record but
    counts for nothing at the lane. Returns False on the dedupe."""

    state = RunState.load(naming.state_file(root, task_id))

    if any(a.get("actor") == actor and a.get("sha") == sha for a in state.approvals):
        return False

    from datetime import UTC, datetime

    state.approvals.append(
        {"actor": actor, "sha": sha, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )

    state.save()

    return True


# ....................... #


def _dispose_conflict(
    root: Path,
    state: RunState,
    task_id: str,
    branch: str,
    base: str,
    base_tip: str,
    on_conflict: Callable[[str], str],
    results: list[LaneResult],
    found_by: str,
) -> None:
    """The A-35 disposal, shared by the landing's real conflict and the
    pre-approval probe (D-6.13, A-42): escalate — the record and the
    queue-age alarm stand — then capture, keep the branch, re-queue. A
    refused cleanup leaves the escalation standing for the human."""

    state.escalate(
        EscalationReason.MERGE_CONFLICT,
        f"rebase onto {base!r} conflicts ({found_by}); capturing for the "
        "revision loop and re-queueing (A-35)",
    )

    try:
        cleanup = on_conflict(task_id)

    except RuntimeError as exc:
        results.append(
            LaneResult(
                task_id,
                branch,
                "conflict",
                f"merge_conflict ({found_by}): re-queue cleanup refused ({exc}) — run escalated",
            )
        )

        return

    state.transition(TaskState.QUEUED, f"conflict auto-requeue ({cleanup})")
    state.conflict_base = base_tip
    state.save()

    engine_event(
        root,
        "lane_conflict_requeued",
        {"task": task_id, "base_tip": base_tip, "found_by": found_by},
    )

    results.append(
        LaneResult(
            task_id,
            branch,
            "conflict requeued",
            f"merge_conflict ({found_by}): captured for the revision loop and re-queued",
        )
    )


# ....................... #


def process_lane(
    root: Path,
    vcs: LaneVcs,
    dry_run: bool = False,
    only: str | None = None,
    ci: CiStatus | None = None,
    approvals_required: int = 0,
    require_review: bool = False,
    quiet_window_s: int = 0,
    on_conflict: Callable[[str], str] | None = None,
) -> list[LaneResult]:
    base = vcs.current_branch(root)

    if not dry_run:
        dirt = [p for p in vcs.dirty_paths(root) if not _engine_record(root, p)]

        if dirt:
            raise RuntimeError(
                f"the working tree on {base!r} is not clean — the lane "
                "fast-forwards the checkout, commit or stash first: " + ", ".join(sorted(dirt)[:5])
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
            results.append(
                LaneResult(
                    task_id, branch, "no branch", "ran outside the engine or already cleaned up"
                )
            )

            continue

        if vcs.is_ancestor(root, branch_tip, base):
            results.append(LaneResult(task_id, branch, "already landed", sha=branch_tip))
            continue

        if require_review and not dry_run and state.reviewed_by is None:
            # §3's review criterion as a lane predicate (D-6.14, A-43):
            # the producing run recorded no concluded review — refused
            # before CI is polled and before the approvals prompt, so a
            # candidate the policy cannot land is never offered for
            # approval.
            engine_event(root, "lane_review_missing", {"task": task_id, "sha": branch_tip})

            results.append(
                LaneResult(
                    task_id,
                    branch,
                    "review missing",
                    "run recorded no review verdict (promotion.require_review)",
                    sha=branch_tip,
                )
            )

            continue

        if ci is not None and not dry_run:
            # ci: green_on_current_head (RFC 0006 §3): the remote's verdict
            # for the tip the remote actually saw. Only "success" lands; a
            # rebased tree is additionally judged by the local battery below.
            verdict = ci.conclusion(branch_tip)

            if verdict != "success":
                engine_event(
                    root,
                    "lane_ci_not_green",
                    {"task": task_id, "sha": branch_tip, "verdict": verdict},
                )

                results.append(
                    LaneResult(
                        task_id, branch, "ci not green", f"remote ci {verdict} on {branch_tip[:10]}"
                    )
                )

                continue

        if approvals_required and not dry_run:
            # Sha-bound (D-6.3): only approvals of the tip as measured now
            # count — an approval of a superseded tip approves nothing.
            current = [a for a in state.approvals if a.get("sha") == branch_tip]

            if len(current) < approvals_required:
                probe_base = vcs.tip(root, base) or base

                if (
                    on_conflict is not None
                    and state.conflict_base != probe_base
                    and not vcs.is_ancestor(root, probe_base, branch_tip)
                    and vcs.rebase_conflicts(root, branch, base)
                ):
                    # The probe precedes the prompt (D-6.13, A-42): a
                    # provably conflicting tip is never offered for
                    # approval — the disposal that would have burned the
                    # approval fires now, before anyone is asked.
                    engine_event(
                        root, "lane_conflict", {"task": task_id, "base": base, "probe": True}
                    )

                    _dispose_conflict(
                        root,
                        state,
                        task_id,
                        branch,
                        base,
                        probe_base,
                        on_conflict,
                        results,
                        found_by="probe",
                    )

                    continue

                engine_event(
                    root,
                    "lane_approvals_short",
                    {
                        "task": task_id,
                        "sha": branch_tip,
                        "have": len(current),
                        "need": approvals_required,
                    },
                )

                results.append(
                    LaneResult(
                        task_id,
                        branch,
                        "approvals short",
                        f"{len(current)} of {approvals_required} approval(s) for {branch_tip[:10]}",
                        sha=branch_tip,
                    )
                )

                continue

        if quiet_window_s and not dry_run:
            age = vcs.tip_age_s(root, branch_tip)

            if age < quiet_window_s:
                # Pushing reset the window (§3): the tip is too fresh.
                engine_event(
                    root,
                    "lane_quiet_window",
                    {"task": task_id, "sha": branch_tip, "age_s": age, "window_s": quiet_window_s},
                )

                results.append(
                    LaneResult(
                        task_id,
                        branch,
                        "quiet window",
                        f"tip is {age:.0f}s old; the window is {quiet_window_s}s",
                    )
                )

                continue

        base_tip = vcs.tip(root, base) or base

        if vcs.is_ancestor(root, base_tip, branch_tip):
            # The base has not moved under this branch: the tree that would
            # land is byte-identical to the one the gates measured.
            if dry_run:
                results.append(
                    LaneResult(
                        task_id, branch, "would land", "fast-forward, gates already measured"
                    )
                )

                continue

            # D-19.11 (A-28): the landing may carry the task's own records
            # — an untracked byte-identical root copy is adopted, never a
            # reason for git to refuse the fast-forward.
            vcs.adopt_identical(root, branch_tip)
            sha = vcs.merge_ff(root, branch_tip)

            engine_event(
                root,
                "lane_landed",
                {"task": task_id, "mode": "fast-forward", "sha": sha, "approver": approver},
            )

            results.append(LaneResult(task_id, branch, "landed", "fast-forward", sha))
            continue

        if dry_run:
            results.append(
                LaneResult(
                    task_id, branch, "would rebase", "base moved; gates re-run before landing"
                )
            )

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

            if on_conflict is not None and state.conflict_base != base_tip:
                # D-6.10 as amended by A-35: the escalation's standard
                # disposal is mechanical, so the loop applies it in place
                # — bounded by progress: once per base tip (D-6.12); a
                # repeat against an unmoved base falls through to the
                # human fork below.
                _dispose_conflict(
                    root,
                    state,
                    task_id,
                    branch,
                    base,
                    base_tip,
                    on_conflict,
                    results,
                    found_by="rebase",
                )

                continue

            state.escalate(
                EscalationReason.MERGE_CONFLICT,
                f"rebase onto {base!r} conflicts; branch untouched — re-queue or abandon",
            )

            results.append(
                LaneResult(
                    task_id,
                    branch,
                    "conflict",
                    "merge_conflict: rebase aborted, branch untouched — run escalated",
                )
            )

            continue

        try:
            exit_code, summary = _regate(workdir, base, task_id)

        finally:
            vcs.remove_worktree(root, workdir)

        if exit_code != 0:
            engine_event(root, "lane_gates_red", {"task": task_id, "gates": summary})
            results.append(LaneResult(task_id, branch, "gates red", summary))
            continue

        vcs.adopt_identical(root, branch)
        sha = vcs.merge_ff(root, branch)

        engine_event(
            root,
            "lane_landed",
            {"task": task_id, "mode": "rebased", "sha": sha, "approver": approver},
        )

        results.append(LaneResult(task_id, branch, "landed", "rebased, gates green", sha))

    return results
