"""Several candidates for one unit (S-0122/D-7, S-0127/D-6): the class that
sets the width, the first green that wins and cuts the others, the critic
reading it alone, the class's budget, and the runner leaving the chosen
candidate in the task's worktree with its pass and verdict."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from test_review_run import review_rig  # noqa: F401 — the fixture, by name
from test_run_loop import ScriptedAgent, task_for

import torve.application.runner as run_module
from torve.adapters.workspace.git import GitWorkspace
from torve.application import selection
from torve.application.ports import AgentResult
from torve.application.runner import run_task
from torve.config.runconfig import ReviewConfig, RunnerConfig
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


def _candidate(index: int, *, green: bool = True, patch: str = ""):
    return selection.Candidate(index=index, result=None, green=green, patch=patch or f"+{index}")


def test_first_green_is_read_by_the_critic_alone() -> None:
    """S-0127/D-6: candidates finishing together are read in index order, so
    the lower green one wins; the critic reads it and no other, and a block
    leaves no winner — the blocked candidate goes on to the next attempt,
    never a sibling."""

    plans = {1: False, 2: True, 3: True}
    clocks: list[float] = []
    read: list[int] = []

    async def launch(index: int, clock: float) -> selection.Candidate:
        clocks.append(clock)
        candidate = _candidate(index, green=plans[index])
        candidate.wall_s = 100.0

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return False

    chosen = asyncio.run(selection.select(3, launch, review, agent_clock=1200, seed="T-0001"))

    assert read == [2]
    assert chosen.first_green is not None and chosen.first_green.index == 2
    assert chosen.winner is None and chosen.chosen.index == 2
    assert chosen.critic_calls == 1 and chosen.outcome == "blocked"
    # The candidates start together (S-0124/D-4), so each is handed the seat's
    # own clock.
    assert clocks == [1200, 1200, 1200]

    record = selection.unit_record(
        chosen, task_id="T-0001", attempt=1, width=3, size="L", at="2026-11-05T00:00:00Z"
    )

    assert (record["kind"], record["schema_version"], record["selected"]) == ("unit.closed", 2, 2)
    assert [c["verdict"] for c in record["candidates"]] == [None, "blocked", None]


def test_first_green_with_no_critic_call_left_is_handed_on_unread() -> None:
    async def launch(index: int, _clock: float) -> selection.Candidate:
        return _candidate(index)

    async def review(_candidate: selection.Candidate) -> bool:
        raise AssertionError("no critic call is left")

    chosen = asyncio.run(
        selection.select(3, launch, review, agent_clock=60, seed="T-0001", critic_budget=0)
    )

    assert chosen.critic_calls == 0
    assert chosen.winner is None and chosen.chosen.index == 1
    assert chosen.outcome == "blocked"


def test_first_green_wins_with_no_critic() -> None:
    async def launch(index: int, _clock: float) -> selection.Candidate:
        return _candidate(index, green=index == 2)

    chosen = asyncio.run(selection.select(2, launch, None, agent_clock=60, seed="T-0001"))

    assert chosen.winner is not None and chosen.winner.index == 2
    assert chosen.outcome == "selected" and chosen.critic_calls == 0


@given(
    st.integers(min_value=1, max_value=3).flatmap(
        lambda width: st.tuples(
            st.permutations(range(1, width + 1)),
            st.sets(st.integers(min_value=1, max_value=width)),
            st.booleans(),
        )
    )
)
def test_first_green_over_finish_orders_and_green_sets(case) -> None:
    """S-0127/D-6 as a rule over inputs: whatever order the candidates finish
    in and whichever of them are green, the winner is the first green one to
    finish, every candidate still running then is cut, and the critic is
    asked once — about it — or never, when none is green."""

    order, greens, clean = case
    width = len(order)
    read: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        # A candidate's place in the finish order is how many turns of the
        # event loop it waits; ten turns apart leaves selection time to read
        # one finish and cut the rest before the next lands.
        for _ in range(order.index(index) * 10):
            await asyncio.sleep(0)

        return _candidate(index, green=index in greens)

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return clean

    chosen = asyncio.run(selection.select(width, launch, review, agent_clock=60, seed="T-0001"))
    first = next((index for index in order if index in greens), None)

    assert [c.index for c in chosen.candidates] == list(range(1, width + 1))

    if first is None:
        assert read == [] and chosen.critic_calls == 0
        assert chosen.winner is None and chosen.outcome == "red"
        assert not any(c.cut for c in chosen.candidates)
        return

    after = set(order[order.index(first) + 1 :])

    assert read == [first] and chosen.critic_calls == 1
    assert chosen.chosen.index == first
    assert (chosen.winner is not None) is clean
    assert {c.index for c in chosen.candidates if c.cut} == after


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


def test_unit_budget_stops_before_the_critic_call() -> None:
    """S-0124/D-7: the bound is checked again before the critic call. The
    batch it launched under finished at the bound, so the first green one is
    never read, and the round says the bound stopped a call it still needed;
    under it the call is made and its own spend counted."""

    read: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        candidate = _candidate(index)
        candidate.spend = 0.25
        candidate.critic_spend = 1.5

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        read.append(candidate.index)
        return True

    stopped = asyncio.run(
        selection.select(
            3, launch, review, agent_clock=60, seed="T-0001", spent=2.5, budget_usd=3.0
        )
    )

    assert read == []
    assert stopped.spent_usd == 3.25 and stopped.exhausted
    assert stopped.winner is None and stopped.outcome == "blocked"

    passed = asyncio.run(
        selection.select(3, launch, review, agent_clock=60, seed="T-0001", budget_usd=3.0)
    )

    assert read == [1]
    assert passed.spent_usd == 2.25 and not passed.exhausted
    assert passed.winner is not None and passed.winner.index == 1


def test_class_budget_is_the_class_s_dollars_and_minutes() -> None:
    """S-0127/D-9: S $1.50 and 45 minutes, M $3 and 90, L $6 and 180. M keeps
    the flat bound every class had, and is the bound `select` stops at when
    its caller names none; an L unit's $6 lets a batch launch past $3."""

    assert selection.UNIT_BUDGETS == {"S": (1.5, 45), "M": (3.0, 90), "L": (6.0, 180)}

    started: list[int] = []

    async def launch(index: int, _clock: float) -> selection.Candidate:
        started.append(index)
        return _candidate(index)

    at_m = asyncio.run(selection.select(1, launch, None, agent_clock=60, seed="T-0001", spent=3.0))

    assert at_m.exhausted and started == []

    large = selection.UNIT_BUDGETS["L"][0]
    under_l = asyncio.run(
        selection.select(
            1, launch, None, agent_clock=60, seed="T-0001", spent=3.0, budget_usd=large
        )
    )

    assert not under_l.exhausted and started == [1]
    assert under_l.winner is not None and under_l.winner.index == 1


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


def test_candidates_run_side_by_side_and_the_first_green_cuts_the_others() -> None:
    """Every launch starts before any finishes; the first green one's finish
    cuts the candidate still in its agent turn, which is cancelled and
    recorded red with its elapsed time (S-0127/D-6), and the candidates are
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

        candidate = _candidate(index, green=index == 3)
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
    assert cut.cut and cut.result is None and not cut.green
    assert not chosen.candidates[0].cut and not chosen.candidates[0].green
    assert chosen.winner is not None and chosen.winner.index == 3


def test_side_by_side_cut_lands_at_the_first_green_finish(monkeypatch) -> None:
    """S-0127/D-6: the others are cut when the first green one finishes, not
    at a deadline after it. Selection reads the test's own clock, so the cut
    candidate's wall time is the winner's: 0.05s into the round."""

    from types import SimpleNamespace

    now = [100.0]
    monkeypatch.setattr(selection, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def launch(index: int, _clock: float) -> selection.Candidate:
        if index == 2:
            await asyncio.Event().wait()

        now[0] += 0.05
        candidate = _candidate(index, green=True)
        candidate.wall_s = 0.05

        return candidate

    async def review(candidate: selection.Candidate) -> bool:
        return True

    chosen = asyncio.run(selection.select(2, launch, review, agent_clock=60, seed="T-0001"))

    cut = chosen.candidates[1]
    assert cut.cut and cut.result is None
    assert round(cut.wall_s, 6) == 0.05
    assert chosen.winner is not None and chosen.winner.index == 1


def test_side_by_side_cut_carries_its_siblings_seat() -> None:
    """S-0124/D-6, D-9: every candidate of an attempt runs on one seat, so the
    one the first green finish cut, which returned no record of its own, is
    recorded on that seat and not on none."""

    async def launch(index: int, _clock: float) -> selection.Candidate:
        if index == 2:
            await asyncio.sleep(5)

        candidate = _candidate(index, green=True)
        candidate.wall_s = 0.01
        candidate.tier, candidate.model = "builder", "model-a"

        return candidate

    chosen = asyncio.run(selection.select(2, launch, None, agent_clock=60, seed="T-0001"))
    record = selection.unit_record(
        chosen, task_id="T-0001", attempt=1, width=2, size="M", at="2026-11-07T00:00:00Z"
    )

    assert [c["cut"] for c in record["candidates"]] == [False, True]
    assert [(c["tier"], c["model"]) for c in record["candidates"]] == [("builder", "model-a")] * 2


def test_side_by_side_gate_passes_never_read_a_half_written_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """Sibling candidates resolve the task's one cached base manifest from
    gate passes that run together (S-0124/D-4), so the cache is replaced
    whole: a sibling reading it while this write is under way reads the cache
    as it was, never a truncated file."""

    from types import SimpleNamespace

    from torve.application.dispatch import Dispatch
    from torve.base import naming

    worktree = tmp_path / "wt"
    (worktree / ".torve").mkdir(parents=True)
    (worktree / ".torve" / "gates.yaml").write_text("gates: []\n", encoding="utf-8")

    for args in (
        ("init", "-q", "-b", "main"),
        ("add", "-A"),
        ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, check=True)

    root = tmp_path / "root"
    held = naming.base_manifest_file(root, "T-0001")
    held.parent.mkdir(parents=True)
    held.write_text("gates: []  # a sibling's\n", encoding="utf-8")
    seen: list[str] = []
    write_text = Path.write_text

    def interleaved(path: Path, data: str, *args, **kwargs) -> int:
        # A sibling's gate pass reads the cache once this write truncated its file.
        write_text(path, "", *args, **kwargs)
        seen.append(held.read_text(encoding="utf-8"))
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interleaved)
    run = SimpleNamespace(
        shadow=False,
        worktree=worktree,
        root=root,
        gates_base="main",
        config=SimpleNamespace(base=None),
        task=SimpleNamespace(id="T-0001"),
    )

    assert Dispatch.manifest_file.fget(run) == held
    assert seen == ["gates: []  # a sibling's\n"]
    assert held.read_text(encoding="utf-8") == "gates: []\n"


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
    """S-0124/D-7: a candidate the first green finish cut keeps what its
    finished turns spent — in the bound before the critic call, and in the
    unit's record."""

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


def test_unit_record_carries_tier_model_spend_and_the_revision_round():
    """S-0124/D-9: every candidate names the seat it ran on and what it spent,
    and a revision adds the round's own block — `kind`, `round`, `rereview`,
    `reraised`, `spend_usd` and `wall_s` — whether it is carried on the
    selection or handed in."""

    one = _candidate(1, green=True, patch="+a")
    one.tier, one.model, one.spend = "executor", "deepseek-v4.1-flash", 0.4
    one.reviewed, one.clean = True, False
    round_ = selection.Revision(
        kind="session",
        round=1,
        rereview="blocked",
        reraised=True,
        spend_usd=0.2,
        wall_s=12.5,
    )
    chosen = selection.Selection([one], one, spent_usd=0.9, revision=round_)

    record = selection.unit_record(
        chosen, task_id="T-0001", attempt=1, width=1, size="S", at="2026-11-05T00:00:00Z"
    )

    assert record["revision"] == {
        "kind": "session",
        "round": 1,
        "rereview": "blocked",
        "reraised": True,
        "spend_usd": 0.2,
        "wall_s": 12.5,
    }
    assert (
        record["candidates"][0]["tier"],
        record["candidates"][0]["model"],
        record["candidates"][0]["spend_usd"],
    ) == ("executor", "deepseek-v4.1-flash", 0.4)

    # A mapping reads the same, and a selection with no revision carries none.
    plain = selection.unit_record(
        chosen,
        task_id="T-0001",
        attempt=2,
        width=1,
        size="S",
        at="2026-11-05T00:01:00Z",
        revision={"kind": "fresh", "round": 2, "rereview": "clean"},
    )
    assert plain["revision"] == {
        "kind": "fresh",
        "round": 2,
        "rereview": "clean",
        "reraised": False,
        "spend_usd": None,
        "wall_s": None,
    }

    none = selection.unit_record(
        selection.Selection([one], one), task_id="T-0001", attempt=3, width=1, size="S", at="x"
    )
    assert "revision" not in none


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
    # Candidate 2 goes green only once candidate 1's pass is under way, so the
    # cut its finish makes leaves that shielded pass to finish (S-0124/D-4).
    judging = threading.Event()

    def gate_pass(run, _state):
        passes.append(run.candidate or 1)

        if run.candidate <= 1:
            # What candidate 1 left in the task's worktree, which the winner's
            # tree replaces.
            (run.worktree / "own.txt").write_text("1\n", encoding="utf-8")
            judging.set()
            return 1, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

        judging.wait(30)
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "run_gate_pass", gate_pass)
    critic = ScriptedAgent([AgentResult(exit_code=0, output=answer())])
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic"))
    deps = deps_for(critic)

    state = run_task(repo.root, task_for(repo), config, deps)

    assert state.state is TaskState.READY
    # The candidates run together (S-0124/D-4); both were judged, and the
    # green one won.
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
    """A turn that halted is judged by nobody: no gate pass runs and no
    second turn is asked for (S-0127/D-6)."""

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
