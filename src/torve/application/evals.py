"""The eval loop (S-0009/evals): with-skill versus without-skill shadow
replays of the same completed tasks. Each task replays twice — once under
the configured role sets, once with the skill removed from every set —
and nothing a replay produces ever merges (S-0004 S-0004/D-4): the record is
the product.

The verdict compares arms as direction, never magnitude (S-0004/measurement-defects-to-fix-before-trusting-a-number —
a quasi-experiment): green outcomes first, then iterations, then cost.
`baseline_matched` true means the without-skill arm did as well as the
with-skill arm on this evidence; deleting a skill that does not earn its
tokens stays a human act (S-0009/D-4), and this record is what the human acts
on. Eval records append to the evals ledger beside the telemetry, one
line per eval, replayable and diffable like every other engine record.

S-0027/the-measurement-obligation (S-0027/D-7) adds the paired-digest measurement beside it: an
incumbent configuration versus a candidate with one tier's image
overridden, the same tasks replayed through the same shadow machinery.
Landing a configuration change never displaces the department's regime by
itself — only a verdict here, citing both digests, does — and that verdict
stays a quasi-experiment like every other eval in this ledger.

S-0034 (S-0034/D-10) extends the paired measurement with a tier-variant
override beside the image override: the candidate arm resolves the seat to
a configured dotted variant, the record names the variant and cites both
config hashes, and image and variant overrides refuse to combine in one
invocation.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from torve.application.dispatch import RunDeps
from torve.application.ports import Agent, AgentResult
from torve.application.runner import AttemptHooks, drive_attempts, real_hooks
from torve.application.runstate import RunState
from torve.application.shadow import ShadowSource, run_shadow
from torve.application.telemetry import RECORD_SCHEMA_VERSION, append_record, config_hash
from torve.base import naming
from torve.base.clock import stamp
from torve.config import layout
from torve.config.runconfig import RunnerConfig, SkillsConfig, image_for, tier_for, tier_name_for
from torve.domain.states import TaskState
from torve.domain.task import Task

# ----------------------- #

# The eval ledger's own shape version (T-0321).
SCHEMA_VERSION = 1

EVAL_LEDGER = "evals.jsonl"


# The arm axis (S-0074/D-1): an arm is named by the apparatus it removes,
# and there are three — the harness alone, the harness under the battery,
# torve as configured. Each result row carries which arm produced it, so
# the three-arm comparison is rebuildable from the ledger alone.
BARE, GATED, CONFIGURED = "bare", "gated", "configured"
ARMS: tuple[str, str, str] = (BARE, GATED, CONFIGURED)


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
    """The candidate arm's configuration (S-0027/D-7, S-0034/D-10): `tier`'s image
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
            raise ValueError(f"tier {tier!r} already resolves image {image!r} — nothing to measure")

        updated = current.model_copy(update={"image": image})

        return config.model_copy(update={"tiers": {**config.tiers, tier: updated}})

    # S-0034/D-10: a tier variant is a dotted tier entry beside the seat; the
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


def _arm_row(record: dict[str, Any], arm: str) -> dict[str, Any]:
    """One result row, naming the arm that produced it (S-0074/D-1): the
    row carries `arm` so the read is rebuildable from the ledger alone,
    whatever dict the arms happened to sit under."""

    return {
        "arm": arm,
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


def three_arm_table(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """The three-arm table rebuilt from the eval ledger alone (S-0074/D-1):
    per task, per axis arm, the latest result that arm produced — a year
    later, with no other input, this is the comparison. A record whose
    arms name no apparatus removed (a config eval) contributes nothing to
    the axis; the ledger is append-only, so the last line for a task-arm
    pair is the most recent."""

    ledger = root / layout.TORVE_DIR / EVAL_LEDGER

    if not ledger.is_file():
        return {}

    table: dict[str, dict[str, dict[str, Any]]] = {}

    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)
        except json.JSONDecodeError:
            continue

        arms = record.get("arms")

        if not isinstance(arms, dict):
            continue

        for arm, rows in arms.items():
            if arm not in ARMS:
                continue

            for row in rows:
                task = row.get("task")

                if isinstance(task, str):
                    table.setdefault(task, {})[arm] = row

    return table


# ....................... #


def eligible_tasks(root: Path) -> dict[str, dict[str, Any]]:
    """Which tasks an arm run may name, and what each already cost
    (S-0082/D-3): a task is eligible when the tree holds a landing that names
    a commit — that commit's parent is where a replay starts — and the cost
    beside it is what the task cost when it was done for real, summed from
    the live attempts in the telemetry ledger. A replay's own attempts and a
    fake adapter's are not spend and are not counted.

    One reader, taking no configuration and no agent, so the refusal for an
    arm with no task and the pre-flight a verb prints before it spends come
    from the same read and cannot disagree. Keyed by task id, ascending.
    """

    from torve.application.projections import shipped_landings

    found: dict[str, dict[str, Any]] = {
        task: {"commit": commit, "attempts": 0, "cost_usd": None}
        for task, commit in sorted(shipped_landings(root).items())
    }

    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if not telemetry.is_file():
        return found

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        agent: Any = record.get("agent")
        row = found.get(str(record.get("task_id") or ""))

        if row is None or not isinstance(agent, dict):
            continue

        # A shadow replay is a measurement of the task, never the task being
        # done; a fake adapter is simulation, neither spend nor conviction
        # (S-0004/D-6). Both would inflate what a real attempt cost.
        if agent.get("shadow") or agent.get("adapter") == "fake":
            continue

        row["attempts"] += 1
        cost = agent.get("cost_usd")

        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            row["cost_usd"] = round((row["cost_usd"] or 0.0) + float(cost), 6)

    return found


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

    seat = config.tiers.get("executor")
    # S-0004/D-6: a fake adapter is simulation, neither spend nor conviction. The
    # cost and quality projections already exclude its rows; an eval that
    # ignored it compares two arms of nothing and matches them at zero
    # (S-0009/A-5). Recorded rather than refused, because a test asserting the
    # record's shape runs a fake agent on purpose — what must not happen is
    # a *verdict*.
    simulated = seat is not None and seat.adapter == "fake"

    arms = {"with": config, "without": without_skill(config, skill)}
    results: dict[str, list[dict[str, Any]]] = {"with": [], "without": []}

    for task in tasks:
        for arm, arm_config in arms.items():
            record = run_shadow(
                root, task, arm_config, deps, source, annotation={"skill": skill, "arm": arm}
            )

            results[arm].append(_arm_row(record, arm))

    with_arm, without_arm = _summary(results["with"]), _summary(results["without"])

    # A comparison needs something to compare, and something real to compare
    # it with. Both arms failing every task is not the baseline keeping up —
    # it is the replay failing for a reason upstream of the skill — and a
    # simulated arm is not a measurement at all. Either way the verdict is
    # absent rather than false, because a false one invites a deletion on no
    # evidence (S-0009/A-5).
    matched: bool | None = None

    if not simulated and (with_arm["green"] or without_arm["green"]):
        matched = (
            without_arm["green"] >= with_arm["green"]
            and without_arm["attempts"] <= with_arm["attempts"]
        )

    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "skill-eval",
        "at": stamp(),
        "skill": skill,
        "tasks": [task.id for task in tasks],
        "arms": results,
        "summary": {"with": with_arm, "without": without_arm},
        # What the arms ran on, so a reader can tell a measurement from a
        # rehearsal without reconstructing the configuration (S-0004/D-6).
        "simulated": simulated,
        # Direction, never magnitude: true says the baseline did as well
        # here — the deletion decision stays with a person (S-0009/D-4).
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
    """The paired replay (S-0027/D-7, S-0034/D-10): the incumbent configuration
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

            results[arm].append(_arm_row(shadow, arm))
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
        "at": stamp(),
        "tier": tier,
    }

    if variant is not None:
        # S-0034/D-10: the record names the dotted variant the candidate arm
        # resolved and cites both config hashes — the regime identity of
        # each arm. A variant eval is a config measurement, not an image
        # displacement: it carries no digests, so it never feeds the
        # S-0027/D-7 displacement guard.
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
            # act reading both digests (S-0027/D-7), never this record acting
            # on its own verdict.
            "candidate_matched": matched,
        }
    )

    append_record(root / layout.TORVE_DIR / EVAL_LEDGER, record)

    return record


# ....................... #


def run_bare_shadow(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    source: ShadowSource,
    commit: str | None = None,
    annotation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The bare arm's replay (S-0074/D-3): the same shadow replay as
    `run_shadow`, with the battery removed as a property of the replay — the
    bare flag swaps the gate pass for one that runs nothing, and the gate
    manifest is never the thing that changed (a manifest with gates removed
    would be a different regime, and the regime digest would be right to say
    so). A bare arm still merges nothing (S-0004/D-4): the land hook is the
    same no-op a gated replay's is. The record's `config_hash` is the
    unchanged manifest's — the same regime the gated arm replayed under.

    Raises ValueError when no shipped commit is findable; RuntimeError on
    infrastructure failure — as run_shadow does."""

    import asyncio

    resolved = commit or source.shipped_commit(task.id)

    if resolved is None:
        raise ValueError(
            f"no shipped commit found for {task.id} — no landing of it names a "
            "commit (S-0059/D-12); pass --commit explicitly"
        )

    parent = source.parent_of(resolved)
    workspace = source.create_workspace(task.id, parent)

    state = RunState(
        task_id=naming.shadow_id(task.id),
        path=naming.state_file(root, naming.shadow_id(task.id)),
    )

    state.transition(TaskState.CLAIMED, f"shadow replay of {resolved[:10]} from {parent[:10]}")

    inner = real_hooks(
        root, task, config, deps, workspace, shadow=True, gates_base=parent, bare=True
    )
    costs: list[float] = []
    traces: list[str] = []
    model_versions: list[str] = []

    async def attempt(attempt_state: RunState) -> AgentResult:
        result = await inner.attempt(attempt_state)

        if result.cost_usd is not None:
            costs.append(result.cost_usd)

        if result.trace_ref is not None:
            traces.append(result.trace_ref)

        if result.model_version is not None:
            model_versions.append(result.model_version)

        return result

    async def land(_state: RunState, _digest: str) -> str:
        # The one divergence from a live run's hooks, inherited from the
        # gated replay: nothing is committed, nothing is pushed — a shadow
        # run never merges, bare or gated (S-0004/D-4).
        return "shadow measurement recorded; nothing merged"

    hooks = AttemptHooks(
        attempt=attempt,
        halted=inner.halted,
        gates=inner.gates,
        land=land,
        close=inner.close,
    )
    final = asyncio.run(drive_attempts(state, task, config, hooks))
    final.save()

    manifest_path = layout.gates_file(workspace)
    image_digest = deps.runtime.resolve_image(
        image_for(config, tier_for(config, tier_name_for(task)))
    )

    record: dict[str, Any] = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": "shadow",
        "at": stamp(),
        "config_hash": (
            config_hash(manifest_path, workspace, config, image_digest=image_digest)
            if manifest_path.is_file()
            else None
        ),
        "image_digest": image_digest,
        "task_id": task.id,
        "commit": resolved,
        "parent": parent,
        "state": str(final.state),
        "attempts": final.attempts,
        "escalation": final.escalation.reason if final.escalation else None,
        "tier": tier_name_for(task),
        "adapter": getattr(deps.agent, "kind", "unknown"),
        "cost_usd_total": sum(costs) if costs else None,
        "model_versions": sorted(set(model_versions)),
        "trace_refs": traces,
        "shadow_diff": source.diff_worktree(workspace, parent),
        "shipped_diff": source.diff_range(resolved),
    }

    shadow_files = set(record["shadow_diff"].get("files", {}))
    shipped_files = set(record["shipped_diff"].get("files", {}))
    record["overlap_files"] = sorted(shadow_files & shipped_files)

    if annotation is not None:
        # The bare arm's name is a property of the replay, like the gated
        # arm's: carried on the record, never read back from any gate output.
        record["eval"] = annotation

    append_record(root / layout.TORVE_DIR / "telemetry.jsonl", record)

    return record
