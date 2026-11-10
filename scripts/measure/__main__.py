"""The harness's verbs; the package docstring says what each does."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import yaml

from . import paired, pass_hat

# The paths a blinded diff leaves out: an engine's own records name the arm.
UNBLINDING = [":(exclude).torve", ":(exclude).wt"]


def git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True
    ).stdout


def last_json(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def telemetry_cost(tree: Path) -> float | None:
    path = tree / ".torve" / "telemetry.jsonl"
    if not path.exists():
        return None
    costs = [
        (json.loads(line).get("agent") or {}).get("cost_usd")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    known = [c for c in costs if c is not None]
    return sum(known) if known else None


def results(out: Path) -> list[dict]:
    path = out / "results.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def trial(set_dir: Path, out: Path, task: str, arm: str, spec: dict, number: int) -> dict:
    task_dir = set_dir / "tasks" / task
    meta = yaml.safe_load((task_dir / "task.yaml").read_text())
    tree = out / "trees" / f"{task}.{arm}.{number}"
    shutil.rmtree(tree, ignore_errors=True)
    tree.parent.mkdir(parents=True, exist_ok=True)

    # Only the base's history: a clone would carry the tags and every later
    # commit, and a tag past the base holds the very change the arm is asked for.
    tree.mkdir()
    git(tree, "init", "-q")
    git(tree, "fetch", "-q", "--no-tags", str(meta["repo"]), meta["base"])
    git(tree, "checkout", "-q", "-B", meta.get("branch", "main"), "FETCH_HEAD")

    env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG") if name in os.environ}
    # Only what the host sets: an empty `no_proxy` is not an unset one, and
    # Python lets it override the host's `NO_PROXY`.
    env |= {name: os.environ[name] for name in spec.get("env", []) if name in os.environ}
    env |= {
        "MEASURE_TASK": task,
        "MEASURE_ARM": arm,
        "MEASURE_TRIAL": str(number),
        "MEASURE_BASE": meta["base"],
        "MEASURE_DOCUMENT": meta.get("document", ""),
        "MEASURE_PROMPT": str(task_dir / "prompt.md"),
    }
    log = out / "logs" / f"{task}.{arm}.{number}.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    try:
        done = subprocess.run(
            ["bash", "-c", spec["run"]],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            timeout=spec.get("timeout", 5400),
        )
        exit_code, stdout, stderr, timed_out = done.returncode, done.stdout, done.stderr, False
    except subprocess.TimeoutExpired as expired:
        exit_code, timed_out = None, True
        stdout = (expired.stdout or b"").decode(errors="replace")
        stderr = (expired.stderr or b"").decode(errors="replace")
    wall_s = time.monotonic() - start
    log.write_text(stdout + "\n--- stderr ---\n" + stderr)

    reported = last_json(stdout)
    cost = reported.get("total_cost_usd", reported.get("cost_usd"))

    git(tree, "add", "-A")
    diff = out / "diffs" / f"{task}.{arm}.{number}.diff"
    diff.parent.mkdir(parents=True, exist_ok=True)
    diff.write_text(git(tree, "diff", "--cached", meta["base"], "--", ".", *UNBLINDING))

    # The check arrives only now, so no arm can read it.
    shutil.copytree(task_dir / "check", tree, dirs_exist_ok=True)
    try:
        check = subprocess.run(
            ["bash", "-c", meta["check"]],
            cwd=tree,
            env={k: v for k, v in env.items() if k in ("PATH", "HOME")},
            capture_output=True,
            timeout=meta.get("check_timeout", 900),
        )
        passed = check.returncode == 0
    except subprocess.TimeoutExpired:
        passed = False

    return {
        "task": task,
        "arm": arm,
        "trial": number,
        "passed": passed,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "wall_s": round(wall_s, 3),
        "cost_usd": cost if cost is not None else telemetry_cost(tree),
        "owner_minutes": reported.get("owner_minutes"),
        "diff": str(diff.relative_to(out)),
    }


def run(set_dir: Path, out: Path, arms: list[str], tasks: list[str], trials: int) -> None:
    specs = yaml.safe_load((set_dir / "arms.yaml").read_text())["arms"]
    arms = arms or sorted(specs)
    tasks = tasks or sorted(p.name for p in (set_dir / "tasks").iterdir() if p.is_dir())
    done = {(r["task"], r["arm"], r["trial"]) for r in results(out)}
    out.mkdir(parents=True, exist_ok=True)

    for task in tasks:
        for arm in arms:
            for number in range(1, trials + 1):
                if (task, arm, number) in done:
                    continue
                row = trial(set_dir, out, task, arm, specs[arm], number)
                with (out / "results.jsonl").open("a") as sink:
                    sink.write(json.dumps(row) + "\n")
                print(f"{task} {arm} #{number}: {'pass' if row['passed'] else 'fail'}")


def pairs(out: Path, seed: int) -> None:
    first = {(r["task"], r["arm"]): r for r in results(out) if r["trial"] == 1}
    by_task: dict[str, list[str]] = defaultdict(list)
    for task, arm in sorted(first):
        by_task[task].append(arm)

    rng = random.Random(seed)
    target = out / "pairs"
    target.mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {}
    for task, arms in by_task.items():
        for n, (a, b) in enumerate(combinations(arms, 2), 1):
            if rng.random() < 0.5:
                a, b = b, a
            ident = f"{task}.{n}"
            key[ident] = {"task": task, "A": a, "B": b}
            body = [f"# {task}\n", "Which change is better? Answer A, B or tie.\n"]
            for side, arm in (("A", a), ("B", b)):
                diff = (out / first[(task, arm)]["diff"]).read_text()
                body += [f"## {side}\n", "```diff", diff, "```\n"]
            (target / f"{ident}.md").write_text("\n".join(body))
    (target / "key.json").write_text(json.dumps(key, indent=2))
    print(f"{len(key)} pair(s) under {target}")


def report(out: Path) -> None:
    rows = results(out)
    runs: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for r in rows:
        runs[(r["arm"], r["task"])].append(r["passed"])
    arms = sorted({arm for arm, _ in runs})
    p1 = {key: sum(v) / len(v) for key, v in runs.items()}

    print(
        f"{'arm':<16} {'tasks':>5} {'pass@1':>7} {'pass^3':>7} {'cost $':>8} {'wall s':>8} {'owner m':>8}"
    )
    for arm in arms:
        mine = [r for r in rows if r["arm"] == arm]
        scores = [p1[key] for key in p1 if key[0] == arm]
        hats = [
            pass_hat(sum(v), len(v), 3) for key, v in runs.items() if key[0] == arm and len(v) >= 3
        ]
        costs = [r["cost_usd"] for r in mine if r["cost_usd"] is not None]
        owner = [r["owner_minutes"] for r in mine if r["owner_minutes"] is not None]
        print(
            f"{arm:<16} {len(scores):>5} {statistics.fmean(scores):>7.2f}"
            f" {statistics.fmean(hats) if hats else float('nan'):>7.2f}"
            f" {statistics.fmean(costs) if costs else float('nan'):>8.3f}"
            f" {statistics.median(r['wall_s'] for r in mine):>8.0f}"
            f" {sum(owner) if owner else float('nan'):>8.1f}"
        )

    print("\npaired pass@1, row minus column, over the tasks both ran (mean ± 95%)")
    for a, b in combinations(arms, 2):
        shared = sorted({t for arm, t in p1 if arm == a} & {t for arm, t in p1 if arm == b})
        if shared:
            mean, half = paired([p1[(a, t)] - p1[(b, t)] for t in shared])
            print(f"  {a} vs {b}: {mean:+.2f} ± {half:.2f}  ({len(shared)} tasks)")

    verdicts = out / "pairs" / "verdicts.jsonl"
    if verdicts.exists():
        key = json.loads((out / "pairs" / "key.json").read_text())
        tally: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        for line in verdicts.read_text().splitlines():
            if not line.strip():
                continue
            verdict = json.loads(line)
            sides = key[verdict["pair"]]
            a, b = sorted((sides["A"], sides["B"]))
            prefer = verdict["prefer"]
            winner = sides[prefer] if prefer in ("A", "B") else None
            tally[(a, b)][0 if winner == a else 1 if winner == b else 2] += 1
        print("\nblind pairwise review (wins / losses / ties)")
        for (a, b), (won, lost, tied) in sorted(tally.items()):
            print(f"  {a} vs {b}: {won} / {lost} / {tied}")


def selfcheck() -> None:
    assert pass_hat(3, 3, 3) == 1.0 and pass_hat(2, 3, 3) == 0.0
    assert abs(pass_hat(4, 5, 3) - 0.4) < 1e-9
    assert paired([0.5, 0.5]) == (0.5, 0.0)
    assert last_json('noise\n{"total_cost_usd": 1.5}\n{bad\n') == {"total_cost_usd": 1.5}

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        repo, set_dir, out = root / "repo", root / "set", root / "out"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "app.py").write_text("x = 1\n")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=m", "-c", "user.email=m@m", "commit", "-qm", "base")
        base = git(repo, "rev-parse", "HEAD").strip()
        (repo / "app.py").write_text("x = 2\n")
        git(repo, "-c", "user.name=m", "-c", "user.email=m@m", "commit", "-qam", "the answer")
        git(repo, "tag", "v1")
        later = git(repo, "rev-parse", "HEAD").strip()

        task = set_dir / "tasks" / "t1"
        (task / "check").mkdir(parents=True)
        (task / "check" / "check_app.py").write_text("import app\nassert app.x == 2\n")
        (task / "prompt.md").write_text("Make x two.\n")
        (task / "task.yaml").write_text(
            yaml.safe_dump({"repo": str(repo), "base": base, "check": "python3 check_app.py"})
        )
        (set_dir / "arms.yaml").write_text(
            yaml.safe_dump(
                {
                    "arms": {
                        # The check is not there to read while the arm runs.
                        # A variable the host does not set reaches no arm, not even empty.
                        "right": {
                            "run": 'test ! -e check_app.py && test -z "${MEASURE_UNSET+x}"'
                            " && echo 'x = 2' > app.py",
                            "env": ["MEASURE_UNSET"],
                        },
                        "wrong": {"run": "echo '{\"cost_usd\": 0.25}'"},
                    }
                }
            )
        )
        run(set_dir, out, [], [], 3)
        run(set_dir, out, [], [], 3)
        rows = results(out)
        assert len(rows) == 6, rows
        assert all(r["passed"] == (r["arm"] == "right") for r in rows), rows
        assert {r["cost_usd"] for r in rows if r["arm"] == "wrong"} == {0.25}
        assert git(repo, "status", "--porcelain") == ""
        tree = out / "trees" / "t1.right.1"
        assert git(tree, "tag") == "" and git(tree, "rev-list", "--all").split() == [base]
        assert subprocess.run(["git", "-C", str(tree), "cat-file", "-e", later]).returncode != 0

        pairs(out, 0)
        key = json.loads((out / "pairs" / "key.json").read_text())
        side = "A" if key["t1.1"]["A"] == "right" else "B"
        (out / "pairs" / "verdicts.jsonl").write_text(json.dumps({"pair": "t1.1", "prefer": side}))
        assert "right" not in (out / "pairs" / "t1.1.md").read_text()
        report(out)
    print("selfcheck ok")


def main() -> None:
    if "--selfcheck" in sys.argv:
        selfcheck()
        return
    parser = argparse.ArgumentParser(prog="python3 -m scripts.measure")
    verbs = parser.add_subparsers(dest="verb", required=True)
    run_p = verbs.add_parser("run")
    run_p.add_argument("set", type=Path)
    run_p.add_argument("out", type=Path)
    run_p.add_argument("--arm", action="append", default=[])
    run_p.add_argument("--task", action="append", default=[])
    run_p.add_argument("--trials", type=int, default=3)
    pairs_p = verbs.add_parser("pairs")
    pairs_p.add_argument("out", type=Path)
    pairs_p.add_argument("--seed", type=int, default=0)
    verbs.add_parser("report").add_argument("out", type=Path)
    args = parser.parse_args()

    if args.verb == "run":
        run(args.set, args.out, args.arm, args.task, args.trials)
    elif args.verb == "pairs":
        pairs(args.out, args.seed)
    else:
        report(args.out)


if __name__ == "__main__":
    main()
