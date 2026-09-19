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

from collections.abc import Sequence
from dataclasses import dataclass

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
