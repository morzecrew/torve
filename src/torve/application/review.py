"""Review as a run (RFC 0005): the runner mints the review task when its
target's gates go green (D-5.11), composes the reviewer's input — the diff,
the target's contract, its inherited decisions and the gate results, never
the author's session trace (D-5.3) — and drives one attempt in a read-only
sandbox (D-5.2). Findings come back as data; unlocatable evidence is
discarded before anyone sees it (D-5.4), a surviving blocker escalates the
target as blocker_finding, and configuration — never the model — decided
that consequence (D-2).

v1 drives a single attempt (`budget: iterations: 1`, RFC 0005 §1.1) with the
same sandbox mechanics and telemetry shape as any run; the multi-attempt
loop for reviews arrives when a reviewer earns retries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from torve.application.ports import Agent, AgentContext, Runtime, SandboxSpec
from torve.application.runstate import RunState
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig, image_for, tier_for
from torve.domain.attempt import Finding, GateResult
from torve.domain.states import TaskState
from torve.domain.task import SCHEMA_VERSION, Budget, Task
from torve.gates.evidence import filter_findings

# ----------------------- #


def mint_review_task(root: Path, target: Task) -> Task:
    """Runner-minted at gated (D-5.11): the same contract shape with a
    different role (D-5.9), decisions inherited from the target, one
    iteration. The planner never mints these — review follows execution."""
    from torve.application.planner import next_task_number

    review = Task(
        id=f"T-{next_task_number(root):04d}",
        rfc=target.rfc,
        role="review",
        targets=[target.id],
        intent=(f"Review {target.id}'s diff against its contract and "
                "inherited decisions."),
        decisions=target.decisions,
        budget=Budget(iterations=1),
        tier="reviewer",
    )
    contract_dir = root / layout.TORVE_DIR / "tasks" / review.id
    contract_dir.mkdir(parents=True, exist_ok=True)
    document = review.model_dump(exclude_defaults=True)
    document["schema_version"] = SCHEMA_VERSION
    document["decisions"] = [d.model_dump() for d in review.decisions]
    (contract_dir / "contract.yaml").write_text(
        "# Minted by the runner at gated — review follows execution.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return review


def build_review_prompt(
    target: Task, diff_text: str, gate_results: list[GateResult],
    degraded: bool = False,
) -> str:
    """The reviewer's whole input (D-5.3: no author trace, ever). The
    calibration paragraph is deliberate — this reviewer sees a diff after
    green gates, where clean is the normal outcome; without permission to
    say so it manufactures work."""
    gates_summary = "\n".join(
        f"- {r.name}: {r.outcome}" for r in gate_results) or "- (none recorded)"
    decisions = "\n".join(
        f"- {d.id} [{d.grade}] {d.text}" for d in target.decisions) or "- none"
    spec_block = (
        "No task contract exists for this change: you are reviewing in "
        "degraded mode. You have no scope and no inherited decisions — do "
        "NOT invent a specification to check against; spec-drift findings "
        "are unavailable, and that is expected."
        if degraded else
        f"## The task under review\n\nintent:\n{target.intent}\n\n"
        f"inherited decisions:\n{decisions}"
    )
    return f"""# Review

You are reviewing a change, not fixing it. The workspace is read-only.

{spec_block}

## Gate results

{gates_summary}

## The diff

```diff
{diff_text}
```

## What to produce

Judge the change: is it wrong, unsafe, or contradicting an inherited
decision marked LOCKED? A small diff after green gates is often clean —
"no findings" is a normal, frequent outcome, not a failure to work.
Severities: blocker (the change is wrong, unsafe, or contradicts a LOCKED
decision), major (a reviewer would insist before merge), minor or nit
(preferences; at most two).

Every finding needs evidence that locates: a leading `path:line` citation
(against the files in this workspace) followed by " — " and one sentence,
or a backticked command with its output. A finding whose evidence does not
locate is discarded unread.

Your final output must be exactly one JSON document, nothing after it:

{{"findings": [{{"severity": "major", "claim": "...", "evidence": "path.py:12 — ..."}}]}}

An empty list is a valid, complete review: {{"findings": []}}
"""


class _FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    findings: list[Finding]


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_findings(output: str) -> list[Finding] | None:
    """The last JSON document with a `findings` key anywhere in the output,
    parsed and validated; None when no such document exists — recorded as
    unparseable, never invented as clean. Harness output is hostile ground:
    ANSI escapes are stripped and the document may span lines or be followed
    by session chatter, so balanced decoding wins over line splitting."""
    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last: object | None = None
    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and "findings" in document:
            last = cast("dict[str, Any]", document)
    if last is None:
        return None
    try:
        return _FindingsDocument.model_validate(last).findings
    except ValidationError:
        return None


@dataclass
class ReviewOutcome:
    review_id: str
    fact: str
    blockers: list[Finding] = field(default_factory=list)
    kept: list[Finding] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    unparseable: bool = False


def run_review(
    root: Path, worktree: Path, target: Task, review: Task,
    config: RunnerConfig, runtime: Runtime, agent: Agent,
    diff_text: str, gate_results: list[GateResult],
    config_digest: str, degraded: bool = False,
) -> ReviewOutcome:
    """One review attempt over the target's worktree, mounted read-only.
    Produces the review's run state and telemetry record; the caller applies
    the consequence to the target."""
    tier = tier_for(config, review.tier)
    state = RunState(task_id=review.id, path=naming.state_file(root, review.id))
    state.transition(TaskState.CLAIMED, "runner-minted review")
    state.transition(TaskState.RUNNING, f"reviewing {target.id}")
    state.save()

    image = image_for(config, tier)
    spec = SandboxSpec(
        name=naming.sandbox_name(review.id, state.run_id) + "-a1",
        image=image,
        labels=naming.labels(review.id, state.run_id),
        timeout_s=config.runtime.sandbox_timeout,
        env_passthrough=tuple(tier.api_key_env),
        workspace_read_only=True,
    )
    prompt = build_review_prompt(target, diff_text, gate_results, degraded=degraded)
    handle = runtime.create(spec, worktree)
    state.sandbox_id = handle.id
    state.save()
    try:
        result = agent.run(AgentContext(
            task=review, attempt=1, workspace=worktree, handle=handle,
            runtime=runtime, workdir=spec.workdir,
            timeout_s=config.runtime.agent_timeout, prompt=prompt,
        ))
    finally:
        runtime.destroy(handle)
        state.sandbox_id = None
        state.save()

    findings = parse_findings(result.output)
    unparseable = findings is None
    kept: list[Finding] = []
    discarded: list[str] = []
    if findings is not None:
        kept, discarded = filter_findings(findings, worktree)
    blockers = [f for f in kept if f.severity == "blocker"]

    if unparseable:
        fact = "review output unparseable — no findings recorded"
    elif blockers:
        fact = f"review found {len(blockers)} blocker(s)"
    elif kept:
        fact = f"review recorded {len(kept)} non-blocking finding(s)"
    else:
        fact = "review clean"

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "review",
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_hash": config_digest,
        "task_id": review.id,
        "target": target.id,
        "findings": [f.model_dump() for f in kept],
        "discarded": discarded,
        "unparseable": unparseable,
        "agent": {
            "tier": review.tier, "adapter": getattr(agent, "kind", tier.adapter),
            "provider": tier.provider or None, "model": tier.model or None,
            "model_version": result.model_version, "cost_usd": result.cost_usd,
            "trace_ref": result.trace_ref, "shadow": False,
        },
    }
    from torve.application.telemetry import append_record

    manifest = layout.gates_file(worktree)
    if manifest.is_file():
        from torve.config.manifest import load_manifest

        append_record(root / load_manifest(manifest).telemetry, record)

    state.transition(TaskState.GATED, "findings produced")
    state.transition(TaskState.REVIEWED, fact)
    state.transition(TaskState.READY, fact)
    state.save()
    return ReviewOutcome(review_id=review.id, fact=fact, blockers=blockers,
                         kept=kept, discarded=discarded, unparseable=unparseable)
