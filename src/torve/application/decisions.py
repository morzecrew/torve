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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pathspec import GitIgnoreSpec

from torve.application.planner import PlanError, globs_intersect
from torve.config import spec
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
from torve.domain.source import Source, corpus_source_id
from torve.domain.spec import rule_fingerprint

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from torve.application.eventlog import EventLog
    from torve.domain.rfc import Grade
    from torve.domain.spec import Corpus, Coverage, Decision, Document

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
    consequence: str = ""  # D-54.1: carried since RFC 0054
    check: str | None = None


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
                    consequence=str(payload.get("consequence") or ""),
                    check=payload.get("check"),
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
        consequence=state.consequence,
        check=state.check,
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


def corpus_sources(rfc_dir: Path) -> dict[str, Source]:
    """Every importable document as a source, keyed by source id.

    Importable is accepted and not superseded — the same admission
    `standing_decisions` applies, because a draft's rows were never in force
    and recording them would date them wrongly (D-47.6).
    """

    return {
        corpus_source_id(doc.id): _source_of(doc)
        for doc in load_corpus(rfc_dir).standing()
        if not doc.superseded_by
    }


# ....................... #


def load_corpus(rfc_dir: Path) -> Corpus:
    """The corpus and its archive as the model (D-53.1), with the loader's
    refusals raised as `PlanError` so an import never records a grade
    `torve rfc check` would not accept — the same promise the parser-based
    importer made, kept at the same boundary."""

    try:
        return spec.load_corpus(rfc_dir)
    except spec.SpecError as exc:
        raise PlanError("; ".join(exc.problems)) from exc


# ....................... #


def _source_of(doc: Document) -> Source:
    return Source(
        id=corpus_source_id(doc.id),
        kind="specification",
        ref=str(Path(doc.path).name) if doc.path else f"{doc.id}.yaml",
        title=doc.title,
    )


# ....................... #


def import_corpus(graph: Graph, rfc_dir: Path) -> list[PendingEvent]:
    """The events that would bring the record level with the corpus.

    An unchanged corpus returns an empty list — the idempotence that makes
    running this on a schedule safe, and the headline property of the tests.
    Raises `PlanError` on a table the corpus's own checker would refuse, so
    an import never records a grade `torve rfc check` would not accept.

    Read through the model (D-53.13): a standing document's rows are
    recorded as before; an archived document (D-53.8) is recorded as a
    source and every row it carries is retired with the archive named as
    the reason (D-53.9), so an identifier cited from the archive still
    resolves in the record.
    """

    corpus = load_corpus(rfc_dir)
    pending: list[PendingEvent] = []
    standing_docs = {corpus_source_id(d.id): d for d in corpus.standing() if not d.superseded_by}
    archived_docs = {corpus_source_id(d.id): d for d in corpus.documents if d.archived}
    seen: set[str] = set()

    for source_id in sorted(standing_docs):
        doc = standing_docs[source_id]
        source = _source_of(doc)
        known = graph.sources.get(source_id)

        if known is None or known.ref != source.ref or known.title != source.title:
            pending.append(_source_event(source))

        for row in doc.decisions:
            seen.add(row.id)
            standing = graph.get(row.id)

            if (
                standing is not None
                and not standing.retired
                and standing.grade == row.grade
                and standing.text == row.text.strip()
                and standing.paths == row.paths
                and standing.source_id == source.id
                and standing.consequence == row.consequence.strip()
                and standing.check == row.check
            ):
                continue

            pending.append(
                PendingEvent(
                    kind=EventKind.DECISION_RECORDED,
                    subject_type=SubjectType.DECISION,
                    subject_id=row.id,
                    payload=_row_payload(row, source.id),
                )
            )

    # An archived document: its source is recorded so the archive is a
    # provenance the record knows, and every row it still carries retires
    # with the archive as the reason (D-53.9). A row already retired stays
    # as it was — the first reason is the true one.
    for source_id in sorted(archived_docs):
        doc = archived_docs[source_id]
        source = _source_of(doc)
        known = graph.sources.get(source_id)

        if known is None or known.ref != source.ref or known.title != source.title:
            pending.append(_source_event(source))

        for row in doc.decisions:
            seen.add(row.id)
            standing = graph.get(row.id)
            superseded = f", superseded by {doc.superseded_by}" if doc.superseded_by else ""

            if standing is None:
                pending.append(
                    PendingEvent(
                        kind=EventKind.DECISION_RECORDED,
                        subject_type=SubjectType.DECISION,
                        subject_id=row.id,
                        payload=_row_payload(row, source.id),
                    )
                )
            elif standing.retired:
                continue

            pending.append(
                PendingEvent(
                    kind=EventKind.DECISION_RETIRED,
                    subject_type=SubjectType.DECISION,
                    subject_id=row.id,
                    payload={"reason": f"archived in {Path(doc.path).name}{superseded}"},
                )
            )

    # A row the record holds for a corpus source that the corpus no longer
    # carries. Scoped to sources this import actually read: a decision from
    # an incident or an operator ask is not retired by a corpus import that
    # never had anything to say about it.
    sources = {**standing_docs, **archived_docs}
    retired_where = {
        ident: Path(doc.path).name for doc in corpus.documents for ident in doc.retired if doc.path
    }

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
                        else f"no longer in {_source_of(sources[state.source_id]).ref}"
                    )
                },
            )
        )

    return pending


# ....................... #


def _row_payload(row: Decision, source_id: str) -> dict[str, Any]:
    """What `decision.recorded` carries (D-54.1): grade, text and paths as
    before, and beside them the consequence and the check, so a reader of
    the record gets the reason and the command the corpus wrote."""

    return {
        "grade": row.grade,
        "text": row.text.strip(),
        "paths": list(row.paths),
        "source_id": source_id,
        "consequence": row.consequence.strip(),
        "check": row.check,
    }


def _source_event(source: Source) -> PendingEvent:
    return PendingEvent(
        kind=EventKind.SOURCE_IMPORTED,
        subject_type=SubjectType.SOURCE,
        subject_id=source.id,
        payload={"source_kind": source.kind, "ref": source.ref, "title": source.title},
    )


# ----------------------- #


def _governs(globs: list[str], path: str) -> bool:
    """Whether one set of declared globs reaches a path: the path matches
    a glob, or a glob names something under the path (a directory asked
    about is governed by rows that reach into it)."""

    if not globs:
        return False

    if GitIgnoreSpec.from_lines(globs).match_file(path):
        return True

    return globs_intersect(globs, [path.rstrip("/") + "/**" if not path.endswith("**") else path])


# ....................... #


def coverage(corpus: Corpus, path: str) -> Coverage:
    """One of three for any path (D-53.6): governed — a standing row's
    paths or an accepted document's phase scope reaches it; retired — only
    archived documents' rows ever did; ungoverned — nothing, which is the
    ratchet's frontier and never a finding."""

    for doc in corpus.standing():
        if doc.superseded_by:
            continue

        if any(_governs(row.paths, path) for row in doc.decisions):
            return "governed"

        if any(_governs(entry.scope, path) for entry in doc.phasing):
            return "governed"

    for doc in corpus.documents:
        if not doc.archived:
            continue

        if any(_governs(row.paths, path) for row in doc.decisions):
            return "retired"

    return "ungoverned"


# ....................... #


@dataclass(frozen=True)
class RottedRow:
    """A row whose every glob matches nothing in the tree (D-53.7)."""

    document: str
    identifier: str
    grade: str
    paths: list[str]

    def line(self) -> str:
        return (
            f"{self.document}: {self.identifier} ({self.grade}) declares "
            f"{' '.join(self.paths)} and nothing in the tree matches — retire it "
            f"with `torve rfc amend {self.document[:4]} --retire {self.identifier} "
            "--reason path-rot`"
        )


# ....................... #


def _matches(root: Path, pattern: str) -> bool:
    try:
        return next(root.glob(pattern), None) is not None
    except (ValueError, NotImplementedError):
        return False


def path_rot(corpus: Corpus, root: Path) -> list[RottedRow]:
    """Every standing row on an accepted, implemented document whose globs
    all match nothing under *root* — governance that governs nothing. A
    document not yet implemented names areas that do not exist yet, which
    is intent, not rot (D-32)."""

    rotted: list[RottedRow] = []

    for doc in corpus.standing():
        if doc.implementation == "none" or doc.superseded_by:
            continue

        for row in doc.decisions:
            if not row.paths:
                continue

            if any(_matches(root, pattern) for pattern in row.paths):
                continue

            rotted.append(
                RottedRow(
                    document=Path(doc.path).name if doc.path else doc.id,
                    identifier=row.id,
                    grade=row.grade,
                    paths=list(row.paths),
                )
            )

    return rotted


# ----------------------- #


def fingerprint_drift(corpus: Corpus) -> tuple[list[str], list[str]]:
    """(problems, warnings) over every row the tool has ever stamped: a
    grade or paths change by hand is a problem — a row with no history —
    and a text-only change is editorial drift, a warning that names the
    verb that re-stamps it (D-53.4). A row never stamped is not compared:
    the corpus's existing rows have no recorded change to differ from."""

    problems: list[str] = []
    warnings: list[str] = []

    for doc in corpus.documents:
        if doc.archived:
            continue

        where = Path(doc.path).name if doc.path else doc.id

        for row in doc.decisions:
            if not row.fingerprint:
                continue  # never stamped: no recorded change to differ from

            full, _, rule = row.fingerprint.partition("/")

            if row.content_fingerprint() == full:
                continue

            if rule and rule_fingerprint(row.grade, row.paths) == rule:
                warnings.append(
                    f"{where}: {row.id}'s text changed by hand since its last recorded change "
                    f'(editorial drift) — `torve rfc fix {row.id} "…"` re-stamps it'
                )
            else:
                problems.append(
                    f"{where}: {row.id}'s grade or paths changed by hand since its last recorded "
                    "change — a row with no history; change it through `torve rfc amend`"
                )

    return problems, warnings


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
    "RottedRow",
    "corpus_sources",
    "coverage",
    "fingerprint_drift",
    "import_corpus",
    "load",
    "load_corpus",
    "path_rot",
    "project",
    "record_all",
]
