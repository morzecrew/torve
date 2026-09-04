"""The decision graph, and the corpus as one importer of it (RFC 0047).

Two halves that meet at a fold. `project` turns the record's source and
decision events into current state, version history and supersession edges;
`import_corpus` compares an accepted document's table to that state and says
which events the difference calls for. Nothing here writes: the importer
returns the events and the caller appends them, so a dry run is the same
comparison with the write skipped and `--check` cannot drift from what a
real import would do (D-47.5).

The one distinction worth stating twice, because getting it backwards makes
every count wrong and the error invisible (D-47.1): a second
`decision.recorded` on the same subject is a **new version of that
decision** — the regrade a corpus amendment performs. `supersedes` names a
**different** decision that this one replaces. They are different edges in
the graph and neither expresses the other.

Retirement is recorded, never inferred from a row that stopped appearing
(D-47.2, A-89). Absence cannot tell a deliberate retirement from a table
someone broke, and for a source that is an incident rather than a file it
means nothing at all.

What this does *not* do: minting. A contract still copies its grades at
write time from the document a human committed (D-47.3). The paths rule is
shared with `planner.standing_decisions` rather than reimplemented, so the
record's answer and the corpus's differ only by staleness — which is a fact
about when someone last imported, and is what the parity test measures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from torve.application.planner import PlanError, globs_intersect, inherit_decisions
from torve.config import rfc_parse
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
from torve.domain.source import Source, corpus_source_id

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from pathlib import Path

    from torve.application.eventlog import EventLog
    from torve.domain.rfc import Grade

# ----------------------- #

# What an importer is allowed to say about a corpus document. Draft and
# superseded documents are never read: their decisions do not stand, which
# is already the rule at every other reader (D-7.7, D-30.1, D-47.6).
ACCEPTED = "accepted"


# ....................... #


@dataclass(frozen=True)
class DecisionState:
    """One decision as the record currently holds it.

    `version` counts the records on this subject, so version 1 is the row as
    first imported and version 3 is the row after two amendments. `at` is
    when the version was recorded, which is the question `git log -p` over
    the corpus answers today.
    """

    id: str
    grade: Grade
    text: str
    paths: list[str]
    source_id: str
    version: int
    at: datetime
    retired: bool = False
    retired_reason: str = ""
    supersedes: str | None = None


# ....................... #


@dataclass
class Graph:
    """The sources and decisions of one partition, folded.

    A projection, so it is rebuilt by reading again and never edited: every
    query below reads the two dictionaries this fold produced.
    """

    sources: dict[str, Source] = field(default_factory=dict)
    versions: dict[str, list[DecisionState]] = field(default_factory=dict)

    # ....................... #

    def get(self, identifier: str) -> DecisionState | None:
        """The decision as it stands, retired or not. None means the record
        has never heard of it — which is different from retired, and callers
        that conflate the two report a broken corpus as an empty one."""

        history = self.versions.get(identifier)

        return history[-1] if history else None

    # ....................... #

    def history(self, identifier: str) -> list[DecisionState]:
        """Every version, oldest first."""

        return list(self.versions.get(identifier, ()))

    # ....................... #

    def current(self) -> list[DecisionState]:
        """Every decision in force, by identifier. A retired decision keeps
        its history and leaves this list."""

        live = (self.get(one) for one in sorted(self.versions))

        return [one for one in live if one is not None and not one.retired]

    # ....................... #

    def for_paths(self, globs: list[str]) -> list[DecisionState]:
        """The decisions in force whose declared paths cross `globs` — the
        standing-inheritance rule (RFC 0030 §5.1), from the same
        `globs_intersect` that rule calls.

        Rows without declared paths are never standing: they govern their
        own document's work only (D-30.1). Conservative on purpose — a false
        inclusion costs a few contract lines, a false exclusion costs the
        silence check.
        """

        return [one for one in self.current() if one.paths and globs_intersect(one.paths, globs)]

    # ....................... #

    def by_source(self, source_id: str) -> list[DecisionState]:
        """One source's decisions in force, in identifier order."""

        return [one for one in self.current() if one.source_id == source_id]


# ....................... #


def project(events: Iterable[EventRecord]) -> Graph:
    """Fold the record's intent half. Events arrive oldest first, which is
    what makes the last record on a subject its current version."""

    graph = Graph()

    for event in events:
        payload = event.payload

        if event.kind == EventKind.SOURCE_IMPORTED:
            graph.sources[event.subject_id] = Source(
                id=event.subject_id,
                kind=payload["source_kind"],
                ref=str(payload.get("ref", "")),
                title=str(payload.get("title", "")),
            )

        elif event.kind == EventKind.DECISION_RECORDED:
            history = graph.versions.setdefault(event.subject_id, [])
            history.append(
                DecisionState(
                    id=event.subject_id,
                    grade=payload["grade"],
                    text=str(payload.get("text", "")),
                    paths=list(payload.get("paths") or []),
                    source_id=str(payload.get("source_id", "")),
                    version=len(history) + 1,
                    at=event.created_at,
                    supersedes=payload.get("supersedes"),
                )
            )

        elif event.kind == EventKind.DECISION_RETIRED:
            standing = graph.get(event.subject_id)

            if standing is None:
                # A retirement for a decision the record never held. Kept
                # rather than dropped: it is a fact somebody recorded, and a
                # projection that silently discards one answers "no such
                # decision" to a question whose answer is "retired".
                graph.versions.setdefault(event.subject_id, [])
                continue

            # Retirement does not add a version — the decision's text and
            # grade are the ones it retired at (D-47.1).
            history = graph.versions[event.subject_id]
            history[-1] = _retired(standing, str(payload.get("reason", "")))

    return graph


# ....................... #


def _retired(state: DecisionState, reason: str) -> DecisionState:
    return DecisionState(
        id=state.id,
        grade=state.grade,
        text=state.text,
        paths=state.paths,
        source_id=state.source_id,
        version=state.version,
        at=state.at,
        retired=True,
        retired_reason=reason,
        supersedes=state.supersedes,
    )


# ....................... #


@dataclass(frozen=True)
class PendingEvent:
    """One event an import would append. Returned rather than written so a
    dry run and a real one are the same comparison (D-47.5)."""

    kind: EventKind
    subject_type: SubjectType
    subject_id: str
    payload: dict[str, Any]

    # ....................... #

    def line(self) -> str:
        """One line for an operator reading `--check`."""

        return f"{self.kind} {self.subject_id}"


# ....................... #


def corpus_sources(rfc_dir: Path) -> dict[str, tuple[Source, str]]:
    """Every importable document as (source, text), keyed by source id.

    Importable is accepted and not superseded — the same admission
    `standing_decisions` applies, because a draft's rows were never in force
    and recording them would date them wrongly (D-47.6).
    """

    found: dict[str, tuple[Source, str]] = {}

    for number, path in rfc_parse.rfc_files(rfc_dir).items():
        text = path.read_text(encoding="utf-8")
        frontmatter = rfc_parse.parse_frontmatter(text)

        if frontmatter is None:
            continue

        if str(frontmatter.get("status", "")) != ACCEPTED or frontmatter.get("superseded_by"):
            continue

        source = Source(
            id=corpus_source_id(number),
            kind="specification",
            ref=str(path.name),
            title=str(frontmatter.get("title", "")),
        )
        found[source.id] = (source, text)

    return found


# ....................... #


def import_corpus(graph: Graph, rfc_dir: Path) -> list[PendingEvent]:
    """The events that would bring the record level with the corpus.

    An unchanged corpus returns an empty list — the idempotence that makes
    running this on a schedule safe, and the headline property of the tests.
    Raises `PlanError` on a table the corpus's own checker would refuse, so
    an import never records a grade `torve rfc check` would not accept.
    """

    pending: list[PendingEvent] = []
    sources = corpus_sources(rfc_dir)
    seen: set[str] = set()

    for source_id in sorted(sources):
        source, text = sources[source_id]
        known = graph.sources.get(source_id)

        if known is None or known.ref != source.ref or known.title != source.title:
            pending.append(
                PendingEvent(
                    kind=EventKind.SOURCE_IMPORTED,
                    subject_type=SubjectType.SOURCE,
                    subject_id=source.id,
                    payload={
                        "source_kind": source.kind,
                        "ref": source.ref,
                        "title": source.title,
                    },
                )
            )

        for row in inherit_decisions(text, source.ref):
            seen.add(row.id)
            standing = graph.get(row.id)

            if (
                standing is not None
                and not standing.retired
                and standing.grade == row.grade
                and standing.text == row.text
                and standing.paths == row.paths
                and standing.source_id == source.id
            ):
                continue

            pending.append(
                PendingEvent(
                    kind=EventKind.DECISION_RECORDED,
                    subject_type=SubjectType.DECISION,
                    subject_id=row.id,
                    payload={
                        "grade": row.grade,
                        "text": row.text,
                        "paths": list(row.paths),
                        "source_id": source.id,
                    },
                )
            )

    # A row the record holds for a corpus source that the corpus no longer
    # carries. Scoped to sources this import actually read: a decision from
    # an incident or an operator ask is not retired by a corpus import that
    # never had anything to say about it.
    retired_where = rfc_parse.retired_identifiers(rfc_parse.rfc_files(rfc_dir))

    for state in graph.current():
        if state.id in seen or state.source_id not in sources:
            continue

        where = retired_where.get(state.id)
        pending.append(
            PendingEvent(
                kind=EventKind.DECISION_RETIRED,
                subject_type=SubjectType.DECISION,
                subject_id=state.id,
                payload={
                    "reason": (
                        f"retired in {where}"
                        if where
                        else f"no longer in {sources[state.source_id][0].ref}"
                    )
                },
            )
        )

    return pending


# ....................... #


async def load(log: EventLog, *, partition: str) -> Graph:
    """The partition's decision graph, read by subject type rather than by
    folding its whole execution history (D-47.7)."""

    sources = await log.of_subject_type(SubjectType.SOURCE, partition=partition)
    decisions = await log.of_subject_type(SubjectType.DECISION, partition=partition)

    return project(sorted([*sources, *decisions], key=lambda one: (one.created_at, str(one.id))))


# ....................... #


async def record_all(
    log: EventLog,
    pending: Iterable[PendingEvent],
    *,
    partition: str,
    actor_id: str,
    actor_kind: ActorKind = ActorKind.OPERATOR,
) -> int:
    """Append what an import decided, in order. The authority table refuses
    an actor that may not write these kinds before any store sees the write
    (D-44.2) — an agent importing a corpus is not a thing that happens."""

    count = 0

    for event in pending:
        await log.record(
            event.kind,
            partition=partition,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            payload=event.payload,
        )
        count += 1

    return count


# ....................... #


__all__ = [
    "DecisionState",
    "Graph",
    "PendingEvent",
    "PlanError",
    "corpus_sources",
    "import_corpus",
    "load",
    "project",
    "record_all",
]
