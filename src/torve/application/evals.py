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

RFC 0027 §5.4 (D-27.7) adds the paired-digest measurement beside it: an
incumbent configuration versus a candidate with one tier's image
overridden, the same tasks replayed through the same shadow machinery.
Landing a configuration change never displaces the department's regime by
itself — only a verdict here, citing both digests, does — and that verdict
stays a quasi-experiment like every other eval in this ledger.

RFC 0034 (D-34.10) extends the paired measurement with a tier-variant
override beside the image override: the candidate arm resolves the seat to
a configured dotted variant, the record names the variant and cites both
config hashes, and image and variant overrides refuse to combine in one
invocation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from torve.application.ports import Agent
from torve.application.runner import RunDeps
from torve.application.shadow import ShadowSource, run_shadow
from torve.application.telemetry import append_record
from torve.config import layout
from torve.config.runconfig import RunnerConfig, SkillsConfig, image_for, tier_for
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


def candidate_config(
    config: RunnerConfig,
    tier: str,
    image: str | None = None,
    *,
    variant: str | None = None,
) -> RunnerConfig:
    """The candidate arm's configuration (D-27.7, D-34.10): `tier`'s image
    overridden, or the seat resolved to a configured dotted variant — one
    override per invocation, the same shape as `without_skill`'s role-set
    override. An override the tier already resolves is a configuration
    error — there is nothing to measure."""

    if image is not None and variant is not None:
        raise ValueError(
            "an image and a tier variant refuse to combine — the candidate arm "
            "takes one override at a time"
        )

    if image is None and variant is None:
        raise ValueError("the candidate arm needs an override: an image, or a tier variant")

    current = tier_for(config, tier)

    if image is not None:
        if image_for(config, current) == image:
            raise ValueError(
                f"tier {tier!r} already resolves image {image!r} — nothing to measure"
            )

        updated = current.model_copy(update={"image": image})

        return config.model_copy(update={"tiers": {**config.tiers, tier: updated}})

    # D-34.10: a tier variant is a dotted tier entry beside the seat; the
    # candidate resolves the seat to the variant's content, so a candidate
    # differing in model, command, adapter or image runs as itself in every
    # respect, never as the incumbent's agent under a candidate label.
    assert variant is not None
    variant_name = f"{tier}.{variant}"
    resolved = tier_for(config, variant_name)

    if current.model_dump() == resolved.model_dump():
        raise ValueError(
            f"tier {tier!r} already resolves variant {variant_name!r} — nothing to measure"
        )

    return config.model_copy(update={"tiers": {**config.tiers, tier: resolved}})


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


# ....................... #


def run_config_eval(
    root: Path,
    tier: str,
    tasks: list[Task],
    config: RunnerConfig,
    deps: RunDeps,
    source: ShadowSource,
    *,
    image: str | None = None,
    variant: str | None = None,
    candidate_agent: Agent | None = None,
) -> dict[str, Any]:
    """The paired replay (D-27.7, D-34.10): the incumbent configuration
    against a candidate with `tier`'s image overridden, or the seat
    resolved to a configured dotted variant — one override per invocation —
    both arms over every task, one eval record appended and returned. Each
    arm runs the agent its own configuration resolves: `deps.agent` is the
    incumbent's, `candidate_agent` the candidate's (None falls back to the
    incumbent's; the CLI always wires the candidate's own). Raises
    ValueError for an override the tier already resolves or a task with no
    shipped commit; RuntimeError on infrastructure failure — as run_shadow
    does."""

    if (image is None) == (variant is None):
        raise ValueError("run_config_eval needs exactly one of image= or variant=")

    arms = {
        "incumbent": config,
        "candidate": candidate_config(config, tier, image, variant=variant),
    }
    agents = {"incumbent": deps.agent, "candidate": candidate_agent or deps.agent}
    results: dict[str, list[dict[str, Any]]] = {"incumbent": [], "candidate": []}
    digests: dict[str, str | None] = {"incumbent": None, "candidate": None}
    configs: dict[str, str | None] = {"incumbent": None, "candidate": None}

    for task in tasks:
        for arm, arm_config in arms.items():
            annotation: dict[str, Any] = {"tier": tier, "arm": arm}
            if variant is not None:
                annotation["variant"] = f"{tier}.{variant}"

            shadow = run_shadow(
                root,
                task,
                arm_config,
                replace(deps, agent=agents[arm]),
                source,
                annotation=annotation,
            )

            results[arm].append(_arm_row(shadow))
            digests[arm] = shadow["image_digest"]
            configs[arm] = shadow["config_hash"]

    incumbent_arm, candidate_arm = _summary(results["incumbent"]), _summary(results["candidate"])

    matched = (
        candidate_arm["green"] >= incumbent_arm["green"]
        and candidate_arm["attempts"] <= incumbent_arm["attempts"]
    )

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "config-eval",
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": tier,
    }

    if variant is not None:
        # D-34.10: the record names the dotted variant the candidate arm
        # resolved and cites both config hashes — the regime identity of
        # each arm. A variant eval is a config measurement, not an image
        # displacement: it carries no digests, so it never feeds the
        # D-27.7 displacement guard.
        record["variant"] = f"{tier}.{variant}"
    else:
        assert image is not None
        record["image"] = image
        record["digests"] = digests

    record.update(
        {
            "tasks": [task.id for task in tasks],
            "configs": configs,
            "arms": results,
            "summary": {"incumbent": incumbent_arm, "candidate": candidate_arm},
            # Direction, never magnitude: true says the candidate did as
            # well here — displacing the incumbent default stays a human
            # act reading both digests (D-27.7), never this record acting
            # on its own verdict.
            "candidate_matched": matched,
        }
    )

    append_record(root / layout.TORVE_DIR / EVAL_LEDGER, record)

    return record
