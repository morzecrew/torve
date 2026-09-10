"""Sources and decisions as records (S-0047).

The importer's contract is a comparison, so most of these build a small
corpus on disk, import it into an in-memory log, and check what the second
import has to say — which for an unchanged corpus is nothing. The one case
that reaches for the real corpus is the parity test: the record's answer to
"what governs these paths" against the file reader's, over this
repository's own corpus and archive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from forze.application.execution import DepsRegistry, ExecutionRuntime
from test_decisions import corpus as spec_corpus
from test_decisions import document as spec_document

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
    superseded_by: str | None = None,
) -> Path:
    """One corpus document with the rows the case needs, through the corpus
    builder every suite shares (S-0057 S-0057/D-1), and the corpus directory
    it was written into."""

    return spec_corpus(
        tmp_path,
        **{
            number: spec_document(
                number,
                rows,
                status=status,
                title=title,
                retired=retired,
                superseded_by=superseded_by,
                implementation="none",
            )
        },
    )


# ....................... #


async def sync(log: EventLog, rfc_dir: Path) -> list[decisions.PendingEvent]:
    """One import round trip: read the record, compare, append, return what
    was appended."""

    graph = await decisions.load(log, partition=PARTITION)
    pending = decisions.import_sources(graph, rfc_dir.parent.parent, rfc_dir)

    await decisions.record_all(log, pending, partition=PARTITION, actor_id="tester")

    return pending


# ....................... #


def test_every_filed_source_is_recorded_with_its_own_kind(tmp_path):
    """S-0060/D-7: the four kinds beside `specification` finally have a
    producer, and a second import over an unchanged tree records nothing.
    A source whose file is deleted keeps what was recorded — the record holds
    what was true, and deleting the file is how a source stops being offered."""

    import yaml

    from torve.config.sources import schema_header

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])
    filed = tmp_path / ".torve" / "sources" / "audit" / "soc2-2026.yaml"
    filed.parent.mkdir(parents=True, exist_ok=True)
    filed.write_text(
        f"{schema_header()}\n"
        + yaml.safe_dump(
            {
                "id": "audit/soc2-2026",
                "kind": "audit",
                "title": "A gap",
                "ref": "https://example.invalid/42",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    async def scenario(log: EventLog) -> None:
        pending = await sync(log, rfc_dir)
        filed_events = [
            p
            for p in pending
            if p.kind is EventKind.SOURCE_IMPORTED and p.subject_id == "audit/soc2-2026"
        ]

        assert len(filed_events) == 1
        assert filed_events[0].payload["source_kind"] == "audit"
        assert filed_events[0].payload["title"] == "A gap"

        assert await sync(log, rfc_dir) == []

        filed.unlink()

        # The record keeps what was recorded: nothing retires a source.
        assert await sync(log, rfc_dir) == []

    run(scenario)


# ....................... #


def test_an_unchanged_corpus_imports_nothing_the_second_time(tmp_path):
    """Idempotence is the property that makes running this on a schedule
    safe, and it is asserted on the returned events, so it holds for the dry
    run and the real one alike (S-0047/D-5)."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

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
    """S-0047/D-1: a second record on the same subject is a version, not a
    supersession. `current` holds one, `history` holds both, and the version
    count is what a reader asking "when did this become LOCKED" reads."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "ASSUMED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.DECISION_RECORDED]

        graph = await decisions.load(log, partition=PARTITION)

        assert len(graph.current()) == 1
        assert [one.grade for one in graph.history("S-0001/D-1")] == ["ASSUMED", "LOCKED"]
        assert [one.version for one in graph.history("S-0001/D-1")] == [1, 2]
        assert graph.get("S-0001/D-1").grade == "LOCKED"

    run(scenario)


# ....................... #


def test_a_row_leaving_an_accepted_table_is_recorded_as_retired(tmp_path):
    """S-0047/D-2: absence is not the record. The decision leaves `current` and
    keeps its history, so "what happened to S-0001/D-2" has an answer."""

    rfc_dir = document(
        tmp_path,
        "0001",
        [
            ("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`"),
            ("S-0001/D-2", "ASSUMED", "Another.", "`src/b.py`"),
        ],
    )

    async def scenario(log):
        await sync(log, rfc_dir)
        document(
            tmp_path,
            "0001",
            [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")],
            retired=["S-0001/D-2"],
        )

        pending = await sync(log, rfc_dir)

        assert [(one.kind, one.subject_id) for one in pending] == [
            (EventKind.DECISION_RETIRED, "S-0001/D-2")
        ]

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["S-0001/D-1"]
        assert graph.get("S-0001/D-2").retired
        assert len(graph.history("S-0001/D-2")) == 1  # retirement is not a version
        assert "S-0001" in graph.get("S-0001/D-2").retired_reason

    run(scenario)


# ....................... #


def test_a_retired_row_that_comes_back_is_recorded_again(tmp_path):
    """The corpus can restore a row a human removed by mistake. The record
    must let it back into force rather than treating retirement as
    terminal — a retired state compared as if it were current would leave
    the decision permanently invisible."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        document(tmp_path, "0001", [])
        await sync(log, rfc_dir)

        assert (await decisions.load(log, partition=PARTITION)).current() == []

        document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])
        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.DECISION_RECORDED]

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["S-0001/D-1"]
        assert len(graph.history("S-0001/D-1")) == 2

    run(scenario)


# ....................... #


def test_a_draft_imports_nothing_and_the_same_document_accepted_imports_at_version_one(tmp_path):
    """S-0047/D-6: a draft's rows were never in force, so recording them would
    date them wrongly — the record's version 1 is the row as accepted."""

    rfc_dir = document(
        tmp_path, "0001", [("S-0001/D-1", "OPEN", "Undecided.", "`src/a.py`")], status="draft"
    )

    async def scenario(log):
        assert await sync(log, rfc_dir) == []

        document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "Decided.", "`src/a.py`")])
        await sync(log, rfc_dir)

        graph = await decisions.load(log, partition=PARTITION)
        history = graph.history("S-0001/D-1")

        assert [one.version for one in history] == [1]
        assert history[0].grade == "LOCKED"

    run(scenario)


# ....................... #


def test_a_superseded_document_is_never_imported(tmp_path):
    rfc_dir = document(
        tmp_path,
        "0001",
        [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")],
        superseded_by="0002",
    )

    async def scenario(log):
        assert await sync(log, rfc_dir) == []

    run(scenario)


# ....................... #


def test_a_source_is_identified_by_number_not_by_filename(tmp_path):
    """S-0047/D-4: the source is keyed by number, so what a document calls
    itself is free to move. A directory is its identifier alone now
    (S-0057/D-1), so the slug that used to move is the title — the source is
    re-imported and the decisions stay attached to `S-0001`."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        await sync(log, rfc_dir)
        document(
            tmp_path,
            "0001",
            [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")],
            title="A renamed document",
        )

        pending = await sync(log, rfc_dir)

        assert [one.kind for one in pending] == [EventKind.SOURCE_IMPORTED]

        graph = await decisions.load(log, partition=PARTITION)

        assert graph.sources["S-0001"].ref == "S-0001"
        assert graph.sources["S-0001"].title == "A renamed document"
        assert [one.id for one in graph.by_source("S-0001")] == ["S-0001/D-1"]

    run(scenario)


# ....................... #


def test_a_row_the_corpus_checker_would_refuse_is_never_imported(tmp_path):
    """An import must not record a grade `torve spec check` would not accept:
    the record would then hold a vocabulary the corpus does not have. The
    loader refuses it by field now, before the importer sees a row."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "PROBABLY", "A rule.", "`src/a.py`")])

    async def scenario(log):
        graph = await decisions.load(log, partition=PARTITION)

        with pytest.raises(PlanError, match=r"decisions\.0\.grade"):
            decisions.import_sources(graph, rfc_dir.parent.parent, rfc_dir)

    run(scenario)


# ....................... #


def test_an_agent_may_not_import(tmp_path):
    """The authority table refuses before any store sees the write (S-0044/D-2).
    An agent importing a corpus is not a thing that happens."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

    async def scenario(log):
        graph = await decisions.load(log, partition=PARTITION)
        pending = decisions.import_sources(graph, rfc_dir.parent.parent, rfc_dir)

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
    """S-0047/D-7: the corpus-sized slice, not the partition's whole tail. A log
    carrying attempts must not put them in front of a decision query."""

    rfc_dir = document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])

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

        assert [one.subject_id for one in read] == ["S-0001/D-1"]
        # And the partition's whole tail does carry them, so the narrow read
        # is doing the narrowing rather than the store having nothing to give.
        assert len(await log.since(partition=PARTITION)) == 7

        graph = await decisions.load(log, partition=PARTITION)

        assert [one.id for one in graph.current()] == ["S-0001/D-1"]

    run(scenario)


# ....................... #


def test_a_read_pages_past_its_page_size_rather_than_stopping_there(tmp_path):
    """A projection built on a prefix of the log is a wrong answer that
    looks like a right one, and nothing in it says so. The read pages to
    the end instead (A-99)."""

    from torve.application import eventlog

    rfc_dir = document(
        tmp_path,
        "0001",
        [(f"D-1.{n}", "LOCKED", "A rule.", "`src/a.py`") for n in range(1, 6)],
    )

    async def scenario(log):
        await sync(log, rfc_dir)

        # A page size smaller than the answer is the whole point: five
        # decisions, two at a time, and the fold sees all five.
        monkey = eventlog.PAGE
        eventlog.PAGE = 2

        try:
            records = await log.of_subject_type(SubjectType.DECISION, partition=PARTITION)

        finally:
            eventlog.PAGE = monkey

        assert len({record.subject_id for record in records}) == 5

    run(scenario)


# ....................... #


def test_the_record_answers_the_paths_question_the_corpus_answers(tmp_path):
    """The parity that says the import read the corpus correctly, over this
    repository's own corpus rather than a fixture.

    `for_paths` and `standing_decisions` call the same `globs_intersect`, so
    a difference here is a difference in what was imported, never in how the
    rule is applied — which is exactly what this is meant to catch.
    """

    rfc_dir = Path(__file__).resolve().parents[1] / ".torve" / "specs"
    allow = ["src/torve/application/**", "src/torve/domain/**"]

    async def scenario(log):
        await sync(log, rfc_dir)
        graph = await decisions.load(log, partition=PARTITION)

        from_record = sorted((one.id, one.grade) for one in graph.for_paths(allow))
        from_files = sorted((one.id, one.grade) for one in standing_decisions(rfc_dir, allow))

        assert from_record == from_files
        assert from_record, "the corpus governs these paths; an empty answer is a broken read"

    run(scenario)


# ....................... #


def test_the_check_verb_reports_without_writing(tmp_path):
    """`--check` and a real import are the same comparison with the write
    skipped (S-0047/D-5), which only means anything if the check writes nothing.

    Run against the mock, so the record is empty at process start and the
    whole corpus reads as pending — which is also what the verb should say
    when nobody has imported yet.
    """

    import json

    from typer.testing import CliRunner

    from torve.cli import app

    document(tmp_path, "0001", [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")])
    (tmp_path / ".torve" / "config.yaml").write_text(
        "schema_version: 1\nspecs:\n  path: .torve/specs\n", encoding="utf-8"
    )

    checked = CliRunner().invoke(
        app,
        ["decisions", "import", "p", "--check", "--root", str(tmp_path), "--format", "json"],
    )

    assert checked.exit_code == 0, checked.output

    report = json.loads(checked.stdout)

    assert report["written"] is False
    assert [one["kind"] for one in report["events"]] == [
        "source.imported",
        "decision.recorded",
    ]


# ----------------------- #
# S-0057 S-0057/D-8: the execution file is a carrier the import replays once


def test_a_landing_is_recorded_once_under_the_actors_its_kinds_name(tmp_path):
    entry = {
        "decision": "S-0001/D-1",
        "grade": "LOCKED",
        "claim": "held",
        "evidence": "src/a.py:1 - x",
        "action": "decided",
        "attempt": 1,
    }
    landing = {
        "task": "T-0001",
        "phase": 1,
        "commit": "abc",
        "at": "2026-09-09",
        "agent": "session/x",
        "entries": [entry],
    }
    rfc_dir = spec_corpus(
        tmp_path,
        **{
            "0001": spec_document(
                "0001",
                [("S-0001/D-1", "LOCKED", "A rule.", "`src/a.py`")],
                implementation="none",
                landings=[landing],
            )
        },
    )

    async def scenario(log: EventLog) -> None:
        found = decisions.landings(rfc_dir.parent.parent, rfc_dir)
        pending = decisions.landing_events(found, {})

        assert [(p.kind, p.subject_id, p.actor_kind, p.actor_id) for p in pending] == [
            (EventKind.DIVERGENCE_RECORDED, "T-0001", ActorKind.AGENT, "session/x"),
            (EventKind.LANDING_RECORDED, "T-0001", ActorKind.MANAGER, "execution"),
        ]
        assert pending[0].payload["claim"] == "held" and pending[1].payload["sha"] == "abc"

        await decisions.record_all(log, pending, partition=PARTITION, actor_id="tester")
        history = await log.history("T-0001", partition=PARTITION)

        assert [e.actor_kind for e in history] == [ActorKind.AGENT, ActorKind.MANAGER]
        assert decisions.landing_events(found, {"T-0001": history}) == []

    run(scenario)
