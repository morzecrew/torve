"""`torve log divergence` taking several rows in one call (S-0073/D-7).

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
        "--kind": "resolved",
        "--class": "discovery",
        "--claim": f"what reality says about {decision}",
        "--evidence": evidence,
        "--action": "decided",
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
