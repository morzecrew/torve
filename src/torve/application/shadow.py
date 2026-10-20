"""Shadow runs (S-0004/shadow-runs): replay an already-completed task from the
parent of the commit that shipped it, never merging, and compare against what
actually shipped. This is the only risk-free source of baseline numbers, it
precedes any live loop (S-0004/D-4), and it is where the gate set is tuned — not
before.

The loop is `drive_attempts` — the same code a live run executes — over the
same sandbox-and-gates hooks, with two differences: the workspace is a
truncated clone the CLI's ShadowWorkspace built (S-0004/D-7: no refs beyond the
parent, so the agent cannot read the answer out of history), and the landing
hook records a fact instead of committing — nothing a shadow run produces
ever reaches a branch.

A shadow run is a measurement: the record is the product, and a red replay is
a successful measurement of a red outcome. Baseline comparison stays a
quasi-experiment (§6a) — direction, never magnitude.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from torve.application.dispatch import RunDeps
from torve.application.ports import AgentResult, JudgePort
from torve.application.runner import (
    HOLDOUT_LIMIT,
    HOLDOUT_REL,
    AttemptHooks,
    drive_attempts,
    real_hooks,
)
from torve.application.runstate import RunState
from torve.application.telemetry import RECORD_SCHEMA_VERSION, append_record, config_hash
from torve.base import naming
from torve.base.clock import stamp
from torve.config import layout
from torve.config.runconfig import RunnerConfig, image_for, tier_for, tier_name_for
from torve.domain.events import HoldoutFile
from torve.domain.states import TaskState
from torve.domain.task import Task

# ----------------------- #


@dataclass
class ShadowSource:
    """Host-side git callables the CLI injects — the application layer
    orchestrates, the workspace adapter owns the history mechanics."""

    create_workspace: Callable[[str, str], Path]  # (task_id, parent_sha) -> path
    shipped_commit: Callable[[str], str | None]  # task_id -> the commit its landing names
    parent_of: Callable[[str], str]
    diff_range: Callable[[str], dict[str, Any]]  # what shipped, commit vs parent
    # What the replay produced vs the parent — never vs HEAD, which the
    # agent can move by committing inside the self-contained clone.
    diff_worktree: Callable[[Path, str], dict[str, Any]]
    # Opens the replay's judge checkout from its clone (S-0114/D-2): the clone
    # is no worktree of the root, so the workspace's own cannot snapshot it.
    judge: JudgePort


# ....................... #


@dataclass
class SharedJudge:
    """One clone, judged under one name (S-0116/D-12): a composed arm replays
    more than one task in the same workspace, and every judge lookup names the
    task the clone belongs to rather than the task being replayed."""

    inner: JudgePort
    label: str

    def judge_checkout(self, _task_id: str) -> Path:
        return self.inner.judge_checkout(self.label)

    def remove_judge(self, _task_id: str) -> None:
        self.inner.remove_judge(self.label)


# ....................... #


def replay_source(source: ShadowSource, label: str) -> ShadowSource:
    """`source` judging under *label* — the composed arm's own source."""

    return replace(source, judge=SharedJudge(source.judge, label))


# ....................... #


def shadow_workspace(source: ShadowSource, task_id: str) -> tuple[Path, str, str]:
    """(workspace, resolved, parent) — a clone cut at the parent of the commit
    the task's landing names, the tree every arm starts from (S-0116/D-12).
    Raises ValueError when no landing names a commit."""

    resolved = source.shipped_commit(task_id)

    if resolved is None:
        raise ValueError(
            f"no shipped commit found for {task_id} — no landing of it names a "
            "commit (S-0059/D-12); pass --commit explicitly"
        )

    parent = source.parent_of(resolved)

    return source.create_workspace(task_id, parent), resolved, parent


# ....................... #


def take_holdout(worktree: Path) -> list[HoldoutFile]:
    """The held-out properties a verifier left in its worktree, read and
    removed (S-0116/D-9's directory, D-12): the candidate arm injects them into
    the implement replay's battery, and the implementer's own tree never
    carries them."""

    directory = worktree / HOLDOUT_REL
    files: list[HoldoutFile] = []

    try:
        if directory.is_dir():
            budget = HOLDOUT_LIMIT

            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue

                text = path.read_text(encoding="utf-8", errors="replace")

                if len(text) > budget:
                    text = text[:budget]

                files.append(HoldoutFile(name=path.relative_to(directory).as_posix(), text=text))
                budget -= len(text)

                if budget <= 0:
                    break

    finally:
        shutil.rmtree(directory, ignore_errors=True)

    return files


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
    workspace: Path | None = None,
) -> dict[str, Any]:
    """One shadow replay; returns the telemetry record it appended. Raises
    ValueError when no shipped commit is findable (a configuration problem),
    RuntimeError on infrastructure failure.

    *workspace* replays into a clone the caller already cut (S-0116/D-12, a
    composed arm) instead of building one from the commit's parent."""

    resolved = commit or source.shipped_commit(task.id)

    if resolved is None:
        raise ValueError(
            f"no shipped commit found for {task.id} — no landing of it names a "
            "commit (S-0059/D-12); pass --commit explicitly"
        )

    parent = source.parent_of(resolved)
    clone = workspace if workspace is not None else source.create_workspace(task.id, parent)

    return shadow_replay(root, task, config, deps, source, clone, parent, resolved, annotation)


# ....................... #


def shadow_replay(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    source: ShadowSource,
    workspace: Path,
    parent: str,
    resolved: str,
    annotation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One replay inside *workspace*, cut at *parent* and recorded against
    *resolved*. The shared body of every replay — `run_shadow`'s and the
    composed verifier arm's, which drives two of them in one clone
    (S-0116/D-12)."""

    import asyncio

    state = RunState(
        task_id=naming.shadow_id(task.id),
        path=naming.state_file(root, naming.shadow_id(task.id)),
    )

    state.transition(TaskState.CLAIMED, f"shadow replay of {resolved[:10]} from {parent[:10]}")

    # `shadow=True` is where the warm-state exclusion rides (S-0035/D-3): the
    # hooks below compose no cache mount under it, so the replay measures
    # the cold truth and an eval comparing arms never compares caches —
    # whatever cache_volume the task's tier names.
    inner = real_hooks(
        root, task, config, deps, workspace, shadow=True, gates_base=parent, judges=source.judge
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
        # The one divergence from a live run's hooks: nothing is committed,
        # nothing is pushed — a shadow run never merges (S-0004/D-4).
        return "shadow measurement recorded; nothing merged"

    hooks = AttemptHooks(
        attempt=attempt,
        halted=inner.halted,
        gates=inner.gates,
        land=land,
        close=inner.close,
    )
    final = asyncio.run(_drive(state, task, config, hooks))
    final.save()

    manifest_path = layout.gates_file(workspace)
    # The replay's image identity, resolved the same way a live dispatch
    # resolves it (S-0017/D-1) — a rebuild between two replays is two regimes.
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
        # The caller's measurement context — the eval loop (S-0009/evals)
        # marks its arm here so the population stays separable.
        record["eval"] = annotation

    append_record(root / layout.TORVE_DIR / "telemetry.jsonl", record)

    return record
