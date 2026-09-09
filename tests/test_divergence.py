"""The divergence intake (S-0044/the-typed-divergence-intake, S-0044/D-10).

Each case here is one of the failure classes that produced a poison ceiling
in the window S-0044 cites, asserted as unreachable rather than rarer: an
unparseable log, an evidence line in the wrong grammar, a log the gate never
saw because nothing staged it, and a bookkeeping count an agent had to keep
by hand.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
import yaml
from conftest import HOSTILE, context_for
from forze.application.execution import DepsRegistry, ExecutionRuntime
from typer.testing import CliRunner

from torve.adapters.eventstore.document import mock_module
from torve.application.divergence import (
    IntakeRefused,
    ingest,
    journal_sync,
    open_log,
    project,
    record,
    seed,
    stage,
)
from torve.application.eventlog import event_log
from torve.cli.main import app
from torve.config import layout
from torve.config.manifest import Gate
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.gates.decisions_reported import check_decisions_reported, check_entry
from torve.gates.sabotage import LOCKED_D1, TASK_ID, base_task

GATE = Gate(name="test", run="@decisions-reported", state="blocking", origin="structural")
PARTITION = "morzecrew/torve"


def one_entry(worktree, **overrides):
    fields = {
        "decision": "S-0001/D-1",
        "grade": "LOCKED",
        "kind": "resolved",
        "klass": "spec-gap",
        "claim": "the engine writes the log now",
        "evidence": HOSTILE,
        "action": "decided",
        "attempt": 1,
    }

    return record(worktree.root, TASK_ID, **{**fields, **overrides})


def test_a_refused_entry_leaves_the_log_untouched(worktree):
    with pytest.raises(IntakeRefused) as refused:
        one_entry(worktree, evidence="a sentence, which is a claim and not evidence")

    assert not layout.log_file(worktree.root, TASK_ID).exists()
    assert "not locatable" in str(refused.value)
    # The teaching half the gate added for this exact class survives the move.
    assert "first defect: no citation at all" in str(refused.value)


def test_the_refusal_is_the_gates_own_judgement(worktree):
    """Parity is the shared function, not a copied string: the intake's
    problems are what the gate would print, because it is what the gate
    prints."""

    with pytest.raises(IntakeRefused) as refused:
        one_entry(worktree, action="halted")

    stated = {
        "decision": "S-0001/D-1",
        "grade": "LOCKED",
        "kind": "resolved",
        "class": "spec-gap",
        "at": "2026-09-04T00:00:00Z",
        "attempt": 1,
        "claim": "the engine writes the log now",
        "evidence": HOSTILE,
        "action": "halted",
    }

    assert refused.value.problems == check_entry(stated, worktree.root)


def test_a_scalar_that_breaks_naive_yaml_round_trips(worktree):
    path, _, _ = one_entry(worktree)
    loaded = yaml.safe_load(path.read_text())

    assert loaded["entries"][0]["evidence"] == HOSTILE


def test_the_written_log_is_one_the_gate_accepts(worktree):
    one_entry(worktree)
    worktree.commit("the log")
    result = check_decisions_reported(GATE, context_for(worktree))

    assert result.outcome == "pass", result.output


def test_the_log_is_staged_by_the_writer(worktree):
    path, _, _ = one_entry(worktree)
    staged = subprocess.run(
        ["git", "-C", str(worktree.root), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert str(path.relative_to(worktree.root)) in staged


def test_stage_reports_a_worktree_that_is_no_checkout(tmp_path):
    (tmp_path / "loose.yaml").write_text("entries: []\n")

    assert stage(tmp_path, tmp_path / "loose.yaml") is False


def test_the_drift_count_is_derived_not_declared(worktree):
    one_entry(worktree)
    _, document, _ = one_entry(
        worktree,
        klass="drift",
        decision="unlisted",
        grade="UNLISTED",
        proposal="the specification should name this",
    )

    assert document["drift_count"] == 1
    assert len(document["entries"]) == 2


def test_the_pin_is_derived_from_the_worktree(worktree):
    _, document, _ = one_entry(worktree)

    assert document["repo"] == "morzecrew/torve"
    assert len(document["base_sha"]) == 40


def test_a_pin_that_cannot_be_derived_is_refused_at_write_time(repo):
    repo.seed()  # no remote configured
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), None)

    with pytest.raises(IntakeRefused) as refused:
        one_entry(repo)

    assert "carries no repo" in str(refused.value)


def test_an_existing_log_keeps_its_own_pin(worktree):
    one_entry(worktree)
    first = open_log(worktree.root, TASK_ID)["base_sha"]
    worktree.write("src/app.py", "print('again')\n")
    worktree.commit("more work")
    one_entry(worktree, claim="a second entry")

    assert open_log(worktree.root, TASK_ID)["base_sha"] == first


def test_ingest_records_one_event_per_entry(worktree):
    one_entry(worktree)
    one_entry(worktree, claim="a second entry")

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            recorded = await ingest(
                log,
                worktree.root,
                TASK_ID,
                partition="morzecrew/torve",
                actor_id="agent-1",
            )

            assert [event.kind for event in recorded] == [EventKind.DIVERGENCE_RECORDED] * 2

            payload = recorded[0].typed_payload().model_dump()

            assert payload["decision_id"] == "S-0001/D-1"
            assert payload["entry_class"] == "spec-gap"
            assert payload["evidence"] == HOSTILE
            assert len(await log.history(TASK_ID)) == 2

    asyncio.run(scenario())


def test_the_verb_refuses_with_a_config_exit_and_writes_nothing(worktree):
    result = CliRunner().invoke(
        app,
        [
            "log",
            "divergence",
            TASK_ID,
            "--root",
            str(worktree.root),
            "--decision",
            "S-0001/D-1",
            "--grade",
            "LOCKED",
            "--kind",
            "resolved",
            "--class",
            "spec-gap",
            "--claim",
            "the entry the gate would refuse",
            "--evidence",
            "a sentence, which is a claim and not evidence",
            "--action",
            "decided",
        ],
    )

    assert result.exit_code == EXIT_CONFIG
    assert not layout.log_file(worktree.root, TASK_ID).exists()


def test_the_verb_reports_what_it_wrote(worktree):
    result = CliRunner().invoke(
        app,
        [
            "log",
            "divergence",
            TASK_ID,
            "--root",
            str(worktree.root),
            "--decision",
            "S-0001/D-1",
            "--grade",
            "LOCKED",
            "--kind",
            "resolved",
            "--class",
            "spec-gap",
            "--claim",
            "the engine writes the log now",
            "--evidence",
            HOSTILE,
            "--action",
            "decided",
            "--format",
            "json",
        ],
    )
    reported = json.loads(result.stdout)

    assert result.exit_code == EXIT_OK
    assert reported == {
        "accepted": True,
        # No broker in this worktree, so no channel: the file is the carrier
        # and the report says which one (S-0045/D-6).
        "channel": False,
        "log": f".torve/tasks/{TASK_ID}/log.yaml",
        "entries": 1,
        "staged": True,
    }


def test_the_pin_is_dropped_before_the_agent_runs_and_leaves_no_log(worktree):
    """A sandbox cannot resolve the commit its evidence cites: the worktree's
    `.git` points into a host tree it never sees. The engine drops the pin
    host-side, at dispatch, so the intake reads it back instead of the agent
    copying it — and an untouched run still leaves no log behind."""

    seed(worktree.root, TASK_ID, base_sha="0" * 40)

    assert not layout.log_file(worktree.root, TASK_ID).exists()

    _, document, _ = one_entry(worktree)

    assert document["base_sha"] == "0" * 40
    assert document["repo"] == "morzecrew/torve"


def test_the_dropped_pin_serves_a_worktree_git_cannot_read(tmp_path):
    """The sandbox case, without a sandbox: no git at all, and the intake
    still writes a log the gate's pin check accepts."""

    (tmp_path / ".torve" / "tasks" / TASK_ID).mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n")
    seeded = seed(tmp_path, TASK_ID, base_sha="a" * 40)
    seeded.write_text(json.dumps({"repo": "morzecrew/torve", "base_sha": "a" * 40}))
    _, document, staged = record(
        tmp_path,
        TASK_ID,
        decision="S-0001/D-1",
        grade="ASSUMED",
        kind="departed",
        klass="discovery",
        claim="the pin came from the engine",
        evidence="src/app.py:1 — a line the worktree carries",
        action="departed",
        attempt=1,
    )

    assert document["repo"] == "morzecrew/torve"
    assert document["base_sha"] == "a" * 40
    assert staged is False


# ....................... #

# The store as the carrier the gate actually reads (A-82): the engine
# records what the attempt wrote, then writes the file back from the record.


def test_a_second_ingest_records_only_what_is_new(worktree):
    one_entry(worktree)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            first = await ingest(
                log, worktree.root, TASK_ID, partition=PARTITION, actor_id="agent-1"
            )

            one_entry(worktree, claim="the second attempt found something else")

            # The log file is cumulative and a run ingests between attempts,
            # so without the offset attempt two records attempt one again.
            second = await ingest(
                log,
                worktree.root,
                TASK_ID,
                partition=PARTITION,
                actor_id="agent-1",
                after=len(first),
            )

            assert len(first) == 1
            assert len(second) == 1
            assert len(await log.history(TASK_ID)) == 2

    asyncio.run(scenario())


def test_the_projection_rewrites_the_log_from_the_record(worktree):
    one_entry(worktree)
    path = layout.log_file(worktree.root, TASK_ID)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            await ingest(log, worktree.root, TASK_ID, partition=PARTITION, actor_id="agent-1")

            # Whatever the sandbox left behind is not what the gate reads.
            path.write_text("entries: []\n", encoding="utf-8")

            assert await project(log, worktree.root, TASK_ID, partition=PARTITION) == 1

            document = yaml.safe_load(path.read_text())

            assert len(document["entries"]) == 1
            assert document["entries"][0]["evidence"] == HOSTILE
            assert document["entries"][0]["decision"] == "S-0001/D-1"
            # The pin survives, and the count stays derived.
            assert document["base_sha"]
            assert document["drift_count"] == 0
            # And it is staged, because a log outside the diff is a log the
            # gate cannot see.
            staged = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(worktree.root), "diff", "--cached", "--name-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert "log.yaml" in staged.stdout

    asyncio.run(scenario())


def test_a_task_with_nothing_recorded_gets_no_log(worktree):
    path = layout.log_file(worktree.root, TASK_ID)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())

            assert await project(log, worktree.root, TASK_ID, partition=PARTITION) == 0
            # A missing log is an empty log (A-13, S-0003/D-21): writing an empty
            # one turns "nothing to report" into a claim somebody made.
            assert not path.exists()

    asyncio.run(scenario())


def test_the_journal_sync_runs_from_the_runner_thread(worktree):
    one_entry(worktree)
    path = layout.log_file(worktree.root, TASK_ID)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            sync = journal_sync(
                log,
                asyncio.get_running_loop(),
                partition=PARTITION,
                task_id=TASK_ID,
                seat="worker-1",
            )

            # The runner is synchronous and lives on another thread; the
            # store's loop is this one.
            await asyncio.to_thread(sync, worktree.root)

            assert len(await log.history(TASK_ID)) == 1

            # Idempotent across attempts: the same worktree synced twice
            # records once, because the file is cumulative and the offset
            # is not.
            path.write_text(path.read_text(), encoding="utf-8")
            await asyncio.to_thread(sync, worktree.root)

            assert len(await log.history(TASK_ID)) == 1

    asyncio.run(scenario())


def test_the_gate_judges_what_the_record_holds(worktree):
    """The point of the projection (A-82): the sandbox cannot reach the
    store, so the store is made authoritative by the engine writing the file
    the battery reads — from the record, before it reads it."""

    path = layout.log_file(worktree.root, TASK_ID)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            await log.record(
                EventKind.DIVERGENCE_RECORDED,
                partition=PARTITION,
                subject_type=SubjectType.TASK,
                subject_id=TASK_ID,
                actor_kind=ActorKind.AGENT,
                actor_id="agent-1",
                payload={
                    "attempt": 1,
                    "decision_id": "S-0001/D-1",
                    "grade": "LOCKED",
                    "entry_kind": "resolved",
                    "entry_class": "spec-gap",
                    "claim": "the locked area was touched and this says why",
                    "evidence": HOSTILE,
                    "action": "decided",
                },
            )

            # Nothing in the worktree at all: the record is the only carrier.
            assert not path.exists()
            await project(log, worktree.root, TASK_ID, partition=PARTITION)

    asyncio.run(scenario())
    worktree.commit("the projected log")
    result = check_decisions_reported(GATE, context_for(worktree))

    assert result.outcome == "pass", result.output


def test_a_redispatch_does_not_record_a_landed_log_twice(worktree):
    """The worktree a re-dispatch cuts may already carry a log an earlier
    dispatch landed. The offset starts from what the record holds, not from
    zero, or every one of those entries is recorded a second time."""

    one_entry(worktree)

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())

            # The first dispatch recorded it and landed.
            await ingest(log, worktree.root, TASK_ID, partition=PARTITION, actor_id="agent-1")

            # A second dispatch, a fresh sync, the same file on disk.
            sync = journal_sync(
                log,
                asyncio.get_running_loop(),
                partition=PARTITION,
                task_id=TASK_ID,
                seat="worker-1",
            )
            await asyncio.to_thread(sync, worktree.root)

            assert len(await log.history(TASK_ID)) == 1

    asyncio.run(scenario())


# ....................... #

# `torve log owed`: the silence check, asked before the gate asks it.


def test_owed_names_the_decisions_the_log_has_not_cited(worktree):
    result = CliRunner().invoke(
        app,
        [
            "log",
            "owed",
            TASK_ID,
            "--root",
            str(worktree.root),
            "--touched",
            "src/app.py",
            "--format",
            "json",
        ],
    )
    reported = json.loads(result.stdout)

    assert result.exit_code == EXIT_OK
    assert reported["owed"], "a LOCKED decision governs src/app.py and nothing cites it"
    assert "S-0001/D-1" in reported["owed"][0]


def test_owed_goes_quiet_once_the_entry_exists(worktree):
    one_entry(worktree)

    result = CliRunner().invoke(
        app,
        [
            "log",
            "owed",
            TASK_ID,
            "--root",
            str(worktree.root),
            "--touched",
            "src/app.py",
            "--format",
            "json",
        ],
    )

    assert json.loads(result.stdout)["owed"] == []


def test_owed_answers_exactly_what_the_gate_would_convict(worktree):
    """The pre-check and the conviction share their implementation. A green
    answer here and a red gate later would be worse than no pre-check."""

    from torve.gates.decisions_reported import check_decisions_reported

    # The fixture already committed the change; the log is what is missing.
    gate = check_decisions_reported(GATE, context_for(worktree))
    result = CliRunner().invoke(
        app,
        [
            "log",
            "owed",
            TASK_ID,
            "--root",
            str(worktree.root),
            "--touched",
            "src/app.py",
            "--format",
            "json",
        ],
    )

    assert gate.outcome == "fail"
    assert json.loads(result.stdout)["owed"][0] in gate.output


def test_owed_refuses_a_task_with_no_contract(tmp_path):
    result = CliRunner().invoke(
        app, ["log", "owed", "T-9999", "--root", str(tmp_path), "--format", "json"]
    )

    assert result.exit_code == EXIT_CONFIG


# ----------------------- #
# S-0057 S-0057/D-7: the landing goes beside the rows it cites


def _landing_repo(tmp_path):
    """A root with a corpus of one document and a contract naming it."""

    from test_decisions import corpus, document

    from torve.domain.task import Task

    spec_dir = corpus(
        tmp_path, **{"0001": document("0001", [("S-0001/D-1", "LOCKED", "x", "`src/**`")])}
    )
    task = Task(id="T-0001", spec="S-0001", phase=1, decisions=[])
    (tmp_path / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    return spec_dir, task


ENTRY = {
    "decision": "S-0001/D-1",
    "grade": "LOCKED",
    "kind": "resolved",
    "at": "2026-09-09T10:00:00Z",
    "attempt": 1,
    "claim": "the rule held",
    "evidence": "src/a.py:1 - the line",
    "action": "decided",
}


def test_land_appends_the_worktree_log_to_the_documents_execution_file(tmp_path):
    from torve.application.decisions import land
    from torve.config.spec import load_document

    spec_dir, task = _landing_repo(tmp_path)
    log_path = layout.log_file(tmp_path, task.id)
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        yaml.safe_dump({"schema_version": 1, "task": task.id, "entries": [ENTRY]}), encoding="utf-8"
    )

    path = land(tmp_path, spec_dir, task, attempt=1, agent="session/x", at="2026-09-09T00:00:00Z")

    assert path == spec_dir / "S-0001" / "execution" / "T-0001-1-20260909T000000Z.yaml"
    assert path.read_text(encoding="utf-8").startswith(
        "# yaml-language-server: $schema=../../../schemas/landing.json\n"
    )
    landings = load_document(spec_dir / "S-0001").landings
    assert [(one.task, one.phase, one.attempt, one.commit, one.agent) for one in landings] == [
        ("T-0001", 1, 1, "", "session/x")
    ]
    assert landings[0].entries[0].claim == "the rule held"
    assert landings[0].base == ""  # the log carried no pin

    # the record's entries, when given, stand in for the file's
    land(tmp_path, spec_dir, task, attempt=2, commit="abc", entries=[{**ENTRY, "claim": "again"}])
    landings = load_document(spec_dir / "S-0001").landings
    assert [(one.attempt, one.commit, one.entries[0].claim) for one in landings] == [
        (1, "", "the rule held"),
        (2, "abc", "again"),
    ]


def test_land_refuses_no_document_and_an_unknown_one_and_replays_idempotently(tmp_path):
    from torve.application.decisions import land
    from torve.domain.task import Task

    spec_dir, task = _landing_repo(tmp_path)

    with pytest.raises(ValueError, match="names no document"):
        land(tmp_path, spec_dir, Task(id="T-0002", decisions=[]), attempt=1, entries=[])

    stranger = Task(id="T-0003", spec="S-0009", decisions=[])

    with pytest.raises(ValueError, match="does not hold"):
        land(tmp_path, spec_dir, stranger, attempt=1, entries=[])

    first = land(tmp_path, spec_dir, task, attempt=1, entries=[ENTRY])
    # an identical replay is the same landing: nothing written (S-0058/D-6)
    assert land(tmp_path, spec_dir, task, attempt=1, entries=[ENTRY]) == first
    assert len(list((spec_dir / "S-0001" / "execution").iterdir())) == 1

    # the same attempt with other entries — a restarted attempt — is a new file
    again = land(tmp_path, spec_dir, task, attempt=1, entries=[{**ENTRY, "claim": "restarted"}])

    assert again != first and len(list((spec_dir / "S-0001" / "execution").iterdir())) == 2


def test_the_log_land_verb_lands_the_contracts_task_with_the_commit_named(tmp_path):
    from torve.application.planner import write_contract
    from torve.config.spec import load_document

    spec_dir, task = _landing_repo(tmp_path)
    write_contract(tmp_path, task, "one")
    log_path = layout.log_file(tmp_path, task.id)
    log_path.write_text(
        yaml.safe_dump({"schema_version": 1, "task": task.id, "entries": [ENTRY]}), encoding="utf-8"
    )

    result = CliRunner().invoke(
        app,
        [
            "log",
            "land",
            task.id,
            "--commit",
            "abc123",
            "--agent",
            "session/x",
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    assert json.loads(result.output)["entries"] == 1
    landing = load_document(spec_dir / "S-0001").landings[0]
    assert (landing.commit, landing.agent, landing.entries[0].decision) == (
        "abc123",
        "session/x",
        "S-0001/D-1",
    )

    refused = CliRunner().invoke(
        app, ["log", "land", "T-0009", "--commit", "abc", "--root", str(tmp_path)]
    )

    assert refused.exit_code == EXIT_CONFIG and "no contract" in refused.output


def test_a_rendered_log_opens_with_its_schema_line_and_still_reads_back(tmp_path):
    from torve.application.divergence import render

    text = render({"schema_version": 1, "task": "T-0001", "entries": [ENTRY]})

    assert text.startswith("# yaml-language-server: $schema=../../schemas/log.json\n")
    assert yaml.safe_load(text)["entries"][0]["claim"] == "the rule held"


def test_land_names_the_base_the_log_pinned(tmp_path):
    from torve.application.decisions import land
    from torve.config.spec import load_document

    spec_dir, task = _landing_repo(tmp_path)
    log_path = layout.log_file(tmp_path, task.id)
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "task": task.id, "base_sha": "b" * 40, "entries": [ENTRY]}
        ),
        encoding="utf-8",
    )

    land(tmp_path, spec_dir, task, attempt=1, commit="c" * 40, at="2026-09-09T00:00:00Z")
    landing = load_document(spec_dir / "S-0001").landings[0]

    # S-0058/D-12: where the attempt started, and where it landed
    assert (landing.base, landing.commit) == ("b" * 40, "c" * 40)
