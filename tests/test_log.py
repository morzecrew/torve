"""`torve log divergence` taking several rows in one call (S-0073/D-7), and
`torve abort`, the halt an executor writes when its task cannot hold
(S-0122/D-14).

The bookkeeping tail measured four to fourteen calls per attempt, one
`log divergence` per governed row. The rows arrive together here, and the
batch is one transaction: what the gate would refuse leaves the whole call
unwritten rather than half of it.
"""

from __future__ import annotations

import json

import yaml
from conftest import HOSTILE
from typer.testing import CliRunner

from torve.cli.main import app
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_OK
from torve.gates.sabotage import TASK_ID


def run(worktree, *args):
    return CliRunner().invoke(
        app, ["log", "divergence", TASK_ID, "--root", str(worktree.root), *args]
    )


def row(decision, evidence=HOSTILE, **overrides):
    fields = {
        "--decision": decision,
        "--grade": "ASSUMED",
        "--kind": "departed",
        "--class": "discovery",
        "--claim": f"what reality says about {decision}",
        "--evidence": evidence,
        "--action": "departed",
    }
    fields.update(overrides)

    return [part for name, value in fields.items() if value is not None for part in (name, value)]


def bare(decision):
    """A row carrying only what every row must carry."""

    return row(decision, **{"--class": None})


def entries(worktree):
    return yaml.safe_load(layout.log_file(worktree.root, TASK_ID).read_text())["entries"]


def test_several_rows_in_one_call_land_as_several_entries(worktree):
    """The whole point: one invocation, one entry per `--decision`, paired
    with the nth of every other option and in the order they were stated."""

    result = run(worktree, *row("S-0001/D-1"), *row("S-0001/D-2"), *row("S-0001/D-3"))

    assert result.exit_code == EXIT_OK

    recorded = entries(worktree)

    assert [one["decision"] for one in recorded] == ["S-0001/D-1", "S-0001/D-2", "S-0001/D-3"]
    assert [one["claim"] for one in recorded] == [
        f"what reality says about {name}" for name in ("S-0001/D-1", "S-0001/D-2", "S-0001/D-3")
    ]


def test_the_report_counts_the_log_and_not_the_call(worktree):
    """A batch reports the same shape a single row does — the log's own
    total, so a second call's report reads as a continuation."""

    assert run(worktree, *row("S-0001/D-1"), "--format", "json").exit_code == EXIT_OK

    result = run(worktree, *row("S-0001/D-2"), *row("S-0001/D-3"), "--format", "json")

    assert json.loads(result.stdout) == {
        "accepted": True,
        "channel": False,
        # The engine owns the layout; the report names whatever it resolves to.
        "log": str(layout.log_file(worktree.root, TASK_ID).relative_to(worktree.root)),
        "entries": 3,
        "staged": True,
    }


def test_one_refused_row_leaves_the_whole_call_unwritten(worktree):
    """The batch is a transaction. A row the gate would refuse takes its
    good neighbours down with it, because an attempt that has to work out
    which half landed is worse off than one told to state all three again."""

    result = run(
        worktree,
        *row("S-0001/D-1"),
        *row("S-0001/D-2", evidence="a sentence, which is a claim and not evidence"),
    )

    assert result.exit_code == EXIT_CONFIG
    assert not layout.log_file(worktree.root, TASK_ID).exists()
    # The refusal names which row to repair, not just what is wrong with it.
    assert "row 2 (S-0001/D-2)" in result.output


def test_options_that_do_not_line_up_are_refused_before_anything_is_checked(worktree):
    """Rows are paired by position, so two decisions carrying one claim
    between them would silently shift a row onto its neighbour's words."""

    stated = row("S-0001/D-1") + row("S-0001/D-2")
    claim = stated.index("--claim")
    result = run(worktree, *stated[:claim], *stated[claim + 2 :])

    assert result.exit_code == EXIT_CONFIG
    assert not layout.log_file(worktree.root, TASK_ID).exists()
    assert "--claim is given once with 2 rows stated" in result.output


def test_an_optional_option_is_given_for_every_row_or_for_none(worktree):
    """`--class` on one row of two would attach to whichever row came
    first. Absent from both is fine; present on one only is refused."""

    neither = run(worktree, *bare("S-0001/D-1"), *bare("S-0001/D-2"))

    assert neither.exit_code == EXIT_OK
    assert all("class" not in one for one in entries(worktree))

    one_of_two = run(worktree, *bare("S-0001/D-3"), *bare("S-0001/D-4"), "--class", "discovery")

    assert one_of_two.exit_code == EXIT_CONFIG
    assert len(entries(worktree)) == 2  # the first call's, and nothing since


def test_a_single_row_behaves_exactly_as_it_did(worktree):
    """The batch form is an addition: one row still writes one entry and
    reports it in the singular."""

    result = run(worktree, *row("S-0001/D-1"))

    assert result.exit_code == EXIT_OK
    assert len(entries(worktree)) == 1
    assert "entry 1 recorded" in result.output


# ----------------------- #
# `torve abort` (S-0122/D-14): one halted entry per row cited, through the
# same intake and the same channel as every other entry (S-0121/D-4).


def abort(worktree, *args):
    return CliRunner().invoke(app, ["abort", "--root", str(worktree.root), *args])


def test_abort_on_a_locked_row_writes_a_halt_the_session_escalates_to_the_owner(worktree):
    """S-0122/D-14: the halt carries the row's grade from the contract, so a
    LOCKED row escalates as `locked_conflict` — the existing reading, kept."""

    from torve.application.session import _halt_reason
    from torve.domain.states import EscalationReason

    result = abort(
        worktree,
        "--row",
        "D-1",
        "--claim",
        "the layout the row settles leaves no place for the new module",
        "--evidence",
        "src/app.py:1 — the only module the layout allows",
    )

    assert result.exit_code == EXIT_OK, result.output
    (halt,) = entries(worktree)
    assert {key: halt[key] for key in ("decision", "grade", "kind", "action", "attempt")} == {
        "decision": "S-0001/D-1",
        "grade": "LOCKED",
        "kind": "contradicted",
        "action": "halted",
        "attempt": 1,
    }
    assert _halt_reason(worktree.root, TASK_ID, 1) is EscalationReason.LOCKED_CONFLICT


def test_abort_naming_no_row_halts_the_task_as_underspecified(worktree):
    """S-0122/D-14: an impossible task cites no row; its halt is `unlisted`,
    owes no proposal, and goes back to the author as `underspecified`."""

    from torve.application.session import _halt_reason
    from torve.domain.states import EscalationReason

    result = abort(
        worktree,
        "--claim",
        "the intent asks for a retry the transport cannot express",
        "--evidence",
        "src/app.py:1 — the call that has no retry",
    )

    assert result.exit_code == EXIT_OK, result.output
    (halt,) = entries(worktree)
    assert (halt["decision"], halt["grade"], halt["kind"]) == ("unlisted", "UNLISTED", "blocked")
    assert _halt_reason(worktree.root, TASK_ID, 1) is EscalationReason.UNDERSPECIFIED


def test_abort_writes_the_attempt_the_engine_pinned(worktree):
    """A halt counts only against its own attempt's entries, and nothing in
    the sandbox knows which attempt it is: the engine's pin says."""

    from torve.application.divergence import seed

    seed(worktree.root, TASK_ID, base="0" * 40, attempt=3)
    result = abort(worktree, "--claim", "c", "--evidence", "src/app.py:1 — the line")

    assert result.exit_code == EXIT_OK, result.output
    assert [one["attempt"] for one in entries(worktree)] == [3]


def test_a_departure_without_attempt_takes_the_pinned_one(worktree):
    """`working-rules` shows the verb without `--attempt`, so a retry's
    departure takes the attempt the engine pinned rather than attempt 1; an
    explicit `--attempt` still wins."""

    from torve.application.divergence import seed

    seed(worktree.root, TASK_ID, base="0" * 40, attempt=3)

    assert run(worktree, *row("S-0001/D-1")).exit_code == EXIT_OK
    assert run(worktree, *row("S-0001/D-2"), "--attempt", "2").exit_code == EXIT_OK
    assert [one["attempt"] for one in entries(worktree)] == [3, 2]


def test_abort_refuses_a_row_the_contract_does_not_carry_and_writes_nothing(worktree):
    result = abort(
        worktree, "--row", "S-0009/D-7", "--claim", "c", "--evidence", "src/app.py:1 — the line"
    )

    assert result.exit_code == EXIT_CONFIG
    assert "S-0009/D-7" in result.output and "S-0001/D-1" in result.output
    assert not layout.log_file(worktree.root, TASK_ID).exists()


def test_abort_refuses_evidence_nobody_can_locate(worktree):
    result = abort(worktree, "--claim", "c", "--evidence", "it just cannot work")

    assert result.exit_code == EXIT_CONFIG
    assert "not locatable" in result.output
    assert not layout.log_file(worktree.root, TASK_ID).exists()


def test_abort_goes_through_the_runs_channel_when_it_has_one(worktree, monkeypatch):
    """S-0121/D-4: an agent writes only `divergence.recorded`, through the
    broker route; abort is no new kind and no new route."""

    sent: list[tuple[str, dict]] = []

    class Recorder:
        def record(self, kind, payload):
            sent.append((kind, payload))

    monkeypatch.setattr("torve.cli.log.open_channel", lambda root: Recorder())

    result = abort(
        worktree, "--row", "S-0001/D-1", "--claim", "c", "--evidence", "src/app.py:1 — the line"
    )

    assert result.exit_code == EXIT_OK, result.output
    ((kind, payload),) = sent
    assert kind == "divergence.recorded"
    assert (payload["action"], payload["grade"], payload["entry_kind"]) == (
        "halted",
        "LOCKED",
        "contradicted",
    )
    assert not layout.log_file(worktree.root, TASK_ID).exists()
