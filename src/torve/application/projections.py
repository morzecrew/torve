"""`torve context` — a projection of accumulated facts into a form a
planning session can consume (RFC 0007 §4). Not a plan: tasks by state,
escalations by reason, execution-log divergences ready to become
decision-table rows, per-gate health, cost against `config_hash`, the
programme view of the RFC graph (D-7.11), and asserted `implementation`
beside derived per-phase progress with disagreements flagged (D-7.15).

Everything here is read from files the engine already writes — contracts,
run states, execution logs, the telemetry stream, the corpus. Progress is
computed on demand and stored nowhere (D-A.12). The projection emits data;
judgement stays with the human reading it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from torve.application.runstate import RunState
from torve.base import naming
from torve.config import layout, rfc_parse
from torve.domain.states import TaskState
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #

ACTIVE = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}


def _load_yaml_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    return cast("dict[str, Any]", raw)


def _tasks(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"
    if not tasks_dir.is_dir():
        return found
    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        record = _load_yaml_dict(contract)
        if record is None:
            continue
        task_id = str(record.get("id", contract.parent.name))
        entry: dict[str, Any] = {
            "id": task_id,
            "rfc": record.get("rfc"),
            "phase": record.get("phase", 0),
            "state": "unstarted",
            "attempts": 0,
            "escalation": None,
            "escalated_at": None,
        }
        state_path = naming.state_file(root, task_id)
        if state_path.exists():
            state = RunState.load(state_path)
            entry["state"] = str(state.state)
            entry["attempts"] = state.attempts
            if state.escalation is not None:
                entry["escalation"] = state.escalation.reason
                entry["escalated_at"] = next(
                    (event["at"] for event in reversed(state.history)
                     if event["to"] == "escalated"), None)
        found.append(entry)
    return found


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
            found.append({
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
            })
    return found


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
            gate = stats.setdefault(name, {
                "runs": 0, "failures": 0, "flaky": 0, "bypassed": 0,
                "total_duration_s": 0.0, "max_duration_s": 0.0,
            })
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


def _costs(root: Path) -> list[dict[str, Any]]:
    """Cost and iterations by task against config_hash (§4) — from records
    whose agent block carries a cost, and from shadow summaries."""
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
            found.append({
                "kind": "shadow", "task": row.get("task_id"),
                "config_hash": row.get("config_hash"),
                "cost_usd": row.get("cost_usd_total"),
                "attempts": row.get("attempts"), "state": row.get("state"),
            })
            continue
        agent: Any = row.get("agent")
        if isinstance(agent, dict) and cast("dict[str, Any]", agent).get("cost_usd") is not None:
            block = cast("dict[str, Any]", agent)
            found.append({
                "kind": "attempt", "task": row.get("task_id"),
                "config_hash": row.get("config_hash"),
                "cost_usd": block.get("cost_usd"),
                "adapter": block.get("adapter"),
                "model_version": block.get("model_version"),
            })
    return found


def _phase_progress(states: list[str]) -> str:
    """planned | in_flight | blocked | shipped, derived per phase (D-7.15) —
    phase-level because that is the granularity at which decisions get made."""
    if states and all(state == "ready" for state in states):
        return "shipped"
    if any(state == "escalated" for state in states):
        return "blocked"
    if any(state in {str(s) for s in ACTIVE} for state in states):
        return "in_flight"
    return "planned"


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
            phases.setdefault(int(task["phase"]), []).append(str(task["state"]))
        progress = {phase: _phase_progress(states) for phase, states in sorted(phases.items())}

        status = statuses[number]
        implementation = str(fm.get("implementation") or "none")
        unsatisfied = [
            dep for dep in _list_field(fm, "depends_on")
            if statuses.get(dep, "") != "accepted"
        ]
        declared_phases: set[int] = {entry.phase for entry in phasing or []}
        unminted = sorted(declared_phases - set(phases))
        plannable = (status == "accepted" and not unsatisfied
                     and bool(unminted))

        disagreement: str | None = None
        if implementation == "complete" and progress and any(
                p != "shipped" for p in progress.values()):
            disagreement = "asserted complete, but a phase is not shipped"
        elif implementation == "none" and any(p == "shipped" for p in progress.values()):
            disagreement = "a phase shipped, but the assertion still says none"

        view.append({
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
        })
    return view


def _list_field(fm: dict[str, Any], name: str) -> list[str]:
    value = fm.get(name)
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast("list[object]", value)]


def context_report(root: Path, rfc_dir: Path) -> dict[str, Any]:
    tasks = _tasks(root)
    escalations: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if task["escalation"]:
            escalations.setdefault(str(task["escalation"]), []).append({
                "task": task["id"], "at": task["escalated_at"], "rfc": task["rfc"],
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasks": tasks,
        "escalations": escalations,
        "proposals": _proposals(root, rfc_dir),
        "gates": _gate_health(root),
        "costs": _costs(root),
        "programme": _programme(root, rfc_dir, tasks),
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
            + (" · " + " · ".join(marks) if marks else ""))
    lines.append("")

    lines.append("## Tasks by state")
    lines.append("")
    by_state: dict[str, list[str]] = {}
    for task in report["tasks"]:
        by_state.setdefault(str(task["state"]), []).append(str(task["id"]))
    for state, ids in sorted(by_state.items()):
        lines.append(f"- {state}: {', '.join(ids)}")
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
                f"{str(item['proposal']).strip()}")
        if landed:
            lines.append(
                f"- …plus {landed} proposal(s) from tasks the decision tables already "
                "cite — likely landed; the JSON report carries them all")
        lines.append("")

    if report["gates"]:
        lines.append("## Gate health")
        lines.append("")
        for name, gate in sorted(report["gates"].items()):
            lines.append(
                f"- {name}: {gate['runs']} run(s), {gate['failures']} failure(s), "
                f"{gate['flaky']} flaky, {gate['bypassed']} bypassed, "
                f"mean {gate['mean_duration_s']}s, max {gate['max_duration_s']}s")
        lines.append("")

    if report["costs"]:
        lines.append("## Cost and iterations")
        lines.append("")
        for row in report["costs"]:
            cost = row.get("cost_usd")
            shown = f"${cost:.4f}" if isinstance(cost, (int, float)) else "unrecorded"
            extra = (f", attempts {row['attempts']}, {row['state']}"
                     if row["kind"] == "shadow" else f", {row.get('adapter')}")
            lines.append(
                f"- {row['kind']} {row['task']} @ {row.get('config_hash')}: {shown}{extra}")
        lines.append("")

    return "\n".join(lines)
