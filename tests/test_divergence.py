"""The divergence intake (RFC 0044 §5.6, D-44.10).

Each case here is one of the failure classes that produced a poison ceiling
in the window RFC 0044 cites, asserted as unreachable rather than rarer: an
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
from conftest import context_for
from forze.application.execution import DepsRegistry, ExecutionRuntime
from typer.testing import CliRunner

from torve.adapters.eventstore.document import mock_module
from torve.application.divergence import IntakeRefused, ingest, open_log, record, seed, stage
from torve.application.eventlog import event_log
from torve.cli.main import app
from torve.config import layout
from torve.config.manifest import Gate
from torve.domain.events import EventKind
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.gates.decisions_reported import check_decisions_reported, check_entry
from torve.gates.sabotage import LOCKED_D1, TASK_ID, base_task

GATE = Gate(name="test", run="@decisions-reported", state="blocking", origin="structural")

# The scalar that ended three attempts of T-0245: backticked `key: value`
# text, which a hand-written log carries unquoted and YAML then reads as a
# nested mapping — or refuses outright.
HOSTILE = "src/app.py:1 — the call is `timeout: 600` here, and the overlay names it too"


@pytest.fixture()
def worktree(repo):
    repo.seed()
    repo.git("remote", "add", "origin", "git@github.com:morzecrew/torve.git")
    repo.task(base_task(allow=["src/**"], decisions=LOCKED_D1), None)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("the work")

    return repo


def one_entry(worktree, **overrides):
    fields = {
        "decision": "D-1",
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
        "decision": "D-1",
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

            assert payload["decision_id"] == "D-1"
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
            "D-1",
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
            "D-1",
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
        decision="D-1",
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
