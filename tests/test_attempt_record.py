"""One record per attempt, two carriers (S-0044 A-85).

The engine used to build an attempt's facts twice: once as the telemetry
row every projection reads, once as the typed event the board folds.
Nothing made them agree. Now the record is one object and the row is
rendered from it, so the cases worth writing are the ones that would let
them drift apart again — a field the row grows that the payload refuses, a
field the payload defaults that the row deliberately omitted.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from conftest import context_for
from pydantic import ValidationError

from torve.adapters.agent.harness import (
    HarnessResult,
    compare_inventory,
    parse_burn,
    parse_inventory,
    parse_metadata,
)
from torve.application.telemetry import (
    agent_burn,
    build_attempt_row,
    build_record,
    classify_tool_calls,
    record_burn_profile,
    record_context,
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


def _ctx(head_sha: str = "", merge_base: str | None = None, base: str | None = None) -> GateContext:
    return GateContext(
        root=Path("."),
        manifest=Manifest(gates=[]),
        head_sha=head_sha,
        base=base,
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


# ....................... #
# The inventory comparison (S-0066/D-4): what the harness says it loaded,
# against what the seat declared, as a fact on the row — the battery judges
# the tree, this judges the claim the regime digest makes about the inputs.

# The three lines that matter of a stream-json attempt: the opening inventory,
# one turn, the result envelope. The seat below declared `mcp/linear` and
# `skill/working-rules`; this session loaded neither, loaded an MCP server
# nobody declared, and named the built-ins every claude session names.
INVENTORY_STREAM = "\n".join(
    [
        (
            '{"type":"system","subtype":"init","tools":["Bash"],'
            '"mcp_servers":[{"name":"stowaway","status":"connected"}],'
            '"skills":["deep-research","dataviz"],"plugins":[],"agents":["general-purpose"]}'
        ),
        '{"type":"assistant","message":{"content":[{"type":"text"}],"usage":{"output_tokens":40}}}',
        '{"type":"result","subtype":"success","total_cost_usd":0.1}',
    ]
)


def test_an_inventory_mismatch_rides_the_row_and_reddens_nothing(tmp_path):
    trace = tmp_path / "T-9010.a1.trace.log"
    trace.write_text(INVENTORY_STREAM, encoding="utf-8")

    loaded = parse_inventory(trace)
    profile = parse_burn(trace)

    assert loaded is not None
    assert profile is not None

    burn = replace(
        profile,
        inventory=compare_inventory(frozenset({"mcp/linear", "skill/working-rules"}), loaded),
    )
    agent = {**AGENT, **agent_burn(HarnessResult(exit_code=0, output="", burn=burn))}
    ctx = _ctx(head_sha="abc", merge_base="def", base="main")
    row = build_record(ctx, RunReport(exit_code=0), "cafe1234", agent=agent)

    # Both directions of the difference, and only the direction that means
    # something in each: the two declared items the session never named, and
    # the one undeclared name it reported. The built-in skills and agents are
    # not in the undeclared list — every claude session names them, so a list
    # that carried them would say the same thing on every attempt.
    assert row["agent"]["burn"]["inventory"] == {
        "unloaded": ["mcp/linear", "skill/working-rules"],
        "undeclared": ["mcp/stowaway"],
    }
    # A mismatch is a fact, never a conviction: the gates passed and the row's
    # verdict is the gate report's, untouched by what the inventory said.
    assert row["verdict"] == "green"
    assert row["exit_code"] == 0
    # And the loose agent block still round-trips through the payload the
    # event kind accepts, which is what a new key inside it has to survive.
    validate_payload(EventKind.GATES_EVALUATED, record_payload(row, 2))


# ....................... #
# The per-request context curve (S-0075/D-1): the adapter books the block
# where the trace lives; the row drains it once, beside the receipt.


def test_the_context_curve_rides_the_agent_block_and_drains_once():
    task = Task.model_validate(base_task(allow=["src/**"]))
    record_context(
        task.id,
        {
            "shape": "input-only",
            "first": 7,
            "median": 5.0,
            "max": 7,
            "sum": 10,
            "requests": 2,
            "receipt_total": 910,
            "matches_receipt": False,
        },
    )

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert row["agent"]["context"] == {
        "shape": "input-only",
        "first": 7,
        "median": 5.0,
        "max": 7,
        "sum": 10,
        "requests": 2,
        "receipt_total": 910,
        "matches_receipt": False,
    }

    # The booking belongs to the attempt that produced it: the next row of
    # the same task carries no curve it was not told (S-0004/D-6).
    again = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert "context" not in again["agent"]


def test_the_context_curve_rides_the_gate_pass_row_too():
    ctx = _ctx(head_sha="abc", merge_base="def", base="main")
    record_context(
        ctx.task.id,
        {
            "shape": "with-cache",
            "first": 60,
            "median": 60,
            "max": 300,
            "sum": 420,
            "requests": 3,
            "receipt_total": 420,
            "matches_receipt": True,
        },
    )

    row = build_record(ctx, RunReport(exit_code=0), "cafe1234", agent=AGENT)

    assert row["agent"]["context"]["shape"] == "with-cache"
    assert row["agent"]["context"]["matches_receipt"] is True
    # A booked curve is a fact, never a conviction: the gates passed and the
    # row's verdict is the gate report's, untouched by what the curve said.
    assert row["verdict"] == "green"
    assert row["exit_code"] == 0
    # And the loose agent block still round-trips through the payload.
    validate_payload(EventKind.GATES_EVALUATED, record_payload(row, 1))


def test_an_unreconstructable_attempt_says_so_on_the_row():
    # A stream with no per-request usage cannot rebuild the curve; the
    # adapter books the none shape so the row names the gap rather than
    # omitting it — D-1's own consequence over the general absent-absent rule.
    task = Task.model_validate(base_task(allow=["src/**"]))
    record_context(task.id, {"shape": "none"})

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert row["agent"]["context"] == {"shape": "none"}
    # A row whose adapter never booked — no harness at all — carries no key
    # and reads as before D-1, exactly like a missing receipt key.
    never_booked = build_attempt_row(
        Task.model_validate(base_task(allow=["src/**"])),
        AGENT,
        verdict="agent_error",
        exit_code=1,
        timed_out=False,
    )

    assert "context" not in never_booked["agent"]
    validate_payload(EventKind.ATTEMPT_FINISHED, record_payload(row, 1))


# ....................... #
# The burn classifier (S-0075/D-2): what an attempt's turns were for, tested
# against a fixture whose classes are asserted one by one — a class silently
# swallowing another fails.

# One call stream in the shape a trace scanner would emit: the opening init
# line's inventories (D-4's counts), then ten calls across eight messages —
# every class at least once — a compaction event, a rerun, and measured bytes
# and latencies on every call. The scope is what separates the in-scope read
# from the orientation reads. The pack index's path is spelled in two parts
# so the layout sweep's uncommitted-path check stays quiet: `.torve/context/`
# names a directory this repository does not commit, and this file carries no
# verdict in that sweep's ledger.
PROFILE_STREAM = [
    {
        "name": "init",
        "tools": ["Bash", "Read"],
        "skills": ["working-rules", "ponytail", "caveman"],
        "mcp_servers": ["linear"],
        "plugins": [],
        "agents": ["general-purpose"],
    },
    {
        "name": "Glob",
        "input": {"pattern": "**/*.py"},
        "message": 0,
        "bytes": 1200,
        "latency_ms": 40,
    },
    {
        "name": "Bash",
        "input": {"command": "git status --short"},
        "message": 0,
        "bytes": 300,
        "latency_ms": 90,
    },
    {
        "name": "WebSearch",
        "input": {"query": "pydantic v2"},
        "message": 0,
        "bytes": 5000,
        "latency_ms": 340,
    },
    {
        "name": "Read",
        "input": {"file_path": ".torve" + "/context/index.md"},
        "message": 1,
        "bytes": 900,
        "latency_ms": 30,
    },
    {
        "name": "Read",
        "input": {"file_path": "src/torve/application/telemetry.py"},
        "message": 2,
        "bytes": 5200,
        "latency_ms": 60,
    },
    {
        "name": "Edit",
        "input": {"file_path": "src/torve/application/telemetry.py"},
        "message": 3,
        "bytes": 700,
        "latency_ms": 70,
    },
    {
        "name": "Bash",
        "input": {"command": "uv run pytest -q"},
        "message": 4,
        "bytes": 2000,
        "latency_ms": 8100,
    },
    {
        "name": "Bash",
        "input": {"command": "uv run pytest"},
        "message": 5,
        "bytes": 2000,
        "latency_ms": 3100,
    },
    {
        "name": "Bash",
        "input": {"command": "uv run ruff check ."},
        "message": 6,
        "bytes": 400,
        "latency_ms": 700,
    },
    {
        "name": "Bash",
        "input": {"command": "torve log owed T-0402 --touched src"},
        "message": 7,
        "bytes": 150,
        "latency_ms": 100,
    },
    {"name": "SessionStart:compact", "message": 8},
]


def test_the_classifier_names_every_class_on_the_fixture():
    profile = classify_tool_calls(PROFILE_STREAM, scope=["src/**"])

    # Every class the vocabulary names, at least once, none swallowing
    # another: ten calls, the two test runs count as test_run (the second
    # differing from the first only in `-q`), the reads split by what they
    # pointed at, and the unclaimed WebSearch landing on `other`.
    assert profile["classes"] == {
        "bookkeeping": 1,
        "edit": 1,
        "in_scope_read": 1,
        "lint_run": 1,
        "orientation": 2,
        "other": 1,
        "pack_read": 1,
        "test_run": 2,
    }
    assert profile["calls"] == 10
    assert profile["messages"] == 8
    assert profile["calls_per_message"] == 1.25
    # Five calls before the first edit (the orientation and read calls the
    # fixture puts ahead of it), one rerun, one compaction event.
    assert profile["calls_before_first_edit"] == 5
    assert profile["reruns"] == 1
    assert profile["compaction_events"] == 1


def test_the_classifier_measures_bytes_and_latency_by_class():
    profile = classify_tool_calls(PROFILE_STREAM, scope=["src/**"])

    assert profile["bytes_by_class"] == {
        "bookkeeping": 150,
        "edit": 700,
        "in_scope_read": 5200,
        "lint_run": 400,
        "orientation": 1500,
        "other": 5000,
        "pack_read": 900,
        "test_run": 4000,
    }
    assert profile["latency_medians"] == {
        "bookkeeping": 100,
        "edit": 70,
        "in_scope_read": 60,
        "lint_run": 700,
        "orientation": 65.0,
        "other": 340,
        "pack_read": 30,
        "test_run": 5600.0,
    }
    # The init line's inventories, recorded how the line reported them —
    # present lists counted, the empty plugins list absent rather than zero.
    assert profile["init_tools"] == 2
    assert profile["init_skills"] == 3
    assert profile["init_mcp_servers"] == 1
    assert profile["init_agents"] == 1
    assert "init_plugins" not in profile


def test_a_call_stream_with_no_calls_produces_no_profile():
    # No stream, no block (S-0039/D-4): an init line with no calls behind it is
    # not a burn profile, and a line that names no call contributes nothing.
    assert classify_tool_calls([]) == {}
    assert classify_tool_calls([{"name": "init", "tools": ["Bash"]}]) == {}
    assert classify_tool_calls([{"name": ""}]) == {}


def test_an_editless_attempt_reports_no_before_first_edit():
    profile = classify_tool_calls(
        [{"name": "Bash", "input": {"command": "uv run pytest"}, "message": 0}]
    )

    # No edit happened, so there is no first edit to count before; absence is
    # the reading, never a zero (S-0004/D-6).
    assert "calls_before_first_edit" not in profile
    assert profile["classes"] == {"test_run": 1}


# ....................... #
# The classified profile rides the row by the context curve's route
# (S-0075/D-2): booked where the trace lives, drained once by whichever row
# ends the attempt, merged under the harness's own `burn` block.


def test_the_classified_profile_rides_the_agent_block_and_drains_once():
    task = Task.model_validate(base_task(allow=["src/**"]))
    record_burn_profile(task.id, {"classes": {"edit": 1}, "calls": 1})

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert row["agent"]["burn"] == {"profile": {"classes": {"edit": 1}, "calls": 1}}

    # The booking belongs to the attempt that produced it: the next row of
    # the same task carries no classification it was not told (S-0004/D-6).
    again = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert "burn" not in again["agent"]


def test_the_profile_merges_into_the_harness_burn_block():
    task = Task.model_validate(base_task(allow=["src/**"]))
    agent = {**AGENT, "burn": {"turns": 2, "tool_calls": 1, "top_turns": []}}
    record_burn_profile(task.id, {"classes": {"test_run": 1}, "calls": 1})

    row = build_attempt_row(task, agent, verdict="agent_error", exit_code=1, timed_out=False)

    # The same profile's two accounts: the harness's per-turn counts and the
    # engine's classification of the calls, side by side under one key.
    assert row["agent"]["burn"] == {
        "turns": 2,
        "tool_calls": 1,
        "top_turns": [],
        "profile": {"classes": {"test_run": 1}, "calls": 1},
    }
    validate_payload(EventKind.ATTEMPT_FINISHED, record_payload(row, 1))


def test_the_profile_round_trips_through_the_gate_pass_row():
    ctx = _ctx(head_sha="abc", merge_base="def", base="main")
    record_burn_profile(ctx.task.id, {"classes": {"edit": 2}, "calls": 2})

    row = build_record(ctx, RunReport(exit_code=0), "cafe1234", agent=AGENT)

    assert row["agent"]["burn"] == {"profile": {"classes": {"edit": 2}, "calls": 2}}
    # A booked classification is a fact, never a conviction: the gates passed
    # and the row's verdict is the gate report's, untouched by what it says.
    assert row["verdict"] == "green"
    assert row["exit_code"] == 0
    validate_payload(EventKind.GATES_EVALUATED, record_payload(row, 2))


# ....................... #
# What every request re-read of the contract (S-0075/D-4): the inherited
# rows' count and the contract prose's size, measured from the contract the
# row itself carries.


def test_the_contracts_row_count_and_size_ride_the_agent_block():
    task = Task.model_validate(
        base_task(
            allow=["src/**"],
            decisions=[
                {
                    "id": "S-0001/D-1",
                    "grade": "LOCKED",
                    "text": "app module layout is settled",
                    "paths": ["src/**"],
                },
                {
                    "id": "S-0002/D-2",
                    "grade": "ASSUMED",
                    "text": "no gate ships without a sabotage case",
                    "paths": ["src/**"],
                },
            ],
        )
    )

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert row["agent"]["contract"] == {
        "rows": 2,
        "characters": len("app module layout is settled")
        + len("no gate ships without a sabotage case"),
    }


def test_a_contract_with_no_rows_is_not_recorded():
    task = Task.model_validate(base_task(allow=["src/**"]))

    row = build_attempt_row(task, AGENT, verdict="agent_error", exit_code=1, timed_out=False)

    assert "contract" not in row["agent"]
    validate_payload(EventKind.ATTEMPT_FINISHED, record_payload(row, 1))
