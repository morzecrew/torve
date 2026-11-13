"""Evidence location, one mechanism with two consumers (S-0005/D-4): the
execution log's check inside `decisions-reported`, and the review findings
filter — a finding whose quoted evidence cannot be located is discarded
before a human sees it.

The rule is the log's rule (S-0001/task-contract-and-execution-log as amended, S-0127/D-3): a
`path:line` citation anywhere in the evidence that resolves inside the
repository, or a backticked command carrying its output. Locating
eliminates fabricated *coordinates*, not fabricated *claims* — a model can
cite a real line and describe something that is not there; the defence against that is measurement (S-0005/measuring-the-reviewer-the-actual-advantage),
and nothing here should be read as more.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, TypeVar

# ----------------------- #

CITATION = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
#: A `path:line` or `path:start-end` citation anywhere in evidence, not only
#: the leading one: a probe or mutation sentence may cite the line it changed
#: mid-text (S-0127/D-3). The path is every character but whitespace or the
#: citing colon, so a supported name like `src/a+b.py` is not cut to `b.py`
#: (a path containing spaces is `citations`' to extend); the first character
#: stays word-shaped, so punctuation before a citation in prose is not
#: swallowed into the path.
CITED = re.compile(r"(?<![\w./+-])(?P<path>[\w./+-][^\s:]*):(?P<start>\d+)(?:-(?P<end>\d+))?(?!\d)")
#: A backticked command anywhere in evidence, with everything after it
#: (S-0127/D-3); the first one found carries the most text after it.
BACKTICKED = re.compile(r"`(?P<command>[^`]+)`(?P<rest>.*)", re.S)


# ....................... #


def locate(evidence: str, root: Path) -> str | None:
    """The problem with *evidence*, or None when it locates: a path:line
    citation anywhere in it resolving to real lines under *root*, as
    `citations` reads it, or a backticked command anywhere with output after
    it (S-0127/D-3).

    Evidence holding neither keeps today's messages: the leading citation's
    problem, else the first citation's, else the command's missing output,
    else that a sentence is a claim."""

    cited = citations(evidence, root)
    backticked = BACKTICKED.search(evidence)

    # Output is what follows the command once whitespace and the separators
    # are gone; a newline alone is none.
    if backticked and backticked.group("rest").strip().strip("—-:").strip():
        return None

    if any(citation_problem(path, start, end, root) is None for path, start, end in cited):
        return None

    citation = evidence.split(" — ")[0].split(" - ")[0].strip()
    found = CITATION.match(citation)

    if found:
        start = int(found.group("start"))
        return citation_problem(found.group("path"), start, int(found.group("end") or start), root)

    if cited:
        return citation_problem(*cited[0], root)

    if backticked:
        return "evidence is a command with no output — the output is the evidence"

    return (
        f"evidence {evidence!r} is neither a path:line citation nor a "
        "backticked command with its output — a sentence is a claim, not evidence"
    )


def citations(evidence: str, root: Path) -> list[tuple[str, int, int]]:
    """Every `path:line` or `path:start-end` *evidence* cites, in order
    (S-0127/D-3), by the grammar the critic's carry read (S-0126/D-1): the
    words before `CITED`'s path are tried as part of a path containing
    spaces, so `src/legacy module.py:12` is not truncated to `module.py:12`."""

    found: list[tuple[str, int, int]] = []

    for match in CITED.finditer(evidence):
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        found.append((_extend_path(evidence, match, start, end, root), start, end))

    return found


def _extend_path(evidence: str, match: re.Match[str], start: int, end: int, root: Path) -> str:
    """*match*'s path with the space-separated words before it that make the
    citation resolve under *root*: `CITATION` accepts a path containing
    spaces, which `CITED` finds only from its last word.

    The longest run that resolves wins, so prose before the path is not
    swallowed: only words whose addition makes an existing file are kept. A
    quote or bracket opening the run is not the path's, so a quoted
    `` `src/legacy module.py:12` `` resolves too."""

    beginning = match.start("path")
    words = evidence[evidence.rfind("\n", 0, beginning) + 1 : beginning].split()

    for index in range(len(words)):
        candidate = " ".join([*words[index:], match.group("path")]).lstrip("`'\"([")

        if citation_problem(candidate, start, end, root) is None:
            return candidate

    return match.group("path")


def citation_problem(path: str, start: int, end: int, root: Path) -> str | None:
    """The problem with lines *start* to *end* of *path*, read whole, or None
    when they are real lines of a file under *root*."""

    citation = f"{path}:{start}" if end == start else f"{path}:{start}-{end}"
    resolved_root = root.resolve()

    try:
        target = (resolved_root / path).resolve()

    except OSError:
        target = None

    if target is None or not target.is_relative_to(resolved_root):
        return f"evidence path {path!r} escapes the repository"

    if not target.is_file():
        return f"evidence path {path!r} does not exist"

    total = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))

    if start < 1 or end < start:
        return f"evidence range {citation!r} is not a range"

    if end > total:
        return f"evidence {citation!r} points past end of file ({total} lines)"

    return None


# ....................... #


class Located(Protocol):
    """What the filter reads off a finding: the classic reviewer's `Finding`
    and the critic's richer one (S-0122/D-1) both carry these, so one filter
    serves both and neither can drift from the other's rule."""

    @property
    def severity(self) -> str: ...

    @property
    def claim(self) -> str: ...

    @property
    def evidence(self) -> str: ...


F = TypeVar("F", bound=Located)


def filter_findings(
    findings: Sequence[F],
    root: Path,
) -> tuple[list[F], list[str]]:
    """(kept, discard reasons): findings whose evidence locates under *root*,
    and one recorded reason per discarded finding — discarded is a counted
    outcome (the noise rate), never a silent drop."""

    kept: list[F] = []
    discarded: list[str] = []

    for finding in findings:
        problem = locate(finding.evidence, root)

        if problem is None:
            kept.append(finding)
        else:
            discarded.append(f"{finding.severity}: {finding.claim!r} — {problem}")

    return kept, discarded
