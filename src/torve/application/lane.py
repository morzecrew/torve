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
from dataclasses import dataclass, field
from pathlib import Path

from torve.application.feedback import capture_feedback
from torve.application.ports import CiStatus, LaneVcs, PrInfo
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.application.threads import group_findings
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


def awaiting_landing(root: Path, vcs: LaneVcs, base: str | None, unit: str = "task") -> list[str]:
    """The ready candidates whose branch tip is not yet on what they land onto
    — the document branch under `unit: document` where it exists, the base
    otherwise. What a night still owes before it may call its queue drained
    (bloomery, 2026-09-19: the night closed one pass before the lane landed
    its last green candidate). A candidate with no branch is nothing to land."""

    owed: list[str] = []

    for state in ready_candidates(root):
        branch = naming.branch(state.task_id)
        tip = vcs.tip(root, branch)

        if tip is None:
            continue

        target = (
            _document_branch(root, vcs, state.task_id, dry_run=True) if unit == "document" else None
        ) or base

        if target is None or vcs.tip(root, target) is None:
            continue

        if not vcs.is_ancestor(root, tip, target):
            owed.append(state.task_id)

    return owed


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


def _regate(workdir: Path, base_ref: str, task_id: str | None) -> tuple[int, str]:
    """The full battery over the rebased tree, exactly as `torve gates run`
    would judge it — fail-closed on a missing manifest.

    A document branch carries several tasks' work and is judged under no one
    task's contract (S-0083/D-13): `task_id` is None there, and the battery
    reads the manifest's own scope as it does anywhere else with no task."""

    from torve.gates.context import build_context, load_task, resolve_base
    from torve.gates.runner import run_gates

    manifest_path = layout.gates_file(workdir)

    if not manifest_path.is_file():
        return 1, "no gate manifest in the rebased tree"

    from torve.config.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    task_path = layout.task_file(workdir, task_id) if task_id else None
    landed = task_path is not None and task_path.is_file()

    ctx = build_context(
        workdir,
        manifest,
        base=resolve_base(workdir, base_ref),
        task_path=task_path if landed else None,
    )

    if landed and task_path is not None:
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

    The served lane leg is the caller (S-0079/D-10); the manual lane passes
    nothing, so `torve merge` escalates as it always has — the operator
    standing at the terminal is exactly who should see a conflict.
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


def _publish(
    root: Path,
    publish: Publisher,
    task_id: str,
    branch: str,
    sha: str,
    results: list[LaneResult],
) -> str | None:
    """The push under lease and the forge call, or None when the forge
    refused — which is this candidate's refusal and not the pass's: the
    remaining candidates are still landed or still refused on their own
    terms."""

    try:
        return publish(task_id, branch)

    except RuntimeError as exc:
        engine_event(root, "lane_pr_refused", {"task": task_id, "sha": sha, "detail": str(exc)})
        results.append(LaneResult(task_id, branch, "pr refused", str(exc), sha))

        return None


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

    reference = _publish(root, publish, task_id, branch, sha, results)

    if reference is None:
        return

    engine_event(
        root,
        "lane_pr_opened",
        {"task": task_id, "mode": mode, "sha": sha, "approver": approver, "pr": reference},
    )

    results.append(LaneResult(task_id, branch, "pull request", reference or mode, sha))


# ....................... #


def _document_branch(root: Path, vcs: LaneVcs, task_id: str, dry_run: bool) -> str | None:
    """The branch this candidate's phases land onto under `unit: document`,
    or None when the contract names no document — that candidate lands by the
    task unit whatever the unit says, and nothing here infers a document for
    it (S-0083/D-4). An unreadable contract is a contract naming no document:
    the lane's own no-branch and gate handling reports the run, it does not
    die on the file.

    The branch is cut once per document (S-0083/D-18), from the remote's `main`
    after a fetch (S-0083/D-5), the first time a candidate of that document
    reaches the landing criteria — every later landing finds it and targets
    the same ref, so two phases of one document cannot leave two branches. A
    dry run cuts nothing, as it publishes nothing.
    """

    from torve.gates.context import load_task, resolve_base

    contract = layout.task_file(root, task_id)

    if not contract.is_file():
        return None

    try:
        spec = load_task(contract).spec

    except ValueError:
        return None

    if spec is None:
        return None

    branch = naming.document_branch(spec)

    if vcs.tip(root, branch) is None and not dry_run:
        base = resolve_base(root, None, fetch=True)
        tip = vcs.tip(root, base) if base else None

        if tip is None:
            raise RuntimeError(f"no base to cut {branch!r} from — the remote has no main")

        vcs.reset_branch(root, branch, tip)
        engine_event(root, "lane_document_branch", {"task": task_id, "branch": branch, "sha": tip})

    return branch


# ....................... #


def _land_document(
    root: Path,
    vcs: LaneVcs,
    publish: Publisher,
    task_id: str,
    document: str,
    tip: str,
    mode: str,
    approver: str,
    results: list[LaneResult],
) -> None:
    """`unit: document`'s landing act (S-0083/D-5, S-0083/D-6, S-0083/D-7).

    The candidate's tip becomes the document branch's tip — the lane's own
    fast-forward of a ref it never checks out, after the same criteria, probe
    and rebase the task unit applies, so the two units cannot drift apart.
    The landing is recorded in the `lane_landed` shape the local lane writes,
    with the unit and the branch named: a phase that landed on a branch is
    landed as far as the record is concerned, and the ledger's counts keep
    reading one record shape.

    Then the branch is pushed under lease and the document's one pull request
    opened or refreshed, and the pass stops — merging is a person's single
    act on the forge, whatever number of phases the branch carries.

    A forge that refuses puts the ref back where it was: the publication is
    half of this landing, so a landing nobody could publish is one the next
    pass must make again.
    """

    before = vcs.tip(root, document)
    vcs.reset_branch(root, document, tip)
    reference = _publish(root, publish, task_id, document, tip, results)

    if reference is None:
        if before is not None:
            vcs.reset_branch(root, document, before)

        return

    engine_event(
        root,
        "lane_landed",
        {
            "task": task_id,
            "mode": mode,
            "sha": tip,
            "approver": approver,
            "carried": _carried(root, task_id),
            "unit": "document",
            "branch": document,
            "pr": reference,
        },
    )

    results.append(LaneResult(task_id, document, "landed", f"{mode} onto {document}", tip))


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

# What the stream last recorded about a document branch's own pull request
# (S-0083/D-10, S-0083/D-11). A verdict is terminal here for the reason it is
# terminal per task: the lane reads it back from its own records and never
# asks the forge about that branch again.
_DOCUMENT_VERDICTS = {
    "lane_document_landed": "landed",
    "lane_document_closed": "closed",
    "lane_pr_unresolved": "unresolved",
}


@dataclass
class _Document:
    """What the lane's own records say about one document branch: the verdict
    its pull request carries, every task its landings named, and the base tip
    a rebase of it last conflicted against."""

    verdict: str = ""
    tasks: list[str] = field(default_factory=list)
    conflict_base: str = ""


def _document_ledger(root: Path) -> dict[str, _Document]:
    """The document pull requests the lane holds, from its own recorded
    events (S-0083/D-12): a branch is open from the first landing onto it, and
    a later verdict closes it out. The tasks are read from the same landings,
    so the join from the branch back to the work it carries needs no second
    record and no forge call (S-0083/D-10).

    Only a branch a landing named is tracked, so a task branch the forge
    could not resolve is not mistaken for a document."""

    from torve.application.projections import stream_rows

    ledger: dict[str, _Document] = {}

    for row in stream_rows(root):
        event = str(row.get("event", ""))
        branch = str(row.get("branch") or "")

        if not branch:
            continue

        if event == "lane_landed" and row.get("unit") == "document":
            entry = ledger.setdefault(branch, _Document())
            entry.verdict = "open"
            task = str(row.get("task") or "")

            if task and task not in entry.tasks:
                entry.tasks.append(task)

        elif branch not in ledger:
            continue

        elif event in _DOCUMENT_VERDICTS:
            ledger[branch].verdict = _DOCUMENT_VERDICTS[event]

        elif event == "lane_document_conflict":
            ledger[branch].conflict_base = str(row.get("base_tip") or "")

    return ledger


# ....................... #


def document_tasks(root: Path, branch: str) -> list[str]:
    """The tasks the lane's own records say a document branch carries, in
    landing order — what the document's pull request is composed from
    (S-0083/D-8). Empty for a branch no landing has named yet."""

    entry = _document_ledger(root).get(branch)

    return list(entry.tasks) if entry is not None else []


# ....................... #


def _record_threads(root: Path, branch: str, info: PrInfo) -> None:
    """What the read-back saw on an open document pull request (S-0084/D-6):
    the branch, the pull request, and each finding with its anchor and its
    thread identifiers — whether or not anything is then minted from it.

    So a night run before the leg exists says in its own record what a leg
    would have acted on, which makes the first honest measurement of the leg
    predate the leg. Nothing is recorded for a pull request carrying no
    unresolved thread: there is nothing a leg would have done."""

    if not info.threads:
        return

    engine_event(
        root,
        "lane_pr_threads",
        {
            "branch": branch,
            "pr": info.number,
            "threads": len(info.threads),
            "findings": [
                {
                    "path": finding.path,
                    "line": finding.line,
                    "end_line": finding.end_line,
                    "threads": list(finding.ids),
                }
                for finding in group_findings(info.threads)
            ],
        },
    )


# ....................... #


def _escalate_document(root: Path, branch: str, tasks: list[str], detail: str) -> None:
    """A document branch a person has to unstick escalates the work it
    carries (S-0083/D-13): the branch has no run state of its own, and an
    escalation nobody can see is not one — the queue's age has to start
    counting somewhere. A task that has already moved on is left alone."""

    for task_id in tasks:
        path = naming.state_file(root, task_id)

        if not path.is_file():
            continue

        state = RunState.load(path)

        if state.state is TaskState.READY:
            state.escalate(EscalationReason.MERGE_CONFLICT, detail)


# ....................... #


def _rebase_document(
    root: Path,
    vcs: LaneVcs,
    publish: Publisher,
    document: str,
    branch: str,
    entry: _Document,
    number: int,
    results: list[LaneResult],
) -> None:
    """A pull request still open (S-0083/D-13). Against an unmoved base it
    already shows the tree the battery judged and it is a person's turn. A
    base that moved under it is the ordinary case for a branch that lives
    days: rebase in a disposable worktree, re-run the battery over the
    rebased tree, republish under lease — bounded once per base tip, so a
    branch against a moving `main` cannot rebase itself in a loop.

    A conflict aborts and escalates for a person; the lane never resolves
    one, and the branch is left exactly as it was (S-0083/D-14)."""

    from torve.gates.context import resolve_base

    branch_tip = vcs.tip(root, branch)
    base = resolve_base(root, None, fetch=True)
    base_tip = vcs.tip(root, base) if base else None

    if (
        branch_tip is None
        or base is None
        or base_tip is None
        or vcs.is_ancestor(root, base_tip, branch_tip)
    ):
        results.append(
            LaneResult(
                document,
                branch,
                "pull request open",
                f"pull request #{number} awaits a person",
                branch_tip or "",
            )
        )

        return

    if entry.conflict_base == base_tip:
        # Once per base tip (S-0006/D-12): the same conflict against the same
        # base is the person's, and re-running it buys no new signal.
        results.append(
            LaneResult(
                document,
                branch,
                "conflict",
                f"merge_conflict: {branch!r} still conflicts with {base!r} — escalated",
                branch_tip,
            )
        )

        return

    workdir = root / naming.WORKTREE_DIR / f"lane-{document}"

    if not vcs.rebase_in_worktree(root, branch, base, workdir):
        engine_event(
            root,
            "lane_document_conflict",
            {"branch": branch, "base_tip": base_tip, "tasks": entry.tasks, "pr": number},
        )
        _escalate_document(
            root,
            branch,
            entry.tasks,
            f"the document branch {branch!r} no longer rebases onto {base!r}; "
            "a person resolves it — the branch is untouched",
        )
        results.append(
            LaneResult(
                document,
                branch,
                "conflict",
                f"merge_conflict: rebase onto {base!r} aborted, branch untouched — run escalated",
                branch_tip,
            )
        )

        return

    try:
        exit_code, summary = _regate(workdir, base, None)

    finally:
        vcs.remove_worktree(root, workdir)

    if exit_code != 0:
        # Back where it stood: a branch left on the new base reads to the
        # next pass as a base that never moved, which is the path that skips
        # the battery.
        vcs.reset_branch(root, branch, branch_tip)
        engine_event(root, "lane_gates_red", {"branch": branch, "gates": summary})
        results.append(LaneResult(document, branch, "gates red", summary, branch_tip))

        return

    rebased = vcs.tip(root, branch) or branch_tip
    reference = _publish(root, publish, entry.tasks[-1], branch, rebased, results)

    if reference is None:
        vcs.reset_branch(root, branch, branch_tip)
        return

    engine_event(
        root,
        "lane_document_rebased",
        {"branch": branch, "sha": rebased, "pr": reference, "tasks": entry.tasks},
    )
    results.append(
        LaneResult(document, branch, "pull request", f"rebased onto {base}, gates green", rebased)
    )


# ....................... #


def _document_verdicts(
    root: Path,
    vcs: LaneVcs,
    forge: Forge,
    publish: Publisher,
    results: list[LaneResult],
) -> dict[str, tuple[str, str]]:
    """The later pass's read-back at the document unit (S-0083/P-3).

    One forge call per open document pull request the lane's own records
    name, and none at all for a pass holding none (S-0083/D-12) — a document
    of six phases costs one call per pass rather than six, because the
    question is now per document.

    **Merged** is the document's landing (S-0083/D-10): one record naming the
    squash commit and every task the branch carried, and not a second landing
    per task — each task's landing was recorded when it landed on the branch,
    and what the merge adds is which commit the document became.

    **Closed** abandons every task the branch carries (S-0083/D-11): a person
    who declined a design declined all of it. Never re-queued and never
    escalated for triage.

    **Still open** is `_rebase_document`'s. Under every verdict the branch is
    kept (S-0083/D-14), so the join from each task to its own commits survives
    a merge that squashed them into one.

    Returns what each carried task is now, as (action, branch): a task the
    record says landed on a document branch has landed, whatever a rebase of
    that branch did to the ancestry its own tip used to have, and a task on a
    branch a person closed is abandoned rather than offered a second pull
    request.
    """

    carried: dict[str, tuple[str, str]] = {}

    for branch, entry in sorted(_document_ledger(root).items()):
        for task_id in entry.tasks:
            carried[task_id] = (
                "abandoned" if entry.verdict == "closed" else "already landed",
                branch,
            )

        if entry.verdict != "open":
            continue

        info = forge(branch)
        document = branch.rsplit("/", 1)[-1]

        if info is None:
            engine_event(root, "lane_pr_unresolved", {"branch": branch, "tasks": entry.tasks})
            results.append(
                LaneResult(
                    document,
                    branch,
                    "pr unresolved",
                    "the forge knows no pull request for the branch; not re-opened",
                )
            )

        elif info.state == "merged":
            sha = info.merge_commit or info.head_sha

            engine_event(
                root,
                "lane_document_landed",
                {
                    "branch": branch,
                    "sha": sha,
                    "pr": info.number,
                    "tasks": entry.tasks,
                    # Read after the fact: `at` is when the engine asked, not
                    # when the person clicked.
                    "observed": True,
                },
            )
            results.append(
                LaneResult(
                    document,
                    branch,
                    "landed",
                    f"pull request #{info.number} merged, carrying {', '.join(entry.tasks)}",
                    sha,
                )
            )

        elif info.state == "closed":
            engine_event(
                root,
                "lane_document_closed",
                {"branch": branch, "pr": info.number, "tasks": entry.tasks},
            )
            results.append(
                LaneResult(
                    document,
                    branch,
                    "abandoned",
                    f"pull request #{info.number} was closed without merging, "
                    f"abandoning {', '.join(entry.tasks)}",
                )
            )

        else:
            _record_threads(root, branch, info)
            _rebase_document(root, vcs, publish, document, branch, entry, info.number, results)

        if info is not None and info.state == "closed":
            carried.update(dict.fromkeys(entry.tasks, ("abandoned", branch)))

    return carried


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
    document: str | None = None,
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
        if document is not None:
            _land_document(
                root, vcs, publish, task_id, document, branch_tip, "fast-forward", approver, results
            )
        else:
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
    document: str | None = None,
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
        rebased_tip = vcs.tip(root, branch) or branch_tip

        if document is not None:
            _land_document(
                root, vcs, publish, task_id, document, rebased_tip, "rebased", approver, results
            )
        else:
            _open_pull_request(
                root, publish, task_id, branch, rebased_tip, "rebased", approver, results
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
    document: str | None = None,
) -> None:
    base_tip = vcs.tip(root, base) or base

    if vcs.is_ancestor(root, base_tip, branch_tip):
        # The base has not moved under this branch: the tree that would
        # land is byte-identical to the one the gates measured.
        _land_fast_forward(
            root, vcs, task_id, branch, branch_tip, dry_run, approver, results, publish, document
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
        document,
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
    unit: str = "task",
) -> list[LaneResult]:
    """One pass of the lane. A `publish` is `pull_request` mode (S-0080/D-3):
    the pass runs unchanged to the landing and then publishes the candidate
    instead of fast-forwarding the base. The mode is a term of configuration
    (S-0080/D-1) decided by the caller — the lane is handed the act, never the
    question of whether a remote exists.

    A `forge` is the same mode's read-back: on a later pass each candidate
    with a pull request open is asked about, and the verdict is recorded
    (S-0080/the-landing-arrives-as-an-answer) — at the document unit once per
    open document rather than once per task (S-0083/D-12). A dry run asks
    nothing, as it publishes nothing.

    `unit` is the landing unit (S-0083/D-1), a term of the same configuration:
    `document` lands every phase of a document onto the document's own branch
    behind one pull request, `task` opens one per task. It governs only where
    there is a pull request to be one per, so a `local` landing ignores it
    (S-0083/D-2), and only a candidate whose contract names a document
    (S-0083/D-4)."""

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

    carried: dict[str, tuple[str, str]] = {}

    if forge is not None and publish is not None and not dry_run:
        # Before the candidates, so a document a person merged or closed this
        # evening is recorded before anything lands onto its branch again.
        # Asked per document rather than per task, from the lane's own
        # records, so a pass holding none asks nothing (S-0083/D-12).
        carried = _document_verdicts(root, vcs, forge, publish, results)

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

        if task_id in carried:
            # The record, not the ancestry: a rebase of the document branch
            # rewrote the commits this tip used to be an ancestor of, and the
            # work is on that branch either way (S-0083/D-10, S-0083/D-11).
            action, held = carried[task_id]
            results.append(LaneResult(task_id, held, action, f"carried by {held}", sha=branch_tip))

            continue

        # Where this candidate lands: the document's branch under
        # `unit: document`, the checkout's base otherwise (S-0083/D-4).
        document = (
            _document_branch(root, vcs, task_id, dry_run)
            if publish is not None and unit == "document"
            else None
        )
        target = document or base

        if vcs.is_ancestor(root, branch_tip, target):
            results.append(LaneResult(task_id, branch, "already landed", sha=branch_tip))
            continue

        if (
            forge is not None
            and ledger
            and _forge_verdict(
                root, vcs, forge, ledger, task_id, target, branch, branch_tip, approver, results
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
            target,
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
            target,
            dry_run,
            on_conflict,
            approver,
            results,
            publish,
            document,
        )

    return results
