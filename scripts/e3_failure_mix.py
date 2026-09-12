"""E3 — baseline the failure mix per seat, from records already written.

A query over .torve/telemetry.jsonl. Changes nothing. Decides which branch of
Stage 1 runs: every later arm is judged per seat, not against a fleet average.

Usage:  python3 e3_failure_mix.py [path/to/telemetry.jsonl]
        python3 e3_failure_mix.py --selfcheck
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The three gates whose check is a pure function of the tree: a path match, a
# verb the prompt already asks the agent to run, and a regex. An attempt that
# failed only on these is the class a hook answers in milliseconds (E16).
IN_SESSION = {"scope", "decisions-reported", "user-facing-text"}


def rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def seat(agent: dict) -> str:
    """A seat is the tier plus the image it was pointed at.

    Keyed this way because one tier name has been pointed at different images
    over the record — `executor` was claude, then dsh, then claude again.
    """
    tier = agent.get("tier") or "?"
    image = (agent.get("image") or "no-image").split(":")[0]
    return f"{tier} / {image}"


def blocking_failures(row: dict) -> list[str]:
    """Names of blocking gates that did not pass.

    Two schema generations: older rows carry `blocking: bool`, newer ones carry
    `state: blocking|shadow`.
    """
    names = []
    for r in row.get("results") or []:
        is_blocking = r.get("state") == "blocking" if "state" in r else bool(r.get("blocking"))
        if is_blocking and r.get("outcome") != "pass":
            names.append(r["name"])
    return names


def shadow_failures(row: dict) -> list[str]:
    names = []
    for r in row.get("results") or []:
        is_blocking = r.get("state") == "blocking" if "state" in r else bool(r.get("blocking"))
        if not is_blocking and r.get("outcome") != "pass":
            names.append(r["name"])
    return names


def analyse(data: list[dict], since: str = "") -> dict:
    # A `fake` adapter never ran a model: its attempts are fixture traffic and
    # counting them makes a seat look catastrophic for free.
    attempts = [
        r
        for r in data
        if not r.get("kind")
        and r.get("agent")
        and r["agent"].get("adapter") != "fake"
        and (not since or r.get("at", "") >= since)
    ]

    seats: dict[str, dict] = defaultdict(
        lambda: {
            "attempts": 0,
            "green": 0,
            "red": 0,
            "no_gates": 0,
            "avoidable": 0,
            "gate_mix": Counter(),
            "shadow_mix": Counter(),
            "walls": [],
            "costs": [],
            "tasks": set(),
            "landed_tasks": set(),
            "models": Counter(),
            "brokered": 0,
            "requests": 0,
        }
    )

    for row in attempts:
        agent = row["agent"]
        s = seats[seat(agent)]
        s["attempts"] += 1
        s["tasks"].add(row.get("task_id"))
        s["models"][agent.get("model") or agent.get("model_version") or "?"] += 1

        if (agent.get("broker") or {}).get("adapter") not in (None, "none"):
            s["brokered"] += 1
            s["requests"] += (agent.get("broker") or {}).get("requests") or 0

        wall = agent.get("wall_time_s")
        if wall:
            s["walls"].append(wall)
        cost = agent.get("cost_usd")
        if cost:
            s["costs"].append(cost)

        failed = blocking_failures(row)
        for name in failed:
            s["gate_mix"][name] += 1
        for name in shadow_failures(row):
            s["shadow_mix"][name] += 1

        if not (row.get("results") or []):
            # The agent never reached the battery — a crash, a clock, a refusal.
            s["no_gates"] += 1
        elif failed:
            s["red"] += 1
            if set(failed) <= IN_SESSION:
                s["avoidable"] += 1
        else:
            s["green"] += 1
            s["landed_tasks"].add(row.get("task_id"))

    reviews = [
        r for r in data if r.get("kind") == "review" and (not since or r.get("at", "") >= since)
    ]
    review_by_seat: dict[str, dict] = defaultdict(lambda: {"n": 0, "unparseable": 0, "findings": 0})
    for row in reviews:
        agent = row.get("agent") or {}
        rs = review_by_seat[seat(agent)]
        rs["n"] += 1
        rs["unparseable"] += 1 if row.get("unparseable") else 0
        rs["findings"] += len(row.get("findings") or [])

    return {"seats": dict(seats), "reviews": dict(review_by_seat), "attempts": len(attempts)}


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "—"


def report(res: dict) -> None:
    seats = res["seats"]
    order = sorted(seats, key=lambda k: -seats[k]["attempts"])

    print(f"\nE3 · failure mix per seat — {res['attempts']} attempts carrying an agent block\n")
    head = f"{'seat':<34} {'att':>4} {'green':>6} {'red':>5} {'nogate':>7} {'avoid':>6} {'med s':>7} {'$/att':>7}"
    print(head)
    print("-" * len(head))
    for key in order:
        s = seats[key]
        med = f"{statistics.median(s['walls']):.0f}" if s["walls"] else "—"
        cost = f"{sum(s['costs']) / len(s['costs']):.2f}" if s["costs"] else "—"
        print(
            f"{key:<34} {s['attempts']:>4} {s['green']:>6} {s['red']:>5} "
            f"{s['no_gates']:>7} {s['avoidable']:>6} {med:>7} {cost:>7}"
        )

    print("\n\nRed rate, avoidable share, and attempts per landing\n")
    head2 = f"{'seat':<34} {'red%':>6} {'avoid/red':>10} {'tasks':>6} {'landed':>7} {'att/land':>9}"
    print(head2)
    print("-" * len(head2))
    for key in order:
        s = seats[key]
        judged = s["green"] + s["red"]
        apl = f"{s['attempts'] / len(s['landed_tasks']):.2f}" if s["landed_tasks"] else "—"
        print(
            f"{key:<34} {pct(s['red'], judged):>6} {pct(s['avoidable'], s['red']):>10} "
            f"{len(s['tasks']):>6} {len(s['landed_tasks']):>7} {apl:>9}"
        )

    print("\n\nFailing gates, per seat\n")
    for key in order:
        s = seats[key]
        if not s["gate_mix"] and not s["shadow_mix"]:
            continue
        print(f"  {key}   ({s['attempts']} attempts, models: {', '.join(sorted(s['models']))})")
        if s["gate_mix"]:
            line = "  ".join(f"{n} {c}" for n, c in s["gate_mix"].most_common())
            print(f"    blocking  {line}")
        if s["shadow_mix"]:
            line = "  ".join(f"{n} {c}" for n, c in s["shadow_mix"].most_common())
            print(f"    shadow    {line}")
        print()

    if res["reviews"]:
        print("\nReview documents, per seat\n")
        head3 = f"{'seat':<34} {'reviews':>8} {'unparse':>8} {'findings':>9}"
        print(head3)
        print("-" * len(head3))
        for key, r in sorted(res["reviews"].items(), key=lambda kv: -kv[1]["n"]):
            print(f"{key:<34} {r['n']:>8} {r['unparseable']:>8} {r['findings']:>9}")


def selfcheck() -> None:
    """The two things that would silently skew the answer: the schema split on
    blocking-vs-state, and the subset test that defines the avoidable class."""
    old = {"results": [{"name": "scope", "outcome": "fail", "blocking": True}]}
    new = {"results": [{"name": "scope", "outcome": "fail", "state": "blocking"}]}
    assert blocking_failures(old) == ["scope"], "old schema: `blocking: true` must count"
    assert blocking_failures(new) == ["scope"], "new schema: `state: blocking` must count"

    shadowed = {"results": [{"name": "coverage-delta", "outcome": "fail", "state": "shadow"}]}
    assert blocking_failures(shadowed) == [], "a shadow gate must never redden an attempt"
    assert shadow_failures(shadowed) == ["coverage-delta"]

    mixed = {
        "results": [
            {"name": "scope", "outcome": "fail", "state": "blocking"},
            {"name": "acceptance", "outcome": "fail", "state": "blocking"},
        ]
    }
    assert not set(blocking_failures(mixed)) <= IN_SESSION, (
        "acceptance is not answerable in-session"
    )

    only = {"results": [{"name": "user-facing-text", "outcome": "fail", "state": "blocking"}]}
    assert set(blocking_failures(only)) <= IN_SESSION

    assert (
        seat({"tier": "executor", "image": "claude-sandbox:2.1.252"}) == "executor / claude-sandbox"
    )
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck()
    else:
        arg = [a for a in sys.argv[1:] if not a.startswith("-")]
        path = Path(arg[0]) if arg else Path(".torve/telemetry.jsonl")
        since = ""
        for a in sys.argv[1:]:
            if a.startswith("--since="):
                since = a.split("=", 1)[1]
        if since:
            print(f"\n(window: at >= {since})")
        report(analyse(rows(path), since))
