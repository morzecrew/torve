"""`torve plan` — the deterministic minter (S-0007/torve-plan). One accepted,
committed specification in; implement-task contracts out. No model call at
any point, for any reason (S-0007/D-1): the planner is a projection of decisions
someone already made, and the absence of that capability — not policy — is
what keeps it from growing into an autonomous orchestrator (§2).

Admission (§3.1) refuses by name with a configuration error: a draft has no
settled decisions to inherit, an unsettled dependency breaks the
copy-grade-at-write-time guarantee, a superseded document's decisions no
longer stand, and a cycle means the readiness order is fiction. Exactly one
document per invocation (§3.2, S-0007/D-8) — batch planning inherits from
documents still being amended, which is the drift this system removes.

The minted contract copies the document's decision table verbatim — grade
and declared paths at write time — and takes intent, scope and acceptance
from the Phasing entry. Dry-run is the default (D-11's convention): minting
writes `.torve/tasks/T-nnnn/contract.yaml`, ids derived max+1 and never
reused, the same discipline as RFC numbering (S-0016/D-24 by analogy).

Beside `inherit_decisions` sits the document-less lane's mechanism,
`standing_decisions` (S-0030): rows of every accepted document whose
declared paths intersect a contract's scope, copied at write time the same
way. `torve plan` itself is unchanged — a document's own table is inherited
whole (S-0007/D-22); standing inheritance is what adoption and the contract lint
read.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from pathspec import GitIgnoreSpec

from torve.application import sizing
from torve.config import layout, spec
from torve.domain.attempt import SizeVerdict
from torve.domain.spec import Corpus, Document, Phase, document_id, number_of
from torve.domain.task import InheritedDecision, Scope, Task
from torve.domain.vocabulary import GRADES

if TYPE_CHECKING:
    from collections.abc import Iterable

    from torve.application.eventlog import EventLog
    from torve.application.manager import Board

# ----------------------- #

TASK_DIR_NAME = re.compile(r"^T-(\d{4,})$")


# ....................... #


class PlanError(ValueError):
    """A refusal at admission or minting — a configuration error (exit 3),
    naming the offending document, edge or entry."""


# ....................... #


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


# ....................... #


def _require_committed(root: Path, doc: Path) -> None:
    """Only a committed, reviewed document is admissible (S-0007/D-2): the commit
    is the human signature in the loop, and planning uncommitted text plans
    something nobody reviewed."""

    rel = doc.resolve().relative_to(root.resolve())
    tracked = _git(root, "ls-files", "--error-unmatch", str(rel))

    if tracked.returncode != 0:
        raise PlanError(f"{doc.name} is not tracked by git — commit the reviewed document first")

    dirty = _git(root, "status", "--porcelain", "--", str(rel))

    if dirty.returncode != 0:
        raise PlanError(f"cannot verify {doc.name} against git: {dirty.stderr.strip()}")

    if dirty.stdout.strip():
        raise PlanError(
            f"{doc.name} has uncommitted changes — `torve plan` accepts only the "
            "committed, reviewed text (S-0007/D-2)"
        )


# ....................... #


def _admit(corpus: Corpus, number: str) -> None:
    doc = corpus.document(number)

    if doc is None or doc.archived:
        raise PlanError(f"{number} is not in the corpus path")

    if doc.status != "accepted":
        raise PlanError(
            f"{number} is {doc.status} — a {doc.status} document "
            "has no settled decisions to inherit (§3.1)"
        )

    if doc.superseded_by:
        raise PlanError(
            f"{number} is superseded by {doc.superseded_by} — its decisions no longer stand"
        )

    for dep in doc.depends_on:
        target = corpus.document(dep)

        if target is None:
            raise PlanError(f"{number} depends on {dep}, which does not exist")

        if target.status != "accepted":
            raise PlanError(
                f"{number} depends on {dep}, which is {target.status} — "
                "inheriting a grade from an unsettled document breaks the "
                "copy-at-write-time guarantee (S-0007/D-7)"
            )

    # A cycle reachable from this document (§3.1) — DFS over depends_on.
    state: dict[str, int] = {}

    def visit(num: str, trail: list[str]) -> None:
        state[num] = 1

        for dep in _depends(corpus, num):
            if state.get(dep) == 1:
                cycle = " -> ".join([*trail, num, dep])
                raise PlanError(f"depends_on cycle reachable from {number}: {cycle}")

            if state.get(dep) != 2 and corpus.document(dep) is not None:
                visit(dep, [*trail, num])

        state[num] = 2

    visit(number, [])


# ....................... #


def _depends(corpus: Corpus, number: str) -> list[str]:
    doc = corpus.document(number)

    return list(doc.depends_on) if doc is not None else []


# ....................... #


@dataclass(frozen=True)
class PlannedTask:
    task: Task
    title: str
    size: SizeVerdict


# ....................... #


@dataclass(frozen=True)
class PlanReport:
    number: str
    document: str  # the identifier the contract's `spec` names (S-0059/D-1)
    tasks: list[PlannedTask]


# ....................... #


def scopes_clash(left: list[str], right: list[str]) -> bool:
    """Whether two tasks may not run at the same time (S-0019/A-6, S-0019/D-14).

    An empty allow-set is unconstrained (S-0002/scope-in-detail), and a task that may
    touch anything can prove itself disjoint from nothing — so it clashes
    with every other task, including another unconstrained one. Everything
    else is `globs_intersect`'s conservative overlap.

    One rule with two callers: the standing loop asks it of the run-state
    files it can see, the manager asks it of the board it folds, and the
    two answering differently is how two agents end up editing one file.
    """

    if not left or not right:
        return True

    return globs_intersect(left, right)


# ....................... #


def globs_intersect(left: list[str], right: list[str]) -> bool:
    """Conservative overlap between two allow-sets: identical globs, or one
    set's glob matching another's glob read as a literal path (with its own
    wildcard tail stripped). Definite overlaps only — this refuses what is
    provably shared, not what is cleverly disjoint."""

    if set(left) & set(right):
        return True

    def literals(globs: list[str]) -> list[str]:
        found: list[str] = []

        for glob in globs:
            stripped = glob.split("*", 1)[0].rstrip("/")

            if stripped:
                found.append(stripped)

        return found

    left_spec = GitIgnoreSpec.from_lines(left)
    right_spec = GitIgnoreSpec.from_lines(right)

    return any(right_spec.match_file(lit) for lit in literals(left)) or any(
        left_spec.match_file(lit) for lit in literals(right)
    )


# ....................... #


def next_task_number(root: Path, taken: Iterable[str] = ()) -> int:
    """Max over the task directories and *taken* (the board's ids, when a
    store holds the tasks — S-0056/D-9), plus one; never reused."""

    tasks_dir = root / layout.TORVE_DIR / "tasks"
    numbers = [0]

    for task_id in taken:
        found = TASK_DIR_NAME.match(task_id)

        if found:
            numbers.append(int(found.group(1)))

    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            found = TASK_DIR_NAME.match(entry.name)

            if found:
                numbers.append(int(found.group(1)))

    return max(numbers) + 1


# ....................... #


def document_of(reference: str) -> str:
    """The document a contract names, as `S-NNNN` — from `spec`, or from
    the path a contract minted before S-0059 carried, whatever its shape
    was — so a recorded mint is recognised across the conversions
    (S-0059/D-3); the empty string when the reference names none."""

    try:
        return document_id(reference)
    except ValueError:
        return ""


def _already_minted(
    root: Path, document: str, phases: set[int] | None, board: Board | None = None
) -> list[str]:
    """Task ids whose contracts already cite this document and one of these
    phases — minting twice mints duplicate work, and what to do with the
    first batch is a human decision. The board's contracts count when a
    store holds the tasks (S-0056/D-9); the files count either way.

    *phases* None is every phase: what `after` names is a whole document, so
    the edge names every task minted from it (S-0085/D-2, S-0085/D-5)."""

    clashes: list[str] = []
    wanted = document_of(document)

    for view in board.tasks.values() if board is not None else []:
        contract = view.contract

        if contract is None or not contract.spec:
            continue

        if phases is not None and contract.phase not in phases:
            continue

        if document_of(contract.spec) == wanted:
            clashes.append(view.task_id)

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return clashes

    for path in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

        except yaml.YAMLError:
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)

        minted = document_of(str(record.get("spec") or ""))

        if minted == wanted and (phases is None or record.get("phase") in phases):
            clashes.append(str(record.get("id", path.parent.name)))

    return sorted(set(clashes))


# ....................... #


def inherit_decisions(doc: Document) -> list[InheritedDecision]:
    """The document's rows as a contract inherits them (§3.1): grade and
    paths copied at write time, so the executor sees what stood when the
    task was minted. One implementation — `torve plan` and adoption mint
    the same rows or the two drift (S-0007/A-3)."""

    name = Path(doc.path).name if doc.path else doc.id
    decisions: list[InheritedDecision] = []

    for row in doc.decisions:
        if row.grade not in GRADES:
            raise PlanError(
                f"{name}: decision {row.id} has grade {row.grade!r} — "
                "not mintable (run `torve spec check`)"
            )

        # S-0054/D-4: a row whose check would block must name the test that
        # proves the check can fail — the manifest's twin rule, one level up.
        if row.check_state == "blocking" and not row.check_twin:
            raise PlanError(
                f"{name}: decision {row.id} has a blocking check and no check_twin — "
                "not mintable (S-0054/D-4)"
            )

        decisions.append(
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
        )

    return decisions


# ....................... #


def load_corpus(rfc_dir: Path) -> Corpus:
    """The corpus as the planner reads it: the loader's refusals as
    `PlanError`, so nothing mints from a document `spec check` refuses."""

    try:
        return spec.load_corpus(rfc_dir)
    except spec.SpecError as exc:
        raise PlanError("; ".join(exc.problems)) from exc


# ....................... #


def standing_decisions(rfc_dir: Path, scope_allow: list[str]) -> list[InheritedDecision]:
    """The document-less lane's inheritance (S-0030/standing-inheritance): every accepted
    document's rows are read through the same one reader `inherit_decisions`
    is (S-0007/A-3), and a row is inherited when any of its declared paths
    intersects `scope_allow` (`globs_intersect`, conservative — a false
    inclusion costs a few contract lines, a false exclusion costs the
    silence check). Rows without declared paths are never standing — they
    govern their own document's work only (S-0030/D-1). Draft, superseded and
    archived documents are never read: their decisions do not stand.
    Deterministic: corpus order, then document order."""

    standing: list[InheritedDecision] = []

    for doc in load_corpus(rfc_dir).standing():
        if doc.superseded_by:
            continue

        for row in inherit_decisions(doc):
            if row.paths and globs_intersect(row.paths, scope_allow):
                standing.append(row)

    return standing


# ....................... #


def plan_document(
    root: Path,
    rfc_dir: Path,
    identifier: str,
    *,
    board: Board | None = None,
    refresh: bool = False,
    phases: set[int] | None = None,
) -> PlanReport:
    """Admission plus minting, dry: nothing is written. Raises PlanError on
    any refusal (§3.1) — each names the offending document or entry. With
    a *board* (S-0056/D-9), task numbers and prior mints are read from the
    record as well as from the task directories.

    *refresh* derives for `--refresh` (S-0088/D-1): the same admission and the
    same derivation, without the two refusals that are about minting — a
    phase already minted is what a refresh is *for*, and the `after` edges
    are the minted contracts' own, which a refresh keeps."""

    files = spec.document_dirs(rfc_dir)

    try:
        number = document_id(identifier)
    except ValueError:
        raise PlanError(f"{identifier!r} names no document") from None

    if number_of(number) not in files:
        raise PlanError(f"no document {number} under {rfc_dir}")

    doc_path = files[number_of(number)]

    _require_committed(root, doc_path)
    corpus = load_corpus(rfc_dir)
    _admit(corpus, number)
    doc = corpus.document(number)
    assert doc is not None
    entries = doc.phasing

    if not entries:
        raise PlanError(
            f"{doc_path.name} has no phasing — a `phasing` list is what `torve plan` "
            "consumes (spec-writer rule 2)"
        )

    known = {e.phase for e in entries}

    for entry in entries:
        for dep in entry.depends_on:
            if dep not in known:
                raise PlanError(
                    f"{doc_path.name}: phase {entry.phase} depends_on {dep}, which no entry defines"
                )

    # Same-phase scopes must not intersect (§3): overlapping tasks cannot run
    # in parallel and the plan silently serialises.
    by_phase: dict[int, list[Phase]] = {}

    for entry in entries:
        by_phase.setdefault(entry.phase, []).append(entry)

    for phase, siblings in sorted(by_phase.items()):
        for i, one in enumerate(siblings):
            for other in siblings[i + 1 :]:
                if globs_intersect(one.scope, other.scope):
                    raise PlanError(
                        f"phase {phase}: scopes of {one.title!r} and {other.title!r} "
                        "intersect — same-phase tasks must be disjoint (§3)"
                    )

    decisions = inherit_decisions(doc)

    document = doc.id

    # `--phase`: a phase an amendment added to a document whose earlier
    # phases are minted and landed (bloomery S-0002/A-4). Only the named
    # phases are minted; the others are what their `depends_on` edges point
    # at, through the contracts those phases already have.
    if phases is not None:
        known = {e.phase for e in entries}
        missing = sorted(phases - known)

        if missing:
            raise PlanError(
                f"{document}: phase(s) {', '.join(map(str, missing))} not in its phasing"
            )

        entries = [e for e in entries if e.phase in phases]

    clashes = [] if refresh else _already_minted(root, document, {e.phase for e in entries}, board)

    if clashes:
        raise PlanError(
            f"phase(s) already minted from {document}: {', '.join(clashes)} — "
            "what to do with the existing tasks is a human decision"
        )

    # S-0085/D-2: `after` is one list of document ids and becomes contract
    # `depends_on`, so nothing downstream learns a new word. A named document
    # whose implementation is complete has already landed and adds no edge;
    # one with no minted tasks names nothing an edge could point at.
    after_tasks: list[str] = []

    for reference in [] if refresh else doc.after:
        target = corpus.document(reference)

        if target is None:
            raise PlanError(f"{document}: after names {reference!r}, no such document")

        if target.implementation == "complete":
            continue

        minted = _already_minted(root, target.id, None, board)

        if not minted:
            raise PlanError(
                f"{document}: after names {target.id}, which has no minted tasks — "
                "plan it first, or an edge to it names nothing"
            )

        after_tasks += minted

    after_tasks = sorted(set(after_tasks))
    ordered = sorted(entries, key=lambda e: e.phase)  # stable: document order within a phase
    next_number = next_task_number(root, board.tasks if board is not None else ())
    ids_by_phase: dict[int, list[str]] = {}
    planned: list[PlannedTask] = []

    for offset, entry in enumerate(ordered):
        task_id = f"T-{next_number + offset:04d}"
        ids_by_phase.setdefault(entry.phase, []).append(task_id)

    if phases is not None:
        for entry in ordered:
            for predecessor in entry.depends_on:
                if predecessor not in ids_by_phase:
                    ids_by_phase[predecessor] = _already_minted(
                        root, document, {predecessor}, board
                    )

    for offset, entry in enumerate(ordered):
        # A task with an in-document predecessor waits through it; one with
        # none is where the other document's landing has to be waited on.
        within = [tid for p in entry.depends_on for tid in ids_by_phase.get(p, [])]
        task = Task(
            id=f"T-{next_number + offset:04d}",
            spec=document,
            phase=entry.phase,
            role="implement",  # review tasks are minted by the runner at `gated` (§3)
            intent=entry.intent.strip(),
            depends_on=within or list(after_tasks),
            scope=Scope(allow=list(entry.scope)),
            acceptance=list(entry.acceptance),
            decisions=decisions,
            tier_variant=entry.tier_variant or None,
            character=entry.character or None,
        )

        planned.append(PlannedTask(task=task, title=entry.title, size=sizing.estimate(task)))

    return PlanReport(number=number, document=document, tasks=planned)


# ....................... #


class _ContractDumper(yaml.SafeDumper):
    """Multiline strings as literal blocks (S-0007/A-1): the default single-quoted
    style writes every newline as a blank-line escape, and a contract's
    intent read like a double-spaced telegram."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)  # pyright: ignore[reportUnknownMemberType]


_ContractDumper.add_representer(str, _str_representer)


def _dump_contract(document: dict[str, object]) -> str:
    return yaml.dump(
        document, Dumper=_ContractDumper, sort_keys=False, allow_unicode=True, width=88
    )


def write_contract(root: Path, task: Task, title: str = "", *, minted_by: str = "") -> Path:
    """One contract as a file under the task directory: the header names
    what wrote it, the body is the contract."""

    path = layout.task_file(root, task.id)

    if path.exists():
        raise PlanError(f"{path} already exists — task ids are never reused")

    path.parent.mkdir(parents=True, exist_ok=True)
    # S-0057/D-5: the first line names the schema `torve init` writes, two
    # levels up from the task directory in the default layout.
    header = f"{spec.SCHEMA_HEADER}../../schemas/contract.json\n"
    header += (
        f"# Minted by `torve plan {minted_by}` — phase {task.phase}: {title}\n"
        if minted_by
        else "# Projected from the record for this attempt (S-0056 S-0056/D-9): the board "
        "holds the task; this file is what the gates and the log verbs read.\n"
    )
    document = task.model_dump()
    document["title"] = document.get("title") or title.replace("-", " ")
    path.write_text(header + _dump_contract(document), encoding="utf-8")

    return path


def write_contracts(root: Path, report: PlanReport) -> list[Path]:
    """The file mode (S-0056/D-9): one directory per task under the root."""

    return [
        write_contract(root, planned.task, planned.title, minted_by=report.number)
        for planned in report.tasks
    ]


def project_contract(worktree: Path, task: Task) -> Path | None:
    """S-0056/D-9: the contract the board holds, written into the worktree for
    the attempt that reads it — the gates, `torve log owed`, the log
    beside it. A worktree that already carries the file (the file mode,
    where the contract is tracked) is left alone; None says so."""

    if layout.task_file(worktree, task.id).is_file():
        return None

    return write_contract(worktree, task, task.title or "")


async def mint_contracts(
    log: EventLog, report: PlanReport, *, partition: str, actor_id: str = "plan"
) -> list[str]:
    """S-0056/D-9: mint into the record and write no file — the same mint the
    manager's importer records, so a task planned here and a task scanned
    from a file are the same row on the board."""

    from torve.application.residency import mint

    return await mint(
        log,
        {planned.task.id: planned.task for planned in report.tasks},
        partition=partition,
        actor_id=actor_id,
    )


# ----------------------- #
# The refresh (S-0088/the-verb): an amendment reaching the contracts
# its document already minted, through the one path that knows how a
# document becomes a contract.

# What the document owns, and therefore what a refresh rewrites (S-0088/D-1):
# the derived `Task`'s fields minus identity (`id`, `spec`, `phase`, `role`,
# `title`) and edges (`depends_on`), which the mint decided and a refresh
# keeps.
REFRESHED_FIELDS = ("intent", "scope", "acceptance", "decisions", "tier_variant", "character")


@dataclass(frozen=True)
class RefreshedTask:
    """One already-minted phase as the refresh leaves it: the fields that
    differ from the derivation, the reason it was left alone if it was, and
    the rewritten contract when there is one to write."""

    task_id: str
    phase: int
    title: str
    changed: list[str]
    held: str = ""  # why it was left alone; empty means it may be rewritten
    task: Task | None = None  # the rewritten contract — None when held or unchanged


@dataclass(frozen=True)
class RefreshReport:
    document: str
    tasks: list[RefreshedTask]

    @property
    def rewritten(self) -> list[tuple[RefreshedTask, Task]]:
        """Each entry with a contract to write, paired with it."""

        return [(one, one.task) for one in self.tasks if one.task is not None]


# ....................... #


def minted_contracts(root: Path, document: str, board: Board | None = None) -> dict[str, Task]:
    """The contracts already minted from *document*, by task id — the board's
    when a store holds the tasks (S-0056/D-9), the files otherwise. An
    unreadable contract is skipped, as it is everywhere else: one malformed
    file is not a reason to refuse the rest."""

    from torve.gates.context import load_task

    wanted = document_of(document)
    found: dict[str, Task] = {}

    for view in board.tasks.values() if board is not None else []:
        contract = view.contract

        if contract is not None and document_of(contract.spec or "") == wanted:
            found[view.task_id] = contract

    for path in sorted((root / layout.TORVE_DIR / "tasks").glob("T-*/contract.yaml")):
        try:
            task = load_task(path)

        except ValueError:
            continue

        if document_of(task.spec or "") == wanted:
            found.setdefault(task.id, task)

    return found


# ....................... #


def _beyond_refresh(root: Path, rfc_dir: Path, document: str) -> dict[str, str]:
    """Task id -> why a refresh leaves it alone, for everything a landing
    proves (S-0088/D-2): a landing file under the document's `execution/` or the
    document-less one, a lane landing on the stream, or a document branch
    carrying the task's landing commit. A landed phase's terms are the ones
    its landing was judged by."""

    from torve.application.projections import stream_rows
    from torve.domain.spec import LANDING_FILE

    directory = spec.document_dir(rfc_dir, document)
    files = spec.landing_files_in(layout.execution_dir(root))
    files += spec.landing_files(directory) if directory is not None else []
    held = {found.group(1): "landed" for path in files if (found := LANDING_FILE.match(path.name))}

    for row in stream_rows(root):
        task_id = str(row.get("task") or "")

        if row.get("event") != "lane_landed" or not task_id:
            continue

        held[task_id] = (
            f"carried by the branch {row.get('branch')}"
            if row.get("unit") == "document"
            else "landed"
        )

    return held


def _in_flight(root: Path, task_id: str, board: Board | None) -> str:
    """Why a refresh leaves a task alone for its state, or the empty string:
    an attempt in flight reads the contract it was dispatched under
    (S-0088/D-2), while an escalated or reaped task starts its next attempt from
    base and reads the contract afresh."""

    from torve.application.manager import IN_FLIGHT
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    view = board.tasks.get(task_id) if board is not None else None

    if view is not None:
        if view.state in IN_FLIGHT:
            return f"running ({view.state})"

        if view.state is TaskState.READY or view.landed_sha:
            return "landed"

    path = naming.state_file(root, task_id)

    if not path.exists():
        return ""

    state = RunState.load(path).state

    if state in IN_FLIGHT:
        return f"running ({state})"

    return "landed" if state is TaskState.READY else ""


# ....................... #


def refresh_document(
    root: Path, rfc_dir: Path, identifier: str, *, board: Board | None = None
) -> RefreshReport:
    """The document derived as for a mint, and each already-minted phase's
    contract compared with the derivation field by field (S-0088/D-1). Dry:
    nothing is written and nothing is recorded.

    A phase's contracts are paired with its phasing entries in id order,
    which is mint order — the planner numbers the entries of a phase in
    document order, so the nth contract of a phase is the nth entry's. A
    contract whose entry the phasing no longer carries is left alone and
    named; a phase never minted is not minted here (that is `plan`'s job,
    refused while any phase is minted, unchanged)."""

    report = plan_document(root, rfc_dir, identifier, board=board, refresh=True)
    landed = _beyond_refresh(root, rfc_dir, report.document)
    existing: dict[int, list[Task]] = {}

    for task in sorted(minted_contracts(root, report.document, board).values(), key=lambda t: t.id):
        existing.setdefault(task.phase, []).append(task)

    found: list[RefreshedTask] = []

    for phase, contracts in sorted(existing.items()):
        entries = [planned for planned in report.tasks if planned.task.phase == phase]

        for offset, current in enumerate(contracts):
            if offset >= len(entries):
                found.append(
                    RefreshedTask(current.id, phase, current.title, [], "no phasing entry")
                )

                continue

            derived = entries[offset].task
            changed = [
                field
                for field in REFRESHED_FIELDS
                if getattr(current, field) != getattr(derived, field)
            ]

            held = landed.get(current.id) or _in_flight(root, current.id, board)

            rewritten = (
                current.model_copy(update={field: getattr(derived, field) for field in changed})
                if changed and not held
                else None
            )

            found.append(
                RefreshedTask(current.id, phase, entries[offset].title, changed, held, rewritten)
            )

    return RefreshReport(document=report.document, tasks=found)


# ....................... #


def record_refresh(root: Path, document: str, one: RefreshedTask) -> None:
    """One rewrite on the engine's stream (S-0088/D-3): a contract edited with no
    record is what the refresh exists to end."""

    from torve.application.telemetry import engine_event

    engine_event(
        root,
        "contract_refreshed",
        {"task": one.task_id, "spec": document, "phase": one.phase, "fields": one.changed},
    )


def refresh_contracts(root: Path, report: RefreshReport) -> list[Path]:
    """The file mode: each rewritten contract written back over its own file,
    keeping the header that names what minted it and stamping the refresh
    beside it (S-0088/D-3)."""

    from torve.base.clock import stamp

    written: list[Path] = []
    at = stamp()

    for one, task in report.rewritten:
        path = layout.task_file(root, task.id)
        header: list[str] = []

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break

            if not line.startswith("# Refreshed by"):
                header.append(line)

        header.append(f"# Refreshed by `torve plan {report.document} --refresh` at {at}")
        path.write_text(
            "\n".join(header) + "\n" + _dump_contract(task.model_dump()), encoding="utf-8"
        )

        record_refresh(root, report.document, one)
        written.append(path)

    return written


async def refresh_into_record(
    root: Path, log: EventLog, report: RefreshReport, *, partition: str, actor_id: str = "plan"
) -> list[str]:
    """The partition mode (S-0088/D-3): the rewritten contracts through the
    residency path the mint uses, so the board's row is the rewritten one on
    the next pass — a re-mint transitions nothing and never happens under an
    attempt in flight (S-0049/D-3, S-0049/D-4)."""

    from torve.application.residency import mint

    for one, _task in report.rewritten:
        record_refresh(root, report.document, one)

    return await mint(
        log,
        {task.id: task for _one, task in report.rewritten},
        partition=partition,
        actor_id=actor_id,
    )


# ....................... #


@dataclass(frozen=True)
class StaleTask:
    """One non-terminal task whose source document became superseded (§3.3,
    charter S-0001/A-8)."""

    task_id: str
    document: str
    superseded_by: str | None
    state: str
    action: str  # escalated | would escalate | skipped (terminal) | already escalated (...)


# ....................... #


def reconcile(root: Path, rfc_dir: Path, dry_run: bool = True) -> list[StaleTask]:
    """Mark every non-terminal task minted from a superseded document,
    escalating each as `stale_inheritance` (S-0007/D-10, charter S-0001/A-8). Nothing is
    deleted or rewritten — what to do with in-flight work is a human
    decision, and this verb records a fact about a task's inheritance rather
    than touching a running aggregate (§2). A task that never ran gains a
    state file through the claimed -> escalated edge the reaper minted; a
    task already escalated for another reason is reported and left — one
    escalation, one human decision at a time."""

    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TERMINAL, EscalationReason, TaskState

    superseded: dict[str, str | None] = {}

    for doc in load_corpus(rfc_dir).documents:
        if doc.archived:
            continue

        if doc.status == "superseded" or doc.superseded_by:
            superseded[doc.id] = doc.superseded_by or None

    found: list[StaleTask] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir() or not superseded:
        return found

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(contract.read_text(encoding="utf-8"))

        except yaml.YAMLError:
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)
        document = document_of(str(record.get("spec") or ""))

        if document not in superseded:
            continue

        task_id = str(record.get("id", contract.parent.name))
        by = superseded[document]

        detail = (
            f"minted from {document}, superseded by {by or 'an unset successor'} "
            "(charter S-0001/A-8): its inherited decisions no longer stand"
        )

        state_path = naming.state_file(root, task_id)

        if state_path.exists():
            state = RunState.load(state_path)

            if state.state in TERMINAL:
                found.append(
                    StaleTask(task_id, document, by, str(state.state), "skipped (terminal)")
                )

                continue

            if state.state is TaskState.ESCALATED:
                reason = state.escalation.reason if state.escalation else "unknown"

                action = (
                    "already escalated (stale_inheritance)"
                    if reason == "stale_inheritance"
                    else f"already escalated ({reason}) — left for triage"
                )

                found.append(StaleTask(task_id, document, by, str(state.state), action))
                continue

            if not dry_run:
                state.escalate(EscalationReason.STALE_INHERITANCE, detail)

            found.append(
                StaleTask(
                    task_id,
                    document,
                    by,
                    str(state.state),
                    "escalated" if not dry_run else "would escalate",
                )
            )
        else:
            if not dry_run:
                state = RunState(task_id=task_id, path=state_path)

                state.transition(
                    TaskState.CLAIMED, "torve plan --reconcile: claiming to record the fact"
                )

                state.escalate(EscalationReason.STALE_INHERITANCE, detail)

            found.append(
                StaleTask(
                    task_id,
                    document,
                    by,
                    "unstarted",
                    "escalated" if not dry_run else "would escalate",
                )
            )

    return found
