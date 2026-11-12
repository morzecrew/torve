"""The execution log's entry checks (format S-0001/task-contract-and-execution-log as
amended by S-0001/A-1: `logs/<task-id>.yaml`, one `entries:` list, YAML only).

What the retired `decisions-reported` gate applied to a log, kept for the
callers that still read one: the halt and departure intake refuses an entry
before it writes (`check_entry`, `check_pin`), and the reports parse a log the
same way (`parse_log`, `ACTIONS`). No gate convicts on any of it (S-0122/D-17).

Checks: schema (required fields, vocabularies, UTC timestamp, positive
attempt, kind or class present), legality (the action the grade licenses,
both ways), and evidence (locatable file ranges or commands carrying output).

A new entry is a halt or a departure, nothing else (S-0122/D-14). The
`decided` action and the `resolved` kind stay in the vocabulary because the
logs, landings and records written before carry them and must still read;
the intake refuses them, because an attestation of compliance was two
thirds of every log and changed no outcome.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

from torve.domain.vocabulary import ENTRY_ACTIONS, ENTRY_CLASSES, ENTRY_GRADES, ENTRY_KINDS
from torve.gates.evidence import BACKTICKED, CITATION, CITED, locate

# ----------------------- #

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(?:Z|\+00:00)$")
REQUIRED = ("decision", "grade", "at", "attempt", "claim", "evidence", "action")
OPTIONAL = ("kind", "class", "proposal", "notes")
# The one vocabulary's words as sets (S-0059/D-5) — the log's and the record's.
GRADES = frozenset(ENTRY_GRADES)
KINDS = frozenset(ENTRY_KINDS)
CLASSES = frozenset(ENTRY_CLASSES)
ACTIONS = frozenset(ENTRY_ACTIONS)
# A LOCKED row is never departed from, only halted on; every other grade is
# licensed to depart, with the departure stated (S-0122/D-14).
LEGAL = {"LOCKED": "halted", "ASSUMED": "departed", "OPEN": "departed", "UNLISTED": "departed"}
# Read in an old log, refused in a new entry (S-0122/D-14).
RETIRED = {("action", "decided"), ("kind", "resolved")}


# ....................... #


def _norm(value: object) -> str:
    """YAML types scalars (timestamps, ints); the checks read strings."""

    if isinstance(value, datetime):
        text = value.isoformat().replace("+00:00", "Z")
        return text if text.endswith("Z") else text + "Z"

    return "" if value is None else str(value)


# ....................... #


def parse_log(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """(document, error). A log that does not parse is an error, never read
    as an empty one."""

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


# ....................... #


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
        problems.append(f"{where}: neither 'kind' nor 'class' present")

    for key, vocabulary, value in (
        ("grade", GRADES, _norm(entry.get("grade"))),
        ("kind", KINDS, kind),
        ("class", CLASSES, klass),
        ("action", ACTIONS, _norm(entry.get("action"))),
    ):
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

    # A departure from no listed row puts one back; a halt states its gap in
    # the claim, and `torve abort` asks for nothing else.
    if (
        _norm(entry.get("decision")) == "unlisted"
        and _norm(entry.get("action")) != "halted"
        and not _norm(entry.get("proposal")).strip()
    ):
        problems.append(f"{where}: decision is unlisted, which owes a proposal")

    return problems


# ....................... #


def _check_legality(index: int, entry: dict[str, Any]) -> list[str]:
    grade, action = _norm(entry.get("grade")), _norm(entry.get("action"))
    kind = _norm(entry.get("kind"))
    retired = [
        f"entry {index + 1}: {key} {value!r} is no longer recorded — the log holds halts "
        "(`torve abort`) and departures only; work that keeps to its rows needs no entry"
        for key, value in sorted(RETIRED)
        if _norm(entry.get(key)) == value
    ]

    if retired:
        return retired

    if grade not in GRADES or action not in ACTIONS:
        return []  # already reported by the schema check

    legal = {"halted"} if kind == "blocked" else {LEGAL[grade]}

    if action in legal:
        return []

    return [
        f"entry {index + 1}: grade {grade}"
        + (f" with kind {kind}" if kind else "")
        + f" licenses {sorted(legal)}, not {action!r}"
    ]


# ....................... #


def _is_grammar_rejection(evidence: str) -> bool:
    """True when the shared locator's rejection of *evidence* is the grammar
    one (no path:line citation and no backticked command anywhere in it), as
    opposed to a citation pointing at a missing or out-of-range path, or a
    command with no output. Mirrors the locator's own branches with its own
    regexes, so the judgement cannot drift from it (S-0005/D-4, S-0127/D-3)."""

    if BACKTICKED.search(evidence) or CITED.search(evidence):
        return False

    citation = evidence.split(" — ")[0].split(" - ")[0].strip()

    return CITATION.match(citation) is None


def _grammar_rejection_message(evidence: str) -> str:
    """The teaching version of the locator's grammar rejection: the expected
    grammar, the offending line quoted, and its defect. A citation may sit
    anywhere in the evidence, so the only grammar defect left is citing
    nothing (S-0127/D-3). The judgement is the locator's — only the message
    changes."""

    return "\n".join(
        [
            f"evidence {evidence!r} is not locatable",
            "expected: a `path:line` citation, or a backticked command with its output",
            "first defect: no citation at all — a sentence is a claim, not evidence",
        ]
    )


def _check_evidence(index: int, entry: dict[str, Any], root: Path) -> list[str]:
    evidence = _norm(entry.get("evidence")).strip()

    if not evidence:
        return []  # already reported by the schema check

    # One locator, two consumers (S-0005/D-4): this check and the review findings
    # filter share the mechanism in gates/evidence.py. The judgement is the
    # locator's; when it rejects the grammar, the message here teaches the
    # repair instead of only naming the failure.
    problem = locate(evidence, root)

    if problem is None:
        return []

    if _is_grammar_rejection(evidence):
        problem = _grammar_rejection_message(evidence)

    return [f"entry {index + 1}: {problem}"]


# ....................... #


def check_entry(entry: dict[str, Any], root: Path, *, index: int = 0) -> list[str]:
    """Every per-entry check, for a caller holding one entry rather than a
    log (S-0044 S-0044/D-10).

    The intake calls this before it writes, so an agent is refused while it
    can still act.
    """

    return (
        _check_schema(index, entry)
        + _check_legality(index, entry)
        + _check_evidence(index, entry, root)
    )


# ....................... #


SHA_SHAPE = re.compile(r"^[0-9a-f]{7,64}$")


def check_pin(document: dict[str, Any]) -> list[str]:
    """S-0001/D-36 (S-0021/A-1): the log opens with `repo` and `base`, so its
    path:line evidence resolves against the commit the work started from.
    Public for the same reason `check_entry` is: the intake checks the
    document it is about to write, so a missing pin is reported while
    someone can still act on it."""

    problems: list[str] = []

    if not str(document.get("repo") or "").strip():
        problems.append(
            "log carries no repo — the pin opening the log names the repository "
            "its evidence resolves against"
        )

    base = str(document.get("base") or "").strip()

    if not base:
        problems.append(
            "log carries no base — evidence resolves against the commit the "
            "work started from; the prompt hands you the pin verbatim"
        )
    elif not SHA_SHAPE.match(base):
        problems.append(f"base {base!r} is not a commit sha")

    return problems
