"""`torve spec health` — the attribution join and the decision-level report
(S-0022/the-join-and-what-it-is-keyed-by, §5.2): telemetry, task logs and contracts, indexed by task
id, joined to the corpus for the row as it stands and to each contract for
the row as it was minted.

The report never edits a decision table, proposes no text and calls no model
(S-0022/D-1, LOCKED): everything here is a read over `.torve/tasks/*/contract.yaml`,
`log.yaml`, run state, git's own landing trailer and the RFC corpus — a plain
reader over JSONL-shaped YAML, no new dependency, so moving to S-0004/telemetry-staged
stage 2 is a change of reader, not a rewrite (S-0022/D-5). The grade compared is
always the one copied onto the contract at mint time, never the row as the
corpus stands today (S-0022/D-2) — that is why every population is built from
`Task.decisions`, and the corpus itself is consulted only for whether an
amendment later cited the identifier, never for its current grade or paths.

`touched` is read from the contract's own declared `scope.allow` intersecting
the decision's declared paths (`torve.application.planner.globs_intersect`,
the same primitive that already answers "do two glob sets overlap" for
same-phase scopes) rather than a literal post-hoc `git diff` of historical
shas. Two considered reasons, logged as a departure from S-0022/D-4/D-22.5's
literal "diff intersected" wording under T-0099: the scope gate already
refuses a landed diff that leaves `scope.allow` (`torve.gates.scope`), so the
declared area is a safe over-approximation of the true diff for any task that
ever passed or was explicitly bypassed; and it keeps this specific reading
what S-0022/D-5 asks for — YAML in, no git subprocess, no dependency on a
historical sha still being resolvable. `TaskFacts.landed` is the module's one
exception (`_landed_task_ids`, T-0133, logged): the run-state file it used to
read is exactly what the reaper deletes on every terminal run.

No score is computed anywhere in this module (S-0022/D-3): a population's
`reading` is `None` until its relevant count clears `floor`, and every ratio
is printed beside the denominator it was taken over (S-0022/D-8).

`dispatch_envelope` (S-0022/D-11, S-0022/A-3) reads the same `TaskFacts` join
prospectively: landed tasks sharing a size verdict's class, median attempts,
cost and wall minutes, denominator always printed, reading suppressed below
`floor`. It is a base rate over history, never a bound — nothing here blocks
or resizes a dispatch.

`operator_attention` (S-0022/D-12, S-0022/A-5) reads the same join corpus-wide: landed
changes beside the operator interventions already recorded behind them —
feedback minutes and escalations triaged —
joined per task id. Each intervention kind reports its count behind landed
changes beside its raw total, every count labeled with its own population —
the joined count's population is interventions whose task landed, the raw
total's is every intervention, and the landed window prints as its own count
beside them, never as a denominator for an intervention count (S-0022/D-8: a
task can hold several interventions, so "joined of landed" would mix two
populations); an intervention whose task never landed in the window stays in
the raw total only. The human-minutes reading is suppressed below `floor`
(S-0022/D-8), no ratio of attention to landed changes computed (S-0022/D-3).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

from torve.application.planner import globs_intersect
from torve.application.runstate import RunState
from torve.application.sizing import estimate_scope
from torve.base import naming
from torve.config import layout, spec
from torve.domain.spec import Corpus, qualify
from torve.domain.states import TaskState
from torve.domain.task import Scope
from torve.gates.decisions_reported import ACTIONS as LOG_ACTIONS
from torve.gates.decisions_reported import parse_log

# ----------------------- #

DEFAULT_FLOOR = 5

# S-0004/measurement-defects-to-fix-before-trusting-a-number, reproduced verbatim (S-0022/D-11: printed with the envelope,
# never paraphrased — the same text S-0022/D-7 requires beside `torve spec
# health`). `torve.cli.spec` and `torve.application.projections` each carry
# their own copy for the layering reason their own comments give; this is
# a third copy rather than a move to `torve.base`, which is out of this
# task's scope.
QUASI_EXPERIMENT_CAVEAT = (
    "Baseline is a quasi-experiment, not an A/B: tasks before "
    "and after are different tasks, done under different conditions. This "
    'supports direction ("iterations fell") and not magnitude ("40% faster").'
)

_ABANDONED_STATE = str(TaskState.ABANDONED)
_ESCALATED_STATE = str(TaskState.ESCALATED)
_QUEUED_STATE = str(TaskState.QUEUED)

# The landing trailer the runner writes into the commit that lands a task
# (S-0010/D-4: git log is the surviving record) — the same trailer
# `torve.adapters.vcs.git.GitVcs.landed_shas` greps for. `read_tasks` reads
# it directly (T-0133, departing S-0022/D-5's "no git subprocess" — logged)
# because it has no caller to inject one for it.


# ....................... #


@dataclass(frozen=True)
class TaskFacts:
    """One minted task, the join's left side (S-0022/the-join-and-what-it-is-keyed-by): its own
    contract, its own log, its own run state — task id keyed, nothing merged
    across tasks here. `decisions` carries the grade and paths exactly as
    copied onto this contract at mint time (S-0022/D-2)."""

    id: str
    rfc: (
        str | None
    )  # S-0022/D-9: carried through so a document-level reader can bucket None on its own
    scope_allow: list[str]
    acceptance: list[str]  # S-0022/D-11: the other half of the size verdict's own inputs
    decisions: list[dict[str, Any]]  # [{id, grade, paths}], mint-time copies
    log_entries: list[dict[str, Any]]
    state: str | None
    attempts: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    # S-0022/D-10's landed reading, sourced from git's own landing trailer
    # (T-0133, departing S-0022/D-5 — see `_landed_task_ids`) rather than
    # RunState.state == ready: the run-state file is exactly what the
    # reaper deletes on every terminal run, so a population read after a
    # reap sweep saw every task as unlanded regardless of what shipped.
    landed: bool = False

    # ....................... #

    @property
    def size(self) -> str:
        """The size verdict this task would receive today (S-0002/task-size),
        recomputed from the same two contract fields `torve.application.
        sizing.estimate` reads rather than stored — the rule may change, and
        a historical population must read under today's rule the same way a
        fresh dispatch does."""

        return estimate_scope(Scope(allow=self.scope_allow), self.acceptance).size

    # ....................... #

    @property
    def abandoned(self) -> bool:
        return self.state == _ABANDONED_STATE

    # ....................... #

    @property
    def requeued_after_escalation(self) -> bool:
        """A human sent the task back around the loop (S-0006 S-0006/D-10,
        charter S-0008/A-4) rather than the row being amended — the LOCKED
        reading's other branch (§5.2)."""

        return any(
            e.get("from") == _ESCALATED_STATE and e.get("to") == _QUEUED_STATE for e in self.history
        )

    # ....................... #

    @property
    def escalations_triaged(self) -> int:
        """Every escalation this task's own history shows a human resolved
        — routed back to the queue or given up on, the state machine's own
        two exits from `escalated` (`TRANSITIONS[ESCALATED]`, S-0006
        S-0006/D-10). Counted per exit, not per task: a task can escalate and be
        triaged more than once."""

        return sum(
            1
            for e in self.history
            if e.get("from") == _ESCALATED_STATE
            and e.get("to") in (_QUEUED_STATE, _ABANDONED_STATE)
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
                "paths": [str(p) for p in cast("list[object]", paths)]
                if isinstance(paths, list)
                else [],
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


def _contract_acceptance(record: dict[str, Any]) -> list[str]:
    acceptance = record.get("acceptance")

    if not isinstance(acceptance, list):
        return []

    return [str(a) for a in cast("list[object]", acceptance)]


# ....................... #


def _log_entries(log_path: Path) -> list[dict[str, Any]]:
    """A missing log is an empty log (S-0003/A-2, S-0003/D-21) — reused from the same
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


def _landed_task_ids(root: Path) -> set[str]:
    """Every task id git's own history records as landed, read in one
    batched pass rather than once per task. A repository git cannot read
    (no commits yet, no `.git`, the binary missing) lands no tasks rather
    than erroring — the same convention `_load_yaml_dict` uses for a file
    it cannot read."""

    # One derivation, not two (S-0007/D-26): projections owns the landing
    # spellings — trailer, parenthesized citation, merge-branch shape —
    # and a second copy here would drift. The trailer-only first cut left
    # every pre-trailer landing uncounted, which is half of the very
    # "(0 landed)" symptom this function exists to fix.
    from torve.application.projections import shipped_ids

    try:
        return shipped_ids(root)

    except OSError:
        return set()


# ....................... #


def read_tasks(root: Path) -> list[TaskFacts]:
    """Every task the corpus knows, joined to its own log, run state and
    landing trailer (S-0022/the-join-and-what-it-is-keyed-by). A directory with no readable
    `contract.yaml` is not a task the join can use and is skipped, not
    fabricated."""

    found: list[TaskFacts] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return found

    landed_ids = _landed_task_ids(root)

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
                acceptance=_contract_acceptance(record),
                decisions=_contract_decisions(record),
                log_entries=_log_entries(contract_path.parent / "log.yaml"),
                state=str(run_state.state) if run_state is not None else None,
                attempts=run_state.attempts if run_state is not None else 0,
                history=run_state.history if run_state is not None else [],
                landed=task_id in landed_ids,
            )
        )

    return found


# ....................... #


def _amendment_cited_ids(rfc_dir: Path) -> set[str]:
    """Every decision identifier mentioned in any corpus document's Amendments
    section (S-0022/unresolved-questions: the honest window is "any time after" until there
    are enough pairs to see a distribution — so this checks presence, never
    a date)."""

    cited: set[str] = set()

    for doc in _corpus(rfc_dir).documents:
        for amendment in doc.amendments:
            prose = spec.FENCED_BLOCK.sub("", amendment.md)
            cited.update(
                m.group(1) for m in spec.GLOBAL_CITE.finditer(prose) if "/D-" in m.group(1)
            )
            cited.update(
                qualify(doc.id, m.group(1))
                for m in spec.LOCAL_CITE.finditer(prose)
                if m.group(1).startswith("D-")
            )
            cited.update(change.subject for change in amendment.changes)

    return cited


# ....................... #


def _corpus(rfc_dir: Path) -> Corpus:
    """The corpus as the model (S-0053/D-13), or an empty one when it does not
    load: the health report is evidence for a human and never the thing
    that refuses a corpus — `torve spec check` is."""

    from pydantic import ValidationError

    from torve.config.spec import SpecError, load_corpus

    try:
        return load_corpus(rfc_dir)
    except (SpecError, ValidationError):
        return Corpus()


# ....................... #


def corpus_shape(root: Path, rfc_dir: Path) -> dict[str, Any]:
    """What the model says about the corpus beside the populations (S-0053/D-6,
    S-0053/D-7): the rows whose paths match nothing in the tree, and the
    coverage frontier by top-level source directory — governed, ungoverned
    or retired, counted over the files under `src/`."""

    from torve.application.decisions import coverage, path_rot

    corpus = _corpus(rfc_dir)
    rot = [
        {
            "document": one.document,
            "identifier": one.identifier,
            "grade": one.grade,
            "paths": one.paths,
        }
        for one in path_rot(corpus, root)
    ]
    frontier: dict[str, dict[str, int]] = {}
    source = root / "src"

    if corpus.documents and source.is_dir():
        for path in sorted(source.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            top = "/".join(relative.split("/")[:3])
            counts = frontier.setdefault(top, {"governed": 0, "ungoverned": 0, "retired": 0})
            counts[coverage(corpus, relative)] += 1

    return {"path_rot": rot, "coverage": frontier}


# ....................... #


def identifiers_for_document(rfc_dir: Path, number: str) -> set[str] | None:
    """Every identifier RFC `number` defines, or None when no such document
    exists in the corpus — the CLI's `torve spec health NNNN` filter."""

    doc = _corpus(rfc_dir).document(number)

    if doc is None or doc.archived:
        return None

    return {row.id for row in doc.decisions}


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
    (empty `allow`, S-0002/scope-in-detail) cannot be proven not to touch anything."""

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
            'judgement of "identically": S-0022/D-1 invokes no model)'
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
            "reaching it (both are defects, and different ones, S-0022/D-4)"
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
            {"task": t, "claim": c}
            for t, c in cast("list[tuple[str, str]]", bucket["decided_claims"])[:10]
        ],
        "reading": reading,
        "reading_detail": detail,
    }


# ....................... #


def decision_report(root: Path, rfc_dir: Path, floor: int = DEFAULT_FLOOR) -> dict[str, Any]:
    """The whole of S-0022/decision-level-report: one population per decision identifier
    inherited anywhere in `.torve/tasks`, each carrying its raw counts always
    and a `reading` only once the relevant count clears `floor` (S-0022/D-8).
    No score anywhere (S-0022/D-3)."""

    tasks = read_tasks(root)
    amended_ids: set[str] = _amendment_cited_ids(rfc_dir) if rfc_dir.is_dir() else set()
    buckets: dict[str, dict[str, Any]] = {}

    for task in tasks:
        cited_here: dict[str, list[dict[str, Any]]] = {}

        for entry in task.log_entries:
            identifier = str(entry.get("decision") or "")

            if not identifier or identifier == "unlisted":
                continue  # S-0022/D-9 family: an unlisted entry cites no declared row

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

    return {
        "schema_version": 1,
        "floor": floor,
        "populations": populations,
        **corpus_shape(root, rfc_dir),
    }


# ....................... #


def telemetry_file(root: Path) -> Path:
    """The telemetry stream's configured location, resolved through the same
    layout/configuration the writer resolves it with: gates.yaml's `telemetry`
    field when the manifest exists, the default path otherwise. The stream is
    relocatable by configuration (a repository that moves it must be read
    where the writer appends, not silently read as empty at the default)."""

    # Same resolution as `telemetry.engine_event` (the writer), lane.py and
    # loop.py: the manifest's `telemetry` field, or the shipped default when
    # no manifest exists.
    from torve.config.manifest import Manifest, load_manifest

    manifest_path = layout.gates_file(root)

    telemetry_rel = (
        load_manifest(manifest_path).telemetry
        if manifest_path.is_file()
        else Manifest(gates=[]).telemetry
    )

    return root / telemetry_rel


# ....................... #


def _task_cost_usd(root: Path) -> dict[str, float]:
    """Real-adapter spend per task, summed across its attempts, read the same
    way `torve.application.projections._costs` reads one attempt at a time
    (S-0022/D-5: a plain JSONL reader, no new dependency), from the telemetry
    stream's configured location — `telemetry_file`, the same manifest
    resolution the writer appends with, so a repository that relocates the
    stream still reports its spend rather than a silent zero (S-0022/D-11).
    Fake-agent attempts are simulation, not spend, and stay out."""

    telemetry = telemetry_file(root)
    totals: dict[str, float] = {}

    if not telemetry.is_file():
        return totals

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)
        task_id = row.get("task_id")
        agent = row.get("agent")

        if not task_id or not isinstance(agent, dict):
            continue

        block = cast("dict[str, Any]", agent)

        if block.get("adapter") == "fake":
            continue

        cost = block.get("cost_usd")

        if isinstance(cost, (int, float)):
            totals[str(task_id)] = totals.get(str(task_id), 0.0) + float(cost)

    return totals


# ....................... #

_HEARTBEAT_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _wall_minutes(task: TaskFacts) -> float | None:
    """First transition to last transition, in minutes — the same `history`
    timestamps `RunState.transition` stamps at every phase boundary, read as
    a wall-clock proxy rather than a new recorded field (S-0022/D-5's non-goal:
    everything needed is already recorded)."""

    if len(task.history) < 2:
        return None

    try:
        start = datetime.strptime(task.history[0]["at"], _HEARTBEAT_FORMAT)
        end = datetime.strptime(task.history[-1]["at"], _HEARTBEAT_FORMAT)

    except (KeyError, ValueError):
        return None

    return max(0.0, (end - start).total_seconds() / 60)


# ....................... #


def dispatch_envelope(root: Path, size: str, floor: int = DEFAULT_FLOOR) -> dict[str, Any]:
    """S-0022/decision-level-report's join read prospectively (S-0022/D-11, S-0022/A-3): among landed
    tasks sharing *size*'s size verdict — recomputed under today's sizing
    rule (`TaskFacts.size`), never stored — the median attempts, cost and
    wall minutes, with the population count printed regardless and every
    median suppressed until it clears `floor` (S-0022/D-8). Nothing here acts on
    the number; the operator does."""

    tasks = [t for t in read_tasks(root) if t.landed and t.size == size]
    costs = _task_cost_usd(root)

    attempts = [t.attempts for t in tasks]
    cost_samples = [costs[t.id] for t in tasks if t.id in costs]
    wall_samples = [m for t in tasks if (m := _wall_minutes(t)) is not None]

    n = len(tasks)
    ready = n >= floor

    return {
        "schema_version": 1,
        "size": size,
        "floor": floor,
        "n": n,
        "attempts_median": statistics.median(attempts) if ready and attempts else None,
        "attempts_n": len(attempts),
        "cost_usd_median": statistics.median(cost_samples) if ready and cost_samples else None,
        "cost_usd_n": len(cost_samples),
        "wall_minutes_median": statistics.median(wall_samples) if ready and wall_samples else None,
        "wall_minutes_n": len(wall_samples),
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }


# ....................... #


def render_envelope(envelope: dict[str, Any]) -> str:
    """One line for `torve run` and the tick's dispatch leg (S-0022/D-11): the
    size class, the population size always, the medians once they clear the
    floor, the caveat printed with it every time — never paraphrased
    (S-0004/measurement-defects-to-fix-before-trusting-a-number)."""

    size = envelope["size"]
    n = envelope["n"]
    floor = envelope["floor"]

    if n < floor:
        body = f"n={n}, below the observation floor of {floor} — no reading yet"
    else:
        parts: list[str] = []

        if envelope["attempts_median"] is not None:
            parts.append(
                f"{envelope['attempts_median']:.1f} attempt(s) (n={envelope['attempts_n']})"
            )

        if envelope["cost_usd_median"] is not None:
            parts.append(f"${envelope['cost_usd_median']:.2f} (n={envelope['cost_usd_n']})")

        if envelope["wall_minutes_median"] is not None:
            parts.append(f"{envelope['wall_minutes_median']:.0f}m (n={envelope['wall_minutes_n']})")

        body = f"n={n} — " + (
            ", ".join(parts) if parts else "no attempt/cost/wall observations recorded"
        )

    return f"size {size} envelope: {body} — {envelope['caveat']}"


# ....................... #


def operator_attention(root: Path, floor: int = DEFAULT_FLOOR) -> dict[str, Any]:
    """S-0022/document-level-report, S-0022/D-12 (S-0022/A-5): landed changes beside the operator
    interventions already recorded behind them — feedback minutes and the
    escalations a human triaged — joined from `read_tasks` and
    `projections.feedback_records`, with no new recorded field. The tracker
    commands and approvals this also counted left with the tracker (S-0008/A-5);
    they were the only kind an external surface produced, and no surface
    produces them now. Feedback rows carry task ids and landings resolve to
    task ids through the shipped derivation, so every intervention kind
    reports its joined
    count (its task landed in the window) beside its raw total; each count
    carries its own population — the joined count is interventions behind
    landed changes, the raw total is every intervention, and `landed` is the
    task window itself, printed as its own count rather than as a denominator
    for an intervention count (S-0022/D-8, T-0174: a task can hold several
    interventions, so joined can exceed landed without anything being wrong).
    An intervention whose task never landed in the window stays in the raw
    total only. Every count prints regardless of `floor`; only the
    human-minutes median, the one statistic here with a real sample-size
    risk, is suppressed below it (S-0022/D-8). No ratio of attention to landed
    changes is computed (S-0022/D-3): the counts are printed beside each other
    for a human to relate."""

    tasks = read_tasks(root)
    landed_tasks = [t for t in tasks if t.landed]
    landed_ids = {t.id for t in landed_tasks}

    # S-0022/D-5 layering: `torve.application.projections` imports `read_tasks`
    # from this module at load time, so the reverse import stays lazy the
    # same way `_landed_task_ids` above imports `shipped_ids`.
    from torve.application.projections import feedback_records

    # Feedback is one row per task id (latest wins); the joined count is the
    # rows whose task landed, the raw total all rows. The human-minutes
    # median keeps its own population and printed n — the join is over the
    # counts, not a new median (S-0022/D-12: "the interventions behind them").
    feedback_rows = [
        (task_id, row)
        for task_id, row in feedback_records(root).items()
        if isinstance(row.get("human_minutes"), int)
    ]
    minutes = [int(row["human_minutes"]) for _, row in feedback_rows]

    return {
        "schema_version": 1,
        "floor": floor,
        "landed": len(landed_tasks),
        "feedback": {
            "joined": sum(1 for task_id, _ in feedback_rows if task_id in landed_ids),
            "total": len(feedback_rows),
        },
        "escalations_triaged": {
            "joined": sum(t.escalations_triaged for t in landed_tasks),
            "total": sum(t.escalations_triaged for t in tasks),
        },
        "human_minutes_median": statistics.median(minutes) if len(minutes) >= floor else None,
        "human_minutes_n": len(minutes),
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }


# ....................... #


def render_operator_attention(report: dict[str, Any]) -> str:
    """One line for the corpus summary and the context section (S-0022/D-12):
    the landed window and every intervention count printed always — each
    kind's joined count (interventions whose task landed) beside its raw
    total, each count labeled with its own population, and the landed window
    as its own count, never as a denominator for an intervention count
    (S-0022/D-8, T-0174: a task can hold several interventions, so joined may
    exceed landed) — the human-minutes median only once it clears the floor,
    the caveat printed with it every time — never paraphrased (S-0004/measurement-defects-to-fix-before-trusting-a-number)."""

    if report["human_minutes_median"] is not None:
        minutes = (
            f"{report['human_minutes_median']:.0f}m human effort (n={report['human_minutes_n']})"
        )
    else:
        minutes = (
            f"human effort below the observation floor of {report['floor']} "
            f"(n={report['human_minutes_n']})"
        )

    landed = report["landed"]

    kinds = (
        ("feedback", report["feedback"]),
        ("escalations triaged", report["escalations_triaged"]),
    )

    counts = "; ".join(
        f"{label}: {block['joined']} behind landed change(s), {block['total']} total"
        for label, block in kinds
    )

    return (
        f"operator attention: {landed} landed change(s) — {counts}; {minutes} — {report['caveat']}"
    )
