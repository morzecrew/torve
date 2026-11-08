"""Several candidates for one unit, and the one that lands (S-0122/D-7, D-8).

A unit's class sets its width: S one candidate, M two, L three, never more.
Each candidate works in its own worktree from the one base the unit was cut
from and is judged by the same battery; a red candidate whose conviction is
mechanical — three or fewer convicted tests, or mypy alone — gets one
follow-up turn in its own session first. A straggler is cut at 1.5 times the
first green candidate's wall time, the extra time capped at fifteen minutes.
The green candidates that edited no protected test collapse where their
normalised diffs are the same, a deterministic pre-rank orders the rest, and
the critic reviews them one per call in that order (S-0122/D-6); the first it
passes is the one that lands.

The candidates run side by side (S-0124/D-4): each `launch` is started as a
task, the first green one's finish sets the straggler deadline, and a
candidate still in its agent turn at that deadline is cancelled and recorded
red with the spend its finished turns reported. The orchestration here takes
its candidates and its critic as callables, so the simulation drives the same
code the runner does.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from torve.base.globs import matches
from torve.config.providers import Price
from torve.domain.attempt import GateResult
from torve.domain.task import Task

# ----------------------- #

WIDTHS = {"S": 1, "M": 2, "L": 3}

# The straggler cut (S-0122/D-7): a later candidate's clock is this many times
# the first green one's wall time, the time beyond it capped.
STRAGGLER = 1.5
STRAGGLER_CAP_S = 15 * 60

# What makes a red candidate's conviction mechanical (S-0122/D-8).
REPAIRABLE_TESTS = 3
REPAIRABLE_GATES = frozenset({"suite", "mypy"})

# A unit stops after this many critic calls (S-0122/D-8).
CRITIC_CALLS = 5

# What a unit may spend, notionally, across its attempts (S-0122/D-8,
# S-0123/D-1), and the clock a critic-mode task gets when its contract names
# none (S-0123/D-2).
UNIT_BUDGET_USD = 3.0
UNIT_MINUTES = 90

# Every escalation detail the unit's cap writes starts with this, and
# `runner._exhausted` and `reaper.clear_escalated` read it back: a spent cap
# is budget exhaustion, so its tree is checkpointed and resumed (S-0026/D-8).
UNIT_BUDGET_MARKER = "unit budget exhausted"

# What an S unit's files may be: documentation and configuration.
_LIGHT = ("*.md", "*.rst", "*.txt", "*.yaml", "*.yml", "*.toml", "*.json", "docs/**")


# ....................... #


def notional_spend(
    agent: Mapping[str, Any], list_prices: Mapping[tuple[str, str], Price]
) -> float | None:
    """What one candidate or critic call costs the unit (S-0123/D-1): the cost
    its block reports, else its token counts at its model's list price — the
    ledger's estimate — else nothing, which adds nothing.

    A subscription seat reports no cost and may carry no list price; that is
    unreported, not zero, and the cap reads only what can be priced. A
    candidate's block is the priced one the record is built from, so a
    roster-priced seat's cost is torve's arithmetic rather than the adapter's
    claim."""

    cost = agent.get("cost_usd")

    if isinstance(cost, int | float) and not isinstance(cost, bool):
        return float(cost)

    price = list_prices.get((str(agent.get("provider") or ""), str(agent.get("model") or "")))

    return None if price is None else price.cost(agent)


# ....................... #


def unit_class(task: Task, tree: Path, *, revision: bool = False, escalated: bool = False) -> str:
    """The unit's class (S-0122/D-7). S: a revision, or files that are all
    documentation and configuration. L: a unit that escalated once, three or
    more source globs, or a module the tree does not hold yet. M otherwise."""

    files = task.scope.files

    if revision or (files and all(matches(glob, _LIGHT) for glob in files)):
        return "S"

    source = [glob for glob in files if not matches(glob, _LIGHT)]
    new = [glob for glob in source if "*" not in glob and not (tree / glob).exists()]

    if escalated or len(source) >= 3 or new:
        return "L"

    return "M"


# ....................... #


def straggler_clock(first_green_s: float, agent_clock: float) -> float:
    """The clock a candidate after the first green one gets."""

    extra = min((STRAGGLER - 1) * first_green_s, STRAGGLER_CAP_S)

    return min(agent_clock, first_green_s + extra)


# ....................... #


def repairable(results: list[GateResult]) -> bool:
    """Whether a red battery's convictions are mechanical (S-0122/D-8): every
    red blocking gate is the suite or mypy, and the suite convicted three
    tests or fewer."""

    from torve.gates.suite import settled_ids

    red = [r for r in results if r.state == "blocking" and r.outcome not in ("pass", "bypassed")]

    if not red or any(r.name not in REPAIRABLE_GATES for r in red):
        return False

    suites = [r for r in red if r.name == "suite"]

    return all(len(settled_ids(r.output)[0]) <= REPAIRABLE_TESTS for r in suites)


# ....................... #


def normalised(patch: str) -> str:
    """A diff's files and changed lines alone, so two candidates that wrote
    the same change to the same files compare equal whatever their hunk
    headers, blob ids and trailing blanks say."""

    return "\n".join(
        line.rstrip() for line in patch.splitlines() if line.startswith(("diff --git", "+", "-"))
    )


# ....................... #


@dataclass
class Candidate:
    """One candidate as selection reads it. `run` is whatever the caller needs
    to land it; selection never looks inside."""

    index: int
    result: Any
    green: bool = False
    patch: str = ""
    wall_s: float = 0.0
    followed_up: bool = False
    # The pre-rank's inputs (S-0122/D-7), lowest first.
    failing: int = 0
    flags: int = 0
    lines: int = 0
    widened: int = 0
    rank: int = 0
    collapsed_into: int = 0
    reviewed: bool = False
    clean: bool = False
    verdict: Any = None
    run: Any = None
    # Whether the straggler deadline cut this candidate's turn (S-0124/D-4):
    # it is recorded red with the spend its finished turns reported.
    cut: bool = False
    # The seat the candidate ran on (S-0124/D-9), recorded by whoever launched
    # it — selection only carries the names to the unit record.
    tier: str = ""
    model: str = ""
    # What this candidate and the critic call that read it cost the unit
    # notionally (S-0123/D-1); None when nothing priced them.
    spend: float | None = None
    critic_spend: float | None = None


# ....................... #


def collapse(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate per distinct normalised diff, the earliest kept; the rest
    name the one they collapsed into."""

    kept: dict[str, Candidate] = {}

    for candidate in candidates:
        key = normalised(candidate.patch)

        if key in kept:
            candidate.collapsed_into = kept[key].index
        else:
            kept[key] = candidate

    return list(kept.values())


# ....................... #


def prerank(candidates: list[Candidate], seed: str) -> list[Candidate]:
    """The deterministic order the critic reads candidates in (S-0122/D-7):
    fewest failing checks, then flags, then source lines changed, then
    contingent paths widened, then a draw seeded by the unit."""

    def draw(candidate: Candidate) -> str:
        return hashlib.sha256(f"{seed}:{candidate.index}".encode()).hexdigest()

    ordered = sorted(candidates, key=lambda c: (c.failing, c.flags, c.lines, c.widened, draw(c)))

    for rank, candidate in enumerate(ordered, start=1):
        candidate.rank = rank

    return ordered


# ....................... #


@dataclass
class Revision:
    """What one revision round was (S-0124/D-9): a `fresh` attempt on the
    revision seat, or a `session` turn continuing the selected candidate.

    `rereview` is `clean`, `blocked`, `stopped` when the unit's budget
    ended the round before its re-review, or `failed` when the turn itself
    timed out, failed or was refused, or its gates were red, so no critic read
    it; `reraised` says the critic named the
    same finding again; `spend_usd` and `wall_s` are the round's own — for a
    session turn, the cumulative session cost after the turn minus before it,
    so a gate-repair turn earlier in the same session is not counted."""

    kind: str
    round: int
    rereview: str
    reraised: bool = False
    spend_usd: float | None = None
    wall_s: float | None = None


# ....................... #


@dataclass
class Selection:
    """What one round of candidates came to: every candidate, the ones the
    critic read in order, and the one the unit goes on with — the first clean
    one, else the best ranked, else the first that ran."""

    candidates: list[Candidate]
    ranked: list[Candidate] = field(default_factory=list)
    winner: Candidate | None = None
    critic_calls: int = 0
    # The unit's notional spend after this round (S-0123/D-1), and whether the
    # bound stopped a candidate or critic call the unit still needed.
    spent_usd: float = 0.0
    exhausted: bool = False
    # The revision round this selection was, when it was one (S-0124/D-9).
    revision: Revision | None = None

    @property
    def chosen(self) -> Candidate:
        if self.winner is not None:
            return self.winner

        return self.ranked[0] if self.ranked else self.candidates[0]

    @property
    def outcome(self) -> str:
        if self.winner is not None:
            return "selected"

        return "blocked" if self.ranked else "red"


# ....................... #


Launch = Callable[[int, float], Coroutine[Any, Any, Candidate]]
Review = Callable[[Candidate], Awaitable[bool]]
# What a cancelled candidate's finished turns cost the unit, by index.
CancelledSpend = Callable[[int], float | None]


async def select(
    width: int,
    launch: Launch,
    review: Review | None,
    *,
    agent_clock: float,
    seed: str,
    critic_budget: int = CRITIC_CALLS,
    stop: Callable[[], bool] = lambda: False,
    spent: float = 0.0,
    budget_usd: float = UNIT_BUDGET_USD,
    cancelled_spend: CancelledSpend | None = None,
) -> Selection:
    """Run *width* candidates side by side (S-0124/D-4), then ask *review* — the
    critic, or None when no critic judges this unit — about the green ones in
    rank order until one is clean, *critic_budget* calls are spent, or the
    unit's notional spend reaches *budget_usd*. *spent* is what its earlier
    attempts already spent; each candidate's and critic call's own spend is
    added as it is known (S-0123/D-1), and a bound that stops a call the unit
    still needed leaves *exhausted* set. The bound is checked once before the
    batch launches and before each critic call (S-0124/D-7), never between
    candidates that already started together. *stop* ends the round early, when
    the run itself has escalated; the candidates still in their turns are
    cancelled. A candidate cut at the straggler deadline is recorded red, and
    *cancelled_spend* names what the turns it finished cost."""

    started = time.monotonic()
    candidates: list[Candidate] = []
    total = spent

    # The bound is a stop before new work (S-0124/D-7): one check before the
    # batch, so the batch may finish above it by the spend it launched with.
    if total >= budget_usd:
        return Selection([], spent_usd=total, exhausted=True)

    tasks: dict[asyncio.Task[Candidate], int] = {
        asyncio.create_task(launch(index, agent_clock)): index for index in range(1, width + 1)
    }

    pending = set(tasks)
    deadline: float | None = None
    first_green: float | None = None

    def record(candidate: Candidate) -> None:
        nonlocal total, first_green, deadline

        candidates.append(candidate)

        if candidate.spend is not None:
            total += candidate.spend

        if candidate.green and first_green is None:
            first_green = candidate.wall_s
            # The straggler cut runs from the first green one's finish: the
            # others get what its clock leaves beyond the time it took
            # (S-0122/D-7, S-0124/D-4).
            deadline = time.monotonic() + max(
                0.0, straggler_clock(first_green, agent_clock) - first_green
            )

    async def cut(index: int) -> Candidate:
        """The record of a candidate cancelled in its agent turn: red, with
        its elapsed time, what its finished turns spent and its seat."""

        spend = cancelled_spend(index) if cancelled_spend is not None else None
        # Every candidate of an attempt runs on one seat (S-0124/D-6), so the
        # cut one, which returned no record of its own, carries a sibling's.
        tier, model = next(((c.tier, c.model) for c in candidates if c.tier), ("", ""))

        return Candidate(
            index=index,
            result=None,
            wall_s=time.monotonic() - started,
            spend=spend,
            cut=True,
            tier=tier,
            model=model,
        )

    async def collect(finished: list[asyncio.Task[Candidate]]) -> list[Candidate]:
        """Settle *finished* tasks, turning every cancellation into its red
        record; an exception from a launch is raised after the rest settle."""

        outcomes = await asyncio.gather(*finished, return_exceptions=True)
        collected: list[Candidate] = []

        for task, outcome in zip(finished, outcomes, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                collected.append(await cut(tasks[task]))

            elif isinstance(outcome, BaseException):
                raise outcome

            else:
                collected.append(outcome)

        return collected

    try:
        while pending:
            if stop():
                # The run escalated under one of the candidates: the others'
                # turns are cut (S-0124/D-4).
                waiting = list(pending)

                for task in waiting:
                    task.cancel()

                for candidate in await collect(waiting):
                    record(candidate)

                pending = set()
                break

            timeout = None if deadline is None else max(0.0, deadline - time.monotonic())

            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )

            for task in sorted(done, key=lambda task: tasks[task]):
                try:
                    record(task.result())

                except asyncio.CancelledError:
                    record(await cut(tasks[task]))

            if not done:
                # The straggler deadline passed: cut the candidates still in
                # their agent turn. A gate pass already running is shielded in
                # the runner and finishes (S-0124/D-4).
                waiting = list(pending)

                for task in waiting:
                    task.cancel()

                for candidate in await collect(waiting):
                    record(candidate)

                pending = set()

    finally:
        # A cancelled `select` must not leave its candidates running.
        if pending:
            for task in pending:
                task.cancel()

            await asyncio.gather(*pending, return_exceptions=True)

    # The run escalated under its candidates: the others' turns are already
    # cancelled, and the round ends here — nothing is collapsed, ranked or
    # shown to the critic (S-0124/D-4). The loop stops on the escalation.
    if stop():
        candidates.sort(key=lambda candidate: candidate.index)

        return Selection(candidates, spent_usd=total)

    # Completion order is a scheduling fact, not the unit's order: candidates
    # are sorted by index before collapse and pre-rank (S-0124/D-4).
    candidates.sort(key=lambda candidate: candidate.index)

    # A protected-test edit is a red blocking gate, so a green one made none.
    pool = collapse([c for c in candidates if c.green])
    selection = Selection(candidates, prerank(pool, seed), spent_usd=total)

    if review is None:
        selection.winner = selection.ranked[0] if selection.ranked else None
        return selection

    for candidate in selection.ranked:
        if selection.critic_calls >= critic_budget:
            break

        if total >= budget_usd:
            selection.exhausted = True
            break

        candidate.reviewed = True
        candidate.clean = await review(candidate)
        selection.critic_calls += 1

        if candidate.critic_spend is not None:
            total += candidate.critic_spend

        selection.spent_usd = total

        if candidate.clean:
            selection.winner = candidate
            break

    return selection


# ....................... #


def _revision_block(revision: Any) -> dict[str, Any] | None:
    """The revision block a `unit.closed` record carries (S-0124/D-9). A
    `Revision`, a mapping and None all read the same, so a caller building
    the record from a plain dict and one carrying the dataclass agree."""

    if revision is None:
        return None

    if isinstance(revision, Mapping):
        values: Mapping[str, Any] = revision
    else:
        values = {
            name: getattr(revision, name, None)
            for name in ("kind", "round", "rereview", "reraised", "spend_usd", "wall_s")
        }

    kind = values.get("kind")
    rereview = values.get("rereview")

    if kind is None or rereview is None:
        return None

    return {
        "kind": str(kind),
        "round": int(values.get("round") or 1),
        "rereview": str(rereview),
        "reraised": bool(values.get("reraised", False)),
        "spend_usd": values.get("spend_usd"),
        "wall_s": values.get("wall_s"),
    }


# ....................... #


def unit_record(
    selection: Selection,
    *,
    task_id: str,
    attempt: int,
    width: int,
    size: str,
    at: str,
    revision: Any = None,
) -> dict[str, Any]:
    """The `unit.closed` record (S-0122/D-16, S-0124/D-9): the unit's class
    and width, every candidate with its tier, model, wall time, cost, gate
    outcome, rank and the critic's verdict, the one selected, the outcome and
    — when this was a revision — the round it spent."""

    block = _revision_block(selection.revision if revision is None else revision)

    record: dict[str, Any] = {
        "kind": "unit.closed",
        "schema_version": 2,
        "at": at,
        "task_id": task_id,
        "attempt": attempt,
        "class": size,
        "width": width,
        "candidates": [
            {
                "index": c.index,
                "tier": c.tier,
                "model": c.model,
                "wall_time_s": round(c.wall_s, 3),
                "cost_usd": getattr(c.result, "cost_usd", None),
                # What this candidate cost the unit notionally, finished turns
                # included when it was cut at the straggler deadline
                # (S-0123/D-1, S-0124/D-7).
                "spend_usd": c.spend,
                "green": c.green,
                "followed_up": c.followed_up,
                "cut": c.cut,
                "rank": c.rank or None,
                "collapsed_into": c.collapsed_into or None,
                "verdict": ("clean" if c.clean else "blocked") if c.reviewed else None,
            }
            for c in selection.candidates
        ],
        # No candidate when the cap stopped the unit before its launch: the
        # attempt still closes, and names no selection (S-0124/D-9).
        "selected": selection.chosen.index if selection.candidates else None,
        "critic_calls": selection.critic_calls,
        "outcome": selection.outcome,
    }

    if block is not None:
        record["revision"] = block

    return record
