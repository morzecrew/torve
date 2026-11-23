"""The telemetry stream's readers (S-0101/D-10).

`.torve/telemetry.jsonl` is a diagnostic — one row per attempt, and the
engine-health rows — and it is the one carrier a decision must not read
(S-0099/D-6): a file a decision reads is a decision that depends on one host's
disk. Every function that opens or parses it lives here, so the read is one
place rather than a wrapper in another module. The ledger, the evals, the night
report and the read-only reports import these; a boundary test refuses any
other importer.

`projections` calls these by module name for the reports it hosts (`why`,
`status`, `context`); `stream_rows` stays importable there for the review
leg's read of the lane's own facts (S-0102/D-3).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

# What the stream last recorded about a task's pull request, and the verdict a
# candidate already carrying one reports on every later pass (S-0080/D-8, D-17).
LANE_VERDICTS = {
    "lane_pr_opened": "open",
    "lane_landed": "landed",
    "lane_pr_closed": "closed",
    "lane_pr_unresolved": "unresolved",
}


def stream_rows(root: Path) -> list[dict[str, Any]]:
    """The telemetry stream, parsed — from the location the writer appends to,
    which a repository may relocate by configuration. Unparseable and
    non-object lines are skipped as everywhere else: the stream is append-only
    and a projection reader is not a repair shop."""

    from torve.application.specquality import telemetry_file

    found: list[dict[str, Any]] = []
    telemetry = telemetry_file(root)

    if not telemetry.is_file():
        return found

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if isinstance(record, dict):
            found.append(cast("dict[str, Any]", record))

    return found


def fold_attempts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* with each attempt's rows folded into its newest (S-0124/D-15).

    S-0038/D-1 writes one row per attempt, but a session revision's turn
    re-runs the gates inside its attempt (S-0124/D-8): the attempt then has a
    row per gate pass, each carrying its whole agent block so far, and a sum
    over them counts the attempt twice and its earlier turns' spend again. An
    attempt is its task, its stamp and its candidate (S-0122/D-7), as `torve
    why` groups it. A row with a kind, a shadow replay's, or one with no stamp
    passes through, so a replay never stands in for the live attempt it
    shares a stamp with, and the order is the stream's."""

    listed = list(rows)
    newest = {key: index for index, row in enumerate(listed) if (key := _attempt(row))}

    return [
        row
        for index, row in enumerate(listed)
        if (key := _attempt(row)) is None or newest[key] == index
    ]


def _attempt(row: dict[str, Any]) -> tuple[str, int, int] | None:
    agent: Any = row.get("agent")

    if row.get("kind") or not isinstance(agent, dict) or agent.get("shadow") is True:
        return None

    stamp, candidate = agent.get("attempt"), agent.get("candidate")

    if not isinstance(stamp, int) or isinstance(stamp, bool):
        return None

    if not isinstance(candidate, int) or isinstance(candidate, bool):
        candidate = 0

    return (str(row.get("task_id") or ""), stamp, candidate)


def lane_verdicts(root: Path) -> dict[str, str]:
    """Task id -> the last verdict the lane recorded for its pull request."""

    ledger: dict[str, str] = {}

    for row in stream_rows(root):
        verdict = LANE_VERDICTS.get(str(row.get("event", "")))
        task = str(row.get("task", ""))

        if verdict and task:
            ledger[task] = verdict

    return ledger


def lane_finding_reraised(root: Path) -> dict[tuple[str, str, int], str]:
    """(branch, path, line) -> the head the lane last recorded a re-raise at
    (S-0099/D-9): a finding raised again on that same head writes no new row."""

    found: dict[tuple[str, str, int], str] = {}

    for row in stream_rows(root):
        if row.get("event") == "lane_finding_reraised":
            key = (
                str(row.get("branch") or ""),
                str(row.get("path") or ""),
                int(row.get("line") or 0),
            )
            found[key] = str(row.get("head") or "")

    return found


def round_already_requeued(root: Path, task_id: str) -> bool:
    """Whether the review leg already widened this round to the whole phasing
    (S-0092/D-4)."""

    return any(
        row.get("event") == "lane_round_requeued" and row.get("task") == task_id
        for row in stream_rows(root)
    )


def recorded_rows(root: Path) -> list[dict[str, Any]]:
    """The whole telemetry stream, for a review leg that reads only its own
    vocabulary of events (S-0086/D-3)."""

    return stream_rows(root)
