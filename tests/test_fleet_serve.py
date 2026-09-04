"""The resident manager over the whole fleet (RFC 0048).

`serve_fleet` is `fleet_tick`'s shape with a manager pass where the tick
was, so what is asserted here is the shape rather than the pass: the
manifest's order, one pause for the fleet, every refusal recorded and the
round carried on, and an idle round waiting where a productive one does not.
The pass itself is injected, which is what lets all of that be checked
without a container.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_fleet import escalate, manifest, root

from torve.application.fleet import escalated_tasks, serve_fleet
from torve.application.manager import Board, TaskView
from torve.config.fleet import FleetRepository
from torve.domain.states import TaskState

# ----------------------- #


def repo(root_path: Path, *, partition: str = "acme/one", trust: str = "own") -> FleetRepository:
    return FleetRepository(root=str(root_path), trust=trust, partition=partition)


# ....................... #


def configure(root_path: Path, body: str) -> None:
    (root_path / ".torve" / "config.yaml").write_text(body, encoding="utf-8")


# ....................... #


class Passes:
    """A `PartitionPass` that reports what it was asked and answers from a
    script — one entry per call, `None` for an idle pass."""

    def __init__(self, *answers: str | Exception | None) -> None:
        self.answers = list(answers)
        self.seen: list[tuple[str, bool]] = []

    async def __call__(self, repo: FleetRepository, paused: bool) -> str | None:
        self.seen.append((repo.partition, paused))
        answer = self.answers.pop(0) if self.answers else None

        if isinstance(answer, Exception):
            raise answer

        return answer


# ....................... #


def test_every_repository_is_served_once_a_round_in_the_manifests_order(tmp_path):
    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    passes = Passes(None, None)

    report = asyncio.run(
        serve_fleet(
            manifest(repo(one, partition="a/one"), repo(two, partition="b/two")),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert [partition for partition, _ in passes.seen] == ["a/one", "b/two"]
    assert [one.partition for one in report.outcomes] == ["a/one", "b/two"]
    assert report.handled == 0


# ....................... #


def test_a_repository_with_no_partition_is_refused_and_the_round_continues(tmp_path):
    """D-48.2: a partition nobody wrote down is a board nobody chose — and a
    fleet does not stop for one missing field."""

    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    passes = Passes("T-0001")

    report = asyncio.run(
        serve_fleet(
            manifest(repo(one, partition=""), repo(two, partition="b/two")),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert [partition for partition, _ in passes.seen] == ["b/two"]
    assert "refused" in report.outcomes[0].outcome
    assert "partition" in report.outcomes[0].outcome
    assert report.outcomes[1].outcome == "handled T-0001"
    assert report.handled == 1


# ....................... #


def test_trust_is_enforced_before_the_pass_not_after(tmp_path):
    """D-24.6 reaches v2 unchanged: a root whose own configuration asks for
    more than its class allows never reaches the pass at all."""

    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    configure(one, "schema_version: 1\nruntime:\n  docker: socket\n")
    passes = Passes("T-0002")

    report = asyncio.run(
        serve_fleet(
            manifest(
                repo(one, partition="a/one", trust="reviewed"),
                repo(two, partition="b/two"),
            ),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert [partition for partition, _ in passes.seen] == ["b/two"]
    assert "refused" in report.outcomes[0].outcome
    assert "socket" in report.outcomes[0].outcome


# ....................... #


def test_a_failing_repository_is_recorded_and_the_rest_still_run(tmp_path):
    """D-24.5: a manager that stops serving four healthy repositories
    because a fifth is broken is worse than one that says so."""

    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    passes = Passes(RuntimeError("the store went away"), "T-0003")

    report = asyncio.run(
        serve_fleet(
            manifest(repo(one, partition="a/one"), repo(two, partition="b/two")),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert report.outcomes[0].outcome == "error: the store went away"
    assert report.outcomes[1].outcome == "handled T-0003"
    assert report.handled == 1


# ....................... #


def test_the_pause_is_decided_for_the_fleet_not_per_repository(tmp_path):
    """D-24.2: two repositories under their own thresholds and over the
    fleet's pause both. The pause reaches the pass, which is what skips the
    mint (D-48.4)."""

    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    escalate(one, "T-0001")
    escalate(two, "T-0002")
    passes = Passes(None, None)

    asyncio.run(
        serve_fleet(
            manifest(
                repo(one, partition="a/one"), repo(two, partition="b/two"), pause_escalations=2
            ),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert [paused for _, paused in passes.seen] == [True, True]


# ....................... #


def test_one_escalation_short_of_the_fleet_budget_does_not_pause(tmp_path):
    one, two = root(tmp_path, "one"), root(tmp_path, "two")
    escalate(one, "T-0001")
    passes = Passes(None, None)

    asyncio.run(
        serve_fleet(
            manifest(
                repo(one, partition="a/one"), repo(two, partition="b/two"), pause_escalations=2
            ),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    assert [paused for _, paused in passes.seen] == [False, False]


# ....................... #


def test_a_productive_round_goes_straight_round_again_and_an_idle_one_waits(tmp_path):
    """Asserted on what the loop did, not on a clock: three rounds, the
    first productive, and the sleep patched to a recorder."""

    one = root(tmp_path, "one")
    slept: list[float] = []
    passes = Passes("T-0001", None, None)

    async def scenario() -> None:
        import torve.application.fleet as fleet_module

        real_sleep = fleet_module.asyncio.sleep

        async def recording_sleep(seconds: float) -> None:
            slept.append(seconds)
            await real_sleep(0)

        fleet_module.asyncio.sleep = recording_sleep  # type: ignore[assignment]

        try:
            await serve_fleet(
                manifest(repo(one, partition="a/one")), passes, rounds=3, idle_seconds=7.5
            )

        finally:
            fleet_module.asyncio.sleep = real_sleep  # type: ignore[assignment]

    asyncio.run(scenario())

    # Round 1 took a task and did not wait; round 2 was idle and did; round 3
    # is the last, so it does not wait for a round that will not come.
    assert slept == [7.5]


# ....................... #


def test_the_escalation_count_unions_both_carriers(tmp_path):
    """D-48.5: a task escalated under v1 and re-escalated on the board is
    one task to triage, and counting it twice pauses a fleet for work that
    does not exist."""

    one = root(tmp_path, "one")
    escalate(one, "T-0001")

    board = Board(
        tasks={
            "T-0001": TaskView(task_id="T-0001", state=TaskState.ESCALATED),
            "T-0002": TaskView(task_id="T-0002", state=TaskState.ESCALATED),
            "T-0003": TaskView(task_id="T-0003", state=TaskState.QUEUED),
        }
    )

    assert escalated_tasks(one) == {"T-0001"}
    assert escalated_tasks(one, board) == {"T-0001", "T-0002"}


# ....................... #


@pytest.mark.parametrize("order", ["manifest", "alphabetical"])
def test_the_order_is_the_manifests_and_never_a_priority(tmp_path, order):
    """D-24.4: a fleet that serves in a chosen order is one config change
    from being a scheduler with opinions."""

    beta, alpha = root(tmp_path, "beta"), root(tmp_path, "alpha")
    passes = Passes(None, None)

    asyncio.run(
        serve_fleet(
            manifest(repo(beta, partition="b/beta"), repo(alpha, partition="a/alpha"), order=order),
            passes,
            rounds=1,
            idle_seconds=0,
        )
    )

    expected = ["a/alpha", "b/beta"] if order == "alphabetical" else ["b/beta", "a/alpha"]

    assert [partition for partition, _ in passes.seen] == expected
