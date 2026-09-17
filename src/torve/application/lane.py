"""The serialized merge lane (S-0006/the-correction-this-document-exists-for, S-0006/D-1): ready is a lane, not a
set. One candidate at a time — a task branch whose base has not moved lands
exactly as it was measured (a rebase that changes nothing buys no new
signal); one whose base moved is rebased in a disposable worktree and its
gate battery re-runs over the rebased tree before landing, which is review
freshness against current head (S-0006/D-3) in the local regime, where the
battery is current-head CI. A conflicted rebase aborts and escalates the
run — `ready -> escalated`, reason `merge_conflict` (charter S-0001/A-9, S-0006/D-10),
the one edge out of ready and the lane's alone — so the escalation queue's
age starts counting the moment a landing fails. The branch stays exactly
as measured; the engine never resolves a conflict, and the lane moves on
to the next candidate. Resolution is the standard escalated fork:
re-queue to re-run against the moved base, or abandon when a human
landed the work by hand.

The operator's invocation is the recorded approval; each outcome rides the
telemetry stream as an engine event (S-0006/D-7).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from torve.application.feedback import capture_feedback
from torve.application.ports import CiStatus, LaneVcs, PrInfo
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.base.clock import stamp
from torve.config import layout
from torve.domain.states import EscalationReason, TaskState

# ----------------------- #


# `pull_request` mode's landing act (S-0080/D-3), injected because the push and
# the forge call are adapters: (task, branch) -> what the forge answered, a
# pull request reference. Raising `RuntimeError` is a refusal for this
# candidate alone.
Publisher = Callable[[str, str], str]

# The later pass's question (S-0080/the-landing-arrives-as-an-answer, S-0080/D-6):
# branch -> what the forge holds for it, or None when the forge knows no pull
# request for that branch. Injected for the same reason `Publisher` is.
Forge = Callable[[str], PrInfo | None]

# What the stream last recorded about a task's pull request, and the action a
# candidate already carrying a verdict reports on every later pass. A verdict
# is terminal: the lane reads it back from its own records and never asks the
# forge again, so a closed pull request is never re-opened (S-0080/D-8, S-0080/D-17).
_VERDICTS = {
    "lane_pr_opened": "open",
    "lane_landed": "landed",
    "lane_pr_closed": "closed",
    "lane_pr_unresolved": "unresolved",
}

_RECORDED = {"landed": "already landed", "closed": "abandoned", "unresolved": "pr unresolved"}


@dataclass
class LaneResult:
    task: str
    branch: str
    # landed | pull request | pull request open | pr refused | pr unresolved |
    # abandoned | conflict | gates red | already landed | no branch | would *
    action: str
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
    (S-0020, S-0020/D-1): it has no branch and nothing to land — adoption
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


def _carried(root: Path, task_id: str) -> bool:
    """Does the carrier hold this landing? (S-0065/D-7.)

    The landing files in the tree are the one count the ledger divides by;
    the lane's `lane_landed` is an event about what the lane did, not a
    second tally, so it is stamped with the carrier's answer at the moment
    the merge produced this tree. A landing the carrier does not hold —
    an attempt whose execution file was never written, work a human landed
    by hand — is then visible as a disagreement rather than as a quietly
    different number.

    Read at the default corpus path: the lane takes no configuration, and
    a repository that moved it reads `false` rather than opening a second
    count of its own.
    """

    from torve.application.projections import shipped_ids

    return task_id in shipped_ids(root)


# ....................... #


def _engine_record(root: Path, rel: str) -> bool:
    """The store's files are records, not landed content: the landing is
    measured from the candidate's committed tree, never composed from the
    checkout's engine state, so engine-authored dirt — minted task
    contracts, telemetry appends, the engine's own ledgers — must not demand
    an operator commit before every landing."""

    from torve.application.enginelock import LOCK
    from torve.application.evals import EVAL_LEDGER
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
        # The tick's own lock (S-0019) must not dirty the lane
        # leg running inside the tick that holds it; the
        # pr-reviews ledger is the same class of record.
        f"{layout.TORVE_DIR}/{LOCK}",
        f"{layout.TORVE_DIR}/{PR_LEDGER}",
        f"{layout.TORVE_DIR}/{EVAL_LEDGER}",
    }


# ....................... #


def record_approval(root: Path, task_id: str, actor: str, sha: str) -> bool:
    """One sha-bound approval (S-0006/promotion, T-0060): recorded on the run
    state, deduped by (actor, sha) — approving the same tip twice is one
    approval, and an approval of a superseded tip stays in the record but
    counts for nothing at the lane. Returns False on the dedupe."""

    state = RunState.load(naming.state_file(root, task_id))

    if any(a.get("actor") == actor and a.get("sha") == sha for a in state.approvals):
        return False

    state.approvals.append({"actor": actor, "sha": sha, "at": stamp()})

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
    """The S-0006/A-1 disposal, shared by the landing's real conflict and the
    pre-approval probe (S-0006/D-13, S-0006/A-2): escalate — the record and the
    queue-age alarm stand — then capture, keep the branch, re-queue. A
    refused cleanup leaves the escalation standing for the human."""

    state.escalate(
        EscalationReason.MERGE_CONFLICT,
        f"rebase onto {base!r} conflicts ({found_by}); capturing for the "
        "revision loop and re-queueing (S-0006/A-1)",
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


def _superseded_diff(root: Path, base_tip: str, branch_tip: str) -> str:
    """The candidate's own changes: a three-dot diff against the merge
    base, so the base's drift is not mistaken for the work (the same
    reading the pull request gives). The application layer runs git
    through the injected `LaneVcs`, but no port method diffs two shas
    and the ports are not this task's to widen; `projections.py` already
    reads history through subprocess for exactly this reason."""

    proc = subprocess.run(
        ["git", "-C", str(root), "diff", f"{base_tip}...{branch_tip}"],
        capture_output=True,
        # A repository holds whatever bytes a candidate committed, and the
        # disposal is built on RuntimeError: a UnicodeDecodeError raised
        # here escaped `_dispose_conflict`'s catch, left the conflicting
        # candidate escalated but never re-queued, and abandoned every
        # remaining candidate in the pass (T-0285). A feedback record is
        # read by a person and a model, so an undecodable byte is worth a
        # replacement character and not a dead lane.
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git diff refused")

    return proc.stdout


# ....................... #


def conflict_disposal(root: Path, vcs: LaneVcs) -> Callable[[str], str]:
    """The disposal a landing leg hands `process_lane` as `on_conflict`
    (S-0052/a-conflict-disposes-of-itself): the superseded candidate's diff rides the task's
    feedback record before the branch is ever replaced, so the next
    attempt sees what it collided with.

    The forge-thread half is deliberately absent — the review allow-list
    that drove it retired with the standing loop, and its capture is
    owed by S-0005's S-0005/D-12, not silently skipped here: the record
    says "none captured" rather than implying there was nothing to say.
    The branch is kept — landing never deletes a candidate; only the
    human fork does. Re-queue stays bound to the moved base tip
    (S-0006/D-12); that bound lives in the lane's own disposal path, so a
    caller cannot loosen it through this factory.

    The manual lane passes nothing: `torve merge` escalates as it always
    has, and this factory is inert until an unattended caller wires it.
    """

    def dispose(task_id: str) -> str:
        base = vcs.current_branch(root)
        base_tip = vcs.tip(root, base)
        branch = naming.branch(task_id)
        branch_tip = vcs.tip(root, branch)

        if base_tip is None or branch_tip is None:
            raise RuntimeError(f"cannot resolve {base!r} or {branch!r} to capture the conflict")

        diff = _superseded_diff(root, base_tip, branch_tip)
        captured = capture_feedback(root, task_id, diff, [])

        return "superseded diff captured" if captured else "nothing to capture"

    return dispose


# ....................... #


def _review_missing(
    root: Path, state: RunState, require_review: bool, dry_run: bool, branch: str, branch_tip: str
) -> LaneResult | None:
    """§3's review criterion as a lane predicate (S-0006/D-14, S-0006/A-3): the
    producing run recorded no concluded review — refused before CI is
    polled and before the approvals prompt, so a candidate the policy
    cannot land is never offered for approval."""

    if not require_review or dry_run or state.reviewed_by is not None:
        return None

    engine_event(root, "lane_review_missing", {"task": state.task_id, "sha": branch_tip})

    return LaneResult(
        state.task_id,
        branch,
        "review missing",
        "run recorded no review verdict (promotion.require_review)",
        sha=branch_tip,
    )


# ....................... #


def _ci_not_green(
    root: Path, ci: CiStatus | None, dry_run: bool, task_id: str, branch: str, branch_tip: str
) -> LaneResult | None:
    """ci: green_on_current_head (S-0006/promotion): the remote's verdict for
    the tip the remote actually saw. Only "success" lands; a rebased tree
    is additionally judged by the local battery in `_regate`."""

    if ci is None or dry_run:
        return None

    verdict = ci.conclusion(branch_tip)

    if verdict == "success":
        return None

    engine_event(
        root, "lane_ci_not_green", {"task": task_id, "sha": branch_tip, "verdict": verdict}
    )

    return LaneResult(task_id, branch, "ci not green", f"remote ci {verdict} on {branch_tip[:10]}")


# ....................... #


def _conflicting_probe(
    vcs: LaneVcs,
    root: Path,
    state: RunState,
    branch: str,
    base: str,
    branch_tip: str,
    probe_base: str,
    on_conflict: Callable[[str], str] | None,
) -> bool:
    """The probe precedes the prompt (S-0006/D-13, S-0006/A-2): true when a wired
    disposal would find this tip provably conflicting against the current
    base before anyone is asked to approve it."""

    return (
        on_conflict is not None
        and state.conflict_base != probe_base
        and not vcs.is_ancestor(root, probe_base, branch_tip)
        and vcs.rebase_conflicts(root, branch, base)
    )


# ....................... #


def _approvals_satisfied(
    root: Path,
    vcs: LaneVcs,
    state: RunState,
    base: str,
    branch: str,
    branch_tip: str,
    approvals_required: int,
    dry_run: bool,
    on_conflict: Callable[[str], str] | None,
    results: list[LaneResult],
) -> bool:
    """True when landing may proceed. False means a `LaneResult` (short,
    or a conflict disposal) was appended and the candidate is done for
    this pass."""

    if not approvals_required or dry_run:
        return True

    task_id = state.task_id
    # Sha-bound (S-0006/D-3): only approvals of the tip as measured now count —
    # an approval of a superseded tip approves nothing.
    current = [a for a in state.approvals if a.get("sha") == branch_tip]

    if len(current) >= approvals_required:
        return True

    probe_base = vcs.tip(root, base) or base

    if on_conflict is not None and _conflicting_probe(
        vcs, root, state, branch, base, branch_tip, probe_base, on_conflict
    ):
        engine_event(root, "lane_conflict", {"task": task_id, "base": base, "probe": True})
        _dispose_conflict(
            root, state, task_id, branch, base, probe_base, on_conflict, results, found_by="probe"
        )

        return False

    engine_event(
        root,
        "lane_approvals_short",
        {"task": task_id, "sha": branch_tip, "have": len(current), "need": approvals_required},
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

    return False


# ....................... #


def _quiet_window(
    root: Path,
    vcs: LaneVcs,
    quiet_window_s: int,
    dry_run: bool,
    task_id: str,
    branch: str,
    branch_tip: str,
) -> LaneResult | None:
    """Pushing reset the window (§3): a tip fresher than the window is
    too fresh to land."""

    if not quiet_window_s or dry_run:
        return None

    age = vcs.tip_age_s(root, branch_tip)

    if age >= quiet_window_s:
        return None

    engine_event(
        root,
        "lane_quiet_window",
        {"task": task_id, "sha": branch_tip, "age_s": age, "window_s": quiet_window_s},
    )

    return LaneResult(
        task_id, branch, "quiet window", f"tip is {age:.0f}s old; the window is {quiet_window_s}s"
    )


# ....................... #


def _open_pull_request(
    root: Path,
    publish: Publisher,
    task_id: str,
    branch: str,
    sha: str,
    mode: str,
    approver: str,
    results: list[LaneResult],
) -> None:
    """`pull_request` mode's landing act (S-0080/D-3): in place of the
    fast-forward, the candidate is pushed under lease and the task's pull
    request opened or refreshed, and the pass stops there — the base is not
    moved by this engine at all. Every criterion, probe and rebase before
    this point ran exactly as `local` mode ran it, which is S-0080/D-11: the
    criteria decide whether a pull request is opened, and the forge's own
    rules govern what happens to it afterwards.

    A forge that refuses is this candidate's refusal, not the pass's: the
    remaining candidates are still landed or still refused on their own
    terms."""

    try:
        reference = publish(task_id, branch)

    except RuntimeError as exc:
        engine_event(root, "lane_pr_refused", {"task": task_id, "sha": sha, "detail": str(exc)})
        results.append(LaneResult(task_id, branch, "pr refused", str(exc), sha))

        return

    engine_event(
        root,
        "lane_pr_opened",
        {"task": task_id, "mode": mode, "sha": sha, "approver": approver, "pr": reference},
    )

    results.append(LaneResult(task_id, branch, "pull request", reference or mode, sha))


# ....................... #


def _pr_ledger(root: Path) -> dict[str, str]:
    """What the stream last recorded about each task's pull request. The
    lane's own events are the memory of what it has open, so a pass holding
    nothing open asks the forge nothing at all (S-0080/D-16) — no ledger of its
    own, and no call per idle candidate."""

    from torve.application.projections import stream_rows

    ledger: dict[str, str] = {}

    for row in stream_rows(root):
        verdict = _VERDICTS.get(str(row.get("event", "")))
        task = str(row.get("task", ""))

        if verdict and task:
            ledger[task] = verdict

    return ledger


# ....................... #


def _forge_verdict(
    root: Path,
    vcs: LaneVcs,
    forge: Forge,
    ledger: dict[str, str],
    task_id: str,
    base: str,
    branch: str,
    branch_tip: str,
    approver: str,
    results: list[LaneResult],
) -> bool:
    """The later pass's read-back (S-0080/the-landing-arrives-as-an-answer): the engine does not
    watch for the merge, it asks about the candidates it has open and
    answers each of the three verdicts a person can give.

    **Merged** is the landing (S-0080/D-7): recorded with the merge commit as
    the landed sha and the mode named, in the `lane_landed` shape the local
    lane writes, so nothing downstream learns a second way of asking what
    shipped. The instant is the instant the engine read it — the record says
    so rather than claiming a precision it does not have.

    **Closed** is a person declining the work (S-0080/D-8): recorded as an
    abandonment, never escalated for triage and never re-queued.

    **Still open** against a moved base falls through to the disposal the
    lane already has — rebase in a disposable worktree, re-run the battery,
    republish (S-0080/D-9). Against an unmoved base the pull request already shows
    the measured tree and it is a person's turn, so the pass reports it and
    spends no push.

    The branch is kept under every verdict (S-0080/D-12): nothing here deletes one.

    True when the candidate is done for this pass.
    """

    record = ledger.get(task_id)

    if record is None:
        return False

    if record != "open":
        results.append(
            LaneResult(task_id, branch, _RECORDED[record], f"recorded {record}", sha=branch_tip)
        )

        return True

    info = forge(branch)

    if info is None:
        # The forge can no longer resolve the branch — deleted on merge, or
        # renamed (S-0080/D-17). A stated gap in the record, and the ledger entry
        # above keeps the next pass from quietly opening a second pull
        # request for the same work.
        engine_event(root, "lane_pr_unresolved", {"task": task_id, "branch": branch})
        results.append(
            LaneResult(
                task_id,
                branch,
                "pr unresolved",
                "the forge knows no pull request for the branch; not re-opened",
                branch_tip,
            )
        )

        return True

    if info.state == "merged":
        sha = info.merge_commit or info.head_sha

        engine_event(
            root,
            "lane_landed",
            {
                "task": task_id,
                "mode": "pull-request",
                "sha": sha,
                "approver": approver,
                "carried": _carried(root, task_id),
                "pr": info.number,
                # Read after the fact: `at` is when the engine asked, not
                # when the person clicked.
                "observed": True,
            },
        )

        results.append(
            LaneResult(task_id, branch, "landed", f"pull request #{info.number} merged", sha)
        )

        return True

    if info.state == "closed":
        engine_event(
            root, "lane_pr_closed", {"task": task_id, "pr": info.number, "sha": branch_tip}
        )
        results.append(
            LaneResult(
                task_id,
                branch,
                "abandoned",
                f"pull request #{info.number} was closed without merging",
                branch_tip,
            )
        )

        return True

    if vcs.is_ancestor(root, vcs.tip(root, base) or base, branch_tip):
        results.append(
            LaneResult(
                task_id,
                branch,
                "pull request open",
                f"pull request #{info.number} awaits a person",
                branch_tip,
            )
        )

        return True

    return False


# ....................... #


def _land_fast_forward(
    root: Path,
    vcs: LaneVcs,
    task_id: str,
    branch: str,
    branch_tip: str,
    dry_run: bool,
    approver: str,
    results: list[LaneResult],
    publish: Publisher | None = None,
) -> None:
    if dry_run:
        if publish is not None:
            results.append(
                LaneResult(task_id, branch, "would open pull request", "gates already measured")
            )
        else:
            results.append(
                LaneResult(task_id, branch, "would land", "fast-forward, gates already measured")
            )

        return

    if publish is not None:
        _open_pull_request(
            root, publish, task_id, branch, branch_tip, "fast-forward", approver, results
        )

        return

    # S-0019/D-11 (S-0019/A-2): the landing may carry the task's own records — an
    # untracked byte-identical root copy is adopted, never a reason for
    # git to refuse the fast-forward.
    vcs.adopt_identical(root, branch_tip)
    sha = vcs.merge_ff(root, branch_tip)

    engine_event(
        root,
        "lane_landed",
        {
            "task": task_id,
            "mode": "fast-forward",
            "sha": sha,
            "approver": approver,
            "carried": _carried(root, task_id),
        },
    )

    results.append(LaneResult(task_id, branch, "landed", "fast-forward", sha))


# ....................... #


def _handle_rebase_conflict(
    root: Path,
    state: RunState,
    task_id: str,
    branch: str,
    base: str,
    base_tip: str,
    on_conflict: Callable[[str], str] | None,
    results: list[LaneResult],
) -> None:
    engine_event(root, "lane_conflict", {"task": task_id, "base": base})

    if on_conflict is not None and state.conflict_base != base_tip:
        # S-0006/D-10 as amended by S-0006/A-1: the escalation's standard disposal is
        # mechanical, so the loop applies it in place — bounded by
        # progress: once per base tip (S-0006/D-12); a repeat against an
        # unmoved base falls through to the human fork below.
        _dispose_conflict(
            root, state, task_id, branch, base, base_tip, on_conflict, results, found_by="rebase"
        )

        return

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


# ....................... #


def _land_rebased(
    root: Path,
    vcs: LaneVcs,
    state: RunState,
    task_id: str,
    branch: str,
    branch_tip: str,
    base: str,
    base_tip: str,
    on_conflict: Callable[[str], str] | None,
    approver: str,
    results: list[LaneResult],
    publish: Publisher | None = None,
) -> None:
    engine_wt = root / naming.WORKTREE_DIR / task_id

    if engine_wt.exists():
        # The run's own worktree still pins the branch, and git refuses to
        # check a branch out twice. A READY candidate's worktree is
        # disposable — the work lives on the branch, and the reap would
        # collect it anyway — so the lane releases it for the rebase.
        vcs.remove_worktree(root, engine_wt)

    workdir = root / naming.WORKTREE_DIR / f"lane-{task_id}"

    # Where the branch stood before the rebase moves it. A red battery has to put
    # it back: `git rebase` runs in a worktree checked out on the branch, so it
    # moves the ref, and a branch left on the new base reads to the next pass as
    # "the base has not moved under this branch" — the fast-forward path, which
    # skips the battery. T-0391 landed that way: one `rebase (finish)` in the
    # reflog, an `acceptance=fail` on the first pass, a fast-forward on the
    # second. Nothing bad shipped that time; the mechanism does not care.
    before_rebase = vcs.tip(root, branch) or branch_tip

    if not vcs.rebase_in_worktree(root, branch, base, workdir):
        _handle_rebase_conflict(root, state, task_id, branch, base, base_tip, on_conflict, results)
        return

    try:
        exit_code, summary = _regate(workdir, base, task_id)

    finally:
        vcs.remove_worktree(root, workdir)

    if exit_code != 0:
        vcs.reset_branch(root, branch, before_rebase)
        engine_event(root, "lane_gates_red", {"task": task_id, "gates": summary})
        results.append(LaneResult(task_id, branch, "gates red", summary))
        return

    if publish is not None:
        # The rebased tip is what the battery just measured, so that is the
        # tree the pull request must show (S-0080/D-9); the publisher's push
        # is leased, so a branch that moved under the engine refuses.
        _open_pull_request(
            root,
            publish,
            task_id,
            branch,
            vcs.tip(root, branch) or branch_tip,
            "rebased",
            approver,
            results,
        )

        return

    vcs.adopt_identical(root, branch)
    sha = vcs.merge_ff(root, branch)

    engine_event(
        root,
        "lane_landed",
        {
            "task": task_id,
            "mode": "rebased",
            "sha": sha,
            "approver": approver,
            "carried": _carried(root, task_id),
        },
    )

    results.append(LaneResult(task_id, branch, "landed", "rebased, gates green", sha))


# ....................... #


def _land_candidate(
    root: Path,
    vcs: LaneVcs,
    state: RunState,
    task_id: str,
    branch: str,
    branch_tip: str,
    base: str,
    dry_run: bool,
    on_conflict: Callable[[str], str] | None,
    approver: str,
    results: list[LaneResult],
    publish: Publisher | None = None,
) -> None:
    base_tip = vcs.tip(root, base) or base

    if vcs.is_ancestor(root, base_tip, branch_tip):
        # The base has not moved under this branch: the tree that would
        # land is byte-identical to the one the gates measured.
        _land_fast_forward(
            root, vcs, task_id, branch, branch_tip, dry_run, approver, results, publish
        )
        return

    if dry_run:
        results.append(
            LaneResult(task_id, branch, "would rebase", "base moved; gates re-run before landing")
        )

        return

    _land_rebased(
        root,
        vcs,
        state,
        task_id,
        branch,
        branch_tip,
        base,
        base_tip,
        on_conflict,
        approver,
        results,
        publish,
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
    publish: Publisher | None = None,
    forge: Forge | None = None,
) -> list[LaneResult]:
    """One pass of the lane. A `publish` is `pull_request` mode (S-0080/D-3):
    the pass runs unchanged to the landing and then publishes the candidate
    instead of fast-forwarding the base. The mode is a term of configuration
    (S-0080/D-1) decided by the caller — the lane is handed the act, never the
    question of whether a remote exists.

    A `forge` is the same mode's read-back: on a later pass each candidate
    with a pull request open is asked about, and the verdict is recorded
    (S-0080/the-landing-arrives-as-an-answer). A dry run asks nothing, as it publishes nothing."""

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
    # Read once for the pass, not once per candidate: the stream is the
    # lane's record of what it has open (S-0080/D-16).
    ledger = _pr_ledger(root) if forge is not None and not dry_run else {}

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

        if (
            forge is not None
            and ledger
            and _forge_verdict(
                root, vcs, forge, ledger, task_id, base, branch, branch_tip, approver, results
            )
        ):
            continue

        review_result = _review_missing(root, state, require_review, dry_run, branch, branch_tip)

        if review_result is not None:
            results.append(review_result)
            continue

        ci_result = _ci_not_green(root, ci, dry_run, task_id, branch, branch_tip)

        if ci_result is not None:
            results.append(ci_result)
            continue

        if not _approvals_satisfied(
            root,
            vcs,
            state,
            base,
            branch,
            branch_tip,
            approvals_required,
            dry_run,
            on_conflict,
            results,
        ):
            continue

        quiet_result = _quiet_window(
            root, vcs, quiet_window_s, dry_run, task_id, branch, branch_tip
        )

        if quiet_result is not None:
            results.append(quiet_result)
            continue

        _land_candidate(
            root,
            vcs,
            state,
            task_id,
            branch,
            branch_tip,
            base,
            dry_run,
            on_conflict,
            approver,
            results,
            publish,
        )

    return results
