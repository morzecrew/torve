"""The eval loop (RFC 0009 §5): with-skill versus without-skill shadow
replays of the same completed tasks. Each task replays twice — once under
the configured role sets, once with the skill removed from every set —
and nothing a replay produces ever merges (RFC 0004 D-4.4): the record is
the product.

The verdict compares arms as direction, never magnitude (RFC 0004 §6a —
a quasi-experiment): green outcomes first, then iterations, then cost.
`baseline_matched` true means the without-skill arm did as well as the
with-skill arm on this evidence; deleting a skill that does not earn its
tokens stays a human act (D-9.4), and this record is what the human acts
on. Eval records append to the evals ledger beside the telemetry, one
line per eval, replayable and diffable like every other engine record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from torve.application.runner import RunDeps
from torve.application.shadow import ShadowSource, run_shadow
from torve.application.telemetry import append_record
from torve.config import layout
from torve.config.runconfig import RunnerConfig, SkillsConfig
from torve.domain.task import SCHEMA_VERSION, Task

# ----------------------- #

EVAL_LEDGER = "evals.jsonl"


# ....................... #


def without_skill(config: RunnerConfig, skill: str) -> RunnerConfig:
    """The baseline arm's configuration: the skill removed from every role
    set. A skill in no set is a configuration error — there is nothing to
    measure."""

    sets = {
        role: [name for name in names if name != skill]
        for role, names in config.skills.sets.items()
    }

    if sets == config.skills.sets:
        raise ValueError(f"skill {skill!r} is in no role set — nothing to measure")

    return config.model_copy(update={"skills": SkillsConfig(sets=sets)})


# ....................... #


def _arm_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": record["task_id"],
        "state": record["state"],
        "attempts": record["attempts"],
        "cost_usd": record["cost_usd_total"],
    }


# ....................... #


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return {
        "green": sum(1 for r in rows if r["state"] == "ready"),
        "attempts": sum(int(r["attempts"]) for r in rows),
        "cost_usd": round(sum(costs), 6) if costs else None,
    }


# ....................... #


def run_skill_eval(
    root: Path,
    skill: str,
    tasks: list[Task],
    config: RunnerConfig,
    deps: RunDeps,
    source: ShadowSource,
) -> dict[str, Any]:
    """Both arms over every task, one eval record appended and returned.
    Raises ValueError for a skill in no role set or a task with no shipped
    commit; RuntimeError on infrastructure failure — as run_shadow does."""

    arms = {"with": config, "without": without_skill(config, skill)}
    results: dict[str, list[dict[str, Any]]] = {"with": [], "without": []}

    for task in tasks:
        for arm, arm_config in arms.items():
            record = run_shadow(
                root, task, arm_config, deps, source, annotation={"skill": skill, "arm": arm}
            )
            results[arm].append(_arm_row(record))

    with_arm, without_arm = _summary(results["with"]), _summary(results["without"])
    matched = (
        without_arm["green"] >= with_arm["green"]
        and without_arm["attempts"] <= with_arm["attempts"]
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "skill-eval",
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skill": skill,
        "tasks": [task.id for task in tasks],
        "arms": results,
        "summary": {"with": with_arm, "without": without_arm},
        # Direction, never magnitude: true says the baseline did as well
        # here — the deletion decision stays with a person (D-9.4).
        "baseline_matched": matched,
    }
    append_record(root / layout.TORVE_DIR / EVAL_LEDGER, record)

    return record
