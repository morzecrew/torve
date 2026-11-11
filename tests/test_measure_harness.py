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
