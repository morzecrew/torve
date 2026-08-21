"""`decisions-reported` — a LOCKED area touched with no log entry, or an
illegal action for the grade (RFC 0002 §4; format RFC 0001 §6 as amended by
A-1: `logs/<task-id>.yaml`, one `entries:` list, YAML only — the converter
owned compatibility and died with it).

Checks: schema (required fields, vocabularies, UTC timestamp, positive
attempt, kind or class present), legality (the action the grade licenses,
both ways, with the D-21b close-out exemptions), evidence (locatable file
ranges or commands carrying output), drift (the declared `drift_count`
against entries classed drift), and silence (every LOCKED decision whose
declared paths the diff touched has an entry citing it; no paths — skipped,
never passed).

The log also carries a `bypasses:` list (D-2.7); its items are records, not
divergences, and are validated only for shape.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast

import yaml

from torve.context import GateContext
from torve.gates.base import NO_TASK, BuiltinOutcome, spec
from torve.models import Gate

# ----------------------- #

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(?:Z|\+00:00)$")
CITATION = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
BACKTICKED = re.compile(r"^`(?P<command>[^`]+)`(?P<rest>.*)$", re.S)

REQUIRED = ("decision", "grade", "at", "attempt", "claim", "evidence", "action")
OPTIONAL = ("kind", "class", "proposal", "notes")
GRADES = {"LOCKED", "ASSUMED", "OPEN", "UNLISTED"}
KINDS = {"contradicted", "departed", "resolved", "blocked"}
CLASSES = {"discovery", "spec-gap", "drift", "irreducible"}
ACTIONS = {"halted", "departed", "decided"}
LEGAL = {"LOCKED": "halted", "ASSUMED": "departed", "OPEN": "decided", "UNLISTED": "decided"}
BYPASS_FIELDS = {"gate", "reason", "author", "commit", "at"}


def _norm(value: object) -> str:
    """YAML types scalars (timestamps, ints); the checks read strings."""
    if isinstance(value, datetime):
        text = value.isoformat().replace("+00:00", "Z")
        return text if text.endswith("Z") else text + "Z"
    return "" if value is None else str(value)


def parse_log(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """(document, error). A log that does not parse is a red result, not a
    skipped one — the gate is fail-closed."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"log is not valid YAML: {exc}"
    if not isinstance(loaded, dict):
        return None, "log must be a mapping (schema_version, task, drift_count, entries)"
    document = cast(dict[str, Any], loaded)
    entries: Any = document.get("entries")
    if entries is None:
        fresh: list[Any] = []
        document["entries"] = fresh
    elif not isinstance(entries, list) or any(
        not isinstance(e, dict) for e in cast(list[object], entries)
    ):
        return None, "'entries' must be a list of mappings"
    return document, None


def _check_schema(index: int, entry: dict[str, Any]) -> list[str]:
    where = f"entry {index + 1}"
    problems: list[str] = []
    for key in entry:
        if key not in REQUIRED and key not in OPTIONAL:
            problems.append(f"{where}: unknown field {key!r}")
    for key in REQUIRED:
        if not _norm(entry.get(key)).strip():
            problems.append(f"{where}: missing {key}")
    kind, klass = _norm(entry.get("kind")), _norm(entry.get("class"))
    if not kind and not klass:
        problems.append(f"{where}: neither 'kind' (RFC 0001) nor 'class' (skill) present")
    for key, vocabulary, value in (("grade", GRADES, _norm(entry.get("grade"))),
                                   ("kind", KINDS, kind), ("class", CLASSES, klass),
                                   ("action", ACTIONS, _norm(entry.get("action")))):
        if value and value not in vocabulary:
            problems.append(f"{where}: {key} {value!r} is not one of {sorted(vocabulary)}")
    at = entry.get("at")
    if at is not None:
        if isinstance(at, datetime):
            offset = at.utcoffset()
            if offset is not None and offset.total_seconds() != 0:
                problems.append(f"{where}: at must be UTC")
        elif not RFC3339.match(_norm(at)):
            problems.append(f"{where}: at {_norm(at)!r} is not a UTC RFC 3339 timestamp")
    attempt = entry.get("attempt")
    if attempt is not None and (not isinstance(attempt, int) or attempt < 1):
        problems.append(f"{where}: attempt {attempt!r} is not a positive integer")
    if _norm(entry.get("decision")) == "unlisted" and not _norm(entry.get("proposal")).strip():
        problems.append(f"{where}: decision is unlisted, which owes a proposal")
    return problems


def _check_legality(index: int, entry: dict[str, Any]) -> list[str]:
    grade, action = _norm(entry.get("grade")), _norm(entry.get("action"))
    kind = _norm(entry.get("kind"))
    if grade not in GRADES or action not in ACTIONS:
        return []  # already reported by the schema check
    if kind == "resolved":
        legal = {"decided", "departed"}  # a close-out attests, it does not contradict
    elif kind == "blocked":
        legal = {"halted"}
    else:
        legal = {LEGAL[grade]}
    if action in legal:
        return []
    return [
        f"entry {index + 1}: grade {grade}"
        + (f" with kind {kind}" if kind else "")
        + f" licenses {sorted(legal)}, not {action!r}"
    ]


def _check_evidence(index: int, entry: dict[str, Any], ctx: GateContext) -> list[str]:
    where = f"entry {index + 1}"
    evidence = _norm(entry.get("evidence")).strip()
    if not evidence:
        return []  # already reported by the schema check

    backticked = BACKTICKED.match(evidence)
    if backticked:
        if not backticked.group("rest").strip(" \t—-:"):
            return [f"{where}: evidence is a command with no output — the output is the evidence"]
        return []

    citation = evidence.split(" — ")[0].split(" - ")[0].strip()
    found = CITATION.match(citation)
    if not found:
        return [
            (
                f"{where}: evidence {evidence!r} is neither a path:line citation nor a "
                "backticked command with its output — a sentence is a claim, not evidence"
            )
        ]
    root = ctx.root.resolve()
    try:
        target = (root / found.group("path")).resolve()
    except OSError:
        target = None
    if target is None or not target.is_relative_to(root):
        return [f"{where}: evidence path {found.group('path')!r} escapes the repository"]
    if not target.is_file():
        return [f"{where}: evidence path {found.group('path')!r} does not exist"]
    total = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))
    start, end = int(found.group("start")), int(found.group("end") or found.group("start"))
    if start < 1 or end < start:
        return [f"{where}: evidence range {citation!r} is not a range"]
    if end > total:
        return [f"{where}: evidence {citation!r} points past end of file ({total} lines)"]
    return []


def _check_drift(document: dict[str, Any]) -> list[str]:
    declared = document.get("drift_count")
    if declared is None:
        return []  # the declared claim's presence is self-audit's finding
    if not isinstance(declared, int) or declared < 0:
        return [f"drift_count {declared!r} is not a non-negative integer"]
    actual = sum(1 for e in document["entries"] if _norm(e.get("class")) == "drift")
    if declared != actual:
        return [f"declared drift count {declared} != {actual} entries classed drift"]
    return []


def _check_bypasses(document: dict[str, Any]) -> list[str]:
    bypasses: Any = document.get("bypasses")
    if bypasses is None:
        return []
    if not isinstance(bypasses, list) or any(
        not isinstance(b, dict) for b in cast(list[object], bypasses)
    ):
        return ["'bypasses' must be a list of mappings"]
    problems: list[str] = []
    for index, record in enumerate(cast(list[dict[str, Any]], bypasses)):
        missing = BYPASS_FIELDS - set(record)
        if missing:
            problems.append(f"bypass {index + 1}: missing {', '.join(sorted(missing))}")
    return problems


def _check_silence(ctx: GateContext, document: dict[str, Any]) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    skipped: list[str] = []
    logged = {_norm(e.get("decision")) for e in document["entries"]}
    assert ctx.task is not None
    for decision in ctx.task.decisions:
        if decision.grade != "LOCKED":
            continue
        if not decision.paths:
            skipped.append(f"{decision.id}: declares no paths, so its area is unknown")
            continue
        area = spec(decision.paths)
        hits = [p for p in ctx.changed_paths if area.match_file(p)]
        if hits and decision.id not in logged:
            shown = ", ".join(sorted(hits)[:3]) + ("…" if len(hits) > 3 else "")
            problems.append(
                f"decision {decision.id}: LOCKED, and the diff touches {len(hits)} "
                f"file(s) it governs ({shown}), with no entry in the log"
            )
    return problems, skipped


def check_decisions_reported(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK
    if ctx.log_text is None:
        if not ctx.task.decisions:
            return BuiltinOutcome("pass", "decisions: [] — none apply, explicitly (D-7.5)")
        return BuiltinOutcome(
            "fail",
            f"no execution log at logs/{ctx.task.id}.yaml, and the task inherits "
            f"{len(ctx.task.decisions)} decision(s)",
        )

    document, parse_error = parse_log(ctx.log_text)
    if document is None:
        return BuiltinOutcome("fail", parse_error or "log did not parse")

    problems: list[str] = []
    for index, entry in enumerate(document["entries"]):
        problems += _check_schema(index, entry)
        problems += _check_legality(index, entry)
        problems += _check_evidence(index, entry, ctx)
    problems += _check_drift(document)
    problems += _check_bypasses(document)
    silence_problems, skipped = _check_silence(ctx, document)
    problems += silence_problems

    if problems:
        return BuiltinOutcome("fail", "\n".join(problems))
    count = len(document["entries"])
    lines = [f"{count} entr{'y' if count == 1 else 'ies'}, all checks green"]
    lines += [f"skipped: {s}" for s in skipped]
    return BuiltinOutcome("pass", "\n".join(lines))
