"""One record per attempt, two carriers (S-0044 A-85).

The engine used to build an attempt's facts twice: once as the telemetry
row every projection reads, once as the typed event the board folds.
Nothing made them agree. Now the record is one object and the row is
rendered from it, so the cases worth writing are the ones that would let
them drift apart again — a field the row grows that the payload refuses, a
field the payload defaults that the row deliberately omitted.
"""

from __future__ import annotations

import pytest
from conftest import context_for
from pydantic import ValidationError

from torve.application.telemetry import (
    build_attempt_row,
    build_record,
    record_payload,
    record_row,
)
from torve.domain.attempt import GateResult
from torve.domain.events import EventKind, gate_outcomes, validate_payload
from torve.domain.task import Task
from torve.gates.runner import RunReport
from torve.gates.sabotage import base_task

AGENT = {
    "adapter": "harness",
    "tier": "executor",
    "attempt": 2,
    "model": "claude-sonnet-5",
    "provider": "anthropic",
    "cost_usd": 0.42,
    "wall_time_s": 91.5,
}


@pytest.fixture
def gated(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("the work")

    report = RunReport(
        exit_code=0,
        results=[
            GateResult(name="scope", outcome="pass", state="blocking", exit_code=0),
            GateResult(name="acceptance", outcome="pass", state="blocking", exit_code=0),
        ],
    )

    return build_record(context_for(repo), report, "cafe1234", agent=AGENT)


# ....................... #


def test_the_gate_record_is_a_payload_its_kind_accepts(gated):
    # The failure this guards: the row grows a field, the payload model
    # forbids extras, and the event write dies at runtime on a real run.
    payload = record_payload(gated, 2)
    validated = validate_payload(EventKind.GATES_EVALUATED, payload)

    assert validated.model_dump()["config_hash"] == "cafe1234"
    assert payload["attempt"] == 2


def test_the_row_renders_back_from_the_payload(gated):
    rendered = record_row(record_payload(gated, 2), task_id=gated["task_id"], at=gated["at"])

    # Byte-for-byte the record the stream has always carried: no projection
    # changes, because nothing it reads changed.
    assert rendered == gated


def test_an_ending_without_a_gate_pass_round_trips_too():
    row = build_attempt_row(
        Task.model_validate(base_task(allow=["src/**"])),
        AGENT,
        verdict="agent_error",
        exit_code=2,
        timed_out=False,
        escalation="poison_ceiling",
    )
    payload = record_payload(row, 2)

    validate_payload(EventKind.ATTEMPT_FINISHED, payload)

    assert record_row(payload, task_id=row["task_id"], at=row["at"]) == row
    assert payload["gates_run"] is False
    assert payload["escalation"] == "poison_ceiling"


def test_absence_survives_the_round_trip():
    """A missing key reads as "written before this key existed" (S-0038/D-6). A
    payload that helpfully defaults one in destroys that reading."""

    row = build_attempt_row(
        Task.model_validate(base_task(allow=["src/**"])),
        AGENT,
        verdict="agent_error",
        exit_code=2,
        timed_out=False,
    )

    assert "escalation" not in row
    assert "transfer" not in row

    payload = record_payload(row, 1)

    assert "escalation" not in payload
    assert "transfer" not in payload
    assert record_row(payload, task_id=row["task_id"], at=row["at"]) == row


def test_the_envelope_is_not_repeated_in_the_payload(gated):
    payload = record_payload(gated, 2)

    # The event carries these itself: a record says what happened, and who
    # it happened to is the envelope's.
    assert "task_id" not in payload
    assert "at" not in payload
    assert "schema_version" not in payload


def test_gate_outcomes_are_derived_from_the_results(gated):
    payload = record_payload(gated, 2)
    outcomes = gate_outcomes(payload)

    # Derived where it is read, never stored beside what it summarises.
    assert outcomes
    assert set(outcomes) == {str(one["name"]) for one in payload["results"]}


def test_a_payload_with_an_unknown_field_is_refused(gated):
    payload = record_payload(gated, 2) | {"invented": True}

    with pytest.raises(ValidationError):
        validate_payload(EventKind.GATES_EVALUATED, payload)
