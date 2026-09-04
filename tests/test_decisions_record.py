"""Sources and decisions as records (RFC 0047).

The importer's contract is a comparison, so most of these build a small
corpus on disk, import it into an in-memory log, and check what the second
import has to say — which for an unchanged corpus is nothing. The one case
that reaches for the real corpus is the parity test: the record's answer to
"what governs these paths" against the file reader's, over this
repository's own 47 documents.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime

from torve.adapters.eventstore.document import mock_module
from torve.application import decisions
from torve.application.eventlog import EventLog, event_log
from torve.application.planner import PlanError, standing_decisions
from torve.domain.events import ActorKind, EventKind, SubjectType, UnauthorizedWrite

PARTITION = "morzecrew/torve"


# ....................... #


def run(scenario: Callable[[EventLog], Awaitable[None]]) -> None:
    """One runtime per case, torn down with the scope — the same helper
    `test_eventlog.py` uses, for the same reason."""

    async def main() -> None:
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            await scenario(event_log(runtime.get_context()))

    asyncio.run(main())


# ....................... #


def document(
    tmp_path: Path,
    number: str,
    rows: list[tuple[str, str, str, str]],
    *,
    status: str = "accepted",
    title: str = "A document",
    retired: list[str] | None = None,
) -> Path:
    """One corpus document with the table the case needs. Written by hand
    rather than through the emitter: what the importer must read is the
    rendered markdown, and a test that produced it from the same model would
    not be reading anything."""

    table = "\n".join(
        f"| {ident} | `{grade}` | {text} | {paths} | — |" for ident, grade, text, paths in rows
    )
    retired_line = f"retired: {retired!r}\n" if retired else ""
    body = f"""---
id: "{number}"
title: {title}
status: {status}
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
{retired_line}owner: tester
description: >-
  A document.
schema_version: 1
---

# RFC {number} — {title}

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
{table}
"""
    rfc_dir = tmp_path / "rfcs"
    rfc_dir.mkdir(exist_ok=True)
    path = rfc_dir / f"{number}-a-document.md"
    path.write_text(body, encoding="utf-8")

    return rfc_dir


# ....................... #


async def sync(log: EventLog, rfc_dir: Path) -> list[decisions.PendingEvent]:
    """One import round trip: read the record, compare, append, return what
    was appended."""

    graph = await decisions.load(log, partition=PARTITION)
    pending = decisions.import_corpus(graph, rfc_dir)

    await decisions.record_all(log, pending, partition=PARTITION, actor_id="tester")

    return pending


# ....................... #


def test_an_unchanged_corpus_imports_nothing_the_second_time(tmp_path):
    """Idempotence is the property that makes running this on a schedule
    safe, and it is asserted on the returned events, so it holds for the dry
    run and the real one alike (D-47.5)."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        first = await sync(log, rfc_dir)

        assert [one.kind for one in first] == [
            EventKind.SOURCE_IMPORTED,
            EventKind.DECISION_RECORDED,
        ]
        assert await sync(log, rfc_dir) == []

    run(scenario)


# ....................... #


def test_a_regrade_is_a_new_version_of_the_same_decision(tmp_path):
    """D-47.1: a second record on the same subject is a version, not a
    supersession. `current` holds one, `history` holds both, and the version
    count is what a reader asking "when did this become LOCKED" reads."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "ASSUMED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.DECISION_RECORDED]

        graph = await decisions.load(log, partition=PARTITION)

        assert len(graph.current()) == 1
        assert [one.grade for one in graph.history("D-1.1")] == ["ASSUMED", "LOCKED"]
        assert [one.version for one in graph.history("D-1.1")] == [1, 2]
        assert graph.get("D-1.1").grade == "LOCKED"

    run(scenario)


# ....................... #


def test_a_row_leaving_an_accepted_table_is_recorded_as_retired(tmp_path):
    """D-47.2: absence is not the record. The decision leaves `current` and
    keeps its history, so "what happened to D-1.2" has an answer."""

    rfc_dir = document(
        tmp_path,
        "0001",
        [
            ("D-1.1", "LOCKED", "A rule.", "`src/a.py`"),
            ("D-1.2", "ASSUMED", "Another.", "`src/b.py`"),
        ],
    )

    async def scenario(log):
        await sync(log, rfc_dir)
        document(
            tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")], retired=["D-1.2"]
        )

        pending = await sync(log, rfc_dir)

        assert [(one.kind, one.subject_id) for one in pending] == [
            (EventKind.DECISION_RETIRED, "D-1.2")
        ]

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["D-1.1"]
        assert graph.get("D-1.2").retired
        assert len(graph.history("D-1.2")) == 1  # retirement is not a version
        assert "0001-a-document.md" in graph.get("D-1.2").retired_reason

    run(scenario)


# ....................... #


def test_a_retired_row_that_comes_back_is_recorded_again(tmp_path):
    """The corpus can restore a row a human removed by mistake. The record
    must let it back into force rather than treating retirement as
    terminal — a retired state compared as if it were current would leave
    the decision permanently invisible."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        document(tmp_path, "0001", [])
        await sync(log, rfc_dir)

        assert (await decisions.load(log, partition=PARTITION)).current() == []

        document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])
        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.DECISION_RECORDED]

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["D-1.1"]
        assert len(graph.history("D-1.1")) == 2

    run(scenario)


# ....................... #


def test_a_draft_imports_nothing_and_the_same_document_accepted_imports_at_version_one(tmp_path):
    """D-47.6: a draft's rows were never in force, so recording them would
    date them wrongly — the record's version 1 is the row as accepted."""

    rfc_dir = document(
        tmp_path, "0001", [("D-1.1", "OPEN", "Undecided.", "`src/a.py`")], status="draft"
    )

    async def scenario(log):
        assert await sync(log, rfc_dir) == []

        document(tmp_path, "0001", [("D-1.1", "LOCKED", "Decided.", "`src/a.py`")])
        await sync(log, rfc_dir)

        graph = await decisions.load(log, partition=PARTITION)
        history = graph.history("D-1.1")

        assert [one.version for one in history] == [1]
        assert history[0].grade == "LOCKED"

    run(scenario)


# ....................... #


def test_a_superseded_document_is_never_imported(tmp_path):
    rfc_dir = tmp_path / "rfcs"
    document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])
    path = rfc_dir / "0001-a-document.md"
    path.write_text(path.read_text().replace("superseded_by: null", 'superseded_by: "0002"'))

    async def scenario(log):
        assert await sync(log, rfc_dir) == []

    run(scenario)


# ....................... #


def test_a_source_is_identified_by_number_not_by_filename(tmp_path):
    """D-47.4: a document renamed on disk keeps its identity in the record —
    `ref` is what moves, and the decisions stay attached."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        old = rfc_dir / "0001-a-document.md"
        old.rename(rfc_dir / "0001-a-renamed-document.md")

        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.SOURCE_IMPORTED]

        graph = await decisions.load(log, partition=PARTITION)

        assert graph.sources["rfc/0001"].ref == "0001-a-renamed-document.md"
        assert [one.id for one in graph.by_source("rfc/0001")] == ["D-1.1"]

    run(scenario)


# ....................... #


def test_a_table_the_corpus_checker_would_refuse_is_never_imported(tmp_path):
    """An import must not record a grade `torve rfc check` would not accept:
    the record would then hold a vocabulary the corpus does not have."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "PROBABLY", "A rule.", "`src/a.py`")])

    async def scenario(log):
        graph = await decisions.load(log, partition=PARTITION)

        with pytest.raises(PlanError, match="not mintable"):
            decisions.import_corpus(graph, rfc_dir)

    run(scenario)


# ....................... #


def test_an_agent_may_not_import(tmp_path):
    """The authority table refuses before any store sees the write (D-44.2).
    An agent importing a corpus is not a thing that happens."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        graph = await decisions.load(log, partition=PARTITION)
        pending = decisions.import_corpus(graph, rfc_dir)

        with pytest.raises(UnauthorizedWrite):
            await decisions.record_all(
                log,
                pending,
                partition=PARTITION,
                actor_id="agent-1",
                actor_kind=ActorKind.AGENT,
            )

        assert await log.of_subject_type(SubjectType.DECISION, partition=PARTITION) == []

    run(scenario)


# ....................... #


def test_the_decision_read_does_not_fold_the_execution_log(tmp_path):
    """D-47.7: the corpus-sized slice, not the partition's whole tail. A log
    carrying attempts must not put them in front of a decision query."""

    rfc_dir = document(tmp_path, "0001", [("D-1.1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)

        for n in range(5):
            await log.record(
                EventKind.ATTEMPT_STARTED,
                partition=PARTITION,
                subject_type=SubjectType.TASK,
                subject_id=f"T-000{n}",
                actor_kind=ActorKind.WORKER,
                actor_id="w-1",
                payload={"attempt": 1, "tier": "executor", "agent": "fake"},
            )

        read = await log.of_subject_type(SubjectType.DECISION, partition=PARTITION)

        assert [one.subject_id for one in read] == ["D-1.1"]
        # And the partition's whole tail does carry them, so the narrow read
        # is doing the narrowing rather than the store having nothing to give.
        assert len(await log.since(partition=PARTITION)) == 7

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["D-1.1"]

    run(scenario)


# ....................... #


def test_a_read_that_hits_its_cap_raises_rather_than_folding_a_prefix(tmp_path):
    """A projection built on a truncated read is a wrong answer that looks
    like a right one. The read says so instead."""

    from torve.application.eventlog import TruncatedRead

    rfc_dir = document(
        tmp_path,
        "0001",
        [(f"D-1.{n}", "LOCKED", "A rule.", "`src/a.py`") for n in range(1, 6)],
    )

    async def scenario(log):
        await sync(log, rfc_dir)

        with pytest.raises(TruncatedRead, match="more than 3"):
            await log.of_subject_type(SubjectType.DECISION, partition=PARTITION, limit=3)

    run(scenario)


# ....................... #


def test_the_record_answers_the_paths_question_the_corpus_answers(tmp_path):
    """The parity that says the import read the corpus correctly, over this
    repository's own corpus rather than a fixture.

    `for_paths` and `standing_decisions` call the same `globs_intersect`, so
    a difference here is a difference in what was imported, never in how the
    rule is applied — which is exactly what this is meant to catch.
    """

    rfc_dir = Path(__file__).resolve().parents[1] / "rfcs"
    allow = ["src/torve/application/**", "src/torve/domain/**"]

    async def scenario(log):
        await sync(log, rfc_dir)
        graph = await decisions.load(log, partition=PARTITION)

        from_record = sorted((one.id, one.grade) for one in graph.for_paths(allow))
        from_files = sorted((one.id, one.grade) for one in standing_decisions(rfc_dir, allow))

        assert from_record == from_files
        assert from_record, "the corpus governs these paths; an empty answer is a broken read"

    run(scenario)
