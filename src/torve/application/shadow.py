"""Shadow runs (RFC 0004 §5): replay an already-completed task from the
parent of the commit that shipped it, never merging, and compare against what
actually shipped. This is the only risk-free source of baseline numbers, it
precedes any live loop (D-4.4), and it is where the gate set is tuned — not
before.

The loop is `drive_attempts` — the same code a live run executes — over the
same sandbox-and-gates hooks, with two differences: the workspace is a
truncated clone the CLI's ShadowWorkspace built (D-4.7: no refs beyond the
parent, so the agent cannot read the answer out of history), and the landing
hook records a fact instead of committing — nothing a shadow run produces
ever reaches a branch.

A shadow run is a measurement: the record is the product, and a red replay is
a successful measurement of a red outcome. Baseline comparison stays a
quasi-experiment (§6a) — direction, never magnitude.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from torve.application.ports import AgentResult
from torve.application.runner import AttemptHooks, RunDeps, drive_attempts, real_hooks
from torve.application.runstate import RunState
from torve.application.telemetry import append_record, config_hash
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig, image_for, tier_for
from torve.domain.states import TaskState
from torve.domain.task import SCHEMA_VERSION, Task

# ----------------------- #


@dataclass
class ShadowSource:
    """Host-side git callables the CLI injects — the application layer
    orchestrates, the workspace adapter owns the history mechanics."""

    create_workspace: Callable[[str, str], Path]  # (task_id, parent_sha) -> path
    shipped_commit: Callable[[str], str | None]  # task_id -> sha
    parent_of: Callable[[str], str]
    diff_range: Callable[[str], dict[str, Any]]  # what shipped, commit vs parent
    # What the replay produced vs the parent — never vs HEAD, which the
    # agent can move by committing inside the self-contained clone.
    diff_worktree: Callable[[Path, str], dict[str, Any]]


# ....................... #


async def _drive(
    state: RunState, task: Task, config: RunnerConfig, hooks: AttemptHooks
) -> RunState:
    return await drive_attempts(state, task, config, hooks)


# ....................... #


def run_shadow(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    source: ShadowSource,
    commit: str | None = None,
    annotation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One shadow replay; returns the telemetry record it appended. Raises
    ValueError when no shipped commit is findable (a configuration problem),
    RuntimeError on infrastructure failure."""

    import asyncio

    resolved = commit or source.shipped_commit(task.id)

    if resolved is None:
        raise ValueError(
            f"no shipped commit found for {task.id} (no 'Torve-Task: {task.id}' trailer "
            f"or '({task.id})' subject in history); pass --commit explicitly"
        )

    parent = source.parent_of(resolved)
    workspace = source.create_workspace(task.id, parent)

    state = RunState(
        task_id=naming.shadow_id(task.id),
        path=naming.state_file(root, naming.shadow_id(task.id)),
    )
    state.transition(TaskState.CLAIMED, f"shadow replay of {resolved[:10]} from {parent[:10]}")

    inner = real_hooks(root, task, config, deps, workspace, shadow=True, gates_base=parent)
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
        # The one divergence from a live run's hooks: nothing is committed,
        # nothing is pushed — a shadow run never merges (D-4.4).
        return "shadow measurement recorded; nothing merged"

    hooks = AttemptHooks(attempt=attempt, halted=inner.halted, gates=inner.gates, land=land)
    final = asyncio.run(_drive(state, task, config, hooks))
    final.save()

    manifest_path = layout.gates_file(workspace)
    # The replay's image identity, resolved the same way a live dispatch
    # resolves it (D-17.1) — a rebuild between two replays is two regimes.
    image_digest = deps.runtime.resolve_image(image_for(config, tier_for(config, task.tier)))
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "shadow",
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        "tier": task.tier,
        "adapter": getattr(deps.agent, "kind", "unknown"),
        "cost_usd_total": sum(costs) if costs else None,
        "model_versions": sorted(set(model_versions)),
        "trace_refs": traces,
        # The comparison §5 asks for — as data, judged by a human: before
        # and after are different conditions (§6a), so this supports
        # "the replay touched the same three files" and never "40% better".
        "shadow_diff": source.diff_worktree(workspace, parent),
        "shipped_diff": source.diff_range(resolved),
    }
    shadow_files = set(record["shadow_diff"].get("files", {}))
    shipped_files = set(record["shipped_diff"].get("files", {}))
    record["overlap_files"] = sorted(shadow_files & shipped_files)

    if annotation is not None:
        # The caller's measurement context — the eval loop (RFC 0009 §5)
        # marks its arm here so the population stays separable.
        record["eval"] = annotation

    append_record(root / layout.TORVE_DIR / "telemetry.jsonl", record)

    return record
