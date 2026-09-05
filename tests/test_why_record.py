"""`why` answered from the record (RFC 0050 phase 1).

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

from torve.application.projections import rows_from_events, why_report
from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType
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
    contract = Task(id=TASK_ID, decisions=[], rfc="rfcs/0050-x.md", **overrides)

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
    assert envelope["rfc"] == "rfcs/0050-x.md"
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
                "rfc": "rfcs/0050-from-the-file.md",
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )


# ....................... #


def test_a_record_without_the_task_falls_back_to_the_files(tmp_path):
    """D-50.2's one automatic rule, and only in that direction: an empty log
    means ask the files."""

    _write_contract(tmp_path)
    envelope = why_report(tmp_path, TASK_ID, recorded=[])

    assert envelope["found"] is True
    assert envelope["rfc"] == "rfcs/0050-from-the-file.md"


# ....................... #


def test_a_mint_with_no_contract_falls_back_too(tmp_path):
    """A mint written before RFC 0049 holds no contract, so the record has
    nothing to answer with — and a partial answer would be worse than the
    files' complete one."""

    _write_contract(tmp_path)
    bare = event(EventKind.TASK_MINTED, {"title": "old", "source_id": "0050"})
    envelope = why_report(tmp_path, TASK_ID, recorded=[bare])

    assert envelope["rfc"] == "rfcs/0050-from-the-file.md"


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
