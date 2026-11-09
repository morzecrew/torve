"""Review threads folded into findings (S-0084/D-3, S-0084/D-4).

A finding is what a round of work is about: one anchor in the tree, one fix,
and every thread that raised it as a reply address. The grouping key is what a
thread ANCHORS TO and never who wrote it — three bots on one null check cost
one task, and the answering half names one commit to all three threads without
a second join.

The exact key is this module's (S-0084/D-4), under one constraint: it errs
toward merging, because an over-merged finding is one task told about two
things and an under-merged one is two tasks colliding on one file of one
branch. Both errors are readable from the record the finding produces — the
span it covers beside the threads it carries says which way it went.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application.ports import ReviewThread

# ----------------------- #

# How far apart two anchored threads may sit and still be one finding. A
# reviewer's line and the line a fix actually belongs on drift by a few — a
# null check flagged at the dereference, fixed at the assignment — so the
# window is a body of code rather than a line, and it errs generous on
# purpose (S-0084/D-4).
WINDOW = 10


@dataclass(frozen=True)
class Finding:
    """One thing to fix, and every thread that asked for it.

    `line`..`end_line` is the span the grouped threads anchor to, None for a
    finding a file-level thread carries with no anchored thread beside it. The
    span and `threads` together are how the record says which way the grouping
    erred.
    """

    path: str
    line: int | None
    end_line: int | None
    threads: tuple[ReviewThread, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        """The reply addresses this finding answers — every thread in it."""

        return tuple(thread.id for thread in self.threads)


# ....................... #


def group_findings(threads: Sequence[ReviewThread]) -> list[Finding]:
    """Group threads into findings by what they anchor to (S-0084/D-3).

    One file at a time; within a file, threads whose lines sit within `WINDOW`
    of each other are one finding, and a file-level thread — one the forge
    holds against no line at all — takes the whole file with it, because it is
    a claim about the file and nothing narrower says otherwise. Ordered by
    path then line, so the record a pass writes is stable across passes.
    """

    by_path: dict[str, list[ReviewThread]] = {}

    for thread in threads:
        by_path.setdefault(thread.path, []).append(thread)

    findings: list[Finding] = []

    for path, group in sorted(by_path.items()):
        anchored = sorted(
            ((thread.line, thread) for thread in group if thread.line is not None),
            key=lambda pair: pair[0],
        )
        whole_file = [thread for thread in group if thread.line is None]

        if whole_file:
            # The merging error, chosen: a file-level thread joins every
            # thread in its file rather than becoming a finding beside them.
            findings.append(
                Finding(
                    path,
                    anchored[0][0] if anchored else None,
                    anchored[-1][0] if anchored else None,
                    tuple(whole_file + [thread for _, thread in anchored]),
                )
            )
            continue

        runs: list[list[tuple[int, ReviewThread]]] = []

        for pair in anchored:
            if runs and pair[0] - runs[-1][-1][0] <= WINDOW:
                runs[-1].append(pair)
            else:
                runs.append([pair])

        findings += [
            Finding(path, run[0][0], run[-1][0], tuple(thread for _, thread in run)) for run in runs
        ]

    return findings


# ....................... #
# S-0126/D-3: one rule for what an attempt rejected, read by both the reply
# and the carry.


def rejections(entries: Sequence[Any], attempt: int) -> list[dict[str, Any]]:
    """The rejections one attempt recorded (S-0084/D-13, S-0126/D-3): the
    entries under `unlisted` with `kind: contradicted` whose `attempt` is
    *attempt*. An entry carrying no `attempt` counts for every attempt,
    because logs written before the pin carried none; identical rejections
    count once, so a claim recorded again on a retry is posted once."""

    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        if entry.get("decision") != "unlisted" or entry.get("kind") != "contradicted":
            continue

        recorded = entry.get("attempt")

        if recorded is not None:
            try:
                recorded = int(recorded)

            # A persisted value that is not an attempt cannot pin a rejection
            # to one; it answers every attempt like an entry written before
            # the pin, so a legacy log is never reported as fixed (S-0126/D-3).
            except (TypeError, ValueError):
                recorded = None

        if recorded is not None and recorded != attempt:
            continue

        claim = str(entry.get("claim", ""))
        key = (claim, str(entry.get("evidence", "")))

        if key in seen:
            continue

        seen.add(key)
        kept.append(entry)

    return kept


def names_spot(claim: str, spot: tuple[Any, Any], spots: Collection[tuple[Any, Any]]) -> bool:
    """Whether *claim* names the finding at *spot* (S-0126/D-3): by its full
    `path:line`, or by the file's own name and the line only when no other
    spot in *spots* shares both. A basename two files of one round share
    names neither, because no reply can tell which thread it answers."""

    path, line = spot

    if not path or line is None:
        return False

    if re.search(rf"(?<![\w/.-]){re.escape(str(path))}:{line}(?!\d)", claim):
        return True

    name = Path(str(path)).name
    shared = any(
        (other_path, other_line) != (path, line)
        and Path(str(other_path)).name == name
        and other_line == line
        for other_path, other_line in spots
    )

    if shared:
        return False

    return bool(re.search(rf"(?<![\w/.-]){re.escape(name)}:{line}(?!\d)", claim))


# ....................... #
# S-0122/D-15: what one critic call per pull-request head makes of each
# unresolved bot thread.

TRIAGE_SCHEMA = "torve.triage/v1"

Verdict = Literal["apply", "minor", "invalid", "duplicate", "out_of_scope"]

# The verdicts a revision carries; the rest are answered from their evidence.
APPLIED: frozenset[str] = frozenset({"apply", "minor"})


class ThreadVerdict(BaseModel):
    """One thread as the critic sorted it."""

    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True)

    thread: str
    """The thread's id, as the prompt named it."""
    verdict: Verdict
    """apply, minor, invalid, duplicate or out_of_scope."""
    evidence: str = ""
    """A `path:line` citation into the head's tree and what it shows."""
    duplicate_of: str = ""
    """The thread a duplicate repeats."""


class TriageDocument(BaseModel):
    """The critic's answer about a head's threads."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_name: Literal["torve.triage/v1"] = Field(alias="schema")
    threads: list[ThreadVerdict] = Field(default_factory=list)


def parse_triage(output: str) -> TriageDocument | None:
    """The last `torve.triage/v1` document in a seat's answer that reads, or
    None when it holds none: a later object of another schema carrying
    `threads` does not hide it."""

    import json
    import re

    from torve.application.review import ANSI, last_document

    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    found: list[object] = []

    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())

        except json.JSONDecodeError:
            continue

        if isinstance(document, dict) and "threads" in document:
            found.append(document)

    # A harness envelope carries the answer escaped inside its `result`.
    if not found and (enveloped := last_document(output, key="threads")) is not None:
        found.append(enveloped)

    for raw in reversed(found):
        try:
            return TriageDocument.model_validate(raw)

        except ValidationError:
            continue

    return None
