"""Review as a run (RFC 0005): the runner mints the review task when its
target's gates go green (D-5.11), composes the reviewer's input — the diff,
the target's contract, its inherited decisions and the gate results, never
the author's session trace (D-5.3) — and drives one attempt in a disposable
copy of the target worktree (D-5.2 as reworded by A-78). The reviewer runs the
battery it is judging inside that copy (D-5.16); nothing written in it
survives the review. Findings come back as data; unlocatable evidence is
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
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Broker,
    BrokerBudget,
    BrokerHandle,
    PrScm,
    PrVcs,
    Runtime,
    SandboxSpec,
)
from torve.application.runstate import RunState
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import (
    RunnerConfig,
    agent_timeout_for,
    image_for,
    sandbox_timeout_for,
    tier_for,
)
from torve.domain.attempt import Finding, GateResult
from torve.domain.states import TaskState
from torve.domain.task import SCHEMA_VERSION, Budget, Task
from torve.gates.evidence import filter_findings

# ----------------------- #


def mint_review_task(root: Path, target: Task, intent: str | None = None) -> Task:
    """Runner-minted at gated (D-5.11): the same contract shape with a
    different role (D-5.9), decisions inherited from the target, one
    iteration. The planner never mints these — review follows execution."""

    from torve.application.planner import next_task_number

    review = Task(
        id=f"T-{next_task_number(root):04d}",
        rfc=target.rfc,
        role="review",
        targets=[target.id],
        intent=intent
        or (f"Review {target.id}'s diff against its contract and inherited decisions."),
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


# ....................... #


def build_review_prompt(
    target: Task,
    diff_text: str,
    gate_results: list[GateResult],
    degraded: bool = False,
) -> str:
    """The reviewer's whole input (D-5.3: no author trace, ever). The
    calibration paragraph is deliberate — this reviewer sees a diff after
    green gates, where clean is the normal outcome; without permission to
    say so it manufactures work.

    The workspace paragraph is the other deliberate one: the reviewer is told
    what its tree actually is — a copy nobody reads back — and that running
    the change is allowed and expected, on this attempt's own clock."""

    gates_summary = (
        "\n".join(f"- {r.name}: {r.outcome}" for r in gate_results) or "- (none recorded)"
    )

    decisions = "\n".join(f"- {d.id} [{d.grade}] {d.text}" for d in target.decisions) or "- none"

    # The battery by name: told what the change was judged green on, the
    # reviewer can run that same battery rather than guess at it (D-5.16).
    acceptance = "\n".join(f"- {command}" for command in target.acceptance) or "- none declared"

    # A-79: the prompt points at the staged diff instead of embedding it —
    # a diff carrying vendored bulk exceeds any context, and a reviewer with
    # tools reads lazily. A short head rides along as orientation only.
    head_lines = diff_text.splitlines()[:80]
    diff_head = "\n".join(head_lines)
    diff_head_lines = len(head_lines)

    spec_block = (
        "No task contract exists for this change: you are reviewing in "
        "degraded mode. You have no scope and no inherited decisions — do "
        "NOT invent a specification to check against; spec-drift findings "
        "are unavailable, and that is expected."
        if degraded
        else f"## The task under review\n\nintent:\n{target.intent}\n\n"
        f"acceptance commands, yours to run in this copy:\n{acceptance}\n\n"
        f"inherited decisions:\n{decisions}"
    )

    return f"""# Review

You are reviewing a change, not landing one. This workspace is a copy of the
tree the change lives in — a copy that is destroyed when this review ends.
Nothing you write here reaches the branch, the candidate, or anyone's view of
them, so an edit of yours cannot fix what you found, and cannot count as
evidence for it either.

Run what you are judging. The target's acceptance commands and its gate
battery are yours to execute in this copy, and a change you have executed is
worth more than a change you have read: run the tests, not only the diff.
That execution spends this attempt's own budget and timeout — the review
never waits, never restarts, and never extends itself. A battery too slow to
finish inside the window is a finding about the battery; say so, and say what
you did not get to run.

{spec_block}

## Gate results

{gates_summary}

## The diff

The complete diff under review is staged at `.torve/tmp/review.diff` in
this workspace. Read it before judging anything — every finding must be
grounded in it. Pull surrounding context from the tree where a hunk
alone is ambiguous. Generated or vendored bulk in the diff (minified
bundles, lockfiles) may be skimmed by filename; the source-side hunks
carry the reviewable substance.

The first {diff_head_lines} lines, as orientation:

```diff
{diff_head}
```

## What to produce

Judge the change: is it wrong, unsafe, or contradicting an inherited
decision marked LOCKED? A small diff after green gates is often clean —
"no findings" is a normal, frequent outcome, not a failure to work.
Severities: blocker (the change is wrong, unsafe, or contradicts a LOCKED
decision), major (a reviewer would insist before merge), minor or nit
(preferences; at most two).

Every finding needs evidence that locates: a leading `path:line` citation
(against the files as they arrived here, not as you left them) followed by
" — " and one sentence, or a backticked command with the output it printed —
what you ran here is evidence exactly like a path:line is, and the output is
the part that locates. A finding whose evidence does not locate is discarded
unread.

Your final output must be exactly one JSON document, nothing after it:

{{"findings": [{{"severity": "major", "claim": "...", "evidence": "path.py:12 — ..."}}]}}

An empty list is a valid, complete review: {{"findings": []}}
"""


# ....................... #


def stage_review_copy(worktree: Path, copy: Path) -> Path:
    """The reviewer's workspace: a copy of the target worktree it may write in
    and run (D-5.2 as reworded by A-78), destroyed with its sandbox.

    Staged only once the review's whole input exists, so nothing read into the
    judgment can be something the reviewer then wrote. And `.git` does not
    travel with the copy: a worktree's gitfile is a pointer into the host's
    repository — the same pointer the OpenSandbox adapter strips from its
    workspace tar, and for the same reason — so a copy that carried it could
    write through the pointer into the very history the review exists to
    leave alone. The tree travels; the repository does not.
    """

    if copy.exists():
        shutil.rmtree(copy, ignore_errors=True)

    copy.parent.mkdir(parents=True, exist_ok=True)

    # symlinks are copied as links: the sandbox saw links, not targets, before
    # the copy existed, and resolving them here would reach outside the tree.
    return shutil.copytree(worktree, copy, ignore=shutil.ignore_patterns(".git"), symlinks=True)


# ....................... #


def destroy_review_copy(copy: Path) -> None:
    """Nothing written in the copy survives the review (D-5.2): the reviewer's
    edits, its build output, its scratch all die with its sandbox.

    `ignore_errors` is the point, not the omission of one: a cleanup that
    raises would replace the review's own verdict with a filesystem complaint,
    and a copy left behind by a dying runner is a `.wt` directory the reaper
    collects like any other worktree of a run that is no longer alive.
    """

    shutil.rmtree(copy, ignore_errors=True)


# ....................... #


class _FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    findings: list[Finding]


# ....................... #

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ....................... #


def parse_findings(output: str) -> list[Finding] | None:
    """The last JSON document with a `findings` key anywhere in the output,
    parsed and validated; None when no such document exists — recorded as
    unparseable, never invented as clean. Harness output is hostile ground:
    ANSI escapes are stripped and the document may span lines or be followed
    by session chatter, so balanced decoding wins over line splitting."""

    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last: object | None = None
    envelopes: list[str] = []

    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())

        except json.JSONDecodeError:
            continue

        if isinstance(document, dict) and "findings" in document:
            last = cast("dict[str, Any]", document)

        elif isinstance(document, dict):
            # A harness result envelope (`claude -p --output-format json`)
            # carries the reviewer's answer as the `result` string — the
            # findings document is inside it, escaped, invisible to this
            # scan (parse_drafts' envelope discipline, learned again when
            # an opus review's two blockers were dropped as unparseable).
            result: Any = cast("dict[str, Any]", document).get("result")

            if isinstance(result, str) and "findings" in result:
                envelopes.append(result)

    if last is None:
        for enveloped in envelopes:
            nested = parse_findings(enveloped)

            if nested is not None:
                return nested

    if last is None:
        return None

    try:
        return _FindingsDocument.model_validate(last).findings

    except ValidationError:
        return None


# ....................... #


@dataclass
class ReviewOutcome:
    review_id: str
    fact: str
    blockers: list[Finding] = field(default_factory=list)
    kept: list[Finding] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    unparseable: bool = False


# ....................... #


def run_review(
    root: Path,
    worktree: Path,
    target: Task,
    review: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    diff_text: str,
    gate_results: list[GateResult],
    config_digest: str,
    degraded: bool = False,
    broker: Broker | None = None,
    broker_handle: BrokerHandle | None = None,
) -> ReviewOutcome:
    """One review attempt in a disposable copy of the target worktree, which
    the reviewer may run and write in (D-5.2 as reworded by A-78). Produces the
    review's run state and telemetry record; the caller applies the consequence
    to the target.

    The order is the guarantee: the input, the executor's evidence and the
    mount point are all settled before the copy exists, so no write the
    reviewer makes can reach anything that was read into the judgment."""

    from torve.application.telemetry import agent_token_counts, append_record, broker_block

    tier = tier_for(config, review.tier)
    state = RunState(task_id=review.id, path=naming.state_file(root, review.id))
    state.transition(TaskState.CLAIMED, "runner-minted review")
    state.transition(TaskState.RUNNING, f"reviewing {target.id}")
    state.save()

    image = image_for(config, tier)

    # The reviewer's whole input, composed first (D-5.3: no author trace,
    # ever) — after this line the diff under judgment is text in a string, and
    # no state the copy can reach has any part in it. The diff itself is
    # staged as a file in the copy below (A-79): a diff carrying a vendored
    # bundle exceeds any prompt, and a reviewer with tools reads lazily.
    prompt = build_review_prompt(target, diff_text, gate_results, degraded=degraded)

    # T-0172: the harness names the session trace after the workspace it is
    # given. The executor's trace is taken in hand here, before the copy is
    # staged, and put back whatever the review does — its record cites those
    # bytes, and the review exists to look at them, not to become them.
    executor_trace = naming.trace_file(worktree, 1)
    saved_executor_trace = executor_trace.read_bytes() if executor_trace.is_file() else None

    # Only now does the reviewer get a tree: a copy of the target worktree at
    # the review's own conventional address (D-3.4), writable, and destroyed
    # with its sandbox.
    copy = stage_review_copy(worktree, naming.worktree(root, review.id))

    # A-79: the diff rides the copy as a file, not the prompt — composed
    # above, before the copy existed, so the guarantee is unchanged; the
    # prompt names the path and the reviewer reads what it needs.
    diff_path = copy / ".torve" / "tmp" / "review.diff"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text, encoding="utf-8")

    spec = SandboxSpec(
        name=naming.sandbox_name(review.id, state.run_id) + "-a1",
        image=image,
        labels=naming.labels(review.id, state.run_id, root),
        # The reviewer seat carries its own clock when its tier names one
        # (RFC 0035 §5.3, D-35.6): absent falls through to the globals. That
        # clock is also the bound on whatever the reviewer executes (D-5.16) —
        # no budget or timeout of its own exists to widen it.
        timeout_s=sandbox_timeout_for(config, tier),
        env_passthrough=tuple(tier.api_key_env),
    )

    handle = runtime.create(spec, copy)
    state.sandbox_id = handle.id
    state.save()

    import time as _time
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    started_at = _datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    review_clock = _time.monotonic()

    try:
        result = agent.run(
            AgentContext(
                task=review,
                attempt=1,
                workspace=copy,
                handle=handle,
                runtime=runtime,
                workdir=spec.workdir,
                timeout_s=agent_timeout_for(config, tier),
                prompt=prompt,
                broker=broker_handle,
            )
        )

    finally:
        runtime.destroy(handle)
        state.sandbox_id = None
        state.save()

        # The copy goes with the sandbox: whatever the reviewer wrote, built
        # or broke reaches no one, because it is no longer anywhere (D-5.2).
        destroy_review_copy(copy)

        # The reviewer's session may have been written over the executor's
        # trace path before anything else could fail — put the executor's
        # evidence back either way (or drop the review's session from a path
        # that never held one before).
        if saved_executor_trace is not None:
            executor_trace.write_bytes(saved_executor_trace)
        else:
            executor_trace.unlink(missing_ok=True)

    # The review's session lives under its own id — which, since the copy sits
    # at the review's own worktree address, is the name the harness gives it
    # here. The write keeps that true for an adapter that named its trace
    # somewhere else, and the record's trace_ref then points at evidence that
    # survives the review. Only an adapter that actually wrote a trace gets the
    # relocation (T-0176): an adapter that wrote none left no file, and a
    # record citing a harness-shaped path nothing produced is a fabricated
    # coordinate — as misleading as a missing one.
    review_trace_ref = None

    if result.trace_ref is not None:
        review_trace = naming.trace_file(naming.worktree(root, review.id), 1)
        review_trace.write_text(result.output, encoding="utf-8")
        review_trace_ref = str(review_trace)

    # T-0186: the reviewer's self-reported token counts must survive the
    # rebuild below — the rebuilt AgentResult is the base shape and carries
    # no token fields, so read them off the original result first.
    token_counts = agent_token_counts(result)

    result = AgentResult(
        exit_code=result.exit_code,
        output=result.output,
        cost_usd=result.cost_usd,
        model_version=result.model_version,
        trace_ref=review_trace_ref,
    )

    broker_usage = (
        broker.usage(broker_handle) if broker is not None and broker_handle is not None else None
    )

    findings = parse_findings(result.output)
    unparseable = findings is None
    kept: list[Finding] = []
    discarded: list[str] = []

    if findings is not None:
        # Evidence resolves against the tree under judgment, never against the
        # copy: a `path:line` the reviewer could only produce by writing there
        # is a coordinate about its own scratch, not about the change (D-5.4,
        # D-5.2). A backticked command with its output locates without a
        # filesystem at all — that is the shape execution evidence takes
        # inside the copy (D-5.16).
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
            "tier": review.tier,
            "adapter": getattr(agent, "kind", tier.adapter),
            "provider": tier.provider or None,
            "model": tier.model or None,
            "model_version": result.model_version,
            "cost_usd": result.cost_usd,
            "trace_ref": result.trace_ref,
            # The review's own span (the broker clock covers the whole run,
            # so it can never answer "how long was the review"): two wall
            # stamps for the humans, the monotonic duration as the truth.
            "started_at": started_at,
            "ended_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wall_time_s": round(_time.monotonic() - review_clock, 3),
            # The reviewer's token counts, flat beside cost (T-0186) — only
            # the reported ones, absent keys omitted (D-4.6).
            **token_counts,
            # The image tag beside the adapter (D-17.4) — the cost table's
            # harness column reads it; the attempt records already carry it.
            "image": image,
            "shadow": False,
            # The reviewer spends the run's budget on the same handle; its
            # counts ride the record beside the adapter's report (D-21.5).
            **(
                {"broker": broker_block(broker.name, broker_usage)}
                if broker is not None and broker_usage is not None
                else {}
            ),
        },
    }

    manifest = layout.gates_file(worktree)

    if manifest.is_file():
        from torve.config.manifest import load_manifest

        append_record(root / load_manifest(manifest).telemetry, record)

    state.transition(TaskState.GATED, "findings produced")
    state.transition(TaskState.REVIEWED, fact)
    state.transition(TaskState.READY, fact)
    state.save()

    return ReviewOutcome(
        review_id=review.id,
        fact=fact,
        blockers=blockers,
        kept=kept,
        discarded=discarded,
        unparseable=unparseable,
    )


# ----------------------- #
# The pull-request trigger (RFC 0005 §4): review on open and update,
# including pull requests no agent wrote.

PR_LEDGER = "pr-reviews.jsonl"


# ....................... #


@dataclass
class PrReviewOutcome:
    action: str  # reviewed | skipped | already reviewed
    detail: str = ""
    review_id: str | None = None
    findings: int = 0
    blockers: int = 0
    comment: str = ""


# ....................... #


def _reviewed_heads(root: Path) -> set[tuple[int, str]]:
    path = root / layout.TORVE_DIR / PR_LEDGER

    if not path.is_file():
        return set()

    heads: set[tuple[int, str]] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = cast("dict[str, Any]", json.loads(line))
            heads.add((int(row["pr"]), str(row["head"])))

    return heads


# ....................... #


def _pr_comment(review_id: str, head_sha: str, outcome: ReviewOutcome, degraded: bool) -> str:
    """Composed from the review's records, never the reviewer's prose —
    and posted by the runner: the reviewer holds no forge credential
    (D-5.2)."""

    lines = [f"torve review — {review_id} · head {head_sha[:12]} · {outcome.fact}"]

    if degraded:
        lines.append(
            "reviewed without a task contract — degraded input: no "
            "scope, no inherited decisions; spec-drift findings "
            "unavailable (D-5.8)"
        )

    ordered = outcome.blockers + [f for f in outcome.kept if f.severity != "blocker"]
    lines.extend(f"- [{f.severity}] {f.claim} ({f.evidence})" for f in ordered)
    lines += ["", "authority: the run store; this comment is a projection"]

    return "\n".join(lines)


# ....................... #


def review_pull_request(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    scm: PrScm,
    vcs: PrVcs,
    number: int,
    token: str | None = None,
    broker: Broker | None = None,
) -> PrReviewOutcome:
    """RFC 0005 §4: skip rules first (draft, zero changed files, configured
    authors, not open); one review per head — the pull regime's debounce,
    since rapid pushes collapse into whatever head is current when the
    trigger fires and a head reviews at most once; then degraded or
    task-informed input (D-5.8) and the findings posted back as one
    marker-deduped comment. Task state is never mutated here — blockers on
    a task-gated run escalate on that path; this one reports."""

    info = scm.pr_info(number)

    if info.state != "open":
        return PrReviewOutcome("skipped", f"pull request is {info.state or 'unknown'}")

    if info.draft:
        return PrReviewOutcome("skipped", "draft")

    if info.changed_files == 0:
        return PrReviewOutcome("skipped", "no changed files")

    if info.author in config.review.skip_authors:
        return PrReviewOutcome("skipped", f"author {info.author} is skipped by configuration")

    if (number, info.head_sha) in _reviewed_heads(root):
        return PrReviewOutcome("already reviewed", f"head {info.head_sha[:12]}")

    base_sha, head_sha = vcs.fetch_pr(root, number, info.base_ref, token)
    degraded, target = True, None

    for task_id in vcs.task_trailers(root, base_sha, head_sha):
        contract = layout.task_file(root, task_id)

        if contract.is_file():
            from torve.gates.context import load_task

            degraded, target = False, load_task(contract)
            break

    if target is None:
        target = Task(
            id=f"PR-{number}",
            role="implement",
            intent=f"Pull request #{number}: {info.title}",
            decisions=[],
        )

    review = mint_review_task(
        root,
        target,
        intent=(
            f"Review pull request #{number} at {head_sha[:12]} — no task contract, degraded input."
            if degraded
            else None
        ),
    )

    workdir = root / naming.WORKTREE_DIR / f"{review.id}.pr"
    vcs.worktree_at(root, head_sha, workdir)

    # The reviewer's provider rides the same broker as any run (RFC 0021):
    # the review sandbox sees the broker's URL and the run-scoped token,
    # never a key.
    broker_handle: BrokerHandle | None = None

    if broker is not None:
        from torve.application.runner import run_routing

        broker_handle = broker.open(
            review.id,
            run_routing(config, review, review_on=False),
            BrokerBudget(tokens=review.budget.tokens),
        )

    try:
        diff_text = vcs.diff(root, base_sha, head_sha)
        from torve.application.telemetry import config_hash, engine_event

        digest = config_hash(layout.gates_file(root), root, config)

        outcome = run_review(
            root,
            workdir,
            target,
            review,
            config,
            runtime,
            agent,
            diff_text,
            [],
            digest,
            degraded=degraded,
            broker=broker,
            broker_handle=broker_handle,
        )

    finally:
        if broker is not None and broker_handle is not None:
            broker.close(broker_handle)

        vcs.remove_worktree(root, workdir)

    url = scm.comment(
        number,
        _pr_comment(review.id, head_sha, outcome, degraded),
        f"review:{number}:{head_sha[:12]}",
    )

    ledger = root / layout.TORVE_DIR / PR_LEDGER

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "pr": number,
                    "head": head_sha,
                    "review": review.id,
                    "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            + "\n"
        )

    engine_event(
        root,
        "pr_review",
        {
            "pr": number,
            "head": head_sha,
            "review": review.id,
            "findings": len(outcome.kept),
            "blockers": len(outcome.blockers),
            "degraded": degraded,
        },
    )

    return PrReviewOutcome(
        "reviewed", outcome.fact, review.id, len(outcome.kept), len(outcome.blockers), url
    )
