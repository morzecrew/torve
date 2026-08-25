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
               results: list[GateResult], worktree: Path,
               changed: list[str] | None = None) -> tuple[str, str]:
    """(title, body), composed entirely from records. The agent's output
    appears nowhere: if it had something to say beyond code, it belongs in
    an execution-log entry with evidence. The body leads with what a
    reader decides from — what changed, whether the gates held, where the
    control surface is — and folds the contract behind a details block."""
    summary = task.intent.strip().splitlines()[0] if task.intent.strip() else "task"
    if len(summary) > 72:  # a folded intent is one long line; titles are not
        summary = summary[:71].rstrip() + "…"
    title = f"{task.id}: {summary}"

    lines = [f"**{task.id} · attempt {attempts} · config `{digest}`**", "",
             ("Reading surface: this pull request lands by fast-forward and "
              "the merge button is never used. Approval and revision live on "
              "the task's issue — `/torve approve` · `/torve revise`."), ""]
    if attempts > 1:
        lines += [(f"Attempt {attempts} supersedes the previous candidate on "
                   "this branch; its review threads were captured into the "
                   "revision record."), ""]
    if changed:
        lines += ["## Changed", *(f"- `{path}`" for path in changed), ""]
    if results:
        red = [r for r in results if r.outcome not in ("pass", "bypassed")]
        if red:
            lines += ["## Gates",
                      *(f"- {r.name}: {r.outcome} ({r.duration_s:.1f}s)"
                        for r in results), ""]
        else:
            slowest = max(results, key=lambda r: r.duration_s)
            lines += [(f"**Gates** — all {len(results)} pass "
                       f"(slowest: {slowest.name} {slowest.duration_s:.1f}s)."), ""]
    divergences = _divergences(worktree, task.id)
    if divergences:
        lines += ["## Divergences", *(f"- {d}" for d in divergences), ""]
    if task.decisions:
        lines += ["## Inherited decisions",
                  *(f"- {d.id} ({d.grade}): {d.text}" for d in task.decisions), ""]
    if task.intent.strip():
        lines += ["<details><summary>Contract</summary>", "",
                  task.intent.strip(), ""]
        if task.acceptance:
            lines += ["**Acceptance**",
                      *(f"- `{command}`" for command in task.acceptance), ""]
        lines += ["</details>", ""]
    cost = meta.get("cost_usd")
    trace = meta.get("trace_ref")
    model = meta.get("model")
    agent = (f"{meta.get('adapter')}/{model}" if model
             else str(meta.get("adapter")))
    footer = [f"agent: {agent}"]
    if cost is not None:
        footer.append(f"cost: ${cost:.4f}")
    if trace:
        # A host-absolute path says nothing on the forge — its basename
        # names the artefact; a URI reference stays whole.
        trace_text = str(trace)
        if "://" not in trace_text:
            trace_text = Path(trace_text).name
        footer.append(f"trace: {trace_text}")
    lines.append(" · ".join(footer))
    return title, "\n".join(lines)
