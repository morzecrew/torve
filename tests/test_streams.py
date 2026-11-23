"""`streams.fold_attempts` (S-0124/D-15): an attempt's rows fold into its newest,
and every other row passes through in the stream's order."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from torve.application.streams import fold_attempts

# ----------------------- #


def _key(row: dict[str, Any]) -> tuple[str, int, int] | None:
    """The attempt a row belongs to, written independently of the fold."""

    agent = row.get("agent")

    if "kind" in row or not isinstance(agent, dict) or "attempt" not in agent:
        return None

    return (row["task_id"], agent["attempt"], agent.get("candidate", 0))


_attempt = st.fixed_dictionaries(
    {
        "task_id": st.sampled_from(["T-1", "T-2"]),
        "agent": st.fixed_dictionaries(
            {"attempt": st.integers(1, 2), "cost_usd": st.floats(0, 5)},
            optional={"candidate": st.integers(1, 2)},
        ),
    }
)
_review = st.fixed_dictionaries(
    {
        "kind": st.just("review"),
        "task_id": st.sampled_from(["T-1", "T-2"]),
        "agent": st.fixed_dictionaries({"attempt": st.integers(1, 2)}),
    }
)
_unstamped = st.fixed_dictionaries(
    {"task_id": st.sampled_from(["T-1", "T-2"]), "agent": st.fixed_dictionaries({})}
)
_event = st.fixed_dictionaries({"kind": st.just("engine"), "event": st.just("blocked")})


@given(st.lists(st.one_of(_attempt, _review, _unstamped, _event), max_size=12))
def test_an_attempt_keeps_its_newest_row_and_every_other_row_passes(rows):
    """S-0124/D-15: whatever the stream holds, the fold keeps, in order, every
    row that is not a stamped attempt row and, of each attempt's rows, the
    last one."""

    indexed = list(enumerate(rows))
    last = {key: index for index, row in indexed if (key := _key(row)) is not None}
    expected = [row for index, row in indexed if _key(row) is None or last[_key(row)] == index]

    folded = fold_attempts(rows)

    assert [id(row) for row in folded] == [id(row) for row in expected]


def test_a_session_revisions_rows_are_one_attempt_at_its_newest_block():
    """S-0124/D-15: the revision's gate pass carries the attempt's whole block
    so far, so it is the attempt and the first pass is not counted again."""

    first = {"task_id": "T-1", "agent": {"attempt": 1, "cost_usd": 2.0}}
    revised = {"task_id": "T-1", "agent": {"attempt": 1, "cost_usd": 3.4}}
    other = {"task_id": "T-1", "agent": {"attempt": 1, "candidate": 2, "cost_usd": 1.0}}

    assert fold_attempts([first, other, revised]) == [other, revised]
