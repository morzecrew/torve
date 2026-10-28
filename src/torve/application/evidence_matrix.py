"""The joined evidence table (S-0115/D-5): after the whole-document battery,
one entry per carried row, joining what proves it and what argues against it.

The `evidence` gate's reading says whether a row was proved and by which tests;
the row's `decision:<id>` gate says whether its own check held; the `mutation`
gate's survivors whose line falls under the row's paths are the wrong
implementations its tests did not notice; and the review findings recorded on
the document's tasks whose evidence path falls under the row's paths are what
the reviewers argued. One table answers, row by row, what proves it and what
argues against it (S-0115/D-5).

The table is carried as a telemetry record keyed by the tip it was measured on
(S-0115/D-6): the lane writes it after the whole-document battery, and the
publisher reads exactly the record for the tip it publishes. A body never
carries a table measured on another tip, and a battery with no `evidence`
result leaves the body as it was (S-0115/D-7).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from torve.base.globs import matches
from torve.config import layout
from torve.domain.attempt import Finding, GateResult, mutation_reading
from torve.domain.task import InheritedDecision
from torve.gates.evidence import CITATION
from torve.gates.row_evidence import evidence_reading

# ----------------------- #

#: The engine event the lane writes its joined table under, keyed by `tip`
#: (S-0115/D-6). The publisher reads the same event back for its own tip.
EVIDENCE_EVENT = "document_evidence"

# ----------------------- #


@dataclass(frozen=True)
class RowRecord:
    """One row of the joined table: the evidence verdict, the row's check, and
    what its paths collected against it."""

    row: str
    """The row's global identifier."""
    state: str
    """The `evidence` verdict: `pass`, `fail`, `check` or `documentation`."""
    tests: tuple[str, ...] = ()
    """The tests the `evidence` gate found proving the row."""
    check: str = ""
    """The row's `decision:<id>` gate outcome; empty when no such gate ran."""
    survivors: tuple[str, ...] = ()
    """Mutation survivors whose line falls under the row's paths."""
    findings: tuple[str, ...] = ()
    """Review findings on the document's tasks whose evidence falls under the
    row's paths, by claim."""


# ....................... #


def _evidence(results: Sequence[GateResult]) -> list[Any] | None:
    """The `evidence` gate's rows, or None when this battery carried no result
    for it — the absent-is-absent rule the body keeps (S-0115/D-7)."""

    for result in reversed(results):
        if result.name == "evidence":
            return evidence_reading(result.output) or None

    return None


def _survivors(results: Sequence[GateResult]) -> list[str]:
    found: list[str] = []

    for result in results:
        reading = mutation_reading(result.model_dump())

        if reading is not None:
            found.extend(cast("list[str]", reading["survivors"]))

    return found


def _line_path(citation: str) -> str:
    """The path a `path:line — edit` reading or a `path:line` citation names."""

    return citation.split(" — ", 1)[0].rsplit(":", 1)[0]


def _evidence_path(evidence: str) -> str | None:
    """The path a finding's leading citation names, or None when its evidence
    is a backticked command — it then falls under no row's paths."""

    citation = evidence.split(" — ")[0].split(" - ")[0].strip()
    found = CITATION.match(citation)

    return found.group("path") if found else None


def join(
    rows: Iterable[InheritedDecision],
    results: Sequence[GateResult],
    findings: Iterable[Finding] = (),
) -> list[RowRecord] | None:
    """The joined table, in the order the `evidence` gate named its rows, or
    None when the battery carried no `evidence` result — there is then nothing
    to join and the body stays as it was (S-0115/D-7)."""

    reading = _evidence(results)

    if reading is None:
        return None

    paths = {row.id: list(row.paths) for row in rows}
    checks = {
        result.name[len("decision:") :]: result.outcome
        for result in results
        if result.name.startswith("decision:")
    }
    survivors = _survivors(results)
    recorded = list(findings)

    joined: list[RowRecord] = []

    for found in reading:
        row_paths = paths.get(found.row, [])
        joined.append(
            RowRecord(
                row=found.row,
                state=found.state,
                tests=found.tests,
                check=checks.get(found.row, ""),
                survivors=tuple(
                    survivor for survivor in survivors if matches(_line_path(survivor), row_paths)
                ),
                findings=tuple(
                    finding.claim
                    for finding in recorded
                    if (path := _evidence_path(finding.evidence)) is not None
                    and matches(path, row_paths)
                ),
            )
        )

    return joined


# ....................... #


def document_decisions(root: Path, document: str) -> list[InheritedDecision]:
    """The document's own rows, read from the corpus under *root*. A corpus the
    loader refuses, or a document it does not hold, names no row — the same
    silence `_document_inputs` keeps."""

    from torve.config import spec

    try:
        corpus = spec.load_corpus(root / layout.SPECS_DIR)

    except (spec.SpecError, OSError):
        return []

    found = corpus.document(document)

    if found is None:
        return []

    return [
        InheritedDecision(
            id=row.id,
            grade=row.grade,
            text=row.text.strip(),
            paths=list(row.paths),
            consequence=row.consequence.strip(),
            check=row.check,
            check_state=row.check_state,
            check_twin=row.check_twin,
        )
        for row in found.decisions
    ]


def recorded_findings(root: Path, tasks: Iterable[str]) -> list[Finding]:
    """The findings the review records carry against *tasks*, read from the
    telemetry stream where the reviewers wrote them. An unparseable finding is
    skipped, as the stream's other readers skip their bad lines."""

    from torve.application.streams import stream_rows

    wanted = set(tasks)
    found: list[Finding] = []

    for row in stream_rows(root):
        if row.get("kind") != "review" or str(row.get("target") or "") not in wanted:
            continue

        raw = row.get("findings")

        if not isinstance(raw, list):
            continue

        for item in cast("list[object]", raw):
            if not isinstance(item, dict):
                continue

            try:
                found.append(Finding.model_validate(item))

            except ValueError:
                continue

    return found


# ....................... #


def record_table(root: Path, tip: str, document: str, rows: Sequence[RowRecord]) -> None:
    """Append the joined table as a telemetry record keyed by the tip it was
    measured on (S-0115/D-6). Append-only, so a later battery at a later tip
    leaves the earlier one readable."""

    from torve.application.telemetry import engine_event

    engine_event(
        root,
        EVIDENCE_EVENT,
        {
            "tip": tip,
            "document": document,
            "rows": [
                {
                    "row": row.row,
                    "state": row.state,
                    "tests": list(row.tests),
                    "check": row.check,
                    "survivors": list(row.survivors),
                    "findings": list(row.findings),
                }
                for row in rows
            ],
        },
    )


def _record(item: dict[str, Any]) -> RowRecord:
    return RowRecord(
        row=str(item.get("row") or ""),
        state=str(item.get("state") or ""),
        tests=tuple(str(test) for test in cast("list[object]", item.get("tests") or [])),
        check=str(item.get("check") or ""),
        survivors=tuple(
            str(survivor) for survivor in cast("list[object]", item.get("survivors") or [])
        ),
        findings=tuple(
            str(finding) for finding in cast("list[object]", item.get("findings") or [])
        ),
    )


def read_table(root: Path, tip: str) -> list[RowRecord] | None:
    """The joined table measured on *tip*, or None when no battery recorded one
    for that tip — the body then keeps its table as it is (S-0115/D-6, D-7)."""

    if not tip:
        return None

    from torve.application.streams import stream_rows

    found: dict[str, Any] | None = None

    for row in stream_rows(root):
        if (
            row.get("kind") == "engine"
            and row.get("event") == EVIDENCE_EVENT
            and str(row.get("tip") or "") == tip
        ):
            found = row

    if found is None:
        return None

    raw = found.get("rows")

    if not isinstance(raw, list):
        return None

    records = [
        _record(cast("dict[str, Any]", item))
        for item in cast("list[object]", raw)
        if isinstance(item, dict)
    ]

    return records or None
