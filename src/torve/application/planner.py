"""`torve plan` — the deterministic minter (RFC 0007 §3). One accepted,
committed specification in; implement-task contracts out. No model call at
any point, for any reason (D-7.1): the planner is a projection of decisions
someone already made, and the absence of that capability — not policy — is
what keeps it from growing into an autonomous orchestrator (§2).

Admission (§3.1) refuses by name with a configuration error: a draft has no
settled decisions to inherit, an unsettled dependency breaks the
copy-grade-at-write-time guarantee, a superseded document's decisions no
longer stand, and a cycle means the readiness order is fiction. Exactly one
document per invocation (§3.2, D-7.8) — batch planning inherits from
documents still being amended, which is the drift this system removes.

The minted contract copies the document's decision table verbatim — grade
and declared paths at write time — and takes intent, scope and acceptance
from the Phasing entry. Dry-run is the default (D-11's convention): minting
writes `.torve/tasks/T-nnnn/contract.yaml`, ids derived max+1 and never
reused, the same discipline as RFC numbering (D-A.17 by analogy).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pathspec import GitIgnoreSpec

from torve.application import sizing
from torve.config import layout, rfc_parse
from torve.domain.attempt import SizeVerdict
from torve.domain.rfc import GRADES
from torve.domain.task import InheritedDecision, Scope, Task

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
    """Only a committed, reviewed document is admissible (D-7.2): the commit
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
            "committed, reviewed text (D-7.2)"
        )


# ....................... #


def _admit(files: dict[str, Path], number: str) -> None:
    frontmatter: dict[str, dict[str, object]] = {}

    for num, path in files.items():
        fm = rfc_parse.parse_frontmatter(path.read_text(encoding="utf-8"))

        if fm is not None:
            frontmatter[num] = fm

    doc = frontmatter.get(number)

    if doc is None:
        raise PlanError(f"RFC {number} has no readable frontmatter")

    status = str(doc.get("status", ""))

    if status != "accepted":
        raise PlanError(
            f"RFC {number} is {status or 'unreadable'} — a {status or 'malformed'} document "
            "has no settled decisions to inherit (§3.1)"
        )

    if doc.get("superseded_by"):
        raise PlanError(
            f"RFC {number} is superseded by {doc.get('superseded_by')} — its decisions "
            "no longer stand"
        )

    for dep in _depends(frontmatter, number):
        target = frontmatter.get(dep)

        if target is None:
            raise PlanError(f"RFC {number} depends on {dep}, which does not exist")

        if str(target.get("status", "")) != "accepted":
            raise PlanError(
                f"RFC {number} depends on {dep}, which is {target.get('status')} — "
                "inheriting a grade from an unsettled document breaks the "
                "copy-at-write-time guarantee (D-7.7)"
            )

    # A cycle reachable from this document (§3.1) — DFS over depends_on.
    state: dict[str, int] = {}

    def visit(num: str, trail: list[str]) -> None:
        state[num] = 1

        for dep in _depends(frontmatter, num):
            if state.get(dep) == 1:
                cycle = " -> ".join([*trail, num, dep])
                raise PlanError(f"depends_on cycle reachable from {number}: {cycle}")

            if state.get(dep) != 2 and dep in frontmatter:
                visit(dep, [*trail, num])

        state[num] = 2

    visit(number, [])


# ....................... #


def _depends(frontmatter: dict[str, dict[str, object]], number: str) -> list[str]:
    raw = frontmatter.get(number, {}).get("depends_on")

    if not isinstance(raw, list):
        return []

    return [str(dep) for dep in cast("list[object]", raw)]


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
    document: str  # repo-relative path, what the contract's `rfc` field cites
    tasks: list[PlannedTask]


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


def next_task_number(root: Path) -> int:
    tasks_dir = root / layout.TORVE_DIR / "tasks"
    numbers = [0]

    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            found = TASK_DIR_NAME.match(entry.name)

            if found:
                numbers.append(int(found.group(1)))

    return max(numbers) + 1


# ....................... #


def _already_minted(root: Path, document: str, phases: set[int]) -> list[str]:
    """Task ids whose contracts already cite this document and one of these
    phases — minting twice mints duplicate work, and what to do with the
    first batch is a human decision."""

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return []

    clashes: list[str] = []

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(contract.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)

        if str(record.get("rfc", "")) == document and record.get("phase") in phases:
            clashes.append(str(record.get("id", contract.parent.name)))

    return clashes


# ....................... #


def inherit_decisions(text: str, name: str) -> list[InheritedDecision]:
    """The document's decision table as a contract inherits it (§3.1): grade
    and paths copied at write time, so the executor sees what stood when the
    task was minted. One implementation — `torve plan` and adoption mint the
    same rows or the two drift (A-47).
    """

    decisions: list[InheritedDecision] = []

    for row in rfc_parse.decision_table(text):
        if row.grade not in GRADES:
            raise PlanError(
                f"{name}: decision {row.identifier} has grade {row.grade!r} — "
                "not mintable (run `torve rfc check`)"
            )

        decisions.append(
            InheritedDecision(
                id=row.identifier,
                grade=row.grade,
                text=row.text.strip(),
                paths=row.paths,
            )
        )

    return decisions


# ....................... #


def plan_document(root: Path, rfc_dir: Path, identifier: str) -> PlanReport:
    """Admission plus minting, dry: nothing is written. Raises PlanError on
    any refusal (§3.1) — each names the offending document or entry."""

    files = rfc_parse.rfc_files(rfc_dir)
    number = identifier.strip().removesuffix(".md")

    if number not in files:
        matches = [n for n, p in files.items() if p.name == identifier or p.stem == number]

        if len(matches) == 1:
            number = matches[0]
        else:
            raise PlanError(f"no RFC {identifier!r} under {rfc_dir}")

    doc_path = files[number]

    _require_committed(root, doc_path)
    _admit(files, number)

    text = doc_path.read_text(encoding="utf-8")

    try:
        entries = rfc_parse.parse_phasing(text)
    except ValueError as exc:
        raise PlanError(f"{doc_path.name}: Phasing section does not mint — {exc}") from exc

    if not entries:
        raise PlanError(
            f"{doc_path.name} has no mintable Phasing section — a fenced YAML block "
            "under `## Phasing` is what `torve plan` consumes (rfc-writer rule 2)"
        )

    # Same-phase scopes must not intersect (§3): overlapping tasks cannot run
    # in parallel and the plan silently serialises.
    by_phase: dict[int, list[rfc_parse.PhasingEntry]] = {}

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

    decisions = inherit_decisions(text, doc_path.name)

    document = str(doc_path.resolve().relative_to(root.resolve()))
    clashes = _already_minted(root, document, {e.phase for e in entries})

    if clashes:
        raise PlanError(
            f"phase(s) already minted from {document}: {', '.join(clashes)} — "
            "what to do with the existing tasks is a human decision"
        )

    ordered = sorted(entries, key=lambda e: e.phase)  # stable: document order within a phase
    next_number = next_task_number(root)
    ids_by_phase: dict[int, list[str]] = {}
    planned: list[PlannedTask] = []

    for offset, entry in enumerate(ordered):
        task_id = f"T-{next_number + offset:04d}"
        ids_by_phase.setdefault(entry.phase, []).append(task_id)

    for offset, entry in enumerate(ordered):
        task = Task(
            id=f"T-{next_number + offset:04d}",
            rfc=document,
            phase=entry.phase,
            role="implement",  # review tasks are minted by the runner at `gated` (§3)
            intent=entry.intent.strip(),
            depends_on=[tid for p in entry.depends_on for tid in ids_by_phase.get(p, [])],
            scope=Scope(allow=list(entry.scope)),
            acceptance=list(entry.acceptance),
            decisions=decisions,
        )
        planned.append(PlannedTask(task=task, title=entry.title, size=sizing.estimate(task)))

    return PlanReport(number=number, document=document, tasks=planned)


# ....................... #


def write_contracts(root: Path, report: PlanReport) -> list[Path]:
    written: list[Path] = []

    for planned in report.tasks:
        path = layout.task_dir(root, planned.task.id) / "contract.yaml"

        if path.exists():
            raise PlanError(f"{path} already exists — task ids are never reused")

        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Minted by `torve plan {report.number}` — phase "
            f"{planned.task.phase}: {planned.title}\n"
        )
        path.write_text(
            header
            + yaml.safe_dump(
                planned.task.model_dump(), sort_keys=False, allow_unicode=True, width=88
            ),
            encoding="utf-8",
        )
        written.append(path)

    return written


# ....................... #


@dataclass(frozen=True)
class StaleTask:
    """One non-terminal task whose source document became superseded (§3.3,
    charter A-22)."""

    task_id: str
    document: str
    superseded_by: str | None
    state: str
    action: str  # escalated | would escalate | skipped (terminal) | already escalated (...)


# ....................... #


def reconcile(root: Path, rfc_dir: Path, dry_run: bool = True) -> list[StaleTask]:
    """Mark every non-terminal task minted from a superseded document,
    escalating each as `stale_inheritance` (D-7.10, charter A-22). Nothing is
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

    for _number, path in rfc_parse.rfc_files(rfc_dir).items():
        fm = rfc_parse.parse_frontmatter(path.read_text(encoding="utf-8"))

        if fm is None:
            continue

        if str(fm.get("status", "")) == "superseded" or fm.get("superseded_by"):
            document = str(path.resolve().relative_to(root.resolve()))
            by = fm.get("superseded_by")
            superseded[document] = str(by) if by else None

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
        document = str(record.get("rfc", ""))

        if document not in superseded:
            continue

        task_id = str(record.get("id", contract.parent.name))
        by = superseded[document]
        detail = (
            f"minted from {document}, superseded by {by or 'an unset successor'} "
            "(charter A-22): its inherited decisions no longer stand"
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
