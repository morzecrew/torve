"""`decisions-reported` — a LOCKED area touched with no log entry, or an
illegal action for the grade (RFC 0002 §4, format from RFC 0001 §6 / D-21a).

Violating a lock is not mechanically detectable; the absence of an entry in an
area a locked decision declares trivially is. This validator therefore checks:

  schema     every required field present, vocabularies respected, `at` a UTC
             RFC 3339 stamp, `attempt` a positive integer
  legality   the action is the one the grade licenses, both ways — halting on
             ASSUMED fails as loudly as departing from LOCKED
  evidence   every citation resolves: a file (and line range) under the root,
             or a backticked command carrying its output
  drift      a declared `Drift count: N` line matches the entries classed drift
  silence    every LOCKED decision whose declared paths the diff touched has an
             entry citing it; a decision declaring no paths is reported as
             skipped, never as passing

Entries carry `kind` (RFC 0001: contradicted | departed | resolved | blocked)
or `class` (the flag-dont-flip skill: discovery | spec-gap | drift |
irreducible), or both — the two conventions coexist in one log, and a
`resolved` close-out is the legal way to attest compliance in a touched LOCKED
area (action `decided`). That reconciliation is a logged decision with a
proposed amendment row; see logs/T-0002.md.
"""

from __future__ import annotations

import re

from torve.context import GateContext
from torve.gates.base import NO_TASK, BuiltinOutcome, spec
from torve.models import Gate

BLOCK = re.compile(r"^```divergence[ \t]*$\n(.*?)^```[ \t]*$", re.M | re.S)
OPENING_FENCE = re.compile(r"^```divergence[ \t]*$", re.M)
FIELD = re.compile(r"^([a-z_]+):[ \t]*(.*)$")
DRIFT_COUNT = re.compile(r"^\*\*Drift count:\s*(\d+)", re.M)
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(?:Z|\+00:00)$")
CITATION = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
BACKTICKED = re.compile(r"^`(?P<command>[^`]+)`(?P<rest>.*)$", re.S)

REQUIRED = ("decision", "grade", "at", "attempt", "claim", "evidence", "action")
GRADES = {"LOCKED", "ASSUMED", "OPEN", "UNLISTED"}
KINDS = {"contradicted", "departed", "resolved", "blocked"}
CLASSES = {"discovery", "spec-gap", "drift", "irreducible"}
ACTIONS = {"halted", "departed", "decided"}
# A grade licenses exactly one action on a contradiction (RFC 0001 §6).
LEGAL = {"LOCKED": "halted", "ASSUMED": "departed", "OPEN": "decided", "UNLISTED": "decided"}


def parse_blocks(text: str) -> list[tuple[int, dict[str, str]]]:
    """Every ```divergence block as (1-based fence line, fields). A repeated
    key keeps its first value and is flagged — silently taking the last would
    let an entry restate `action` after the one that was checked."""
    blocks = []
    for match in BLOCK.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        fields: dict[str, str] = {}
        duplicates: set[str] = set()
        malformed: list[str] = []
        for raw in match.group(1).splitlines():
            if not raw.strip():
                continue
            found = FIELD.match(raw)
            if not found:
                malformed.append(raw.strip())
                continue
            key, value = found.group(1), found.group(2).strip()
            if key in fields:
                duplicates.add(key)
            else:
                fields[key] = value
        fields["__duplicates__"] = ",".join(sorted(duplicates))
        fields["__malformed__"] = "\n".join(malformed)
        blocks.append((line_no, fields))
    return blocks


def _check_schema(line: int, fields: dict[str, str]) -> list[str]:
    where = f"block at line {line}"
    problems = []
    for bad in filter(None, fields["__malformed__"].splitlines()):
        problems.append(f"{where}: not a field: {bad!r}")
    for key in filter(None, fields["__duplicates__"].split(",")):
        problems.append(f"{where}: field {key!r} appears more than once")
    for key in REQUIRED:
        if not fields.get(key, "").strip():
            problems.append(f"{where}: missing {key}")
    if not fields.get("kind") and not fields.get("class"):
        problems.append(f"{where}: neither 'kind' (RFC 0001) nor 'class' (skill) present")
    for key, vocabulary in (("grade", GRADES), ("kind", KINDS), ("class", CLASSES),
                            ("action", ACTIONS)):
        value = fields.get(key, "")
        if value and value not in vocabulary:
            problems.append(f"{where}: {key} {value!r} is not one of {sorted(vocabulary)}")
    at = fields.get("at", "")
    if at and not RFC3339.match(at):
        problems.append(f"{where}: at {at!r} is not a UTC RFC 3339 timestamp")
    attempt = fields.get("attempt", "")
    if attempt and not (attempt.isdigit() and int(attempt) >= 1):
        problems.append(f"{where}: attempt {attempt!r} is not a positive integer")
    if fields.get("decision") == "unlisted" and not fields.get("proposal", "").strip():
        problems.append(f"{where}: decision is unlisted, which owes a proposal")
    return problems


def _check_legality(line: int, fields: dict[str, str]) -> list[str]:
    grade, action, kind = fields.get("grade", ""), fields.get("action", ""), fields.get("kind", "")
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
        f"block at line {line}: grade {grade}"
        + (f" with kind {kind}" if kind else "")
        + f" licenses {sorted(legal)}, not {action!r}"
    ]


def _check_evidence(line: int, fields: dict[str, str], ctx: GateContext) -> list[str]:
    where = f"block at line {line}"
    evidence = fields.get("evidence", "").strip()
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
            f"{where}: evidence {evidence!r} is neither a path:line citation nor a "
            "backticked command with its output — a sentence is a claim, not evidence"
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


def _check_drift(text: str, blocks: list[tuple[int, dict[str, str]]]) -> list[str]:
    """The LAST declared count is the current one — the log is append-only, so
    a revised count is a new line, never an edit. Absence of the line is
    `self-audit`'s finding, not this gate's."""
    found = DRIFT_COUNT.findall(text)
    if not found:
        return []
    actual = sum(1 for _, fields in blocks if fields.get("class") == "drift")
    if int(found[-1]) != actual:
        return [f"declared drift count {found[-1]} != {actual} entries classed drift"]
    return []


def _check_silence(
    ctx: GateContext, blocks: list[tuple[int, dict[str, str]]]
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    skipped: list[str] = []
    logged = {fields.get("decision", "") for _, fields in blocks}
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
            f"no execution log at logs/{ctx.task.id}.md, and the task inherits "
            f"{len(ctx.task.decisions)} decision(s)",
        )

    text = ctx.log_text
    blocks = parse_blocks(text)
    problems: list[str] = []
    for match in OPENING_FENCE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if line not in {block_line for block_line, _ in blocks}:
            problems.append(f"block at line {line}: ```divergence fence is never closed")
    for line, fields in blocks:
        problems += _check_schema(line, fields)
        problems += _check_legality(line, fields)
        problems += _check_evidence(line, fields, ctx)
    problems += _check_drift(text, blocks)
    silence_problems, skipped = _check_silence(ctx, blocks)
    problems += silence_problems

    if problems:
        return BuiltinOutcome("fail", "\n".join(problems))
    lines = [f"{len(blocks)} entr{'y' if len(blocks) == 1 else 'ies'}, all checks green"]
    lines += [f"skipped: {s}" for s in skipped]
    return BuiltinOutcome("pass", "\n".join(lines))
