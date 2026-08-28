"""`torve rfc health` — the attribution join and the decision-level report
(RFC 0022 §5.1, §5.2): telemetry, task logs and contracts, indexed by task
id, joined to the corpus for the row as it stands and to each contract for
the row as it was minted.

The report never edits a decision table, proposes no text and calls no model
(D-22.1, LOCKED): everything here is a read over `.torve/tasks/*/contract.yaml`
and `log.yaml`, run state and the RFC corpus — a plain reader over JSONL-shaped
YAML, no new dependency, so moving to RFC 0004 §6 stage 2 is a change of
reader, not a rewrite (D-22.5). The grade compared is always the one copied
onto the contract at mint time, never the row as the corpus stands today
(D-22.2) — that is why every population is built from `Task.decisions`, and
the corpus itself is consulted only for whether an amendment later cited the
identifier, never for its current grade or paths.

`touched` is read from the contract's own declared `scope.allow` intersecting
the decision's declared paths (`torve.application.planner.globs_intersect`,
the same primitive that already answers "do two glob sets overlap" for
same-phase scopes) rather than a literal post-hoc `git diff` of historical
shas. Two considered reasons, logged as a departure from D-22.4/D-22.5's
literal "diff intersected" wording under T-0099: the scope gate already
refuses a landed diff that leaves `scope.allow` (`torve.gates.scope`), so the
declared area is a safe over-approximation of the true diff for any task that
ever passed or was explicitly bypassed; and it keeps this module exactly what
D-22.5 asks for — YAML in, no git subprocess, no dependency on a historical
sha still being resolvable.

No score is computed anywhere in this module (D-22.3): a population's
`reading` is `None` until its relevant count clears `floor`, and every ratio
is printed beside the denominator it was taken over (D-22.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from torve.application.planner import globs_intersect
from torve.application.runstate import RunState
from torve.base import naming
from torve.config import layout, rfc_parse
from torve.domain.states import TaskState
from torve.gates.decisions_reported import ACTIONS as LOG_ACTIONS
from torve.gates.decisions_reported import parse_log

# ----------------------- #

DEFAULT_FLOOR = 5

_LANDED_STATE = str(TaskState.READY)
_ABANDONED_STATE = str(TaskState.ABANDONED)
_ESCALATED_STATE = str(TaskState.ESCALATED)
_QUEUED_STATE = str(TaskState.QUEUED)


# ....................... #


@dataclass(frozen=True)
class TaskFacts:
    """One minted task, the join's left side (RFC 0022 §5.1): its own
    contract, its own log, its own run state — task id keyed, nothing merged
    across tasks here. `decisions` carries the grade and paths exactly as
    copied onto this contract at mint time (D-22.2)."""

    id: str
    rfc: str | None  # D-22.9: carried through so a document-level reader can bucket None on its own
    scope_allow: list[str]
    decisions: list[dict[str, Any]]  # [{id, grade, paths}], mint-time copies
    log_entries: list[dict[str, Any]]
    state: str | None
    history: list[dict[str, str]] = field(default_factory=list)

    # ....................... #

    @property
    def landed(self) -> bool:
        """D-22.10: the mergeable-and-done reading of "landed" — the engine's
        own terminal success state, never a git-history guess."""

        return self.state == _LANDED_STATE

    # ....................... #

    @property
    def abandoned(self) -> bool:
        return self.state == _ABANDONED_STATE

    # ....................... #

    @property
    def requeued_after_escalation(self) -> bool:
        """A human sent the task back around the loop (RFC 0006 D-6.10,
        charter A-40) rather than the row being amended — the LOCKED
        reading's other branch (§5.2)."""

        return any(
            e.get("from") == _ESCALATED_STATE and e.get("to") == _QUEUED_STATE for e in self.history
        )


# ....................... #


def _load_yaml_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, yaml.YAMLError):
        return None

    if not isinstance(raw, dict):
        return None

    return cast("dict[str, Any]", raw)


# ....................... #


def _contract_decisions(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("decisions")

    if not isinstance(raw, list):
        return []

    found: list[dict[str, Any]] = []

    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue

        entry = cast("dict[str, Any]", item)
        identifier = entry.get("id")

        if not identifier:
            continue

        paths = entry.get("paths")

        found.append(
            {
                "id": str(identifier),
                "grade": str(entry.get("grade") or ""),
                "paths": [str(p) for p in cast("list[object]", paths)] if isinstance(paths, list) else [],
            }
        )

    return found


# ....................... #


def _contract_scope_allow(record: dict[str, Any]) -> list[str]:
    scope = record.get("scope")

    if not isinstance(scope, dict):
        return []

    allow = cast("dict[str, Any]", scope).get("allow")

    if not isinstance(allow, list):
        return []

    return [str(p) for p in cast("list[object]", allow)]


# ....................... #


def _log_entries(log_path: Path) -> list[dict[str, Any]]:
    """A missing log is an empty log (A-13, D-3.21) — reused from the same
    `decisions-reported` parser so the report and the gate never disagree
    about what an entry is."""

    if not log_path.is_file():
        return []

    document, error = parse_log(log_path.read_text(encoding="utf-8"))

    if document is None or error is not None:
        return []

    entries = document.get("entries")

    if not isinstance(entries, list):
        return []

    return [e for e in cast("list[object]", entries) if isinstance(e, dict)]


# ....................... #


def _run_state(root: Path, task_id: str) -> RunState | None:
    state_path = naming.state_file(root, task_id)

    if not state_path.is_file():
        return None

    try:
        return RunState.load(state_path)

    except (OSError, ValueError, KeyError, TypeError):
        return None


# ....................... #


def read_tasks(root: Path) -> list[TaskFacts]:
    """Every task the corpus knows, joined to its own log and run state
    (RFC 0022 §5.1). A directory with no readable `contract.yaml` is not a
    task the join can use and is skipped, not fabricated."""

    found: list[TaskFacts] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return found

    for contract_path in sorted(tasks_dir.glob("T-*/contract.yaml")):
        record = _load_yaml_dict(contract_path)

        if record is None:
            continue

        task_id = str(record.get("id", contract_path.parent.name))
        run_state = _run_state(root, task_id)

        found.append(
            TaskFacts(
                id=task_id,
                rfc=str(record["rfc"]) if record.get("rfc") else None,
                scope_allow=_contract_scope_allow(record),
                decisions=_contract_decisions(record),
                log_entries=_log_entries(contract_path.parent / "log.yaml"),
                state=str(run_state.state) if run_state is not None else None,
                history=run_state.history if run_state is not None else [],
            )
        )

    return found


# ....................... #


def _amendment_cited_ids(rfc_dir: Path) -> set[str]:
    """Every decision identifier mentioned in any corpus document's Amendments
    section (RFC 0022 §10: the honest window is "any time after" until there
    are enough pairs to see a distribution — so this checks presence, never
    a date)."""

    cited: set[str] = set()

    for path in rfc_parse.rfc_files(rfc_dir).values():
        text = path.read_text(encoding="utf-8")
        section = rfc_parse.AMENDMENTS_SECTION.search(text)

        if not section:
            continue

        body = rfc_parse.strip_fences(text[section.end() :])
        cited.update(rfc_parse.DECISION_CITE.findall(body))

    return cited


# ....................... #


def identifiers_for_document(rfc_dir: Path, number: str) -> set[str] | None:
    """Every identifier RFC `number` defines, or None when no such document
    exists in the corpus — the CLI's `torve rfc health NNNN` filter."""

    files = rfc_parse.rfc_files(rfc_dir)
    path = files.get(number)

    if path is None:
        return None

    return {row.identifier for row in rfc_parse.decision_table(path.read_text(encoding="utf-8"))}


# ....................... #


def _bucket(buckets: dict[str, dict[str, Any]], identifier: str) -> dict[str, Any]:
    return buckets.setdefault(
        identifier,
        {
            "identifier": identifier,
            "grades": {},
            "inherited_tasks": set(),
            "inherited_landed": 0,
            "touched_tasks": set(),
            "touched_landed": 0,
            "cited": 0,
            "by_action": {},
            "halted_tasks": set(),
            "departed_tasks": set(),
            "decided_tasks": set(),
            "decided_claims": [],
            "requeued_after_halt": 0,
        },
    )


# ....................... #


def _touched(task: TaskFacts, paths: list[str]) -> bool:
    """Whether the task's declared footprint could have reached this
    decision's declared area — see the module docstring for why this reads
    `scope.allow` rather than a historical `git diff`. Unconstrained scope
    (empty `allow`, RFC 0002 §6) cannot be proven not to touch anything."""

    if not paths:
        return False

    if not task.scope_allow:
        return True

    return globs_intersect(task.scope_allow, paths)


# ....................... #


def _finish(bucket: dict[str, Any], floor: int, amended_ids: set[str]) -> dict[str, Any]:
    identifier = str(bucket["identifier"])
    grades = cast("dict[str, int]", bucket["grades"])
    grade = next(iter(grades)) if len(grades) == 1 else None
    inherited = len(cast("set[str]", bucket["inherited_tasks"]))
    touched = len(cast("set[str]", bucket["touched_tasks"]))
    halted = len(cast("set[str]", bucket["halted_tasks"]))
    departed = len(cast("set[str]", bucket["departed_tasks"]))
    decided = len(cast("set[str]", bucket["decided_tasks"]))
    cited = int(bucket["cited"])
    amended = identifier in amended_ids
    reading: str | None = None
    detail = ""

    if grade == "ASSUMED" and touched >= floor:
        ratio = departed / touched

        if ratio > 0.5:
            reading = "propose-open"
            detail = (
                f"departed in {departed}/{touched} task(s) that touched its paths "
                f"({ratio:.0%}) — propose regrading to OPEN"
            )
    elif grade == "OPEN" and decided >= floor:
        reading = "review-decided-claims"
        detail = (
            f"{decided} task(s) decided this OPEN row independently — read the claims "
            "below for whether they agree; promote to a graded row if so (no automatic "
            "judgement of \"identically\": D-22.1 invokes no model)"
        )
    elif grade == "LOCKED" and halted >= floor:
        if amended:
            reading = "over-grade-or-wrong-boundary"
            detail = (
                f"halted {halted} time(s), and the corpus later amended a row citing "
                f"{identifier} — an over-grade or a wrong boundary"
            )
        elif bucket["requeued_after_halt"]:
            reading = "healthy-boundary"
            detail = (
                f"halted {halted} time(s), re-queued rather than amended — the lock "
                "is doing its job"
            )

    if reading is None and grade == "LOCKED" and touched >= floor and cited == 0:
        reading = "decoration-or-paths-defect"
        detail = (
            f"{touched} task(s) touched the declared paths and none cited {identifier} — "
            "either the Paths cell names the wrong area, or the silence check is not "
            "reaching it (both are defects, and different ones, D-22.4)"
        )

    return {
        "identifier": identifier,
        "grade": grade,
        "grades": dict(sorted(grades.items())),
        "inherited": inherited,
        "inherited_landed": bucket["inherited_landed"],
        "inherited_tasks": sorted(cast("set[str]", bucket["inherited_tasks"])),
        "touched": touched,
        "touched_landed": bucket["touched_landed"],
        "touched_tasks": sorted(cast("set[str]", bucket["touched_tasks"])),
        "cited": cited,
        "by_action": dict(sorted(cast("dict[str, int]", bucket["by_action"]).items())),
        "halted": halted,
        "requeued_after_halt": bucket["requeued_after_halt"],
        "amended": amended,
        "decided": decided,
        "decided_claims": [
            {"task": t, "claim": c} for t, c in cast("list[tuple[str, str]]", bucket["decided_claims"])[:10]
        ],
        "reading": reading,
        "reading_detail": detail,
    }


# ....................... #


def decision_report(root: Path, rfc_dir: Path, floor: int = DEFAULT_FLOOR) -> dict[str, Any]:
    """The whole of RFC 0022 §5.2: one population per decision identifier
    inherited anywhere in `.torve/tasks`, each carrying its raw counts always
    and a `reading` only once the relevant count clears `floor` (D-22.8).
    No score anywhere (D-22.3)."""

    tasks = read_tasks(root)
    amended_ids: set[str] = _amendment_cited_ids(rfc_dir) if rfc_dir.is_dir() else set()
    buckets: dict[str, dict[str, Any]] = {}

    for task in tasks:
        cited_here: dict[str, list[dict[str, Any]]] = {}

        for entry in task.log_entries:
            identifier = str(entry.get("decision") or "")

            if not identifier or identifier == "unlisted":
                continue  # D-22.9 family: an unlisted entry cites no declared row

            cited_here.setdefault(identifier, []).append(entry)

        for decision in task.decisions:
            identifier = decision["id"]
            grade = decision["grade"]
            paths = cast("list[str]", decision["paths"])
            bucket = _bucket(buckets, identifier)
            bucket["grades"][grade] = bucket["grades"].get(grade, 0) + 1
            bucket["inherited_tasks"].add(task.id)

            if task.landed:
                bucket["inherited_landed"] += 1

            if _touched(task, paths):
                bucket["touched_tasks"].add(task.id)

                if task.landed:
                    bucket["touched_landed"] += 1

            entries = cited_here.get(identifier, [])
            bucket["cited"] += len(entries)
            halted_here = False

            for entry in entries:
                action = str(entry.get("action") or "")

                if action not in LOG_ACTIONS:
                    continue

                bucket["by_action"][action] = bucket["by_action"].get(action, 0) + 1

                if action == "halted":
                    halted_here = True
                    bucket["halted_tasks"].add(task.id)
                elif action == "departed":
                    bucket["departed_tasks"].add(task.id)
                elif action == "decided":
                    bucket["decided_tasks"].add(task.id)

                    if grade == "OPEN":
                        claim = str(entry.get("claim") or "").strip()

                        if claim:
                            bucket["decided_claims"].append((task.id, claim))

            if halted_here and task.requeued_after_escalation:
                bucket["requeued_after_halt"] += 1

    populations = [_finish(buckets[i], floor, amended_ids) for i in sorted(buckets)]

    return {"schema_version": 1, "floor": floor, "populations": populations}
