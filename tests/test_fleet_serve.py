"""The resident manager over the whole fleet (S-0048).

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
    """S-0048/D-2: a partition nobody wrote down is a board nobody chose — and a
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
    """S-0024/D-6 reaches v2 unchanged: a root whose own configuration asks for
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
    """S-0024/D-5: a manager that stops serving four healthy repositories
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
    """S-0024/D-2: two repositories under their own thresholds and over the
    fleet's pause both. The pause reaches the pass, which is what skips the
    mint (S-0048/D-4)."""

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
    """S-0048/D-5: a task escalated under v1 and re-escalated on the board is
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


def test_a_file_whose_task_the_log_closed_does_not_hold_the_pause(tmp_path):
    """A hand landing or an abandon leaves the run-state file for `torve reap
    --escalated` (S-0094/D-5); the log has closed the task, so the file is a
    footprint and no longer pauses the pass. A file the board never closed
    still counts."""

    one = root(tmp_path, "one")

    for task in ("T-0001", "T-0002", "T-0003", "T-0004"):
        escalate(one, task)

    board = Board(
        tasks={
            "T-0001": TaskView(task_id="T-0001", state=TaskState.READY, landed_sha="abc123"),
            "T-0002": TaskView(task_id="T-0002", state=TaskState.ABANDONED),
            "T-0003": TaskView(task_id="T-0003", state=TaskState.READY),
        }
    )

    assert escalated_tasks(one, board) == {"T-0003", "T-0004"}


# ....................... #


@pytest.mark.parametrize("order", ["manifest", "alphabetical"])
def test_the_order_is_the_manifests_and_never_a_priority(tmp_path, order):
    """S-0024/D-4: a fleet that serves in a chosen order is one config change
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


def test_the_pause_counts_the_record_and_not_only_the_files(tmp_path):
    """S-0048/D-5's union was written, tested and never called: `survey` counted
    run-state files, so a fleet was blind to every escalation the manager
    had raised — which is now all of them (A-110)."""

    one = root(tmp_path, "one")
    board = Board(
        tasks={"T-0002": TaskView(task_id="T-0002", partition="a/one", state=TaskState.ESCALATED)}
    )
    seen: list[bool] = []

    async def run_pass(repo, paused):
        seen.append(paused)
        return None

    async def boards():
        return {str(one): board}

    asyncio.run(
        serve_fleet(
            manifest(repo(one, partition="a/one"), pause_escalations=1),
            run_pass,
            rounds=1,
            idle_seconds=0,
            boards=boards,
        )
    )

    # Nothing on disk is escalated; the record alone carries the queue.
    assert seen == [True]


# ....................... #
# The fleet's intake leg (S-0119/D-8): one root's pass builds it, and builds
# no agent while the label is empty.


def test_the_fleets_issues_leg_builds_no_agent_while_the_label_is_empty(tmp_path, monkeypatch):
    """S-0119/D-1: a root whose `intake.issue_label` is empty gets the leg off,
    and no drafting agent is built for it — a pass over that root is exactly
    the pass it was before the leg existed."""

    from torve.cli import assembly
    from torve.cli.fleet import _issues_leg
    from torve.config.runconfig import RunnerConfig

    calls: list[int] = []

    def explode(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("no planner agent is built with the leg off")

    monkeypatch.setattr(assembly, "build_tier_agent", explode)

    detail, moved = asyncio.run(_issues_leg(tmp_path, RunnerConfig())())

    assert (detail, moved) == ("issue intake off", False)
    # The detail line alone passes even if the agent was built eagerly and
    # the refusal swallowed, so what is asserted is the build itself.
    assert calls == []


def test_the_fleets_issues_leg_is_built_once_for_the_serve(tmp_path, monkeypatch):
    """S-0119/D-8: the fleet keeps one intake leg per root for the serve, so
    the planner tier's agent is built once rather than on every pass."""

    from torve.application import residency
    from torve.cli import assembly
    from torve.cli.fleet import _serve_fleet
    from torve.config import runconfig as runconfig_module
    from torve.config.fleet import FleetManifest
    from torve.config.runconfig import IntakeConfig, RunnerConfig

    builds: list[int] = []
    legs: list[object] = []

    def agent(*_args, **_kwargs):
        builds.append(1)
        return object()

    async def spy(*_args, **kwargs):
        legs.append(kwargs.get("issues"))

    monkeypatch.setattr(residency, "once", spy)
    monkeypatch.setattr(assembly, "build_tier_agent", agent)
    monkeypatch.setattr(
        runconfig_module,
        "load_runner_config",
        lambda root: RunnerConfig(intake=IntakeConfig(issue_label="torve:ready")),
    )
    (tmp_path / ".torve").mkdir(exist_ok=True)
    manifest_ = FleetManifest(
        repositories=[FleetRepository(root=str(tmp_path), trust="own", partition="acme/one")]
    )

    asyncio.run(_serve_fleet(manifest_, None, worker="w-1", rounds=2, interval=0))

    # Two passes ran, and the planner agent was built for one leg only.
    assert len(legs) == 2
    assert builds == [1]
    assert legs[0] is legs[1]


def test_a_label_change_under_the_serve_builds_a_new_issues_leg(tmp_path, monkeypatch):
    """S-0119/D-1: the fleet reloads a root's configuration each pass, so a
    pass naming another `intake.issue_label` gets a leg built for that label
    rather than drafting from the first one's."""

    from torve.application import residency
    from torve.cli import assembly
    from torve.cli.fleet import _serve_fleet
    from torve.config import runconfig as runconfig_module
    from torve.config.fleet import FleetManifest
    from torve.config.runconfig import IntakeConfig, RunnerConfig

    labels = ["one", "one", "two"]
    seen: list[object] = []

    async def spy(*_args, **kwargs):
        seen.append(kwargs.get("issues"))

    def load(_root):
        return RunnerConfig(intake=IntakeConfig(issue_label=labels[len(seen)]))

    monkeypatch.setattr(residency, "once", spy)
    monkeypatch.setattr(assembly, "build_tier_agent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runconfig_module, "load_runner_config", load)
    (tmp_path / ".torve").mkdir(exist_ok=True)
    manifest_ = FleetManifest(
        repositories=[FleetRepository(root=str(tmp_path), trust="own", partition="acme/one")]
    )

    asyncio.run(_serve_fleet(manifest_, None, worker="w-1", rounds=3, interval=0))

    assert len(seen) == 3
    assert seen[0] is seen[1]
    assert seen[2] is not seen[0]


def test_a_config_change_under_the_same_label_builds_a_new_issues_leg(tmp_path, monkeypatch):
    """S-0119/D-8: the fleet reloads a root's configuration each pass, so a
    configuration that moves under the serve while `intake.issue_label` stays
    put — the forge, the broker, the runtime, the planner's seat — gets a leg
    built from the new settings rather than drafting through the old ones."""

    from torve.application import residency
    from torve.cli import assembly
    from torve.cli.fleet import _serve_fleet
    from torve.config import runconfig as runconfig_module
    from torve.config.fleet import FleetManifest
    from torve.config.runconfig import IntakeConfig, RunnerConfig, ScmConfig

    repos = ["acme/one", "acme/one", "acme/two"]
    legs: list[object] = []
    built_from: list[str | None] = []

    async def spy(*_args, **kwargs):
        legs.append(kwargs.get("issues"))

    def agent(config, _root, _tier):
        built_from.append(config.scm.repo)
        return object()

    def load(_root):
        return RunnerConfig(
            intake=IntakeConfig(issue_label="torve:ready"),
            scm=ScmConfig(repo=repos[len(legs)]),
        )

    monkeypatch.setattr(residency, "once", spy)
    monkeypatch.setattr(assembly, "build_tier_agent", agent)
    monkeypatch.setattr(runconfig_module, "load_runner_config", load)
    (tmp_path / ".torve").mkdir(exist_ok=True)
    manifest_ = FleetManifest(
        repositories=[FleetRepository(root=str(tmp_path), trust="own", partition="acme/one")]
    )

    asyncio.run(_serve_fleet(manifest_, None, worker="w-1", rounds=3, interval=0))

    assert len(legs) == 3
    # The label never moved, so the second pass reuses the first leg; `scm.repo`
    # did, so the third builds again — from the configuration it just loaded,
    # not the one the first leg closed over.
    assert legs[0] is legs[1]
    assert legs[2] is not legs[0]
    assert built_from == ["acme/one", "acme/two"]


def test_the_fleets_issues_leg_keeps_a_planner_build_error_on_its_detail_line(
    tmp_path, monkeypatch
):
    """S-0119/D-8: a planner tier the root cannot build is the leg's detail
    line, not an exception — the rest of that root's pass runs."""

    from torve.cli import assembly
    from torve.cli.fleet import _issues_leg
    from torve.config.runconfig import IntakeConfig, RunnerConfig

    def refuse(*_args, **_kwargs):
        raise ValueError("planner tier names no harness")

    monkeypatch.setattr(assembly, "build_tier_agent", refuse)
    config = RunnerConfig(intake=IntakeConfig(issue_label="torve:ready"))

    detail, moved = asyncio.run(_issues_leg(tmp_path, config)())

    assert moved is False
    assert "planner tier names no harness" in detail
