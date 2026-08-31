"""`torve context` — a projection of accumulated facts into a form a
planning session can consume (RFC 0007 §4). Not a plan: tasks by state,
escalations by reason, execution-log divergences ready to become
decision-table rows, per-gate health, cost against `config_hash`, the
programme view of the RFC graph (D-7.11), asserted `implementation` beside
derived per-phase progress with disagreements flagged (D-7.15), and the
document-level half of the specification-quality report (RFC 0022 §5.3,
D-22.6): the same MCP surface that already exposes this projection carries
it to a planning session with no new tool.

Everything here is read from files the engine already writes — contracts,
run states, execution logs, the feedback and telemetry streams, the corpus.
Progress is computed on demand and stored nowhere (D-A.12). The projection
emits data; judgement stays with the human reading it.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from torve.application.runstate import RunState
from torve.application.specquality import read_tasks
from torve.base import naming
from torve.config import layout, rfc_parse
from torve.config.runconfig import RunnerConfig
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #

ACTIVE = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}

# The shipping spellings the repository's history carries: the Torve-Task
# trailer the runner writes, a parenthesized citation — `(T-0019)`,
# `(T-0087, A-43)`, `(A-19, T-0019)` — and the merge-branch shape
# `merge torve/T-0006`. A bare prose mention ("mint T-0097–T-0104",
# "accept T-0002 proposals", "from the T-0146 wild miss") records the id
# without shipping it and must not count (D-7.26).
SUBJECT_ID = re.compile(r"\([^)]*?(T-\d{4,})[^)]*\)|torve/(T-\d{4,})")
TRAILER_ID = re.compile(r"Torve-Task: (T-\d{4,})")

# RFC 0004 §6a, reproduced verbatim (D-22.7, LOCKED: printed with the report,
# never paraphrased). `torve.cli.rfc` owns and prints this same text for
# `torve rfc health`; the layering contract puts `torve.cli` above
# `torve.application`, so this module cannot import it back and the string
# is copied rather than shared — a wording change updates both call sites.
QUASI_EXPERIMENT_CAVEAT = (
    "Baseline is a quasi-experiment, not an A/B: tasks before "
    "and after are different tasks, done under different conditions. This "
    'supports direction ("iterations fell") and not magnitude ("40% faster").'
)

# The two escalation reasons that indict a document rather than the code
# that executed it (charter A-21, A-22) — RFC 0022 §5.3 asks for these on
# their own line even when a document has never triggered either.
DOCUMENT_INDICTING_REASONS = (str(EscalationReason.UNDERSPECIFIED), str(EscalationReason.STALE_INHERITANCE))

# A table, not a dump — mirrors specquality's own bound on decided_claims.
SPEC_DRIFT_FINDINGS_LIMIT = 10


# ....................... #


def _shipped_ids(root: Path) -> set[str]:
    """Task ids the history records as shipped, in one batched log pass —
    a task with no run state is not necessarily unstarted: the engine did
    not run it, but a shipping commit records that someone did."""

    proc = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--format=%x1e%s%x1f%b"],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        return set()

    found: set[str] = set()

    for record in proc.stdout.split("\x1e"):
        subject, _, body = record.partition("\x1f")
        found.update(g for pair in SUBJECT_ID.findall(subject) for g in pair if g)
        found.update(TRAILER_ID.findall(body))

    return found


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


def _tasks(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return found

    shipped = _shipped_ids(root)

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        record = _load_yaml_dict(contract)

        if record is None:
            continue

        task_id = str(record.get("id", contract.parent.name))

        entry: dict[str, Any] = {
            "id": task_id,
            "rfc": record.get("rfc"),
            # `shipped` is a projection-derived pseudo-state, not a member of
            # the engine's state machine: no run state exists, but a shipping
            # commit cites the task.
            "phase": record.get("phase", 0),
            "role": record.get("role", "implement"),
            # A drafting contract (intake, decompose) with no live run has
            # been consumed by its adoption — "unstarted" would claim work
            # is still owed. A run state below overrides either way.
            "state": (
                "shipped"
                if task_id in shipped
                else ("consumed" if record.get("role") == "draft" else "unstarted")
            ),
            "attempts": 0,
            "escalation": None,
            "escalated_at": None,
            # RFC 0026 D-26.5: read by this projection only — dispatch, lane
            # and store never consult it.
            "parent": record.get("parent"),
        }

        state_path = naming.state_file(root, task_id)

        if state_path.exists():
            state = RunState.load(state_path)
            entry["state"] = str(state.state)
            entry["attempts"] = state.attempts

            if state.escalation is not None:
                entry["escalation"] = state.escalation.reason

                entry["escalated_at"] = next(
                    (
                        event["at"]
                        for event in reversed(state.history)
                        if event["to"] == "escalated"
                    ),
                    None,
                )

        found.append(entry)

    return found


# ....................... #


def _proposals(root: Path, rfc_dir: Path) -> list[dict[str, Any]]:
    """Divergence entries carrying a `proposal:` — data ready to become
    decision-table rows, with the entry that produced each (§4: amendments
    stop being copy-paste; append-only is preserved and nothing is retyped).

    A promoted row cites its task (`see .torve/tasks/T-nnnn`), so a proposal
    from a task the corpus already cites is marked `possibly_landed` — the
    log is append-only and cannot record acceptance, but the citation is
    evidence the author has been through that task's log."""

    cited = ""

    for path in rfc_dir.glob("*.md"):
        cited += path.read_text(encoding="utf-8")

    found: list[dict[str, Any]] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return found

    for log in sorted(tasks_dir.glob("T-*/log.yaml")):
        document = _load_yaml_dict(log)

        if document is None:
            continue

        entries: Any = document.get("entries")

        if not isinstance(entries, list):
            continue

        for entry in cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue

            record = cast("dict[str, Any]", entry)
            proposal = record.get("proposal")

            if not proposal:
                continue

            task_id = str(document.get("task", log.parent.name))

            found.append(
                {
                    "task": task_id,
                    "decision": record.get("decision"),
                    "grade": record.get("grade"),
                    "claim": record.get("claim"),
                    "proposal": proposal,
                    "evidence": record.get("evidence"),
                    # Any corpus mention of the task id is evidence the author
                    # has been through its log — the weaker claim "possibly
                    # landed", never "accepted". Logs promoted before the
                    # provenance convention carry no citation and stay visible;
                    # surfacing them is the feature, not a defect.
                    "possibly_landed": task_id in cited,
                }
            )

    return found


# ....................... #


def _gate_health(root: Path) -> dict[str, dict[str, Any]]:
    """Per-gate counters from the telemetry stream (§4: what gate to write
    comes from data rather than recollection)."""

    stats: dict[str, dict[str, Any]] = {}
    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if not telemetry.is_file():
        return stats

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        results: Any = cast("dict[str, Any]", record).get("results")

        if not isinstance(results, list):
            continue

        for result in cast("list[object]", results):
            if not isinstance(result, dict):
                continue

            row = cast("dict[str, Any]", result)
            name = str(row.get("name", "?"))

            gate = stats.setdefault(
                name,
                {
                    "runs": 0,
                    "failures": 0,
                    "flaky": 0,
                    "bypassed": 0,
                    "total_duration_s": 0.0,
                    "max_duration_s": 0.0,
                },
            )

            gate["runs"] += 1
            outcome = str(row.get("outcome", ""))

            if outcome in ("fail", "error"):
                gate["failures"] += 1
            elif outcome == "flaky":
                gate["flaky"] += 1
            elif outcome == "bypassed":
                gate["bypassed"] += 1

            duration = float(row.get("duration_s", 0.0) or 0.0)
            gate["total_duration_s"] += duration
            gate["max_duration_s"] = max(gate["max_duration_s"], duration)

    for gate in stats.values():
        runs = gate["runs"] or 1
        gate["mean_duration_s"] = round(gate["total_duration_s"] / runs, 2)
        gate["total_duration_s"] = round(gate["total_duration_s"], 2)

    return stats


# ....................... #


def _harness_label(agent: dict[str, Any]) -> str | None:
    """Which harness did the work — identity is the image (D-17.4), so a
    `torve-agent:<name>` tag labels by name; records from before the tag
    was stamped fall back to the adapter kind."""

    image = agent.get("image")

    if isinstance(image, str) and image:
        return image.rsplit(":", 1)[-1] if ":" in image else image

    adapter = agent.get("adapter")
    return str(adapter) if adapter else None


def _costs(root: Path) -> list[dict[str, Any]]:
    """Cost and iterations by task against config_hash (§4) — every real
    agent attempt and every shadow summary. An attempt whose harness reported
    no usage still appears, costless: an uncontrolled regime is a fact worth
    seeing (D-4.6), and a hidden run reads as a run that never happened.
    Fake-agent attempts stay out — simulation is not spend."""

    found: list[dict[str, Any]] = []
    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if not telemetry.is_file():
        return found

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)

        if row.get("kind") == "shadow":
            found.append(
                {
                    "kind": "shadow",
                    "at": row.get("at"),
                    "task": row.get("task_id"),
                    "config_hash": row.get("config_hash"),
                    "cost_usd": row.get("cost_usd_total"),
                    "attempts": row.get("attempts"),
                    "state": row.get("state"),
                }
            )

            continue

        agent: Any = row.get("agent")

        if isinstance(agent, dict) and cast("dict[str, Any]", agent).get("adapter") != "fake":
            block = cast("dict[str, Any]", agent)

            found.append(
                {
                    "kind": "attempt",
                    "at": row.get("at"),
                    "task": row.get("task_id"),
                    "config_hash": row.get("config_hash"),
                    "cost_usd": block.get("cost_usd"),
                    "adapter": block.get("adapter"),
                    # Which harness did the work: identity is the image
                    # (D-17.4) — a torve-agent:<name> tag labels by name;
                    # records from before the tag was stamped fall back to
                    # the adapter kind.
                    "harness": _harness_label(block),
                    "model": block.get("model"),
                    "provider": block.get("provider"),
                    "model_version": block.get("model_version"),
                }
            )

    # Newest first: the reader's question is "what just ran", and the
    # stream on disk is append-ordered.
    found.sort(key=lambda r: str(r.get("at") or ""), reverse=True)

    return found


# ....................... #

# Kinds a harness population does not count as "a run under this tier":
# shadow replays are the measurement machinery comparing two regimes, not
# spend under either one, skill evals are RFC 0009's own population, and
# engine events carry no agent block at all (RFC 0027 D-27.5).
_HARNESS_EXCLUDED_KINDS = {"shadow", "skill-eval", "engine"}


def _task_tier_name(record: dict[str, Any]) -> str:
    """The contract's own declared tier, dotted (D-27.3) — read straight off
    the committed YAML, no `Task` validation needed for a population join."""

    tier = str(record.get("tier") or "")
    variant = record.get("tier_variant")

    return f"{tier}.{variant}" if variant else tier


def harness_populations(root: Path, config: RunnerConfig) -> list[dict[str, Any]]:
    """RFC 0027 D-27.5's fact-feed widening: per-tier runs, cost (D-21.5's
    broker-measured-preferred, self-reported-labelled split), escalations by
    reason, unparseable-review counts, and the most recently recorded image
    digest — every *configured* tier present with its denominator even at
    zero, so a variant nothing uses is visible (RFC 0027 §9's variant-sprawl
    mitigation). All from existing records: telemetry for runs, cost, digest
    and unparseable reviews; contracts and run state for escalations."""

    buckets: dict[str, dict[str, Any]] = {
        name: {
            "tier": name,
            "attempts": 0,
            "cost_usd_broker": 0.0,
            "cost_usd_broker_n": 0,
            "cost_usd_self_reported": 0.0,
            "cost_usd_self_reported_n": 0,
            "escalations_by_reason": {},
            "unparseable_reviews": 0,
            "current_digest": None,
        }
        for name in sorted(config.tiers)
    }

    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if telemetry.is_file():
        for line in telemetry.read_text(encoding="utf-8").splitlines():
            try:
                record: Any = json.loads(line)

            except json.JSONDecodeError:
                continue

            if not isinstance(record, dict):
                continue

            row = cast("dict[str, Any]", record)

            if str(row.get("kind", "")) in _HARNESS_EXCLUDED_KINDS:
                continue

            agent = row.get("agent")

            if not isinstance(agent, dict) or cast("dict[str, Any]", agent).get("adapter") == "fake":
                continue

            block = cast("dict[str, Any]", agent)
            bucket = buckets.get(str(block.get("tier") or ""))

            if bucket is None:
                continue

            bucket["attempts"] += 1
            broker = block.get("broker")

            if isinstance(broker, dict) and isinstance(
                cast("dict[str, Any]", broker).get("cost_usd"), int | float
            ):
                bucket["cost_usd_broker"] += float(cast("dict[str, Any]", broker)["cost_usd"])
                bucket["cost_usd_broker_n"] += 1
            elif isinstance(block.get("cost_usd"), int | float):
                bucket["cost_usd_self_reported"] += float(block["cost_usd"])
                bucket["cost_usd_self_reported_n"] += 1

            digest = block.get("image_digest")

            if digest:
                bucket["current_digest"] = digest

            if row.get("kind") == "review" and row.get("unparseable"):
                bucket["unparseable_reviews"] += 1

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if tasks_dir.is_dir():
        for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
            contract_record = _load_yaml_dict(contract)

            if contract_record is None:
                continue

            bucket = buckets.get(_task_tier_name(contract_record))

            if bucket is None:
                continue

            task_id = str(contract_record.get("id", contract.parent.name))
            state_path = naming.state_file(root, task_id)

            if not state_path.exists():
                continue

            state = RunState.load(state_path)

            if state.state is TaskState.ESCALATED and state.escalation:
                reason = str(state.escalation.reason)
                bucket["escalations_by_reason"][reason] = (
                    bucket["escalations_by_reason"].get(reason, 0) + 1
                )

    return [buckets[name] for name in sorted(buckets)]


# ....................... #


def _feedback(root: Path) -> dict[str, dict[str, Any]]:
    """The latest `torve feedback` record per task id — the stream is
    append-only and keyed by task id, latest wins at analysis time
    (RFC 0022 §3)."""

    found: dict[str, dict[str, Any]] = {}
    path = layout.feedback_file(root)

    if not path.is_file():
        return found

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)
        task_id = row.get("task_id")

        if task_id:
            found[str(task_id)] = row  # later lines overwrite earlier ones

    return found


# ....................... #


def _document_signals(root: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """RFC 0022 §5.3, the document-level half of the specification-quality
    report: tasks minted, attempts to green (median, over tasks that landed
    — a task that never went green has none to count), escalations by
    reason with the two document-indicting reasons always present, spec-drift
    findings and their count (`class: drift` log entries — the same field
    the `decisions-reported` gate checks its declared `drift_count` against),
    human_minutes and rework rate from `torve feedback`. Tasks without an
    `rfc` have no document to indict and are excluded (D-22.9's reading, one
    level up from the decision join).

    Reuses `specquality.read_tasks` for the log join rather than parsing
    `log.yaml` a second way — one parser, one place the two reports could
    disagree, same reasoning specquality gives for reusing the gate's own
    `parse_log`."""

    logs_by_task = {task.id: task.log_entries for task in read_tasks(root)}
    feedback = _feedback(root)
    by_document: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["rfc"]:
            by_document.setdefault(str(task["rfc"]), []).append(task)

    signals: list[dict[str, Any]] = []

    for document, entries in sorted(by_document.items()):
        attempts = [int(t["attempts"]) for t in entries if t["state"] == str(TaskState.READY)]
        escalations = dict.fromkeys(DOCUMENT_INDICTING_REASONS, 0)

        for task in entries:
            if task["escalation"]:
                reason = str(task["escalation"])
                escalations[reason] = escalations.get(reason, 0) + 1

        drift_findings = [
            {"task": task["id"], "claim": str(log_entry.get("claim") or "")}
            for task in entries
            for log_entry in logs_by_task.get(task["id"], [])
            if log_entry.get("class") == "drift"
        ]

        minutes: list[int] = []
        reworked = 0
        with_feedback = 0

        for task in entries:
            row = feedback.get(str(task["id"]))

            if row is None:
                continue

            with_feedback += 1
            human_minutes = row.get("human_minutes")

            if isinstance(human_minutes, int):
                minutes.append(human_minutes)

            if row.get("rework_after_review"):
                reworked += 1

        signals.append(
            {
                "rfc": document,
                "minted": len(entries),
                "attempts_to_green_median": statistics.median(attempts) if attempts else None,
                "attempts_to_green_n": len(attempts),
                "escalations_by_reason": dict(sorted(escalations.items())),
                "drift_count": len(drift_findings),
                "spec_drift_findings": drift_findings[:SPEC_DRIFT_FINDINGS_LIMIT],
                "human_minutes_median": statistics.median(minutes) if minutes else None,
                "human_minutes_n": len(minutes),
                "rework_rate": reworked / with_feedback if with_feedback else None,
                "rework_n": with_feedback,
            }
        )

    return signals


# ....................... #


def _phase_progress(states: list[str]) -> str:
    """planned | in_flight | blocked | shipped, derived per phase (D-7.15) —
    phase-level because that is the granularity at which decisions get made."""

    if states and all(state in ("ready", "shipped") for state in states):
        return "shipped"

    if any(state == "escalated" for state in states):
        return "blocked"

    if any(state in {str(s) for s in ACTIVE} for state in states):
        return "in_flight"

    return "planned"


# ....................... #


def _programme(root: Path, rfc_dir: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The RFC graph rendered for humans (D-7.11): what is accepted, what
    shipped, what became plannable, and where assertion and derivation
    disagree (D-7.15 — the disagreement is the informative part)."""

    by_document: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["rfc"]:
            by_document.setdefault(str(task["rfc"]), []).append(task)

    view: list[dict[str, Any]] = []
    files = rfc_parse.rfc_files(rfc_dir)
    statuses: dict[str, str] = {}
    frontmatter: dict[str, dict[str, Any]] = {}

    for number, path in sorted(files.items()):
        fm = rfc_parse.parse_frontmatter(path.read_text(encoding="utf-8"))

        if fm is not None:
            frontmatter[number] = fm
            statuses[number] = str(fm.get("status", ""))

    for number, path in sorted(files.items()):
        fm = frontmatter.get(number)

        if fm is None:
            continue

        text = path.read_text(encoding="utf-8")

        try:
            phasing = rfc_parse.parse_phasing(text)

        except ValueError:
            phasing = None

        document = str(path.resolve().relative_to(root.resolve()))
        minted = by_document.get(document, [])
        phases: dict[int, list[str]] = {}

        for task in minted:
            # A drafting task (intake, decompose) cites the document that
            # asked for it, but it is not a phase of the document's
            # implementation — one consumed decompose contract otherwise
            # holds a completed RFC at "P0: planned" forever.
            if task.get("role") == "draft":
                continue

            phases.setdefault(int(task["phase"]), []).append(str(task["state"]))

        progress = {phase: _phase_progress(states) for phase, states in sorted(phases.items())}

        status = statuses[number]
        implementation = str(fm.get("implementation") or "none")

        unsatisfied = [
            dep for dep in _list_field(fm, "depends_on") if statuses.get(dep, "") != "accepted"
        ]

        declared_phases: set[int] = {entry.phase for entry in phasing or []}
        unminted = sorted(declared_phases - set(phases))
        plannable = status == "accepted" and not unsatisfied and bool(unminted)

        disagreement: str | None = None

        if (
            implementation == "complete"
            and progress
            and any(p != "shipped" for p in progress.values())
        ):
            disagreement = "asserted complete, but a phase is not shipped"
        elif implementation == "none" and any(p == "shipped" for p in progress.values()):
            disagreement = "a phase shipped, but the assertion still says none"

        view.append(
            {
                "rfc": number,
                "title": str(fm.get("title", "")),
                "status": status,
                "kind": fm.get("kind") or "design",
                "implementation": implementation,
                "unsatisfied_depends_on": unsatisfied,
                "declared_phases": sorted(declared_phases),
                "minted_phases": {str(k): len(v) for k, v in sorted(phases.items())},
                "progress": {str(k): v for k, v in progress.items()},
                "plannable": plannable,
                "disagreement": disagreement,
            }
        )

    return view


# ....................... #


def _list_field(fm: dict[str, Any], name: str) -> list[str]:
    value = fm.get(name)

    if not isinstance(value, list):
        return []

    return [str(item) for item in cast("list[object]", value)]


# ....................... #

# Attention routing (RFC 0006 §4, D-6.4): blockers and locked conflicts
# interrupt, infrastructure pages the harness owner, the rest batches into
# review windows. The projection carries the class; policy stays with people.
ROUTE_NOTIFY = {"blocker_finding", "locked_conflict"}
ROUTE_HARNESS = {"gate_infrastructure_failure"}


# ....................... #


def escalation_route(reason: str) -> str:
    if reason in ROUTE_NOTIFY:
        return "notify"

    if reason in ROUTE_HARNESS:
        return "harness owner"

    return "batch"


# ....................... #


def _age_seconds(stamp: object) -> float | None:
    if not isinstance(stamp, str):
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(stamp, fmt).replace(tzinfo=UTC)

        except ValueError:
            continue

        return max(0.0, (datetime.now(UTC) - parsed).total_seconds())

    return None


# ....................... #


def _decompositions(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Children grouped under their parent (RFC 0026 D-26.5, D-26.6): a
    projection convenience over the `parent` field — dispatch, the lane and
    the store never read it, so this grouping exists nowhere else."""

    groups: dict[str, list[str]] = {}

    for task in tasks:
        parent = task.get("parent")

        if parent:
            groups.setdefault(str(parent), []).append(str(task["id"]))

    return dict(sorted(groups.items()))


# ....................... #


def context_report(root: Path, rfc_dir: Path) -> dict[str, Any]:
    tasks = _tasks(root)
    escalations: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["escalation"]:
            # The queue's age is the primary signal (D-6.8): a queue nobody
            # triages looks identical to success from inside the runner.
            escalations.setdefault(str(task["escalation"]), []).append(
                {
                    "task": task["id"],
                    "at": task["escalated_at"],
                    "rfc": task["rfc"],
                    "age_s": _age_seconds(task["escalated_at"]),
                    "route": escalation_route(str(task["escalation"])),
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasks": tasks,
        "decompositions": _decompositions(tasks),
        "escalations": escalations,
        "proposals": _proposals(root, rfc_dir),
        "gates": _gate_health(root),
        "costs": _costs(root),
        "programme": _programme(root, rfc_dir, tasks),
        "spec_quality": {
            "caveat": QUASI_EXPERIMENT_CAVEAT,
            "documents": _document_signals(root, tasks),
        },
    }


# ....................... #


def render_markdown(report: dict[str, Any]) -> str:
    """The human-facing projection (D-7.4: format decided by use — markdown
    for pasting into a planning session, JSON for machines, both from one
    report)."""

    lines: list[str] = [f"# torve context — {report['at']}", ""]

    lines.append("## Programme")
    lines.append("")

    for doc in report["programme"]:
        marks: list[str] = []

        if doc["plannable"]:
            marks.append("**plannable**")

        if doc["disagreement"]:
            marks.append(f"⚠ {doc['disagreement']}")

        if doc["unsatisfied_depends_on"]:
            marks.append(f"waits on {', '.join(doc['unsatisfied_depends_on'])}")

        progress = ", ".join(f"P{k}: {v}" for k, v in doc["progress"].items()) or "no tasks"

        lines.append(
            f"- **{doc['rfc']}** {doc['title']} — {doc['status']}, "
            f"impl {doc['implementation']} · {progress}"
            + (" · " + " · ".join(marks) if marks else "")
        )

    lines.append("")

    lines.append("## Tasks by state")
    lines.append("")
    by_state: dict[str, list[str]] = {}

    for task in report["tasks"]:
        by_state.setdefault(str(task["state"]), []).append(str(task["id"]))

    for state, ids in sorted(by_state.items()):
        lines.append(f"- {state}: {', '.join(ids)}")

    lines.append("")

    if report["decompositions"]:
        lines.append("## Decompositions")
        lines.append("")

        for parent, children in report["decompositions"].items():
            lines.append(f"- {parent} (integration task) -> {', '.join(children)}")

        lines.append("")

    if report["escalations"]:
        lines.append("## Escalations by reason")
        lines.append("")

        for reason, items in sorted(report["escalations"].items()):
            names = ", ".join(str(item["task"]) for item in items)
            lines.append(f"- {reason} ({len(items)}): {names}")

        lines.append("")

    if report["proposals"]:
        fresh = [p for p in report["proposals"] if not p.get("possibly_landed")]
        landed = len(report["proposals"]) - len(fresh)
        lines.append("## Proposals awaiting the author")
        lines.append("")

        for item in fresh:
            lines.append(
                f"- `{item['decision']}` ({item['grade']}) from {item['task']}: "
                f"{str(item['proposal']).strip()}"
            )

        if landed:
            lines.append(
                f"- …plus {landed} proposal(s) from tasks the decision tables already "
                "cite — likely landed; the JSON report carries them all"
            )

        lines.append("")

    if report["gates"]:
        lines.append("## Gate health")
        lines.append("")

        for name, gate in sorted(report["gates"].items()):
            lines.append(
                f"- {name}: {gate['runs']} run(s), {gate['failures']} failure(s), "
                f"{gate['flaky']} flaky, {gate['bypassed']} bypassed, "
                f"mean {gate['mean_duration_s']}s, max {gate['max_duration_s']}s"
            )

        lines.append("")

    if report["costs"]:
        lines.append("## Cost and iterations")
        lines.append("")

        for row in report["costs"]:
            cost = row.get("cost_usd")
            shown = f"${cost:.4f}" if isinstance(cost, (int, float)) else "unrecorded"

            if row["kind"] == "shadow":
                extra = f", attempts {row['attempts']}, {row['state']}"
            else:
                agent_bits = " · ".join(
                    str(part)
                    for part in (row.get("harness"), row.get("model_version") or row.get("model"))
                    if part
                )
                extra = f", {agent_bits}" if agent_bits else ""

            stamp = f"{row['at']} · " if row.get("at") else ""
            lines.append(
                f"- {stamp}{row['kind']} {row['task']} @ {row.get('config_hash')}: {shown}{extra}"
            )

        lines.append("")

    if report["spec_quality"]["documents"]:
        lines.append("## Specification quality")
        lines.append("")
        lines.append(report["spec_quality"]["caveat"])
        lines.append("")

        for doc in report["spec_quality"]["documents"]:
            attempts = (
                f"{doc['attempts_to_green_median']:.1f} attempt(s) to green "
                f"(n={doc['attempts_to_green_n']})"
                if doc["attempts_to_green_median"] is not None
                else "no landed tasks yet"
            )
            minutes = (
                f"{doc['human_minutes_median']:.0f}m human effort (n={doc['human_minutes_n']})"
                if doc["human_minutes_median"] is not None
                else "no feedback recorded"
            )
            rework = (
                f"{doc['rework_rate']:.0%} rework (n={doc['rework_n']})"
                if doc["rework_rate"] is not None
                else "no feedback recorded"
            )
            escalations = (
                ", ".join(
                    f"{reason} ({count})"
                    for reason, count in doc["escalations_by_reason"].items()
                    if count
                )
                or "none"
            )

            lines.append(
                f"- **{doc['rfc']}** — {doc['minted']} minted, {attempts}, {minutes}, {rework}, "
                f"{doc['drift_count']} spec-drift finding(s), escalations: {escalations}"
            )

        lines.append("")

    return "\n".join(lines)
