"""The harness's verbs; the package docstring says what each does."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
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

from . import bootstrap, mcnemar, paired, pass_hat, sign_flip_p

# The paths a blinded diff leaves out: an engine's own records name the arm.
UNBLINDING = [":(exclude).torve", ":(exclude).wt"]

# What a trial's log or traces show when an arm went looking for the answer
# rather than writing it (the first pilot: clones of the upstream repository,
# its PR diffs over the API, the installed engine's own source). A hit flags
# the trial for review; it convicts nothing by itself.
LEAKS = {
    "fetch": r"\bgit (?:clone|ls-remote|fetch)\s+\S*://|\bcurl\b|\bwget\b|\burlopen\(|\bpip download\b",
    "forge": r"api\.github\.com|raw\.githubusercontent\.com|codeload\.github\.com",
    "registry": r"pypi\.org/(?:pypi|simple)/|files\.pythonhosted\.org|registry\.npmjs\.org",
    "history": r"\bgit (?:log|rev-list)\b[^\n]*--all\b|\bgit reflog\b|\bgit fsck\b|--lost-found",
    "installed": r"site-packages/|/opt/torve/(?:cli|cache)|archive-v0",
}


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

    # A home of the trial's own: an operator's settings, memory, plugins and
    # credentials live under the host's HOME, and a trial that could read them
    # would be measuring the operator's machine. Caches are the set's, shared
    # across trials, so a fresh home does not mean a cold install every time.
    home = out / "homes" / f"{task}.{arm}.{number}"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    env = {name: os.environ[name] for name in ("PATH", "LANG") if name in os.environ}
    env |= {"HOME": str(home), "XDG_CACHE_HOME": str(set_dir.resolve() / ".cache")}
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

    # The check arrives only now, so no arm can read it. A check that judges
    # several units says which passed in its last JSON line, `{"units": {...}}`.
    shutil.copytree(task_dir / "check", tree, dirs_exist_ok=True)
    units = None
    try:
        check = subprocess.run(
            ["bash", "-c", meta["check"]],
            cwd=tree,
            env={k: v for k, v in env.items() if k in ("PATH", "HOME", "XDG_CACHE_HOME")},
            capture_output=True,
            text=True,
            timeout=meta.get("check_timeout", 900),
        )
        passed = check.returncode == 0
        named = last_json(check.stdout).get("units")
        if isinstance(named, dict):
            units = {str(unit): bool(ok) for unit, ok in named.items()}
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
        "units": units,
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
            # Each pair in both orders: a judge that prefers a position over a
            # change answers the two differently, and the report counts that
            # as a tie rather than a win.
            for order, (first_arm, second_arm) in enumerate(((a, b), (b, a)), 1):
                ident = f"{task}.{n}.{order}"
                key[ident] = {"task": task, "pair": f"{task}.{n}", "A": first_arm, "B": second_arm}
                body = [f"# {task}\n", "Which change is better? Answer A, B or tie.\n"]
                for side, arm in (("A", first_arm), ("B", second_arm)):
                    diff = (out / first[(task, arm)]["diff"]).read_text()
                    body += [f"## {side}\n", "```diff", diff, "```\n"]
                (target / f"{ident}.md").write_text("\n".join(body))
    # Shuffled so a judge working down the list never sees a pair's two orders
    # side by side.
    order = list(key)
    rng.shuffle(order)
    (target / "order.txt").write_text("\n".join(order) + "\n")
    (target / "key.json").write_text(json.dumps(key, indent=2))
    print(f"{len(key)} comparison(s) under {target}")


def audit(out: Path) -> dict[str, dict[str, int]]:
    """Each trial's hits against `LEAKS`, in its log and in every trace its tree
    kept, written to `<out>/audit.json` and printed with whether the trial
    passed — a flagged pass is the one to read before counting it."""

    passed = {f"{r['task']}.{r['arm']}.{r['trial']}": r["passed"] for r in results(out)}
    patterns = {name: re.compile(rx) for name, rx in LEAKS.items()}
    found: dict[str, dict[str, int]] = {}

    for trial_id in sorted(passed):
        texts = [out / "logs" / f"{trial_id}.log"]
        texts += sorted((out / "trees" / trial_id / ".torve" / "traces").glob("*.log"))
        hits: dict[str, int] = defaultdict(int)
        for path in texts:
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            for name, pattern in patterns.items():
                hits[name] += len(pattern.findall(text))
        if any(hits.values()):
            found[trial_id] = {name: n for name, n in hits.items() if n}

    (out / "audit.json").write_text(json.dumps(found, indent=1))
    for trial_id, hits in found.items():
        verdict = "PASS" if passed[trial_id] else "fail"
        print(f"{trial_id:<32} {verdict}  " + " ".join(f"{k}={v}" for k, v in sorted(hits.items())))
    flagged_passes = sum(passed[t] for t in found)
    print(f"{len(found)} trial(s) flagged, {flagged_passes} of them passed")
    return found


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

    unit_report(rows)

    verdicts = out / "pairs" / "verdicts.jsonl"
    if verdicts.exists():
        key = json.loads((out / "pairs" / "key.json").read_text())
        # One winner per pair across its two orders: a split is a tie.
        winners: dict[str, set[str | None]] = defaultdict(set)
        for line in verdicts.read_text().splitlines():
            if not line.strip():
                continue
            verdict = json.loads(line)
            sides = key[verdict["pair"]]
            prefer = verdict["prefer"]
            winners[sides.get("pair", verdict["pair"])].add(
                sides[prefer] if prefer in ("A", "B") else None
            )
        tally: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        for pair, chosen in winners.items():
            sides = next(s for s in key.values() if s.get("pair", pair) == pair)
            a, b = sorted((sides["A"], sides["B"]))
            winner = next(iter(chosen)) if len(chosen) == 1 else None
            tally[(a, b)][0 if winner == a else 1 if winner == b else 2] += 1
        print("\nblind pairwise review, both orders agreeing (wins / losses / ties)")
        for (a, b), (won, lost, tied) in sorted(tally.items()):
            print(f"  {a} vs {b}: {won} / {lost} / {tied}")


def unit_report(rows: list[dict]) -> None:
    """The unit-level result for trials whose check named its units: each arm's
    unit pass rate, then each pair of arms compared per document — the
    difference of their unit pass rates over that document's units and
    trials — with the mean over documents, its bootstrap interval and its
    sign-flip p, and exact McNemar over trial 1's units."""

    unit_rows = [r for r in rows if r.get("units")]
    if not unit_rows:
        return

    outcomes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    first: dict[tuple[str, str, str], bool] = {}
    for r in unit_rows:
        for unit, ok in r["units"].items():
            outcomes[(r["arm"], r["task"])].append(ok)
            if r["trial"] == 1:
                first[(r["arm"], r["task"], unit)] = ok

    arms = sorted({arm for arm, _ in outcomes})
    print("\nunit pass rate (units × trials)")
    for arm in arms:
        mine = [ok for (a, _), oks in outcomes.items() if a == arm for ok in oks]
        print(f"  {arm:<16} {sum(mine) / len(mine):.2f}  ({len(mine)})")

    print(
        "\nper document, row minus column: mean [95% bootstrap]  sign-flip p  · McNemar p (trial 1)"
    )
    for a, b in combinations(arms, 2):
        documents = sorted(
            {t for arm, t in outcomes if arm == a} & {t for arm, t in outcomes if arm == b}
        )
        if not documents:
            continue
        rate = {key: sum(v) / len(v) for key, v in outcomes.items()}
        differences = [rate[(a, t)] - rate[(b, t)] for t in documents]
        low, high = bootstrap(differences)
        shared = [(t, u) for (arm, t, u) in first if arm == a and (b, t, u) in first]
        only_a = sum(first[(a, t, u)] and not first[(b, t, u)] for t, u in shared)
        only_b = sum(first[(b, t, u)] and not first[(a, t, u)] for t, u in shared)
        print(
            f"  {a} vs {b}: {statistics.fmean(differences):+.2f} [{low:+.2f}, {high:+.2f}]"
            f"  p={sign_flip_p(differences):.3f}  · McNemar {only_a}/{only_b} p={mcnemar(only_a, only_b):.3f}"
            f"  ({len(documents)} documents)"
        )


def selfcheck() -> None:
    assert pass_hat(3, 3, 3) == 1.0 and pass_hat(2, 3, 3) == 0.0
    assert abs(pass_hat(4, 5, 3) - 0.4) < 1e-9
    assert paired([0.5, 0.5]) == (0.5, 0.0)
    assert last_json('noise\n{"total_cost_usd": 1.5}\n{bad\n') == {"total_cost_usd": 1.5}
    # Four documents all one way: only the two all-same sign assignments are as
    # extreme, so p is 2/16.
    assert sign_flip_p([1.0, 1.0, 1.0, 1.0]) == 0.125
    assert sign_flip_p([1.0, -1.0]) == 1.0
    assert all(abs(bound - 0.2) < 1e-9 for bound in bootstrap([0.2, 0.2, 0.2]))
    assert mcnemar(0, 0) == 1.0 and mcnemar(5, 0) == 0.0625 and mcnemar(3, 3) == 1.0

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
        (task / "check" / "check_app.py").write_text(
            "import json, sys\nimport app\n"
            'print(json.dumps({"units": {"u1": app.x == 2, "u2": True}}))\n'
            "sys.exit(0 if app.x == 2 else 1)\n"
        )
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
                        # The home is the trial's own, never the host's.
                        "right": {
                            "run": 'test ! -e check_app.py && test -z "${MEASURE_UNSET+x}"'
                            ' && case "$HOME" in */homes/t1.right.*) ;; *) exit 1;; esac'
                            " && echo 'x = 2' > app.py",
                            "env": ["MEASURE_UNSET"],
                        },
                        "wrong": {
                            "run": "echo '{\"cost_usd\": 0.25}'"
                            " && echo 'curl -s https://api.github.com/repos/o/r' >&2"
                        },
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
        assert {r["arm"]: tuple(sorted(r["units"].items())) for r in rows} == {
            "right": (("u1", True), ("u2", True)),
            "wrong": (("u1", False), ("u2", True)),
        }

        flagged = audit(out)
        assert set(flagged) == {f"t1.wrong.{n}" for n in (1, 2, 3)}, flagged
        assert all(hits == {"fetch": 1, "forge": 1} for hits in flagged.values()), flagged

        pairs(out, 0)
        key = json.loads((out / "pairs" / "key.json").read_text())
        assert key["t1.1.1"]["A"] == key["t1.1.2"]["B"] and key["t1.1.1"]["pair"] == "t1.1"
        verdicts = [
            {"pair": ident, "prefer": "A" if sides["A"] == "right" else "B"}
            for ident, sides in key.items()
        ]
        (out / "pairs" / "verdicts.jsonl").write_text(
            "\n".join(json.dumps(v) for v in verdicts) + "\n"
        )
        assert all("right" not in (out / "pairs" / f"{i}.md").read_text() for i in key)
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
    verbs.add_parser("audit").add_argument("out", type=Path)
    args = parser.parse_args()

    if args.verb == "run":
        run(args.set, args.out, args.arm, args.task, args.trials)
    elif args.verb == "pairs":
        pairs(args.out, args.seed)
    elif args.verb == "audit":
        audit(args.out)
    else:
        report(args.out)


if __name__ == "__main__":
    main()
