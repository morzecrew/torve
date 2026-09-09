"""`why` answered from the record (S-0050 phase 1).

The design is A-85 read backwards: the telemetry row is a *rendering* of the
event payload, so rather than re-implement five joins against a second
vocabulary, the record is rendered into the rows those joins already read.
Parity then holds because both sides are the same object through the same
function, and a difference is a defect in the rendering rather than a
disagreement between two readings.

What is worth testing is therefore the rendering and the selection rule —
not the joins, which have their own tests and did not change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from torve.application.manager import Board, project
from torve.application.projections import (
    context_report,
    rows_from_events,
    runs_from_board,
    status_report,
    tasks_from_events,
    why_report,
)
from torve.application.runstate import RunState
from torve.base import naming
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
from torve.domain.states import TaskState
from torve.domain.task import Task

TASK_ID = "T-0900"
PARTITION = "acme/one"


# ....................... #


def event(kind, payload, *, at="2026-09-05T10:00:00Z", subject=TASK_ID) -> EventRecord:
    """One record, built rather than written: these cases are about the
    rendering, and a real store stamps its own clock."""

    stamped = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    return EventRecord(
        id=uuid.uuid4().hex,
        rev=1,
        created_at=stamped,
        last_update_at=stamped,
        kind=kind,
        partition=PARTITION,
        subject_type=SubjectType.TASK,
        subject_id=subject,
        actor_kind=ActorKind.WORKER,
        actor_id="w-1",
        payload=payload,
    )


# ....................... #


def minted(**overrides) -> EventRecord:
    contract = Task(**{"id": TASK_ID, "decisions": [], "spec": "S-0050", **overrides})

    return event(
        EventKind.TASK_MINTED,
        {
            "title": "a task",
            "source_id": "0050",
            "phase": 0,
            "depends_on": [],
            "contract": contract.model_dump(mode="json"),
        },
        at="2026-09-05T09:00:00Z",
    )


# ....................... #


def test_a_gate_pass_renders_the_row_its_payload_already_carries():
    """A-85: the row is the payload plus the envelope, so the rendering is
    the same function the stream itself is written with."""

    payload = {
        "attempt": 2,
        "verdict": "green",
        "exit_code": 0,
        "results": [{"name": "acceptance", "outcome": "pass", "state": "blocking"}],
        "agent": {"tier": "executor", "adapter": "harness", "attempt": 2, "cost_usd": 1.5},
    }
    rows = rows_from_events([event(EventKind.GATES_EVALUATED, payload)])

    assert len(rows) == 1
    assert rows[0]["task_id"] == TASK_ID
    assert rows[0]["verdict"] == "green"
    assert rows[0]["results"] == payload["results"]
    assert rows[0]["agent"]["cost_usd"] == 1.5
    # `attempt` rides the payload, never the row (record_row strips it).
    assert "attempt" not in rows[0]


# ....................... #


def test_an_escalation_renders_the_engine_row_the_stream_holds():
    """`state.escalate` writes an engine event named `escalation` carrying
    the task, the reason and the detail. The record's own escalation must
    render to the same row or the events leg reads empty."""

    rows = rows_from_events(
        [
            event(
                EventKind.ESCALATION_RAISED,
                {"reason": "poison_ceiling", "detail": "3 attempts, ceiling 3"},
            )
        ]
    )

    assert rows == [
        {
            "kind": "engine",
            "at": "2026-09-05T10:00:00Z",
            "event": "escalation",
            "task": TASK_ID,
            "reason": "poison_ceiling",
            "detail": "3 attempts, ceiling 3",
        }
    ]


# ....................... #


def test_a_review_renders_its_counts_back_into_findings():
    """The envelope wants counts; the file row carries findings and the join
    counts them. The record carries the counts, so the rendering expands
    them back to the depth the join reads at."""

    rows = rows_from_events(
        [event(EventKind.REVIEW_RECORDED, {"review_id": "T-0901", "findings": 3, "blockers": 1})]
    )

    assert rows[0]["kind"] == "review"
    assert rows[0]["target"] == TASK_ID
    assert rows[0]["task_id"] == "T-0901"
    assert len(rows[0]["findings"]) == 3
    assert sum(1 for f in rows[0]["findings"] if f["severity"] == "blocker") == 1


# ....................... #


def test_a_kind_the_envelope_says_nothing_about_renders_no_row():
    """The same silence the stream keeps for a fact nobody wrote down — a
    burn event is not a why entry, and inventing one would be judgement."""

    assert rows_from_events([event(EventKind.SEAT_CONSUMED, {"cost_usd": 0.1})]) == []


# ....................... #


def test_the_record_answers_the_whole_envelope(tmp_path):
    rows = [
        minted(),
        event(
            EventKind.GATES_EVALUATED,
            {
                "attempt": 1,
                "verdict": "gates_red",
                "exit_code": 1,
                "results": [],
                "agent": {"tier": "executor", "adapter": "harness", "attempt": 1, "cost_usd": 1.0},
            },
            at="2026-09-05T10:00:00Z",
        ),
        event(
            EventKind.ESCALATION_RAISED,
            {"reason": "poison_ceiling", "detail": "3 attempts"},
            at="2026-09-05T11:00:00Z",
        ),
    ]
    envelope = why_report(tmp_path, TASK_ID, recorded=rows)

    assert envelope["found"] is True
    assert envelope["spec"] == "S-0050"
    assert [one["attempt"] for one in envelope["attempts"]] == [1]
    assert [one["event"] for one in envelope["events"]] == ["escalation"]


# ....................... #


def _write_contract(root: Path) -> None:
    task_dir = root / ".torve" / "tasks" / TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "contract.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": TASK_ID,
                "role": "implement",
                "spec": "S-0051",
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )


# ....................... #


def test_a_record_without_the_task_falls_back_to_the_files(tmp_path):
    """S-0050/D-2's one automatic rule, and only in that direction: an empty log
    means ask the files."""

    _write_contract(tmp_path)
    envelope = why_report(tmp_path, TASK_ID, recorded=[])

    assert envelope["found"] is True
    assert envelope["spec"] == "S-0051"


# ....................... #


def test_a_mint_with_no_contract_falls_back_too(tmp_path):
    """A mint written before S-0049 holds no contract, so the record has
    nothing to answer with — and a partial answer would be worse than the
    files' complete one."""

    _write_contract(tmp_path)
    bare = event(EventKind.TASK_MINTED, {"title": "old", "source_id": "0050"})
    envelope = why_report(tmp_path, TASK_ID, recorded=[bare])

    assert envelope["spec"] == "S-0051"


# ....................... #


def test_an_unknown_task_is_found_false_on_both_paths(tmp_path):
    """A typo must not fake a taskless history, whichever source answered."""

    assert why_report(tmp_path, "T-9999")["found"] is False
    assert why_report(tmp_path, "T-9999", recorded=[])["found"] is False


# ....................... #


def test_the_two_readers_agree_on_the_same_attempt(tmp_path):
    """Parity where both can answer, over one attempt written to both
    carriers from one payload — which is the property A-85 bought and this
    projection spends."""

    from torve.application.telemetry import append_record, record_row

    _write_contract(tmp_path)
    payload = {
        "attempt": 1,
        "verdict": "green",
        "exit_code": 0,
        "results": [],
        "agent": {
            "tier": "executor",
            "adapter": "harness",
            "attempt": 1,
            "cost_usd": 2.0,
            "wall_time_s": 30.0,
        },
    }
    append_record(
        tmp_path / ".torve" / "telemetry.jsonl",
        record_row(payload, task_id=TASK_ID, at="2026-09-05T10:00:00Z"),
    )

    from_files = why_report(tmp_path, TASK_ID)
    from_record = why_report(
        tmp_path,
        TASK_ID,
        recorded=[minted(), event(EventKind.GATES_EVALUATED, payload)],
    )

    assert from_files["attempts"] == from_record["attempts"]
    assert from_files["totals"] == from_record["totals"]
    # The envelopes carry the same keys — a key added to one reader and not
    # the other passes every value assertion above.
    assert set(from_files) == set(from_record)


# ....................... #
# `status` over the board (S-0050 phase 2). The two vocabularies are the
# same TaskState reached by different paths, so what is worth testing is
# the depth each carrier reaches and the selection between them.
# ....................... #


def board_of(*events) -> Board:
    return project(list(events))


# ....................... #


def test_a_board_row_renders_the_run_record_status_reports():
    """State, attempts, holder's landing and escalation all come off the
    board; the host facts a run state also carries come back empty."""

    runs = runs_from_board(
        board_of(
            minted(),
            event(EventKind.ATTEMPT_STARTED, {"attempt": 1}, at="2026-09-05T10:00:00Z"),
            event(
                EventKind.ESCALATION_RAISED,
                {"reason": "poison_ceiling", "detail": "3 attempts"},
                at="2026-09-05T10:05:00Z",
            ),
        )
    )

    assert len(runs) == 1
    assert runs[0]["task_id"] == TASK_ID
    assert runs[0]["state"] == "escalated"
    assert runs[0]["attempts"] == 1
    assert runs[0]["heartbeat"].startswith("2026-09-05T10:05:00")
    # The record carries the reason and not the detail: the board folds the
    # reason and the detail stays in the log the fold read.
    assert runs[0]["escalation"] == {"reason": "poison_ceiling", "detail": ""}
    assert runs[0]["worktree"] is None and runs[0]["sandbox_id"] is None


# ....................... #


def test_a_landing_renders_its_sha_and_the_ready_state():
    runs = runs_from_board(
        board_of(
            minted(),
            event(EventKind.ATTEMPT_STARTED, {"attempt": 1}),
            event(EventKind.LANDING_RECORDED, {"sha": "abc123", "attempt": 1}),
        )
    )

    assert runs[0]["state"] == "ready"
    assert runs[0]["landed_sha"] == "abc123"


# ....................... #


def test_a_minted_task_that_never_ran_is_not_a_run():
    """The board carries every task a partition has ever minted; `status`
    reports runs. A queued contract with no attempt behind it belongs to
    the board's own reading, not to this one."""

    assert runs_from_board(board_of(minted())) == []


# ....................... #


def test_an_imported_landing_is_history_and_not_a_run():
    """A partition's first pass mints every contract the tree carries and
    records the landings the trailer already proves (S-0049/D-1) — on this
    repository, 184 of them. They are `ready` with no attempt behind them,
    and reporting them as runs would bury the handful that are."""

    assert (
        runs_from_board(
            board_of(minted(), event(EventKind.LANDING_RECORDED, {"sha": "abc123", "attempt": 0}))
        )
        == []
    )


# ....................... #


def test_both_carriers_answer_with_the_same_keys(tmp_path):
    """A run the record holds and a run the file holds must be the same
    shape, or a reader written against one breaks on the other."""

    state = RunState(
        task_id=TASK_ID,
        path=tmp_path / naming.WORKTREE_DIR / f"{TASK_ID}.state.json",
        state=TaskState.RUNNING,
        attempts=1,
    )
    state.save()

    from_files = status_report(tmp_path)
    from_record = status_report(
        tmp_path,
        board=board_of(minted(), event(EventKind.ATTEMPT_STARTED, {"attempt": 1})),
    )

    assert set(from_files) == set(from_record)
    assert set(from_files["runs"][0]) == set(from_record["runs"][0])
    assert from_record["runs"][0]["state"] == from_files["runs"][0]["state"] == "running"
    assert from_record["runs"][0]["attempts"] == from_files["runs"][0]["attempts"] == 1


# ....................... #


def test_a_board_holding_no_run_falls_back_to_the_files(tmp_path):
    """A-86's direction: a v1 run left a file and no log, so an empty
    record means ask the files. The reverse is never done — a populated
    record is not second-guessed by a stale file."""

    RunState(
        task_id=TASK_ID,
        path=tmp_path / naming.WORKTREE_DIR / f"{TASK_ID}.state.json",
        state=TaskState.READY,
    ).save()

    assert [run["task_id"] for run in status_report(tmp_path, board=Board())["runs"]] == [TASK_ID]
    # And with no board at all, which is what an unnamed partition passes.
    assert status_report(tmp_path, board=None) == status_report(tmp_path)


# ....................... #
# `context`'s task block over the record (S-0050 phase 3). The record
# holds every contract since A-96, so this is a fold; what is worth
# testing is the one place the vocabularies differ and the fallback.
# ....................... #


def test_a_task_entry_is_folded_from_its_contract_and_its_facts():
    entries = tasks_from_events(
        [
            minted(phase=3, spec="S-0050"),
            event(EventKind.ATTEMPT_STARTED, {"attempt": 1}),
            event(
                EventKind.ESCALATION_RAISED,
                {"reason": "underspecified", "detail": "no acceptance"},
                at="2026-09-05T10:05:00Z",
            ),
        ]
    )

    assert entries == [
        {
            "id": TASK_ID,
            "spec": "S-0050",
            "phase": 3,
            "role": "implement",
            "state": "escalated",
            "attempts": 1,
            "escalation": "underspecified",
            "escalated_at": "2026-09-05T10:05:00Z",
            "parent": None,
            "targets": [],
        }
    ]


# ....................... #


def test_the_three_starting_words_the_record_has_one_of():
    """The vocabularies differ only at the start: the board says `queued`
    where this projection says `unstarted` for a task a worker will take
    and `consumed` for one a run mints and concludes with."""

    implement = tasks_from_events([minted()])
    review = tasks_from_events([minted(role="review", targets=["T-0001"])])

    assert implement[0]["state"] == "unstarted"
    assert review[0]["state"] == "consumed"


# ....................... #


def test_a_row_whose_contract_the_record_does_not_hold_is_skipped():
    """A mint written before A-91 carries no contract, so there is no
    document, phase or role to report — and every downstream block would
    have to special-case an entry without them (S-0049/D-5)."""

    pre_a91 = event(EventKind.TASK_MINTED, {"title": "a task", "source_id": "0050", "phase": 0})

    assert tasks_from_events([pre_a91]) == []


# ....................... #


def test_context_falls_back_to_the_files_when_the_record_holds_no_contract(tmp_path):
    (tmp_path / ".torve" / "tasks" / TASK_ID).mkdir(parents=True)
    (tmp_path / ".torve" / "tasks" / TASK_ID / "contract.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": TASK_ID,
                "spec": "S-0050",
                "intent": "work",
                "scope": {"allow": ["src/**"]},
                "acceptance": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    from_files = context_report(tmp_path, tmp_path / "rfcs")
    from_empty = context_report(tmp_path, tmp_path / "rfcs", recorded=[])

    assert [entry["id"] for entry in from_files["tasks"]] == [TASK_ID]
    assert from_empty["tasks"] == from_files["tasks"]


# ....................... #
# The attempt-counting blocks over the record (S-0050 phase 3, A-102).
# ....................... #


def test_the_report_says_which_carrier_answered(tmp_path):
    """Every count below the header is correct about its carrier and says
    nothing about the other one. A reader cannot tell a quiet record from a
    quiet repository by the numbers, so the report says which it read."""

    payload = {
        "attempt": 1,
        "verdict": "green",
        "exit_code": 0,
        "results": [{"name": "acceptance", "outcome": "pass", "state": "blocking"}],
        "agent": {"tier": "executor", "adapter": "harness", "attempt": 1, "cost_usd": 2.0},
    }
    recorded = [minted(), event(EventKind.GATES_EVALUATED, payload)]

    from_record = context_report(tmp_path, tmp_path / "rfcs", recorded=recorded)
    from_files = context_report(tmp_path, tmp_path / "rfcs")

    assert from_record["sources"] == {
        "tasks": "record",
        "attempts": "record",
        "attempt_rows": 1,
        "divergences": "files",
        "corpus": "files",
        "findings": "files",
        "feedback": "files",
    }
    assert from_files["sources"]["tasks"] == "files"
    assert from_files["sources"]["attempts"] == "files"

    # And the blocks that count attempts counted the record's one.
    assert from_record["gates"]["acceptance"]["runs"] == 1
    assert [row["cost_usd"] for row in from_record["costs"]] == [2.0]


# ....................... #


def test_the_findings_ledger_stays_on_the_stream(tmp_path):
    """The record carries a claim only for a blocker, and this ledger
    reports every finding's claim. Rendering it from the record would drop
    the text an operator triages by."""

    recorded = [minted(), event(EventKind.REVIEW_RECORDED, {"review_id": "T-0901", "findings": 2})]
    report = context_report(tmp_path, tmp_path / "rfcs", recorded=recorded)

    assert report["sources"]["findings"] == "files"
    assert report["findings"] == []


# ....................... #


def test_divergences_render_as_the_log_file_the_engine_writes(tmp_path):
    """The worktree's log.yaml is a projection of these events, so the
    reader that folds the events and the one that parses the file it wrote
    must not be able to disagree: both use the engine's own rendering."""

    recorded = [
        minted(),
        event(
            EventKind.DIVERGENCE_RECORDED,
            {
                "attempt": 1,
                "decision_id": "S-0050/D-1",
                "grade": "LOCKED",
                "entry_kind": "resolved",
                "entry_class": "drift",
                "claim": "the file reader stays",
                "evidence": "tests/test_why_record.py",
                "action": "followed",
                "proposal": "record the rule",
            },
        ),
    ]
    report = context_report(tmp_path, tmp_path / "rfcs", recorded=recorded)

    assert report["sources"]["divergences"] == "record"
    assert [p["decision"] for p in report["proposals"]] == ["S-0050/D-1"]
    assert report["proposals"][0]["proposal"] == "record the rule"
