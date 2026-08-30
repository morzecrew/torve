"""Pre-dispatch size estimate (RFC 0002 §6b, D-2.9): file-count in allow,
number of acceptance commands, presence of more than one module.

Rules of thumb, and a function rather than a policy object (A-49) — the
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


# ....................... #


def estimate_scope(scope: Scope, acceptance: list[str]) -> SizeVerdict:
    """The rule set itself, over the two fields it actually reads — shared
    by a minted task (`estimate`) and a still-unminted draft (RFC 0026
    D-26.12's per-child check), so a decomposition judges its own children
    by the identical rule the parent was judged by."""

    reasons: list[str] = []

    if len(scope.allow) > MAX_ALLOW_GLOBS:
        reasons.append(f"{len(scope.allow)} allow globs (threshold {MAX_ALLOW_GLOBS})")

    if len(acceptance) > MAX_ACCEPTANCE:
        reasons.append(f"{len(acceptance)} acceptance commands (threshold {MAX_ACCEPTANCE})")

    modules = {glob.split("/", 1)[0] for glob in scope.allow if "/" in glob}

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
    (RFC 0026 D-26.6) — true once a decomposition of it has been adopted,
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


# ....................... #


def awaiting_decomposition(root: Path, task: Task) -> bool:
    """The too_large route's predicate (D-26.7): a contract this large,
    that has not already been split into children, awaits decomposition —
    dispatch skips it until either a decomposition is adopted or the
    operator overrides explicitly."""

    return estimate(task).size == "too_large" and not has_children(root, task.id)
