"""Several candidates for one unit (S-0122/D-7, D-8): the class that sets the
width, the straggler clock, the repair turn's qualification, the collapse and
the pre-rank, the critic reading in rank order, and the runner leaving the
chosen candidate in the task's worktree with its pass and verdict."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from test_review_run import review_rig  # noqa: F401 — the fixture, by name
from test_run_loop import ScriptedAgent, task_for

import torve.application.runner as run_module
from torve.adapters.workspace.git import GitWorkspace
from torve.application import selection
from torve.application.ports import AgentResult
from torve.application.runner import run_task
from torve.config.runconfig import ReviewConfig, RunnerConfig
from torve.domain.attempt import GateResult
from torve.domain.states import TaskState
from torve.domain.task import Scope, Task

# ----------------------- #


def _task(*files: str) -> Task:
    return Task(id="T-0001", decisions=[], scope=Scope(files=list(files)))


def test_a_unit_s_class_sets_its_width(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    assert selection.unit_class(_task("docs/**", "/README.md"), tmp_path) == "S"
    assert selection.unit_class(_task("src/app.py"), tmp_path, revision=True) == "S"
    assert selection.unit_class(_task("src/app.py"), tmp_path) == "M"
    assert selection.unit_class(_task("src/app.py"), tmp_path, escalated=True) == "L"
    assert selection.unit_class(_task("src/a/**", "src/b/**", "src/c/**"), tmp_path) == "L"
    # A module the tree does not hold yet.
    assert selection.unit_class(_task("src/new.py"), tmp_path) == "L"
    assert [selection.WIDTHS[size] for size in "SML"] == [1, 2, 3]


def test_the_width_reads_the_revision_record_at_the_root(tmp_path: Path) -> None:
    # S-0124/D-1: the session plants the record in the worktree only after the
    # width is read, so every revision of T-0809 and T-0811 ran three.
    from types import SimpleNamespace

    from torve.application.feedback import feedback_file
    from torve.application.runstate import RunState

    worktree = tmp_path / ".wt" / "T-0001"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    run = SimpleNamespace(
        root=tmp_path,
        task=_task("src/app.py"),
        worktree=worktree,
        config=RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic")),
        shadow=False,
    )
    state = RunState(task_id="T-0001", path=tmp_path / ".wt" / "T-0001.state.json")

    assert run_module._width(run, state) == (2, "M")

    record = feedback_file(tmp_path, "T-0001")
    record.parent.mkdir(parents=True)
    record.write_text("# Revision feedback for T-0001\n", encoding="utf-8")

    assert run_module._width(run, state) == (1, "S")


def test_the_width_is_capped_by_the_configuration_and_a_round_runs_one(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from torve.application.runstate import RunState

    files = ["src/a/**", "src/b/**", "src/c/**"]
    state = RunState(task_id="T-0001", path=tmp_path / ".wt" / "T-0001.state.json")

    def width(max_width: int | None, task: Task) -> tuple[int, str]:
        review = ReviewConfig(on=["task_gated"], mode="critic", max_width=max_width)
        run = SimpleNamespace(
            root=tmp_path,
            task=task,
            worktree=tmp_path,
            config=RunnerConfig(review=review),
            shadow=False,
        )
        return run_module._width(run, state)

    assert width(None, _task(*files)) == (3, "L")
    assert width(2, _task(*files)) == (2, "L")
    assert width(1, _task(*files)) == (1, "L")

    round_task = Task.model_validate(
        {
            "id": "T-0001",
            "decisions": [],
            "scope": {"files": files},
            "round": {"branch": "torve/S-0001"},
        }
    )

    assert width(None, round_task) == (1, "L")


def test_a_straggler_gets_half_again_the_first_green_time_capped() -> None:
    assert selection.straggler_clock(100, 1200) == 150
    assert selection.straggler_clock(3000, 5000) == 3000 + selection.STRAGGLER_CAP_S
    assert selection.straggler_clock(1000, 1200) == 1200


def _gate(name: str, outcome: str = "fail", output: str = "") -> GateResult:
    return GateResult(name=name, outcome=outcome, state="blocking", output=output)


def _suite(*tests: str) -> GateResult:
    return _gate(
        "suite",
        output=f"the change broke {len(tests)} test(s) the base tree passes "
        f"(or cannot run): {', '.join(tests)}",
    )


def test_only_a_mechanical_red_buys_a_repair_turn() -> None:
    assert selection.repairable([_suite("t::a", "t::b", "t::c")])
    assert selection.repairable([_gate("mypy"), _gate("scope", "pass")])
    assert not selection.repairable([_suite("t::a", "t::b", "t::c", "t::d")])
    assert not selection.repairable([_gate("mypy"), _gate("scope")])
    assert not selection.repairable([_gate("suite", "pass")])


def _candidate(index: int, *, green: bool = True, patch: str = "", lines: int = 0):
    return selection.Candidate(
        index=index, result=None, green=green, patch=patch or f"+{index}", lines=lines
    )


def test_the_same_diff_collapses_and_the_rank_is_deterministic() -> None:
    one, two, three = (
        _candidate(1, patch="+a\n"),
        _candidate(2, patch="+b"),
        _candidate(3, patch="+a"),
    )

    kept = selection.collapse([one, two, three])

    assert kept == [one, two] and three.collapsed_into == 1

    small, large = _candidate(1, lines=40), _candidate(2, lines=4)

    assert [c.index for c in selection.prerank([small, large], "T-0001")] == [2, 1]
    assert (large.rank, small.rank) == (1, 2)


def test_the_same_edit_to_another_file_is_another_candidate() -> None:
    one = _candidate(1, patch="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n+fix")
    two = _candidate(2, patch="diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n+fix")

    assert selection.collapse([one, two]) == [one, two]


def test_the_critic_reads_in_rank_order_until_one_is_clean() -> None:
    plans = {1: (False, 0), 2: (True, 50), 3: (True, 10)}
    clocks: list[float] = []
    read: list[int] = []

    async def launch(index: int, clock: float) -> selection.Candidate:
        clocks.append(clock)
        green, lines = plans[index]
        candidate = _candidate(index, green=green, lines=lines)
        candidate.wall_s = 100.0

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return candidate.index == 2

    chosen = asyncio.run(selection.select(3, launch, review, agent_clock=1200, seed="T-0001"))

    assert read == [3, 2]
    assert chosen.winner is not None and chosen.winner.index == 2
    assert chosen.critic_calls == 2 and chosen.outcome == "selected"
    # The candidates start together (S-0124/D-4), so each is handed the seat's
    # own clock; the straggler cut is the deadline selection sets, not a
    # shorter clock on a later launch.
    assert clocks == [1200, 1200, 1200]

    record = selection.unit_record(
        chosen, task_id="T-0001", attempt=1, width=3, size="L", at="2026-11-05T00:00:00Z"
    )

    assert (record["kind"], record["schema_version"], record["selected"]) == ("unit.closed", 2, 2)
    assert [c["verdict"] for c in record["candidates"]] == [None, "clean", "blocked"]


def test_the_critic_budget_ends_the_reading_and_hands_on_the_best_ranked() -> None:
    async def launch(index: int, _clock: float) -> selection.Candidate:
        return _candidate(index, lines=index)

    async def review(_candidate: selection.Candidate) -> bool:
        return False

    chosen = asyncio.run(
        selection.select(3, launch, review, agent_clock=60, seed="T-0001", critic_budget=1)
    )

    assert chosen.critic_calls == 1
    assert chosen.winner is None and chosen.chosen.index == 1
    assert chosen.outcome == "blocked"


def test_with_no_green_candidate_the_first_is_handed_on() -> None:
    async def launch(index: int, _clock: float) -> selection.Candidate:
        return _candidate(index, green=False)

    chosen = asyncio.run(selection.select(2, launch, None, agent_clock=60, seed="T-0001"))

    assert chosen.chosen.index == 1 and chosen.outcome == "red"


# ....................... #
# The unit's budget (S-0123/D-1).


def test_unit_budget_stops_before_the_batch() -> None:
    """S-0124/D-7: the bound is checked once before the candidates launch, so
    a batch launched under it may finish above it by the spend it launched
    with — and a unit already at the bound launches nothing."""

    launched: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        launched.append(index)
        candidate = _candidate(index, green=False)
        candidate.spend = 0.5

        return candidate

    async def review(_candidate: selection.Candidate) -> bool:
        return False

    chosen = asyncio.run(
        selection.select(3, launch, review, agent_clock=60, seed="T-0001", spent=2.75)
    )

    # All three launched under the bound; the batch finished above it, and
    # with no green candidate there is no critic call to stop.
    assert launched == [1, 2, 3]
    assert chosen.spent_usd == 4.25 and not chosen.exhausted
    assert chosen.winner is None

    started: list[int] = []

    async def never(index: int, _clock: float) -> selection.Candidate:
        started.append(index)
        return _candidate(index)

    at_bound = asyncio.run(
        selection.select(3, never, review, agent_clock=60, seed="T-0001", spent=3.0)
    )

    assert started == []
    assert at_bound.candidates == [] and at_bound.exhausted


def test_unit_budget_stops_before_the_next_critic_call() -> None:
    """The critic call counts as the candidate does: once adding one would
    reach the bound, the next candidate is never read."""

    read: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        candidate = _candidate(index, lines=index)
        candidate.spend = 0.25
        candidate.critic_spend = 1.5

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return False

    chosen = asyncio.run(
        selection.select(3, launch, review, agent_clock=60, seed="T-0001", budget_usd=3.0)
    )

    assert read == [1, 2]
    assert chosen.critic_calls == 2
    assert chosen.spent_usd == 3.75 and chosen.exhausted
    assert chosen.winner is None


def test_unit_budget_prices_a_call_from_its_cost_else_its_list_price() -> None:
    """A call's spend is the cost its block reports, else its tokens at the
    model's list price, else nothing — never an invented zero."""

    from torve.config.providers import Price

    prices = {("p", "m"): Price(input=2.0, output=10.0)}

    assert selection.notional_spend({"cost_usd": 0.4}, prices) == 0.4
    assert (
        selection.notional_spend(
            {"cost_usd": None, "provider": "p", "model": "m", "input_tokens": 1_000_000}, prices
        )
        == 2.0
    )
    assert (
        selection.notional_spend(
            {"cost_usd": None, "provider": "q", "model": "m", "input_tokens": 100}, prices
        )
        is None
    )
    assert (
        selection.notional_spend({"cost_usd": None, "provider": "p", "model": "m"}, prices) is None
    )


# ....................... #
# The candidates run side by side (S-0124/D-4).


def test_candidates_run_side_by_side_and_the_straggler_is_cut() -> None:
    """Every launch starts before any finishes; the first green one's finish
    sets the straggler deadline, the candidate still in its agent turn is
    cancelled and recorded red with its elapsed time, and the candidates are
    ordered by index however they completed."""

    started: set[int] = set()
    all_started = asyncio.Event()
    clocks: list[float] = []
    cancelled: list[int] = []

    async def launch(index: int, clock: float) -> selection.Candidate:
        clocks.append(clock)
        started.add(index)

        if len(started) == 3:
            all_started.set()

        await all_started.wait()

        if index == 2:
            try:
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                cancelled.append(index)
                raise

        candidate = _candidate(index, green=index != 2)
        candidate.wall_s = 0.05

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        return candidate.index == 3

    chosen = asyncio.run(selection.select(3, launch, review, agent_clock=60, seed="T-0001"))

    # Side by side: no launch could leave the barrier before the third started.
    assert started == {1, 2, 3}
    assert clocks == [60, 60, 60]
    assert cancelled == [2]
    # Sorted by index, never by completion.
    assert [c.index for c in chosen.candidates] == [1, 2, 3]
    cut = chosen.candidates[1]
    assert cut.result is None and not cut.green and cut.wall_s >= 0.02
    assert chosen.winner is not None and chosen.winner.index == 3


def test_side_by_side_cut_lands_at_the_straggler_clock(monkeypatch) -> None:
    """S-0124/D-4: the deadline is `straggler_clock` of the first green
    candidate's wall time, measured from that candidate's finish. A fixed
    deadline, or one that ignores the clock, cuts somewhere else."""

    calls: list[tuple[float, float]] = []

    def clock(first_green_s: float, agent_clock: float) -> float:
        calls.append((first_green_s, agent_clock))
        return first_green_s + 0.3

    monkeypatch.setattr(selection, "straggler_clock", clock)

    async def launch(index: int, _clock: float) -> selection.Candidate:
        if index == 2:
            await asyncio.sleep(5)

        candidate = _candidate(index, green=index != 2)
        candidate.wall_s = 0.05

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        return True

    chosen = asyncio.run(selection.select(2, launch, review, agent_clock=60, seed="T-0001"))

    assert calls == [(0.05, 60)]
    cut = chosen.candidates[1]
    assert cut.cut and cut.result is None
    # Cut 0.3s after candidate 1 finished, which was at once.
    assert 0.25 <= cut.wall_s < 0.6


def test_stop_cancels_the_others_and_asks_no_critic() -> None:
    """S-0124/D-4: when the run escalates under one candidate, the others'
    turns are cancelled and the critic is never asked — stop ends the round,
    so a green sibling is not read for a run that will not land it."""

    started: set[int] = set()
    all_started = asyncio.Event()
    cancelled: list[int] = []
    read: list[int] = []
    escalated = False

    async def launch(index: int, _clock: float) -> selection.Candidate:
        nonlocal escalated

        started.add(index)

        if len(started) == 3:
            all_started.set()

        await all_started.wait()

        if index == 1:
            escalated = True
            candidate = _candidate(index, green=True)
            candidate.wall_s = 0.05

            return candidate

        try:
            await asyncio.sleep(5)

        except asyncio.CancelledError:
            cancelled.append(index)
            raise

        return _candidate(index, green=True)

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return True

    chosen = asyncio.run(
        selection.select(
            3,
            launch,
            review,
            agent_clock=60,
            seed="T-0001",
            stop=lambda: escalated,
        )
    )

    assert sorted(cancelled) == [2, 3]
    assert read == []
    assert chosen.winner is None and chosen.outcome == "red"
    # Every candidate is on the record, in index order, however it ended.
    assert [candidate.index for candidate in chosen.candidates] == [1, 2, 3]


def test_unit_budget_counts_a_cancelled_candidate_s_finished_spend() -> None:
    """S-0124/D-7: a candidate cut at the straggler deadline keeps what its
    finished turns spent — in the bound before the next critic call, and in
    the unit's record."""

    started: set[int] = set()
    all_started = asyncio.Event()
    cancelled: list[int] = []
    read: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        started.add(index)

        if len(started) == 2:
            all_started.set()

        await all_started.wait()

        if index == 2:
            try:
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                cancelled.append(index)
                raise

        candidate = _candidate(index, green=True)
        candidate.wall_s = 0.05
        candidate.spend = 0.5

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return True

    chosen = asyncio.run(
        selection.select(
            2,
            launch,
            review,
            agent_clock=60,
            seed="T-0001",
            spent=2.3,
            cancelled_spend=lambda index: 0.4,
        )
    )

    assert cancelled == [2]
    # 2.30 already spent, 0.50 for the green candidate, 0.40 for the turns the
    # cut one finished: over the bound, so no critic call is spent.
    assert round(chosen.spent_usd, 6) == 3.2
    assert chosen.exhausted and chosen.winner is None
    assert read == []

    record = selection.unit_record(
        chosen, task_id="T-0001", attempt=1, width=2, size="M", at="2026-11-05T00:00:00Z"
    )

    assert [c["spend_usd"] for c in record["candidates"]] == [0.5, 0.4]
    assert [c["green"] for c in record["candidates"]] == [True, False]
    assert record["outcome"] == "blocked"


# ....................... #


def test_the_runner_leaves_the_chosen_candidate_in_the_task_s_worktree(
    review_rig,  # noqa: F811
    monkeypatch,
) -> None:
    """Candidate 1 is red and 2 green: the critic reads candidate 2 once, its
    worktree takes the task's place, the loop's own gates and review read the
    pass and the verdict selection already holds, and the unit's record names
    what happened."""

    from test_critic import answer

    repo, _runtime, deps_for = review_rig
    passes: list[int] = []

    def gate_pass(run, _state):
        passes.append(run.candidate or 1)
        red = run.candidate <= 1
        return (1 if red else 0), "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "run_gate_pass", gate_pass)
    critic = ScriptedAgent([AgentResult(exit_code=0, output=answer())])
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic"))
    deps = deps_for(critic)
    (repo.root / ".wt" / "T-9001").mkdir(parents=True, exist_ok=True)
    (repo.root / ".wt" / "T-9001" / "own.txt").write_text("1\n", encoding="utf-8")

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    # The candidates run together (S-0124/D-4), so the two passes may finish
    # in either order; both were judged, and the green one won.
    assert sorted(passes) == [1, 2]
    assert sum("critic reviewed candidate 2" in h["fact"] for h in state.history) == 1
    assert not (repo.root / ".wt" / "T-9001~2").exists()
    assert not (repo.root / ".wt" / "T-9001" / "own.txt").exists()

    rows = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    [unit] = [row for row in rows if row.get("kind") == "unit.closed"]

    assert (unit["width"], unit["selected"], unit["outcome"]) == (2, 2, "selected")
    assert [c["green"] for c in unit["candidates"]] == [False, True]


# ....................... #


def test_a_candidate_worktree_starts_at_the_task_s_base_and_takes_its_place(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str, at: Path = root) -> str:
        return subprocess.run(
            ["git", "-C", str(at), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    workspace = GitWorkspace(root)
    own = workspace.create("T-0001", "HEAD")
    (own / "f.txt").write_text("own attempt\n", encoding="utf-8")

    # The tree as an earlier attempt left it, its work uncommitted.
    base = workspace.snapshot("T-0001")
    # The first candidate commits over it; a later one still starts there.
    git("commit", "-q", "-am", "a convicted tree", at=own)
    (own / "f.txt").write_text("candidate one\n", encoding="utf-8")
    candidate = workspace.create_candidate("T-0001", 2, base)

    assert git("rev-parse", "HEAD", at=candidate) == base
    assert (candidate / "f.txt").read_text(encoding="utf-8") == "own attempt\n"

    (candidate / "f.txt").write_text("the chosen work\n", encoding="utf-8")
    workspace.promote_candidate("T-0001", 2)

    assert not candidate.exists()
    assert (own / "f.txt").read_text(encoding="utf-8") == "the chosen work\n"
    assert git("symbolic-ref", "HEAD", at=own) == "refs/heads/torve/T-0001"


def test_a_halted_turn_buys_no_repair_and_runs_no_battery(repo, monkeypatch) -> None:
    """A turn that halted is judged by nobody: the repair callback runs no
    gate pass and asks for no second turn (S-0122/D-8)."""

    from test_run_loop import OK, MockRuntime, MockScm, MockVcs, MockWorkspace

    from torve.adapters.store.durable import open_store
    from torve.application.dispatch import RunDeps

    class HaltingSession(ScriptedAgent):
        kind = "harness"

        def __init__(self) -> None:
            super().__init__([OK], halted_on_attempt=1)
            self.follow_ups = 0

        def follow_up(self, ctx, previous, text):
            self.follow_ups += 1
            return None

    repo.seed()
    passes: list[int] = []
    monkeypatch.setattr(
        run_module, "run_gate_pass", lambda run, _state: passes.append(1) or (0, "", "", [], "")
    )
    agent = HaltingSession()
    deps = RunDeps(
        workspace=MockWorkspace(repo.root),
        runtime=MockRuntime(),  # type: ignore[arg-type]
        agent=agent,
        vcs=MockVcs(),  # type: ignore[arg-type]
        scm=MockScm(),  # type: ignore[arg-type]
        store=open_store,
    )

    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps)

    assert state.state is TaskState.ESCALATED
    assert passes == [] and agent.follow_ups == 0


def test_a_gate_pass_that_raises_inside_selection_escalates_the_run(
    review_rig,  # noqa: F811
    monkeypatch,
) -> None:
    """The loop's own reading of a battery that raised, inside selection: the
    run escalates as a gate infrastructure failure and nothing else runs."""

    repo, _runtime, deps_for = review_rig
    passes: list[int] = []

    def broken(run, _state):
        passes.append(run.candidate or 1)
        raise RuntimeError("the gates sandbox would not start")

    monkeypatch.setattr(run_module, "run_gate_pass", broken)
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic"))

    state = run_task(repo.root, task_for(repo), config, deps_for(ScriptedAgent([])))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"
    # Both candidates launched before the first pass raised (S-0124/D-4); the
    # first escalation is the run's, and a sibling's pass that raises too does
    # not transition the state again.
    assert 1 in passes and set(passes) <= {1, 2}
