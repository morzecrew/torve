"""Evidence location, one mechanism with two consumers (D-5.4): the
execution log's check inside `decisions-reported`, and the review findings
filter — a finding whose quoted evidence cannot be located is discarded
before a human sees it.

The rule is the log's rule (RFC 0001 §6 as amended): a leading `path:line`
citation that resolves inside the repository, or a backticked command
carrying its output. Locating eliminates fabricated *coordinates*, not
fabricated *claims* — a model can cite a real line and describe something
that is not there; the defence against that is measurement (RFC 0005 §6),
and nothing here should be read as more.
"""

from __future__ import annotations

import re
from pathlib import Path

from torve.domain.attempt import Finding

# ----------------------- #

CITATION = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
BACKTICKED = re.compile(r"^`(?P<command>[^`]+)`(?P<rest>.*)$", re.S)


# ....................... #


def locate(evidence: str, root: Path) -> str | None:
    """The problem with *evidence*, or None when it locates: a leading
    path:line citation resolving to real lines under *root*, or a backticked
    command with output after it."""
    backticked = BACKTICKED.match(evidence)
    if backticked:
        if not backticked.group("rest").strip(" \t—-:"):
            return "evidence is a command with no output — the output is the evidence"
        return None

    citation = evidence.split(" — ")[0].split(" - ")[0].strip()
    found = CITATION.match(citation)
    if not found:
        return (
            f"evidence {evidence!r} is neither a path:line citation nor a "
            "backticked command with its output — a sentence is a claim, not evidence"
        )
    resolved_root = root.resolve()
    try:
        target = (resolved_root / found.group("path")).resolve()
    except OSError:
        target = None
    if target is None or not target.is_relative_to(resolved_root):
        return f"evidence path {found.group('path')!r} escapes the repository"
    if not target.is_file():
        return f"evidence path {found.group('path')!r} does not exist"
    total = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))
    start, end = int(found.group("start")), int(found.group("end") or found.group("start"))
    if start < 1 or end < start:
        return f"evidence range {citation!r} is not a range"
    if end > total:
        return f"evidence {citation!r} points past end of file ({total} lines)"
    return None


# ....................... #


def filter_findings(
    findings: list[Finding],
    root: Path,
) -> tuple[list[Finding], list[str]]:
    """(kept, discard reasons): findings whose evidence locates under *root*,
    and one recorded reason per discarded finding — discarded is a counted
    outcome (the noise rate), never a silent drop."""
    kept: list[Finding] = []
    discarded: list[str] = []
    for finding in findings:
        problem = locate(finding.evidence, root)
        if problem is None:
            kept.append(finding)
        else:
            discarded.append(f"{finding.severity}: {finding.claim!r} — {problem}")
    return kept, discarded
