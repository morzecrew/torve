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
result checked out in the clone. Its `HOME` is a fresh directory of the
trial's own, never the operator's, and `XDG_CACHE_HOME` is `<set>/.cache`,
shared by the set's trials. Its cost and owner minutes are the
`total_cost_usd` (or `cost_usd`) and `owner_minutes` of the last JSON object
it prints; an arm that prints none and leaves `.torve/telemetry.jsonl` is
costed from its rows. The check runs with only `PATH`, that `HOME` and that
cache; a check that judges several units — a task that is a whole document —
names them in its last JSON line, `{"units": {"<unit>": true | false}}`.

    python3 -m scripts.measure run <set> <out> [--arm A]... [--task T]... [--trials 3] [--seed 0]
    python3 -m scripts.measure pairs <out> [--seed 0]
    python3 -m scripts.measure audit <out>
    python3 -m scripts.measure report <out>
    python3 -m scripts.measure --selfcheck

`run` resumes: a (task, arm, trial) already in `<out>/results.jsonl` is not
run again, and the rest run in a seeded random order, so whatever drifts over
a run falls on every arm alike. `pairs` writes each pair of arms' first trials
twice, once in each order, under `<out>/pairs/`, with the key in `key.json` and
a shuffled reading order in `order.txt`; a judge who never opens the key
writes `verdicts.jsonl`, one `{"pair": id, "prefer": "A" | "B" | "tie"}` per
line, and `report` unblinds it, counting a pair whose two orders disagree as a
tie and one judged in a single order as incomplete. `audit` flags
the trials whose log or traces show an arm fetching, mining history or reading
an installed package (`LEAKS`), and says which of them passed.

`report` gives pass@1 and pass^3 per task, and for unit-judged tasks the unit
pass rate and each pair of arms compared per document: the difference of
their unit pass rates, its bootstrap interval and sign-flip p over documents,
and exact McNemar over trial 1's units. Units of one document are not
independent, so the document is what the inference counts. Roughly: ten
documents of six units at five trials an arm detect a 10-point difference;
one trial an arm needs about 160 independent units for the same.
"""

from __future__ import annotations

import random
import statistics
from itertools import product
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


# Units of one document share its code, its author and its mistakes, so they
# are not independent: the document is the unit of inference, and a per-unit
# test reads clustered noise as signal.


def sign_flip_p(differences: list[float], seed: int = 0) -> float:
    """Two-sided p of the mean difference under "the arms are exchangeable":
    every sign assignment over the clusters, exactly when there are at most 16,
    otherwise 20,000 drawn at random."""

    observed = abs(statistics.fmean(differences))
    if len(differences) <= 16:
        flips = product((1, -1), repeat=len(differences))
        total = 2 ** len(differences)
    else:
        rng = random.Random(seed)
        total = 20_000
        flips = ([rng.choice((1, -1)) for _ in differences] for _ in range(total))
    extreme = sum(
        abs(statistics.fmean(s * d for s, d in zip(signs, differences, strict=True)))
        >= observed - 1e-12
        for signs in flips
    )
    return extreme / total


def bootstrap(differences: list[float], seed: int = 0, draws: int = 10_000) -> tuple[float, float]:
    """The 95% percentile interval of the mean difference, resampling clusters."""

    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choices(differences, k=len(differences))) for _ in range(draws)
    )
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def mcnemar(only_a: int, only_b: int) -> float:
    """Exact two-sided McNemar p from the discordant counts: units only arm A
    passed, and units only arm B passed."""

    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2**n
    return min(1.0, 2 * tail)
