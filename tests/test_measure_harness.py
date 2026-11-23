"""The measurement harness's own selfcheck (S-0122/P-11): it builds a source
repository with a later commit and a tag, runs two arms against it, and asserts
what a trial may and may not see, the unit-level results, the leak audit and
the blind pairs in both orders. Run here so CI holds it, since nothing else
imports `scripts.measure`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ----------------------- #

ROOT = Path(__file__).resolve().parent.parent


def test_the_measurement_harness_selfcheck_passes() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "scripts.measure", "--selfcheck"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.rstrip().endswith("selfcheck ok")


def test_the_failure_mix_selfcheck_passes() -> None:
    """`scripts/e3_failure_mix.py` folds a re-gated attempt and leaves a replay
    out (S-0124/D-15); nothing else runs its selfcheck."""

    done = subprocess.run(
        [sys.executable, "scripts/e3_failure_mix.py", "--selfcheck"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.rstrip().endswith("selfcheck ok")
