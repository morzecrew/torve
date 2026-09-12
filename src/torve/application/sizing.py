"""Pre-dispatch size estimate (S-0002/task-size, S-0002/D-9): file-count in allow,
number of acceptance commands, presence of more than one module.

Rules of thumb, and a function rather than a policy object (S-0002/A-3) — the
`HistoricalPercentile` arm the protocol was shaped for needs the attempt
store and retrospective calibration, and neither exists. Until it does,
observations are carried by the telemetry records themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from torve.domain.attempt import SizeVerdict
from torve.domain.task import Scope, Task

# ----------------------- #

MAX_ALLOW_GLOBS = 10
MAX_ACCEPTANCE = 6
MAX_MODULES = 1

# Not modules a task spans — what any task carries, and so no evidence of size.
CARRIED = frozenset({"tests", ".torve"})


# ....................... #


def estimate_scope(scope: Scope, acceptance: list[str]) -> SizeVerdict:
    """The rule set itself, over the two fields it actually reads — shared
    by a minted task (`estimate`) and a still-unminted draft (S-0026
    S-0026/D-12's per-child check), so a decomposition judges its own children
    by the identical rule the parent was judged by."""

    reasons: list[str] = []

    if len(scope.allow) > MAX_ALLOW_GLOBS:
        reasons.append(f"{len(scope.allow)} allow globs (threshold {MAX_ALLOW_GLOBS})")

    if len(acceptance) > MAX_ACCEPTANCE:
        reasons.append(f"{len(acceptance)} acceptance commands (threshold {MAX_ACCEPTANCE})")

    # `tests` and `.torve` are not modules a task spans, they are what any task
    # carries. Tests accompany every change (every minted phase carries
    # `tests/**`); `.torve` holds the task's own contract, the amendment its
    # landing records and the configuration it may touch. Counting either calls
    # a correctly-sized task too_large — which under S-0055/D-47 is not a
    # cosmetic number but a route to a decomposition run, so the estimate would
    # split a task for being properly recorded. Measured over this repository's
    # 61 contracts: counting `.torve` calls 9 of them oversized on that ground
    # alone, and 33 stay oversized for reasons that are real.
    modules = {glob.split("/", 1)[0] for glob in scope.allow if "/" in glob} - CARRIED

    if len(modules) > MAX_MODULES:
        listed = ", ".join(sorted(modules))
        reasons.append(f"touches {len(modules)} top-level modules: {listed}")

    if reasons:
        return SizeVerdict(size="too_large", reasons=reasons)

    if not scope.allow and not acceptance:
        return SizeVerdict(
            size="too_small",
            reasons=["no scope and no acceptance — not worth the machinery"],
        )

    return SizeVerdict(size="ok")


# ....................... #


def estimate(task: Task) -> SizeVerdict:
    return estimate_scope(task.scope, task.acceptance)


# ....................... #


def has_children(root: Path, task_id: str) -> bool:
    """Whether some other contract already carries this task as `parent`
    (S-0026 S-0026/D-6) — true once a decomposition of it has been adopted,
    at which point it is the integration task and its own too_large
    verdict has already routed once and does not route again."""

    from torve.config import layout

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return False

    for contract in tasks_dir.glob("T-*/contract.yaml"):
        try:
            record = yaml.safe_load(contract.read_text(encoding="utf-8"))

        except yaml.YAMLError:
            continue

        if isinstance(record, dict) and cast("dict[str, Any]", record).get("parent") == task_id:
            return True

    return False
