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

import fcntl
import functools
import re
import subprocess
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec

from torve.application.feedback import capture_feedback
from torve.application.planner import change_type_of
from torve.application.ports import (
    COMPLETION_CONTEXT,
    REBASE_CONTEXT,
    REGATE_CONTEXT,
    CiStatus,
    LaneVcs,
    PrInfo,
    StatusScm,
)
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.application.threads import group_findings
from torve.base import naming
from torve.base.clock import stamp
from torve.config import layout
from torve.domain.states import EscalationReason, TaskState

if TYPE_CHECKING:
    from torve.gates.context import GateContext

# ----------------------- #

#: A document branch (S-0083/D-3): the one name under which a battery judges a
#: document's whole tree under no task's contract, and so owes the document's
#: own rows and invariants (S-0106/D-2).
DOCUMENT_BRANCH = re.compile(r"^torve/(S-\d+)$")

#: Every git the lane starts runs with the repository's hooks switched off
#: (S-0110/D-7): configuration an agent wrote never executes on the host
#: through a hook. `-c` is inherited by git's own child processes, so the
#: commits a rebase makes and the send-pack behind a push are covered by the
#: one flag on the parent command. The lane reads git through subprocess
#: where no port method fits (the ports are not its to widen).
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


# `pull_request` mode's landing act (S-0080/D-3), injected because the push and
# the forge call are adapters: (task, branch) -> what the forge answered, a
# pull request reference. Raising `RuntimeError` is a refusal for this
# candidate alone.
Publisher = Callable[[str, str], str]

# The later pass's question (S-0080/the-landing-arrives-as-an-answer, S-0080/D-6):
# branch -> what the forge holds for it, or None when the forge knows no pull
# request for that branch. Injected for the same reason `Publisher` is.
Forge = Callable[[str], PrInfo | None]

# S-0101/D-8: a candidate the lane keeps finding red at the same base tip and
# the same head is not regated for ever — the second red escalates. With a forge
# the count is the `torve/regate` status it holds for the head; when the forge
# holds none — no forge, or a head it was never given, as under the document
# unit where the task branch stays local — it lives in the process that observed
# it, so the ceiling survives the mode the branch is published in (T-0528).
_RED_RUNS: dict[tuple[str, str, str, str], int] = {}


def _note_red(root: Path, task_id: str, base_tip: str, head: str) -> int:
    """How many consecutive reds this candidate has now had at this (base, head)."""

    key = (str(root), task_id, base_tip, head)
    count = _RED_RUNS.get(key, 0) + 1
    _RED_RUNS[key] = count

    return count


@dataclass
class LaneResult:
    task: str
    branch: str
    # landed | pull request | pull request open | pr refused | pr unresolved |
    # abandoned | conflict | gates red | awaiting round | already landed |
    # no branch | would *
    action: str
    detail: str = ""
    sha: str = ""

    # ....................... #

    @property
    def landed(self) -> bool:
        return self.action == "landed"


# ....................... #


def ready_candidates(root: Path) -> list[RunState]:
    """The READY run states the lane still owes a landing. A task whose landing
    file the base tree holds has landed, whatever its run state says: a
    document squash-merged and its branch deleted while no pass ran leaves
    nothing else to read the merge from, and re-cutting the branch to land the
    task again replays work the base already has (S-0059/D-12, S-0099/D-3)."""

    from torve.application.decisions import landings

    states = RunState.load_all(root / naming.WORKTREE_DIR)
    based = {one.task for one in landings(root, root / layout.SPECS_DIR)}

    return sorted(
        (
            s
            for s in states
            if s.state is TaskState.READY
            and s.task_id not in based
            and not _awaits_adoption(root, s.task_id)
        ),
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
    # The record, not the ancestry (S-0083/D-10): a task the lane landed onto a
    # document branch is off its hands, whether a rebase of that branch onto a
    # moved base renamed its commit or a squash merge left none of them on it.
    # The branches are the remote's (S-0099/D-3) and the tasks are the landing
    # files they carry (S-0099/D-2).
    on_documents = {
        task for branch in open_documents(root) for task in document_tasks(root, branch)
    }

    for state in ready_candidates(root):
        if state.task_id in on_documents:
            continue

        branch = naming.branch(state.task_id)
        tip = vcs.tip(root, branch)

        if tip is None:
            continue

        target = (
            _document_branch(root, vcs, state.task_id, dry_run=True) if unit == "document" else None
        )

        # The document branch does not exist yet: the first landing cuts it, so
        # until then the candidate is measured against the base (S-0108/D-3).
        # `_document_branch(..., dry_run=True)` names the branch whether or not
        # it exists, and a candidate skipped here lets the night close before
        # the lane landed it.
        if target is None or vcs.tip(root, target) is None:
            target = base

        if target is None or vcs.tip(root, target) is None:
            continue

        if not vcs.is_ancestor(root, tip, target):
            owed.append(state.task_id)

    return owed


def contract_file(root: Path, task_id: str) -> Path:
    """Where the landing reads a task's contract: the root's copy where the
    files are the record, else the one dispatch projected into the run's
    worktree. With a store the board holds the task and the root has no file
    (S-0056/D-9)."""

    path = layout.task_file(root, task_id)

    return path if path.is_file() else layout.task_file(naming.worktree(root, task_id), task_id)


def _awaits_adoption(root: Path, task_id: str) -> bool:
    """A READY draft run is intake's output, not the lane's input
    (S-0020, S-0020/D-1): it has no branch and nothing to land — adoption
    consumes it. Anything unreadable stays a candidate; the lane's own
    no-branch handling reports it rather than hiding it."""

    contract = contract_file(root, task_id)

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
        # S-0106/D-6: `red-on-base` reads the document's change type.
        ctx.change_type = change_type_of(workdir / ctx.specs, ctx.task)

    if task_id is None:
        _document_inputs(ctx)

    report = run_gates(ctx)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)

    return report.exit_code, summary


def _document_inputs(ctx: GateContext) -> None:
    """What a document-branch battery judges beside the manifest (S-0106/D-2):
    the rows of the document the branch names — the table every phase inherits
    whole — and the standing invariants whose paths the branch's diff reaches.
    One reader, so the completion battery and the document-branch regate run
    the same checks (S-0106/D-2).

    Read at the worktree the battery judges: the document branch carries the
    corpus, and the diff is the one the battery sees. A branch naming no
    document, a document the corpus does not hold, or a corpus the loader
    refuses contributes nothing — a corpus error is `spec check`'s to report,
    never the battery's to die on."""

    from torve.base.globs import matches
    from torve.config import spec
    from torve.domain.task import InheritedDecision

    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(ctx.root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    match = DOCUMENT_BRANCH.match(proc.stdout.strip()) if proc.returncode == 0 else None

    if match is None:
        return

    try:
        corpus = spec.load_corpus(ctx.root / ctx.specs)

    except (spec.SpecError, OSError):
        return

    document = corpus.document(match.group(1))

    if document is None:
        return

    # S-0106/D-6: a refactor document's completion battery judges whether
    # behaviour held, so the gate is told the type.
    ctx.change_type = document.change.type if document.change else None

    ctx.inherited = [
        InheritedDecision(
            id=row.id,
            grade=row.grade,
            text=row.text.strip(),
            paths=list(row.paths),
            consequence=row.consequence.strip(),
            check=row.check,
            check_state=row.check_state,
            check_twin=row.check_twin,
        )
        for row in document.decisions
    ]

    reached = [*ctx.changed_paths, *ctx.untracked]

    ctx.invariants = [
        invariant
        for doc in corpus.standing()
        if not doc.superseded_by
        for invariant in doc.invariants
        if any(matches(path, invariant.paths) for path in reached)
    ]


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

    from torve.application.projections import landed

    return task_id in landed(root)


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
            # S-0109/D-1: the base tip the candidate was requeued against, so
            # the caller recording the fact has it without re-reading the base.
            base_tip,
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
        ["git", *NO_HOOKS, "-C", str(root), "diff", f"{base_tip}...{branch_tip}"],
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
        and vcs.rebase_conflicts(root, branch, base, _cut_point(root, branch, base, state.task_id))
    )


def _cut_point(root: Path, branch: str, base: str, task_id: str) -> str | None:
    """The commit *task_id*'s candidate was cut from: the parent of its first
    own commit, by the subject the runner gives every commit it writes
    (`torve(<task>): …`). None when no commit carries it.

    A rebase from the merge base replays every commit of *base*'s old tip
    as well, and once a document branch has been rebased onto a moved base,
    the landing files it restated make the earlier phases' old commits
    conflict with their own rebased copies."""

    proc = subprocess.run(
        [
            "git",
            *NO_HOOKS,
            "-C",
            str(root),
            "log",
            "--reverse",
            "--format=%H %s",
            f"{base}..{branch}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition(" ")

        if subject.startswith(f"torve({task_id}):"):
            return f"{sha}^"

    return None


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


def _document_branch(
    root: Path, vcs: LaneVcs, task_id: str, dry_run: bool, merged: Collection[str] = ()
) -> str | None:
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
    dry run cuts nothing and fetches nothing, as it publishes nothing.

    The remote's copy is the one worked from (S-0091/D-1): the fetch prunes,
    and a branch the remote no longer has, or one the base already holds every
    landing of — a merge — is never landed onto again (S-0099/D-3): the local
    ref is kept under `refs/torve/documents/<branch>/<tip>` so its commits stay
    reachable, and the branch is cut again from the remote's `main`, so the
    next pull request carries only what `main` lacks. An open branch the
    remote has sets the local ref to the remote tip — unless this pass read
    its pull request merged (*merged*), when the branch is cut from the base
    too and the next pull request carries only new work (S-0099/D-3).
    """

    from torve.gates.context import load_task, resolve_base

    contract = contract_file(root, task_id)

    if not contract.is_file():
        return None

    try:
        spec = load_task(contract).spec

    except ValueError:
        return None

    if spec is None:
        return None

    branch = naming.document_branch(spec)

    if dry_run:
        return branch

    vcs.fetch(root, prune=True)
    remote = vcs.remote_tip(root, branch)

    if remote is not None and branch in open_documents(root) and branch not in merged:
        # The remote's copy is the one worked from (S-0091/D-1). A branch
        # carrying landings held for a wave is kept as this checkout has it:
        # the push that will publish them has not happened yet, and resetting
        # to the remote would drop them (S-0097/D-6).
        if not _unpublished(root, vcs, branch, remote):
            vcs.reset_branch(root, branch, remote)

        return branch

    local = vcs.tip(root, branch)
    base = resolve_base(root, None)
    tip = vcs.tip(root, base) if base else None

    if tip is None:
        raise RuntimeError(f"no base to cut {branch!r} from — the remote has no main")

    if local is not None and local != tip:
        _keep_ref(root, f"refs/torve/documents/{branch}/{local}", local)
        engine_event(
            root,
            "lane_document_recut",
            {
                "task": task_id,
                "branch": branch,
                "kept": local,
                "sha": tip,
                "reason": "absent on the remote" if remote is None else "merged",
            },
        )

    if local != tip:
        vcs.reset_branch(root, branch, tip)

    if local is None:
        engine_event(root, "lane_document_branch", {"task": task_id, "branch": branch, "sha": tip})

    return branch


# ....................... #


def _keep_ref(root: Path, ref: str, sha: str) -> None:
    """Write *ref* at *sha* — a retired document branch kept reachable. The
    ports are not this task's to widen beyond the fetch and the remote tip;
    `_superseded_diff` reads git through subprocess for the same reason."""

    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "update-ref", ref, sha],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"could not keep {ref}")


def _ref_sha(root: Path, ref: str) -> str | None:
    """The sha *ref* holds, or None when the ref is not there. Read through
    subprocess for the same reason `_keep_ref` writes through it."""

    proc = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
        check=False,
    )

    return proc.stdout.strip() or None


def _drop_ref(root: Path, ref: str) -> None:
    """Forget *ref* — a verdict a person has since undone is not a verdict. A
    ref that is not there is nothing to forget."""

    subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "update-ref", "-d", ref],
        capture_output=True,
        text=True,
        check=False,
    )


# ....................... #


def _restate_landings(workdir: Path, onto: str) -> None:
    """Point every landing file a rebase replayed at its work commit's new sha.

    A landing names the work commit before it (S-0058/D-12, S-0059/D-9), and a
    rebase gives that commit a new sha while replaying the file unchanged, so
    the record would name a commit no branch holds. A landing commit always
    follows its work commit, so the work commit's new sha is the parent of the
    commit that added the file; the correction amends the rebased tip."""

    from torve.domain.spec import LANDING_FILE

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *NO_HOOKS, "-C", str(workdir), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    changed: list[str] = []

    for name in git("diff", "--name-only", "--diff-filter=A", f"{onto}..HEAD").stdout.split():
        if not LANDING_FILE.match(name.rsplit("/", 1)[-1]):
            continue

        path = workdir / name
        text = path.read_text(encoding="utf-8")
        named = re.search(r"^commit: '?([0-9a-f]{7,40})'?$", text, re.MULTILINE)

        if (
            named is None
            or git("merge-base", "--is-ancestor", named.group(1), "HEAD").returncode == 0
        ):
            continue

        added = git(
            "log", "--format=%H", "--diff-filter=A", f"{onto}..HEAD", "--", name
        ).stdout.split()

        if not added:
            continue

        work = git("rev-parse", f"{added[0]}^").stdout.strip()
        path.write_text(text[: named.start(1)] + work + text[named.end(1) :], encoding="utf-8")
        changed.append(name)

    if changed:
        git("add", "--", *changed)
        git("commit", "--amend", "--no-edit", "--no-verify")


# ....................... #


def _completes(root: Path, task_id: str, document: str) -> bool:
    """Whether this landing leaves the document complete (S-0093/D-1)."""

    from torve.application.forge import DocumentLanding, document_complete
    from torve.gates.context import load_task

    landings = []

    for carried in dict.fromkeys([*carried_tasks(root, document), task_id]):
        try:
            landings.append(DocumentLanding(task=load_task(contract_file(root, carried))))

        except (OSError, ValueError):
            continue

    return document_complete(document.rsplit("/", 1)[-1], landings, root)


def _completion_battery(root: Path, vcs: LaneVcs, document: str) -> str | None:
    """The fallback battery over the document branch tip (S-0093/D-1,
    S-0093/D-5): the red summary, or None when the battery is green.

    Judged under no one task's contract, as the rebase regate judges a
    document, so the manifest's fallback runs under its own cap."""

    from torve.gates.context import resolve_base

    workdir = root / naming.WORKTREE_DIR / f"lane-{document.rsplit('/', 1)[-1]}"
    tip = vcs.tip(root, document) or document

    # A rebase onto its own tip moves nothing; it is the port's one checkout.
    if not vcs.rebase_in_worktree(root, document, tip, workdir):
        return "the document branch could not be checked out"

    try:
        exit_code, summary = _regate(workdir, resolve_base(root, None) or "main", None)

    finally:
        vcs.remove_worktree(root, workdir)

    return summary if exit_code != 0 else None


# ....................... #


def record_battery_red(root: Path, document: str, target: str, tip: str, summary: str) -> None:
    """A red battery at completion as a review finding on *target*
    (S-0093/D-3): the shape the review leg's record source already turns into
    a round, with a command for evidence, so the round anchors to what the
    target touched and takes its phase's scope."""

    from torve.application.specquality import telemetry_file
    from torve.application.telemetry import RECORD_SCHEMA_VERSION, append_record
    from torve.domain.attempt import Finding

    failing = [part for part in summary.split(", ") if not part.endswith(("=pass", "=skipped"))]
    finding = Finding(
        severity="major",
        claim=f"the whole-suite battery is red over the complete document: {', '.join(failing)}",
        evidence=f"`torve gates run` on {document} at {tip[:12]} — {summary}",
    )

    append_record(
        telemetry_file(root),
        {
            "schema_version": RECORD_SCHEMA_VERSION,
            "kind": "review",
            "at": stamp(),
            "task_id": f"{target}-battery-{tip[:12]}",
            "target": target,
            "trigger": "task_gated",
            "battery": True,
            "branch": document,
            "findings": [finding.model_dump()],
        },
    )


def _battery_round(root: Path, document: str, resolved: Collection[str] = ()) -> str:
    """Where this document's one round for a red completion battery stands
    (S-0093/D-4, S-0101/D-1): "" before any, "pending" until the round lands
    or a person resolves it, "answered" after — the rerun that a second red
    escalates on.

    The round is read from its own contract, so it is recognised on any host
    from the record that minted it; whether it landed is the document's
    ledger and whether a person resolved it the board's resolutions the
    caller passes in."""

    from torve.application.reviewleg import round_tasks

    rounds = [task for task, contract in round_tasks(root, document) if contract.round.battery]

    if not rounds:
        return ""

    landed = set(document_tasks(root, document))

    return "answered" if any(task in landed or task in resolved for task in rounds) else "pending"


def _battery_target(root: Path, document: str) -> str | None:
    """The last task landed on the document that is not itself a round: a
    finding on a round's work opens no further round (S-0090/D-1). The rounds
    are read from their contracts, not from a row on the host that minted them
    (S-0099/D-5)."""

    from torve.application.reviewleg import round_tasks

    rounds = {task_id for task_id, _task in round_tasks(root)}
    landed = [task for task in document_tasks(root, document) if task not in rounds]

    return landed[-1] if landed else None


def document_tip_red(root: Path, branch: str, tip: str, status: StatusScm | None = None) -> bool:
    """Whether *tip* on *branch* is not green (S-0099/D-4): the completion
    battery's verdict is a commit status `torve/completion` on the tip it
    judged, and the draft flag reads it from the forge.

    A tip the forge holds no status for keeps the draft — an unreadable
    verdict is never a green one — and a later green battery at a later tip —
    a round's landing that moves the branch — publishes it ready. Without a
    forge (a local landing, or a caller that writes no status) a tip with no
    verdict is red by the same rule."""

    if status is None:
        return True

    return status.completion(tip) != "success"


def _wave_outstanding(root: Path, document: str, resolved: Collection[str] = ()) -> set[str]:
    """The rounds a review leg minted for *document* that have not yet landed
    on its branch (S-0097/D-6): queued, running or waiting to land. A round
    that landed is in the branch's ledger, and one that escalated or that a
    person resolved is not outstanding — and so releases the hold.

    A round with no run state is one no worker has claimed yet, which is
    queued and outstanding: the runner writes the state at dispatch, not at
    the mint. A round a person abandoned and `reap --escalated` swept also
    has none, so the resolutions come from the board, passed in by the
    caller (S-0101/D-2)."""

    from torve.application.reviewleg import round_tasks

    landed = set(document_tasks(root, document))
    outstanding: set[str] = set()

    for task, _contract in round_tasks(root, document):
        if not task or task in landed or task in resolved or task in outstanding:
            continue

        path = naming.state_file(root, task)

        if path.is_file() and RunState.load(path).state in (
            TaskState.ESCALATED,
            TaskState.ABANDONED,
        ):
            continue

        outstanding.add(task)

    return outstanding


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
    rounds: bool = False,
    status: StatusScm | None = None,
    resolved: Collection[str] = (),
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

    `rounds` is whether a review leg reads recorded findings at all
    (S-0096/D-2): a red completion battery is minted as a round only where
    one does, and everywhere else it escalates the completing task at once.
    """

    before = vcs.tip(root, document)
    vcs.reset_branch(root, document, tip)
    complete = _completes(root, task_id, document)
    round_ = _battery_round(root, document, resolved) if complete and rounds else ""

    # The battery reruns once the round answers, not on every pass while it
    # is outstanding (S-0093/D-4).
    if round_ == "pending":
        if before is not None:
            vcs.reset_branch(root, document, before)

        results.append(
            LaneResult(task_id, document, "awaiting round", "the red battery's round", tip)
        )

        return

    red = _completion_battery(root, vcs, document) if complete else None

    if red is not None:
        # A red completion is published all the same, with the pull request
        # kept a draft (S-0093/D-2, S-0098/D-4): the tip carries the red, so
        # the publisher holds the draft until a later battery turns it green,
        # and a round is cut from a branch that carries the completing work.
        # Withholding the landing cut every round about the red without the
        # work the red was about.
        engine_event(
            root,
            "lane_document_gates_red",
            {"task": task_id, "branch": document, "sha": tip, "gates": red},
        )
        reference = _publish(root, publish, task_id, document, tip, results)

        if reference is None:
            if before is not None:
                vcs.reset_branch(root, document, before)

            return

        # The leg raises the battery's round from this status alone (S-0103/D-4),
        # so a red the forge did not take is a red no round will answer.
        written = _write_verdict(root, status, tip, red)
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
        target = _battery_target(root, document)

        # One round per completion (S-0093/D-3, S-0093/D-4); a red rerun, or a
        # document with no landed task a round could be about, is a person's.
        # A lane whose caller mints no rounds (S-0096/D-2) escalates here too,
        # records no finding, and reaches a person on every repository. So does
        # a red whose status the forge refused or that no forge received: the
        # leg reads the round's finding from that status and would see none.
        if rounds and round_ == "" and target is not None and written:
            record_battery_red(root, document, target, tip, red)
            results.append(LaneResult(task_id, document, "gates red", red, tip))

            return

        path = naming.state_file(root, task_id)

        if path.is_file():
            state = RunState.load(path)

            if state.state is TaskState.READY:
                state.escalate(
                    EscalationReason.BLOCKER_FINDING,
                    f"the whole-suite battery is red over the complete document: {red}",
                )

        results.append(LaneResult(task_id, document, "gates red", f"escalated: {red}", tip))

        return

    outstanding = _wave_outstanding(root, document, resolved) - {task_id}

    if outstanding:
        # A wave costs one push (S-0097/D-6): the landing is on the branch as
        # this checkout has it, and the push waits for the round that leaves
        # none outstanding. The landing is still recorded — the branch carries
        # the task — so the next pass reads it back and no round is offered
        # twice.
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
                "pr": "",
                "held": True,
            },
        )
        results.append(
            LaneResult(
                task_id,
                document,
                "landed",
                f"held for the wave: {len(outstanding)} round(s) still outstanding",
                tip,
            )
        )

        return

    reference = _publish(root, publish, task_id, document, tip, results)

    if reference is None:
        if before is not None:
            vcs.reset_branch(root, document, before)

        return

    # The publisher composed the pull request before the tip's verdict could
    # exist on the forge, so it published a draft; once the green verdict is
    # written, publishing again turns the completed document ready.
    if complete and _write_verdict(root, status, tip, None):
        _publish(root, publish, task_id, document, tip, results)

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


def _write_status(
    root: Path, status: StatusScm | None, sha: str, context: str, state: str, description: str
) -> bool:
    """Write a judgement on *sha* under *context*, after the push that put
    *sha* on the forge: the forge refuses a status on a commit it does not
    hold. A refusal is a diagnostic, never the pass's end. Whether a status was
    written (S-0101/D-3, S-0101/D-8)."""

    if status is None:
        return False

    try:
        status.set_status(sha, state, description, context)

    except (RuntimeError, OSError) as exc:
        engine_event(
            root, "lane_status_refused", {"sha": sha, "context": context, "detail": str(exc)}
        )

        return False

    return True


def _status_at(status: StatusScm | None, sha: str, context: str) -> tuple[str, str] | None:
    """The forge's newest status under *context* on *sha*, or None. A forge that
    refuses the read is the same as one holding no status: the caller falls back
    to what it can observe for itself."""

    if status is None or not sha:
        return None

    try:
        return status.status(sha, context)

    except (RuntimeError, OSError):
        return None


def _red_again(
    root: Path, status: StatusScm | None, task_id: str, base_tip: str, head: str
) -> bool:
    """Whether this is the second red at the same base tip and head
    (S-0101/D-8). With a forge the count is the `torve/regate` status it holds
    for the head — a failure naming the same base tip means the last pass was
    red here too, however many processes ago that was. A forge holding no such
    status is the same as none at all, and the count falls back to the process's
    — a task branch the forge was never given, as under the document unit
    (S-0083/D-1), would otherwise be regated for ever (T-0528)."""

    found = _status_at(status, head, REGATE_CONTEXT)

    if found is not None and found[0] == "failure" and found[1] == base_tip:
        return True

    return _note_red(root, task_id, base_tip, head) >= 2


def _write_verdict(root: Path, status: StatusScm | None, tip: str, red: str | None) -> bool:
    """Write the completion battery's verdict as the `torve/completion` commit
    status on *tip* (S-0099/D-4): a refusal is a diagnostic, never the pass's
    end — the tip then has no verdict, which the draft flag reads as not green.
    Whether a status was written."""

    return _write_status(
        root,
        status,
        tip,
        COMPLETION_CONTEXT,
        "failure" if red else "success",
        red or "the completion battery passed",
    )


# ....................... #


# The verdicts a task's pull request can be given, in the order one supersedes
# another. `open` is not one: a pull request still open is a person's turn, not
# a verdict, and a person who reopens one clears what was recorded.
_PR_VERDICTS = ("landed", "closed")


def _verdict_ref(task_id: str, verdict: str) -> str:
    return f"refs/torve/pr-{verdict}/{task_id}"


def _recorded_verdict(root: Path, task_id: str) -> tuple[str, str] | None:
    """*(verdict, sha)* this checkout last read for the task's pull request, or
    None when it read none.

    A fact of the checkout's own git, never of a host's stream (S-0102/D-1):
    the forge is still what a host reads first, and this only keeps a verdict a
    pull request was already given — so one closed and its branch deleted is
    not quietly published again (S-0080/D-8, S-0080/D-17), and one a pass has
    already recorded is not recorded a second time."""

    for verdict in _PR_VERDICTS:
        sha = _ref_sha(root, _verdict_ref(task_id, verdict))

        if sha is not None:
            return verdict, sha

    return None


def _record_verdict(root: Path, task_id: str, verdict: str, sha: str) -> None:
    """Record *verdict* as this checkout's answer for the task's pull request,
    at the branch tip it was read against, dropping any verdict held before: a
    person who reopens a pull request shows the new one on the next pass."""

    for other in _PR_VERDICTS:
        if other != verdict:
            _drop_ref(root, _verdict_ref(task_id, other))

    _keep_ref(root, _verdict_ref(task_id, verdict), sha)


def _clear_verdict(root: Path, task_id: str) -> None:
    """Forget the recorded verdict: the forge reports the pull request open, so
    whatever was recorded no longer stands (S-0080/D-8)."""

    for verdict in _PR_VERDICTS:
        _drop_ref(root, _verdict_ref(task_id, verdict))


def _recorded_result(task_id: str, branch: str, verdict: str, sha: str) -> LaneResult:
    """What a verdict this checkout already holds reads as when the branch it
    was read against is no longer on the remote (S-0080/D-8, S-0080/D-17), so a
    deletion republishes nothing."""

    if verdict == "landed":
        return LaneResult(task_id, branch, "already landed", "the landing is recorded", sha)

    return LaneResult(
        task_id, branch, "abandoned", "the pull request was closed without merging", sha
    )


# ....................... #


def _forge_verdict(
    root: Path,
    vcs: LaneVcs,
    forge: Forge,
    task_id: str,
    base: str,
    branch: str,
    branch_tip: str,
    approver: str,
    results: list[LaneResult],
    recorded: tuple[str, str] | None = None,
) -> bool:
    """The later pass's read-back (S-0080/the-landing-arrives-as-an-answer): the engine does not
    watch for the merge, it asks about the candidates whose task branch the
    remote holds (S-0102/D-1) and answers each of the three verdicts a person
    can give.

    The state is read from the forge on every pass, at most once per pass,
    with no ledger of the lane's own (S-0099/D-3 made the document unit read
    this way): a merge, a close or a reopen a person makes is seen by any
    host's next pass, and a pass holding no task branch on the remote asks
    the forge nothing at all (S-0080/D-16).

    A verdict is recorded once, in the checkout's own git (S-0102/D-1): *recorded*
    is what this checkout last read for the task, so the same verdict read
    again is reported and not written a second time, and the branch it was
    read against going missing publishes nothing (S-0080/D-8, S-0080/D-17).

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

    info = forge(branch)

    if info is None:
        # The remote holds the branch and the forge knows no pull request for
        # it: a publication that pushed and then failed to open one, since the
        # publisher pushes before it opens. With no ledger to say a pull
        # request was ever opened, the candidate is published again rather
        # than given a verdict nobody can clear (S-0102/D-1).
        return False

    if info.state == "merged":
        sha = info.merge_commit or info.head_sha

        if recorded is not None and recorded[0] == "landed":
            results.append(
                LaneResult(
                    task_id,
                    branch,
                    "already landed",
                    f"pull request #{info.number} merged",
                    recorded[1],
                )
            )

            return True

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
        _record_verdict(root, task_id, "landed", branch_tip)

        results.append(
            LaneResult(task_id, branch, "landed", f"pull request #{info.number} merged", sha)
        )

        return True

    if info.state == "closed":
        if recorded is not None and recorded[0] == "closed":
            results.append(
                LaneResult(
                    task_id,
                    branch,
                    "abandoned",
                    f"pull request #{info.number} was closed without merging",
                    recorded[1],
                )
            )

            return True

        engine_event(
            root, "lane_pr_closed", {"task": task_id, "pr": info.number, "sha": branch_tip}
        )
        _record_verdict(root, task_id, "closed", branch_tip)
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

    # Open: a person reopened what a verdict was recorded for, or the pull
    # request is simply still open, and either way nothing recorded stands.
    _clear_verdict(root, task_id)

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


def open_documents(root: Path) -> list[str]:
    """The document branches whose pull request a pass reads back (S-0099/D-3):
    the remote `torve/S-*` branches carrying a landing file the base does not.
    A document whose landings the base holds — the shape a merge leaves — is
    closed by that act, and a branch the remote no longer has is nothing here.

    Read from the fetch the pass already made and from no memory of the lane's
    own: a merge, a close or a reopen a person makes is seen on the next pass
    of any host, and a pass holding no such branch asks the forge nothing at
    all (S-0080/D-16)."""

    from torve.gates.context import resolve_base

    refs = subprocess.run(
        [
            "git",
            *NO_HOOKS,
            "-C",
            str(root),
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin/torve/S-*",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if refs.returncode != 0:
        return []

    base = resolve_base(root, None)
    on_base = _landing_paths(root, base) if base else set()
    found: list[str] = []

    for ref in refs.stdout.split():
        if _landing_paths(root, ref) - on_base:
            found.append(ref.removeprefix("refs/remotes/origin/"))

    return sorted(found)


def _landing_paths(root: Path, ref: str) -> set[str]:
    """The landing files in *ref*'s tree, by path — what a document branch
    carries and the base does not (S-0099/D-3). A ref git cannot resolve
    contributes nothing."""

    from torve.domain.spec import EXECUTION_DIR, LANDING_FILE

    tree = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(root), "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        check=False,
    )

    if tree.returncode != 0:
        return set()

    found: set[str] = set()

    for path in tree.stdout.splitlines():
        directory, _, name = path.rpartition("/")

        if directory.rpartition("/")[2] == EXECUTION_DIR and LANDING_FILE.match(name):
            found.add(path)

    return found


def _unpublished(root: Path, vcs: LaneVcs, branch: str, remote: str) -> bool:
    """Whether this checkout's copy of *branch* carries landings the remote
    does not have yet — a wave's held landing (S-0097/D-6). The branch is kept
    as it is rather than reset to the remote, so the push that publishes the
    wave carries what it holds."""

    local = vcs.tip(root, branch)

    return local is not None and local != remote and not vcs.is_ancestor(root, local, remote)


# ....................... #


def _branch_landings(root: Path, branch: str) -> list[tuple[str, str, str, int]]:
    """*(at, task, commit, attempt)* for the landing files *branch* carries, in
    landing order (S-0099/D-2). The landing file is the carrier of what a
    document branch holds — a phase finished by hand, or one whose publication
    the forge refused, rides in the tree exactly as a lane landing does.

    The remote tip is read beside the local branch, so a host that only
    fetched (no local `torve/S-*` branch) reads what the host that landed
    reads, and a landing this host made but has not published yet still
    counts. Only the branch's own document's execution directory is listed.
    A tree that is no checkout, or a branch git cannot resolve, contributes
    nothing."""

    import yaml

    from torve.domain.spec import EXECUTION_DIR, LANDING_FILE

    document = branch.rsplit("/", 1)[-1]
    directory = f"{layout.SPECS_DIR}/{document}/{EXECUTION_DIR}"
    refs = [branch] if branch.startswith(("origin/", "refs/")) else [f"origin/{branch}", branch]
    found: dict[tuple[str, str, int], str] = {}

    for ref in refs:
        proc = subprocess.run(
            [
                "git",
                *NO_HOOKS,
                "-C",
                str(root),
                "ls-tree",
                "-r",
                "--name-only",
                ref,
                "--",
                directory,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            continue

        for path in proc.stdout.splitlines():
            matched = LANDING_FILE.match(path.rpartition("/")[2])

            if matched is None:
                continue

            key = (matched.group(3), matched.group(1), int(matched.group(2)))

            if key in found:
                continue

            commit = ""
            body = subprocess.run(
                ["git", *NO_HOOKS, "-C", str(root), "show", f"{ref}:{path}"],
                capture_output=True,
                text=True,
                check=False,
            )

            if body.returncode == 0:
                try:
                    raw = yaml.safe_load(body.stdout) or {}
                    commit = str(raw.get("commit") or "") if isinstance(raw, dict) else ""

                except yaml.YAMLError:
                    commit = ""

            found[key] = commit

    return sorted((at, task, commit, attempt) for (at, task, attempt), commit in found.items())


def carried_landings(root: Path, branch: str) -> list[tuple[str, str]]:
    """*(task, commit)* for the tasks a document branch carries, in landing
    order — the landing files on the branch tip (S-0099/D-2), newest landing
    of a task winning. What the document's pull request is composed from."""

    order: list[str] = []
    commits: dict[str, str] = {}

    for _at, task, commit, _attempt in _branch_landings(root, branch):
        if task not in commits:
            order.append(task)

        commits[task] = commit

    return [(task, commits[task]) for task in order]


def document_tasks(root: Path, branch: str) -> list[str]:
    """The tasks a document branch carries, in landing order: the landing
    files on its tip (S-0099/D-2). The pull request body, the completion
    battery and the wave count this one list, whichever host asks. Empty for
    a branch carrying no landing."""

    return [task for task, _commit in carried_landings(root, branch)]


def carried_tasks(root: Path, branch: str) -> list[str]:
    """The tasks a document branch carries whose contract the corpus holds —
    the same carrier as `document_tasks` (S-0099/D-2), so a stray file names
    no task."""

    return [task for task in document_tasks(root, branch) if contract_file(root, task).is_file()]


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
    number: int,
    results: list[LaneResult],
    resolved: Collection[str] = (),
    status: StatusScm | None = None,
) -> None:
    """A pull request still open (S-0083/D-13). Against an unmoved base it
    already shows the tree the battery judged and it is a person's turn. A
    base that moved under it is the ordinary case for a branch that lives
    days: rebase in a disposable worktree, re-run the battery over the
    rebased tree, republish under lease — bounded once per base tip, so a
    branch against a moving `main` cannot rebase itself in a loop.

    A conflict aborts and escalates for a person; the lane never resolves
    one, and the branch is left exactly as it was (S-0083/D-14). The conflict
    is also written as a `torve/rebase` failure on the branch tip naming the
    base tip (S-0101/D-3), so a person sees it in the pull request's checks
    however many hosts look; a later clean rebase writes `success`."""

    from torve.gates.context import resolve_base

    tasks = document_tasks(root, branch)

    base = resolve_base(root, None, fetch=True)
    base_tip = vcs.tip(root, base) if base else None
    # The remote's copy is the one rebased (S-0091/D-1). A hand commit pushed
    # to the open pull request is on it and not on this checkout's ref, and
    # the republish leases against the fetch just made, so rebasing the local
    # ref would drop that commit without a refusal.
    remote = vcs.remote_tip(root, branch)

    if (remote is not None and _unpublished(root, vcs, branch, remote)) or _wave_outstanding(
        root, branch, resolved
    ):
        # The wave has not finished: a head's rounds are still queued or
        # running, so nothing here resets or republishes the branch, and any
        # landing already held on it stays unpublished (S-0097/D-6). The last
        # round's landing publishes, and a later pass rebases what it
        # published as it always has.
        results.append(
            LaneResult(
                document,
                branch,
                "pull request open",
                f"pull request #{number} awaits a person",
                vcs.tip(root, branch) or "",
            )
        )

        return

    if remote is not None:
        vcs.reset_branch(root, branch, remote)

    branch_tip = vcs.tip(root, branch)

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

    reported = _status_at(status, branch_tip, REBASE_CONTEXT)

    if reported is not None and reported == ("failure", base_tip):
        # Once per base tip (S-0006/D-12, S-0101/D-3): the forge already holds
        # this tip's conflict against this base, written by whichever host
        # found it, so the collision is the person's and re-running it buys no
        # new signal — nor a second status.
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
        # S-0101/D-3: the conflict is a judgement about a commit the forge
        # holds, so it is a status keyed by that commit, seen in the checks.
        _write_status(root, status, branch_tip, REBASE_CONTEXT, "failure", base_tip)
        engine_event(
            root,
            "lane_document_conflict",
            {"branch": branch, "base_tip": base_tip, "tasks": tasks, "pr": number},
        )
        _escalate_document(
            root,
            branch,
            tasks,
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
        _restate_landings(workdir, base)
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
    reference = _publish(root, publish, tasks[-1], branch, rebased, results)

    if reference is None:
        vcs.reset_branch(root, branch, branch_tip)
        return

    # S-0101/D-3: a later clean rebase at a new tip carries its own `success`,
    # superseding the failure the conflict wrote.
    _write_status(root, status, rebased, REBASE_CONTEXT, "success", base_tip)
    engine_event(
        root,
        "lane_document_rebased",
        {"branch": branch, "sha": rebased, "pr": reference, "tasks": tasks},
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
    resolved: Collection[str] = (),
    named: dict[str, list[str]] | None = None,
    status: StatusScm | None = None,
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """The later pass's read-back at the document unit (S-0083/P-3, S-0099/D-3).

    One forge call per open document — the remote `torve/S-*` branches
    carrying a landing file the base does not — and none at all for a pass
    holding none (S-0083/D-12) — a document of six phases costs one call per
    pass rather than six, because the question is now per document, and a
    branch whose landings the base holds is not asked about at all.

    A document branch the remote no longer has but a ready candidate still
    names is asked about too (S-0101/D-9): the forge keeps the pull request
    after its branch goes, so a person closing one and deleting the branch
    must not bring its tasks back. Only a closure is acted on there — no pull
    request yet is simply a branch not cut — and the document is asked about
    once per pass however many candidates name it.

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
    request. Beside it, the branches this pass read merged — a branch landed
    onto again is cut from the base first, so its next pull request carries
    only what the base lacks (S-0099/D-3).
    """

    carried: dict[str, tuple[str, str]] = {}
    merged: set[str] = set()
    # The once-per-base-tip bound on a rebase (S-0083/D-13) is the one fact
    # this read-back keeps in the lane's own records; which branches it reads
    # back at all comes from the remote (S-0099/D-3).
    named = named or {}
    open_branches = open_documents(root)
    on_remote = set(open_branches)

    for branch in [*open_branches, *(b for b in named if b not in on_remote)]:
        document = branch.rsplit("/", 1)[-1]
        info = forge(branch)

        if branch not in on_remote:
            # The remote never had it, or no longer does: a candidate names a
            # document whose pull request only the forge still knows about.
            if info is not None and info.state == "closed":
                tasks = named.get(branch, [])
                engine_event(
                    root,
                    "lane_document_closed",
                    {"branch": branch, "pr": info.number, "tasks": tasks},
                )
                results.append(
                    LaneResult(
                        document,
                        branch,
                        "abandoned",
                        f"pull request #{info.number} was closed without merging, "
                        f"abandoning {', '.join(tasks)}",
                    )
                )
                carried.update(dict.fromkeys(tasks, ("abandoned", branch)))

            continue

        tasks = document_tasks(root, branch)

        if info is None:
            engine_event(root, "lane_pr_unresolved", {"branch": branch, "tasks": tasks})
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
            merged.add(branch)

            engine_event(
                root,
                "lane_document_landed",
                {
                    "branch": branch,
                    "sha": sha,
                    "pr": info.number,
                    "tasks": tasks,
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
                    f"pull request #{info.number} merged, carrying {', '.join(tasks)}",
                    sha,
                )
            )

        elif info.state == "closed":
            engine_event(
                root,
                "lane_document_closed",
                {"branch": branch, "pr": info.number, "tasks": tasks},
            )
            results.append(
                LaneResult(
                    document,
                    branch,
                    "abandoned",
                    f"pull request #{info.number} was closed without merging, "
                    f"abandoning {', '.join(tasks)}",
                )
            )

        else:
            _record_threads(root, branch, info)
            _rebase_document(
                root,
                vcs,
                publish,
                document,
                branch,
                info.number,
                results,
                resolved,
                status,
            )

        carried.update(
            dict.fromkeys(
                tasks,
                ("abandoned", branch)
                if info is not None and info.state == "closed"
                else ("already landed", branch),
            )
        )

    return carried, merged


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
    rounds: bool = False,
    status: StatusScm | None = None,
    resolved: Collection[str] = (),
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
                root,
                vcs,
                publish,
                task_id,
                document,
                branch_tip,
                "fast-forward",
                approver,
                results,
                rounds,
                status,
                resolved,
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
    rounds: bool = False,
    status: StatusScm | None = None,
    resolved: Collection[str] = (),
) -> None:
    engine_wt = root / naming.WORKTREE_DIR / task_id

    if engine_wt.exists():
        # The run's own worktree still pins the branch, and git refuses to
        # check a branch out twice. A READY candidate's worktree is
        # disposable — the work lives on the branch, and the reap would
        # collect it anyway — so the lane releases it for the rebase. A task
        # the board holds has no contract at the root (S-0056/D-9): the copy
        # in this worktree is the one the landing still reads, so it is put
        # back where `contract_file` finds it.
        # ponytail: the copy lives until a reap sweeps `.wt/<task>`; the
        # record's `task.minted` is the durable one, once the lane holds the log.
        projected = contract_file(root, task_id)
        held = (
            projected.read_bytes()
            if engine_wt in projected.parents and projected.is_file()
            else None
        )
        vcs.remove_worktree(root, engine_wt)

        if held is not None:
            projected.parent.mkdir(parents=True, exist_ok=True)
            projected.write_bytes(held)

    workdir = root / naming.WORKTREE_DIR / f"lane-{task_id}"

    # Where the branch stood before the rebase moves it. A red battery has to put
    # it back: `git rebase` runs in a worktree checked out on the branch, so it
    # moves the ref, and a branch left on the new base reads to the next pass as
    # "the base has not moved under this branch" — the fast-forward path, which
    # skips the battery. T-0391 landed that way: one `rebase (finish)` in the
    # reflog, an `acceptance=fail` on the first pass, a fast-forward on the
    # second. Nothing bad shipped that time; the mechanism does not care.
    before_rebase = vcs.tip(root, branch) or branch_tip

    upstream = _cut_point(root, branch, base, task_id)

    if not vcs.rebase_in_worktree(root, branch, base, workdir, upstream):
        _handle_rebase_conflict(root, state, task_id, branch, base, base_tip, on_conflict, results)
        return

    try:
        _restate_landings(workdir, base)
        exit_code, summary = _regate(workdir, base, task_id)

    finally:
        vcs.remove_worktree(root, workdir)

    if exit_code != 0:
        vcs.reset_branch(root, branch, before_rebase)

        if _red_again(root, status, task_id, base_tip, branch_tip):
            # S-0101/D-8: the same candidate red at the same base and head a
            # second time is a person's to look at, not another rebase's.
            engine_event(
                root,
                "lane_candidate_stuck",
                {
                    "task": task_id,
                    "base": base_tip,
                    "head": branch_tip,
                    "gates": summary,
                },
            )

            if state.state is TaskState.READY:
                state.escalate(
                    EscalationReason.POISON_CEILING,
                    f"red again at the same base and head: {summary}",
                )

            results.append(
                LaneResult(
                    task_id,
                    branch,
                    "stuck",
                    "red again at the same base and head — escalated",
                )
            )

            return

        # The red is the forge's to remember (S-0101/D-8): a status on the head
        # naming the base tip, so a later `torve merge` reads it back rather
        # than starting its count at zero.
        _write_status(root, status, branch_tip, REGATE_CONTEXT, "failure", base_tip)
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
                root,
                vcs,
                publish,
                task_id,
                document,
                rebased_tip,
                "rebased",
                approver,
                results,
                rounds,
                status,
                resolved,
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
    rounds: bool = False,
    status: StatusScm | None = None,
    resolved: Collection[str] = (),
) -> None:
    base_tip = vcs.tip(root, base) or base

    if vcs.is_ancestor(root, base_tip, branch_tip):
        # The base has not moved under this branch: the tree that would
        # land is byte-identical to the one the gates measured.
        _land_fast_forward(
            root,
            vcs,
            task_id,
            branch,
            branch_tip,
            dry_run,
            approver,
            results,
            publish,
            document,
            rounds,
            status,
            resolved,
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
        rounds,
        status,
        resolved,
    )


# ....................... #


def promotion_needs_durable_store(unit: str, pull_request: bool) -> bool:
    """S-0099/D-11: a document unit and a pull-request landing both need the
    Postgres store — a mock is in-process, so a reaper on another host sees
    nothing of what this one holds."""

    return unit == "document" or pull_request


_P = ParamSpec("_P")


def _one_lane(fn: Callable[_P, list[LaneResult]]) -> Callable[_P, list[LaneResult]]:
    """One lane per checkout at a time. Every manager worker runs a lane leg and
    `torve merge` runs one too, and two passes rebasing in the same `lane-*`
    checkout break each other's battery. An exclusive `flock` on
    `.wt/lane.lock` for the whole pass: a second pass finds it held and says the
    lane is busy instead of waiting, and the kernel releases it when its holder
    exits, so a killed lane never leaves it held."""

    @functools.wraps(fn)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> list[LaneResult]:
        # `root` by position or by name, as `process_lane` has always taken it.
        root = Path(str(kwargs["root"] if "root" in kwargs else args[0]))
        lock = root / naming.WORKTREE_DIR / "lane.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)

        with lock.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

            except BlockingIOError:
                return [
                    LaneResult(
                        "", "", "lane busy", "another process is running the lane on this checkout"
                    )
                ]

            return fn(*args, **kwargs)

    return run


@_one_lane
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
    rounds: bool = False,
    status: StatusScm | None = None,
    store: str = "",
    resolved: Collection[str] = (),
) -> list[LaneResult]:
    """One pass of the lane. A `publish` is `pull_request` mode (S-0080/D-3):
    the pass runs unchanged to the landing and then publishes the candidate
    instead of fast-forwarding the base. The mode is a term of configuration
    (S-0080/D-1) decided by the caller — the lane is handed the act, never the
    question of whether a remote exists.

    A `forge` is the same mode's read-back: on a later pass each candidate
    whose task branch the remote holds is asked about, and the verdict is
    recorded (S-0080/the-landing-arrives-as-an-answer, S-0102/D-1) — at the
    document unit once per open document rather than once per task
    (S-0083/D-12). A verdict this checkout recorded stands in for a branch the
    remote no longer holds, so a closed pull request is not published again
    (S-0080/D-8, S-0080/D-17). A dry run asks nothing, as it publishes nothing.

    `unit` is the landing unit (S-0083/D-1), a term of the same configuration:
    `document` lands every phase of a document onto the document's own branch
    behind one pull request, `task` opens one per task. It governs only where
    there is a pull request to be one per, so a `local` landing ignores it
    (S-0083/D-2), and only a candidate whose contract names a document
    (S-0083/D-4).

    `rounds` is whether a review leg reads recorded findings (S-0096/D-2):
    only then may the lane mint a red completion battery's round, and the
    callers derive it from the threads configuration. Off, a red battery
    escalates the completing task `blocker_finding` at once and records no
    finding."""

    base = vcs.current_branch(root)

    # S-0099/D-11: a document unit and a pull-request landing both need the
    # Postgres store — a mock is in-process, so a reaper on another host sees
    # nothing. `store` is the configured adapter, empty when the caller names
    # none (a direct caller that is not a run).
    if store and store != "postgres" and promotion_needs_durable_store(unit, publish is not None):
        raise RuntimeError(
            f"store {store!r} is in-process and test-only; promotion unit "
            "'document' and landing 'pull_request' need a postgres store"
        )

    if not dry_run:
        dirt = [p for p in vcs.dirty_paths(root) if not _engine_record(root, p)]

        if dirt:
            raise RuntimeError(
                f"the working tree on {base!r} is not clean — the lane "
                "fast-forwards the checkout, commit or stash first: " + ", ".join(sorted(dirt)[:5])
            )

    approver = vcs.approver(root)
    results: list[LaneResult] = []

    carried: dict[str, tuple[str, str]] = {}
    merged_documents: set[str] = set()

    if forge is not None and publish is not None and not dry_run:
        # Before the candidates, so a document a person merged or closed this
        # evening is recorded before anything lands onto its branch again.
        # Asked per document rather than per task, from the lane's own
        # records, so a pass holding none asks nothing (S-0083/D-12).
        named: dict[str, list[str]] = {}

        if unit == "document":
            # A document branch the remote no longer has is still named by a
            # ready candidate whose contract names it, when this checkout
            # holds the branch a previous pass cut (S-0101/D-9): the forge
            # keeps the pull request after the branch goes, so a closure must
            # outlive the deletion. A document this host never held is not
            # asked about, so a pass holding nothing open asks the forge
            # nothing (S-0080/D-16).
            for state in ready_candidates(root):
                document_branch = _document_branch(root, vcs, state.task_id, dry_run=True)

                if document_branch is not None and vcs.tip(root, document_branch) is not None:
                    named.setdefault(document_branch, []).append(state.task_id)

        carried, merged_documents = _document_verdicts(
            root, vcs, forge, publish, results, resolved, named, status
        )

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
            _document_branch(root, vcs, task_id, dry_run, merged_documents)
            if publish is not None and unit == "document"
            else None
        )
        target = document or base

        if vcs.is_ancestor(root, branch_tip, target):
            results.append(LaneResult(task_id, branch, "already landed", sha=branch_tip))
            continue

        # A candidate whose task branch the remote holds is asked about
        # (S-0102/D-1): the state is the forge's, read on every pass. A branch
        # the remote does not hold is not a pull request to read back, and the
        # verdict this checkout recorded stands in for it — a pull request a
        # person closed is not published again when its branch goes
        # (S-0080/D-8, S-0080/D-17).
        if forge is not None and not dry_run:
            recorded = _recorded_verdict(root, task_id)

            if vcs.remote_tip(root, branch) is None:
                if recorded is not None:
                    results.append(_recorded_result(task_id, branch, *recorded))

                    continue

            elif _forge_verdict(
                root,
                vcs,
                forge,
                task_id,
                target,
                branch,
                branch_tip,
                approver,
                results,
                recorded,
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
            rounds,
            status,
            resolved,
        )

    return results
