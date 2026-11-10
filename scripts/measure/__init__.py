"""The measurement harness (S-0122/measurement, S-0122/P-11).

Replays past documents at their base commits through each arm, judges every
result with a behavioural check the arms never see, and reports pass@1,
pass^3, the blind pairwise review, cost, wall time and owner minutes. It lives
outside the engine and runs only after 0.9.0: no comparison of the engine
against a bare session comes before the release.

The task set is private and lives outside the repository:

    <set>/arms.yaml                  the arms, by name
    <set>/tasks/<name>/task.yaml     repo, base, document, check; branch (main)
    <set>/tasks/<name>/prompt.md     what a bare arm is asked
    <set>/tasks/<name>/check/        copied into the tree after the arm, never before

An arm is a shell command run in a fresh repository holding only the base's
history, with no remote and no tags, so nothing it does reaches the source
repository and no later commit shows it the answer:

    arms:
      opus-bare:
        run: claude -p --model claude-opus-5-5 --output-format json < "$MEASURE_PROMPT"
        env: [ANTHROPIC_API_KEY]     # host variables passed through; nothing else is
        timeout: 5400                # seconds; the default is 90 minutes

The command sees `MEASURE_TASK`, `MEASURE_ARM`, `MEASURE_TRIAL`,
`MEASURE_BASE`, `MEASURE_DOCUMENT` and `MEASURE_PROMPT`, and leaves its
result checked out in the clone. Its cost and owner minutes are the
`total_cost_usd` (or `cost_usd`) and `owner_minutes` of the last JSON object
it prints; an arm that prints none and leaves `.torve/telemetry.jsonl` is
costed from its rows. The check runs with only `PATH` and `HOME`.

    python3 -m scripts.measure run <set> <out> [--arm A]... [--task T]... [--trials 3]
    python3 -m scripts.measure pairs <out> [--seed 0]
    python3 -m scripts.measure report <out>
    python3 -m scripts.measure --selfcheck

`run` resumes: a (task, arm, trial) already in `<out>/results.jsonl` is not
run again. `pairs` writes one blinded comparison per task and pair of arms
under `<out>/pairs/`, from each arm's first trial, with the key beside them in
`key.json`; a judge who never opens the key writes `verdicts.jsonl`, one
`{"pair": id, "prefer": "A" | "B" | "tie"}` per line, and `report` unblinds it.
About 60 paired tasks detect a 20-point difference in pass@1.
"""

from __future__ import annotations

import statistics
from math import comb


def pass_hat(passed: int, trials: int, k: int) -> float:
    """pass^k: the chance that k trials drawn from these all pass."""

    return comb(passed, k) / comb(trials, k)


def paired(differences: list[float]) -> tuple[float, float]:
    """The mean of per-task differences and its 95% half-width."""

    mean = statistics.fmean(differences)
    if len(differences) < 2:
        return mean, float("inf")
    return mean, 1.96 * statistics.stdev(differences) / len(differences) ** 0.5
