"""Pull-request composition (RFC 0010 §6, D-10.6): the body is built from
data — the contract, the gate outcomes, the inherited decisions, the
execution log's divergences, cost and trace — never from the agent's prose.
A self-report is not evidence; the pull request reads as a claim with proof
attached, checkable without opening a terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from torve.config import layout
from torve.domain.attempt import GateResult
from torve.domain.task import Task

# ----------------------- #

# The log kinds a reviewer must see before the diff: work that diverged
# from its contract, not work that went to plan.
DIVERGENT_KINDS = ("contradicted", "departed", "blocked")


def _divergences(worktree: Path, task_id: str) -> list[str]:
    log_path = layout.log_file(worktree, task_id)
    if not log_path.is_file():
        return []
    try:
        loaded: object = yaml.safe_load(log_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ["execution log unparseable — read it before merging"]
    if not isinstance(loaded, dict):
        return []
    entries = cast("dict[str, Any]", loaded).get("entries")
    if not isinstance(entries, list):
        return []
    found: list[str] = []
    for raw in cast("list[object]", entries):
        if not isinstance(raw, dict):
            continue
        entry = cast("dict[str, Any]", raw)
        if str(entry.get("kind")) in DIVERGENT_KINDS:
            found.append(f"{entry.get('decision', '?')} {entry.get('kind')}: "
                         f"{str(entry.get('claim', '')).strip()}")
    return found


def compose_pr(task: Task, attempts: int, digest: str, meta: dict[str, Any],
               results: list[GateResult], worktree: Path) -> tuple[str, str]:
    """(title, body), composed entirely from records. The agent's output
    appears nowhere: if it had something to say beyond code, it belongs in
    an execution-log entry with evidence."""
    summary = task.intent.strip().splitlines()[0] if task.intent.strip() else "task"
    if len(summary) > 72:  # a folded intent is one long line; titles are not
        summary = summary[:71].rstrip() + "…"
    title = f"{task.id}: {summary}"

    lines = [f"**Task** {task.id} · attempt {attempts} · config `{digest}`", ""]
    if task.intent.strip():
        lines += ["## Contract", task.intent.strip(), ""]
    if task.acceptance:
        lines += ["## Acceptance",
                  *(f"- `{command}`" for command in task.acceptance), ""]
    if results:
        lines += ["## Gates",
                  *(f"- {r.name}: {r.outcome} ({r.duration_s:.1f}s)"
                    for r in results), ""]
    if task.decisions:
        lines += ["## Inherited decisions",
                  *(f"- {d.id} ({d.grade}): {d.text}" for d in task.decisions), ""]
    divergences = _divergences(worktree, task.id)
    if divergences:
        lines += ["## Divergences", *(f"- {d}" for d in divergences), ""]
    cost = meta.get("cost_usd")
    trace = meta.get("trace_ref")
    footer = [f"agent: {meta.get('adapter')}" ]
    if cost is not None:
        footer.append(f"cost: ${cost:.4f}")
    if trace:
        footer.append(f"trace: {trace}")
    lines.append(" · ".join(footer))
    return title, "\n".join(lines)
