"""One record per attempt, two carriers (S-0044 A-85).

The engine used to build an attempt's facts twice: once as the telemetry
row every projection reads, once as the typed event the board folds.
Nothing made them agree. Now the record is one object and the row is
rendered from it, so the cases worth writing are the ones that would let
them drift apart again — a field the row grows that the payload refuses, a
field the payload defaults that the row deliberately omitted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import context_for
from pydantic import ValidationError

from torve.adapters.agent.harness import parse_metadata
from torve.application.telemetry import (
    build_attempt_row,
    build_record,
    record_payload,
    record_receipt,
    record_row,
)
from torve.config.manifest import Manifest
from torve.domain.attempt import GateResult
from torve.domain.events import EventKind, gate_outcomes, validate_payload
from torve.domain.task import Task
from torve.gates.context import GateContext
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


# ....................... #
# A row with no base sha (S-0065/D-4) and the receipt's own account of the
# ending (S-0065/D-6) — what makes a row joinable, and what makes it readable.


def _ctx(head_sha: str = "", merge_base: str | None = None) -> GateContext:
    return GateContext(
        root=Path("."),
        manifest=Manifest(gates=[]),
        head_sha=head_sha,
        base=None,
        merge_base=merge_base,
        task=Task.model_validate(base_task(allow=["src/**"])),
    )


def test_an_attempt_record_naming_no_sha_at_all_is_refused(gated):
    # The twin: a record that names a sha is written, one that names none
    # raises with the fields in the message. Such a row can never be joined
    # to a contract, a diff or a landing, and it would sit in the record
    # looking like data.
    with pytest.raises(ValueError, match="merge_base and head"):
        build_record(_ctx(), RunReport(exit_code=0), "cafe1234", agent=AGENT)

    assert gated["merge_base"]


def test_either_sha_is_enough_for_the_join():
    # A repository with no base to resolve still writes its own sha, and that
    # is the one the contract is read at — a row is refused for naming
    # nothing, never for naming one of the two.
    report = RunReport(exit_code=0)

    assert build_record(_ctx(head_sha="abc"), report, "cafe1234", agent=AGENT)["head"] == "abc"
    assert build_record(_ctx(merge_base="def"), report, "cafe1234", agent=AGENT)["merge_base"]
    # A bare gate pass over a human PR is not an attempt at all.
    assert build_record(_ctx(), report, "cafe1234")["agent"] is None


def test_the_receipt_rides_the_agent_block_and_drains_once():
    task = Task.model_validate(base_task(allow=["src/**"]))
    record_receipt(task.id, terminal_reason="error_max_turns", session_id="s-1")

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert row["agent"]["terminal_reason"] == "error_max_turns"
    assert row["agent"]["session_id"] == "s-1"

    # The booking belongs to the attempt that produced it: the next row of
    # the same task carries no ending it was not told (S-0004/D-6).
    again = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert "terminal_reason" not in again["agent"]
    assert "session_id" not in again["agent"]


def test_a_receipt_naming_neither_field_records_neither():
    task = Task.model_validate(base_task(allow=["src/**"]))
    record_receipt(task.id, terminal_reason=None, session_id=None)

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert "terminal_reason" not in row["agent"]
    assert "session_id" not in row["agent"]
    # And the block still round-trips: the agent block is loose on purpose.
    validate_payload(EventKind.ATTEMPT_FINISHED, record_payload(row, 1))


def test_the_three_receipt_shapes_the_traces_carry():
    # A completed run, a capped one, and a receipt that names no ending —
    # claude spells the first two in the result envelope's `subtype`, and
    # the images that carry neither field report nothing.
    def reason(line: str) -> str | None:
        return parse_metadata(line).terminal_reason

    assert reason('{"type":"result","subtype":"success","session_id":"abc"}') == "success"
    assert reason('{"type":"result","subtype":"error_max_turns"}') == "error_max_turns"
    assert reason('{"total_cost_usd":0.4,"usage":{"output_tokens":9}}') is None

    # The opening system line spells `init` in the same key and names no
    # ending: a stream that stops there is unreported, never "init".
    assert reason('{"type":"system","subtype":"init","tools":["Bash"]}') is None
    assert parse_metadata(
        '{"type":"result","subtype":"success","session_id":"abc"}'
    ).session_id == ("abc")
