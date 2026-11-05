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

The candidates run one after another, not side by side: each holds the
task's run state, its sandbox and its judge checkout in turn, and the
straggler cut is the clock a later candidate gets. The orchestration here
takes its candidates and its critic as callables, so the simulation drives
the same code the runner does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from torve.base.globs import matches
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

# What an S unit's files may be: documentation and configuration.
_LIGHT = ("*.md", "*.rst", "*.txt", "*.yaml", "*.yml", "*.toml", "*.json", "docs/**")


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
class Selection:
    """What one round of candidates came to: every candidate, the ones the
    critic read in order, and the one the unit goes on with — the first clean
    one, else the best ranked, else the first that ran."""

    candidates: list[Candidate]
    ranked: list[Candidate] = field(default_factory=list)
    winner: Candidate | None = None
    critic_calls: int = 0

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


Launch = Callable[[int, float], Awaitable[Candidate]]
Review = Callable[[Candidate], Awaitable[bool]]


async def select(
    width: int,
    launch: Launch,
    review: Review | None,
    *,
    agent_clock: float,
    seed: str,
    critic_budget: int = CRITIC_CALLS,
    stop: Callable[[], bool] = lambda: False,
) -> Selection:
    """Run *width* candidates one after another, then ask *review* — the
    critic, or None when no critic judges this unit — about the green ones in
    rank order until one is clean or *critic_budget* calls are spent. *stop*
    ends the round early, when the run itself has escalated."""

    candidates: list[Candidate] = []
    first_green: float | None = None

    for index in range(1, width + 1):
        clock = agent_clock if first_green is None else straggler_clock(first_green, agent_clock)
        candidate = await launch(index, clock)
        candidates.append(candidate)

        if stop():
            return Selection(candidates)

        if candidate.green and first_green is None:
            first_green = candidate.wall_s

    # A protected-test edit is a red blocking gate, so a green one made none.
    pool = collapse([c for c in candidates if c.green])
    selection = Selection(candidates, prerank(pool, seed))

    if review is None:
        selection.winner = selection.ranked[0] if selection.ranked else None
        return selection

    for candidate in selection.ranked:
        if selection.critic_calls >= critic_budget:
            break

        candidate.reviewed = True
        candidate.clean = await review(candidate)
        selection.critic_calls += 1

        if candidate.clean:
            selection.winner = candidate
            break

    return selection


# ....................... #


def unit_record(
    selection: Selection, *, task_id: str, attempt: int, width: int, size: str, at: str
) -> dict[str, Any]:
    """The `unit.closed` record (S-0122/D-16): the unit's class and width,
    every candidate with its wall time, cost, gate outcome, rank and the
    critic's verdict, the one selected and the outcome."""

    return {
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
                "wall_time_s": round(c.wall_s, 3),
                "cost_usd": getattr(c.result, "cost_usd", None),
                "green": c.green,
                "followed_up": c.followed_up,
                "rank": c.rank or None,
                "collapsed_into": c.collapsed_into or None,
                "verdict": ("clean" if c.clean else "blocked") if c.reviewed else None,
            }
            for c in selection.candidates
        ],
        "selected": selection.chosen.index,
        "critic_calls": selection.critic_calls,
        "outcome": selection.outcome,
    }
