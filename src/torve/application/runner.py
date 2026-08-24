"""`torve run` — one task, synchronously, exit code is the outcome (D-3.1).

The attempt loop is one durable function executed through the TaskStore
facade (D-5): forze's runner owns the lease heartbeat, cancel observation and
fenced terminal writes; this module owns what the loop *means* — transitions
executed from facts, the poison ceiling checked before dispatch, gates
outside the agent session (D-3; shell gates in a fresh sandbox, a
decision logged in T-0003).

`drive_attempts` is the pure core, driven by hooks: `torve run` supplies the
real sandbox/gates/landing hooks, and the DST simulation supplies simulated
ones — one loop, two harnesses, so the invariants exercise the code that
ships (RFC 0003 §6).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from forze.application.contracts.durable.function import (
    DurableRunStatus,
    current_durable_run,
)
from forze.application.execution import ExecutionContext
from forze.base.primitives import JsonDict
from pathspec import GitIgnoreSpec

from torve.application.forge import compose_pr
from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Runtime,
    SandboxHandle,
    SandboxSpec,
    Scm,
    StoreFactory,
    Vcs,
    WorkspacePort,
)
from torve.application.runstate import RunState
from torve.application.skills import materialize
from torve.application.taskstore import TaskStore
from torve.application.telemetry import append_record, build_record, config_hash, engine_event
from torve.base import naming
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.config.runconfig import RunnerConfig, TierConfig, image_for, tier_for
from torve.domain.attempt import GateResult
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import Task
from torve.gates.context import build_context, resolve_base
from torve.gates.runner import run_gates

# ----------------------- #

# A LOCKED conflict is written to the log as a halted entry; the runner reads
# the fact from the file, the agent cannot cause the transition directly.
# The A-1 YAML log is parsed, not pattern-matched.


@dataclass
class RunDeps:
    workspace: WorkspacePort
    runtime: Runtime
    agent: Agent
    vcs: Vcs
    scm: Scm
    store: StoreFactory
    # The reviewer's agent (RFC 0005), built by the CLI from the reviewer
    # tier when review is configured — cross-model by pointing the tier at
    # a different vendor (D-5.1). None means review cannot run.
    review_agent: Agent | None = None


@dataclass
class AttemptHooks:
    """What one attempt does, one gate pass does, and one landing does. The
    loop below owns the states and facts; the hooks own the mechanism."""

    attempt: Callable[[RunState], Awaitable[AgentResult]]
    halted: Callable[[], bool]  # locked-conflict detection after an attempt
    gates: Callable[[RunState], Awaitable[tuple[int, str, str]]]  # exit, summary, config hash
    land: Callable[[RunState, str], Awaitable[str]]  # returns the recorded fact
    # After green gates, before landing (RFC 0005, D-5.11): returns the fact
    # for the reviewed transition, or None when the review escalated the
    # target. Absent -> the review-not-configured bridge.
    review: Callable[[RunState], Awaitable[str | None]] | None = None


async def drive_attempts(
    state: RunState, task: Task, config: RunnerConfig, hooks: AttemptHooks
) -> RunState:
    ceiling = config.poison_ceiling
    iterations = task.budget.iterations
    while True:
        # Poison ceiling is checked before dispatch, never after (RFC 0001 §4).
        if state.attempts >= ceiling:
            state.escalate(EscalationReason.POISON_CEILING,
                           f"{state.attempts} attempts, ceiling {ceiling}")
            return state
        if iterations is not None and state.attempts >= iterations:
            state.escalate(EscalationReason.BUDGET_EXHAUSTED,
                           f"{state.attempts} attempts, budget {iterations}")
            return state

        state.transition(TaskState.RUNNING, f"attempt {state.attempts + 1} dispatched")
        state.save()
        result = await hooks.attempt(state)

        if hooks.halted():
            # Terminal by design, not an error: the one case where a task
            # stops on working code (RFC 0001 §4).
            state.escalate(EscalationReason.LOCKED_CONFLICT,
                           f"halted divergence entry in the {task.id} execution log")
            return state

        if result.timed_out or result.exit_code != 0:
            fact = ("agent hit the hard timeout" if result.timed_out
                    else f"agent exited {result.exit_code}") + "; gates not run"
            state.transition(TaskState.GATED, fact)
            state.save()
            continue

        state.transition(TaskState.GATED, "agent exited 0; gates running")
        state.save()
        try:
            exit_code, summary, digest = await hooks.gates(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, repr(exc))
            return state

        if exit_code != 0:
            state.history.append({"at": state.heartbeat, "from": str(state.state),
                                  "to": str(state.state), "fact": f"gates red: {summary}"})
            state.save()
            continue

        if hooks.review is not None:
            review_fact = await hooks.review(state)
            if review_fact is None:
                return state  # a surviving blocker escalated the target
            state.transition(TaskState.REVIEWED, review_fact)
        else:
            state.transition(TaskState.REVIEWED, "gates green; review not configured")
        fact = await hooks.land(state, digest)
        state.transition(TaskState.READY, fact)
        state.save()
        return state


def _log_has_halted_entry(worktree: Path, task_id: str) -> bool:
    log = layout.log_file(worktree, task_id)
    if not log.is_file():
        return False
    try:
        document = yaml.safe_load(log.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return False  # an unreadable log is the decisions-reported gate's finding
    if not isinstance(document, dict):
        return False
    entries: Any = cast(dict[str, Any], document).get("entries")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(e, dict) and str(cast(dict[str, Any], e).get("action", "")) == "halted"
        for e in cast(list[object], entries)
    )


def _withhold_never_send(worktree: Path, globs: list[str]) -> dict[Path, bytes]:
    """Lift `never_send` files out of the worktree for the attempt (RFC 0004
    §6b): the sandbox mounts the worktree, so anything present may reach the
    provider. A worktree's `.git` is a host-side pointer the sandbox cannot
    follow, so removal here is removal from the sandbox's world. Contents are
    restored from memory after `sync_out`; an agent edit to a withheld path is
    discarded — the policy protects the file in both directions."""
    if not globs:
        return {}
    spec = GitIgnoreSpec.from_lines(globs)
    withheld: dict[Path, bytes] = {}
    for path in sorted(worktree.rglob("*")):
        rel = path.relative_to(worktree)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if path.is_file() and spec.match_file(str(rel)):
            withheld[path] = path.read_bytes()
            path.unlink()
    return withheld


def _restore_never_send(withheld: dict[Path, bytes]) -> None:
    for path, content in withheld.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _sandbox_auth(tier: TierConfig, worker_slot: int) -> tuple[tuple[str, ...], dict[str, str]]:
    """(env_passthrough, volumes) for the tier's authentication route (RFC
    0004 §1): key names for api and harness, a per-slot volume for
    subscription (D-4.2), nothing for fake."""
    if tier.adapter in ("api", "harness"):
        return tuple(tier.api_key_env), {}
    if tier.adapter == "subscription":
        return (), {f"{tier.auth_volume}-{worker_slot}": tier.auth_mount}
    return (), {}


class _SandboxExecutor:
    """ExecuteOnce over a fresh sandbox, created lazily so gate passes with no
    shell gates cost nothing, destroyed by the caller when the pass ends."""

    def __init__(self, runtime: Runtime, spec: SandboxSpec, workspace: Path) -> None:
        self.runtime, self.spec, self.workspace = runtime, spec, workspace
        self.handle: SandboxHandle | None = None

    def __call__(self, command: str, timeout: float) -> tuple[int | None, str]:
        if self.handle is None:
            self.handle = self.runtime.create(self.spec, self.workspace)
        result = self.runtime.exec(self.handle, command, timeout)
        return result.exit_code, result.output

    def close(self) -> None:
        if self.handle is not None:
            self.runtime.sync_out(self.handle, self.workspace)
            self.runtime.destroy(self.handle)
            self.handle = None


def _run_gates_in_worktree(
    worktree: Path, task_id: str, config: RunnerConfig, runtime: Runtime,
    run_id: str, root: Path, agent_meta: dict[str, Any] | None = None,
    base: str | None = None, image: str | None = None,
    image_digest: str | None = None,
) -> tuple[int, str, str, list[GateResult], str]:
    """(exit_code, summary, config_hash, results, patch) — the results and
    the patch feed the review's input when one is configured. Raises on
    infrastructure failure."""
    manifest_path = layout.gates_file(worktree)
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no gate manifest in the worktree ({manifest_path}) — gates are fail-closed"
        )
    manifest = load_manifest(manifest_path)

    task_file = layout.task_file(worktree, task_id)
    source = layout.task_file(root, task_id)
    if not task_file.is_file() and source.is_file():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, task_file)

    executor = _SandboxExecutor(
        runtime,
        SandboxSpec(
            name=naming.sandbox_name(task_id, run_id) + "-gates",
            # Shell gates run over the same image the agent ran under
            # (D-3.8) — an image swap between attempt and gates would be
            # its own regime change.
            image=image or config.runtime.image,
            labels=naming.labels(task_id, run_id),
            timeout_s=config.runtime.sandbox_timeout,
        ),
        worktree,
    )
    try:
        ctx = build_context(
            worktree, manifest,
            base=resolve_base(worktree, base or config.base),
            task_path=task_file if task_file.is_file() else None,
        )
        ctx.execute = executor
        report = run_gates(ctx)
    finally:
        executor.close()

    digest = config_hash(manifest_path, worktree, config, image_digest=image_digest)
    record = build_record(ctx, report, digest, agent=agent_meta)
    append_record(root / manifest.telemetry, record)
    summary = ", ".join(f"{r.name}={r.outcome}" for r in report.results)
    return report.exit_code, summary, digest, report.results, ctx.patch


def _agent_identity(meta: dict[str, Any]) -> str:
    """The commit author and Torve-Agent trailer value (RFC 0010 §3):
    adapter/model@model_version, degrading gracefully — a fake or mechanical
    attempt is named for what it is, never invented."""
    adapter = str(meta.get("adapter") or "unknown")
    model = meta.get("model")
    ident = f"{adapter}/{model}" if model else adapter
    version = meta.get("model_version")
    return f"{ident}@{version}" if version else ident


def _provenance_message(task: Task, attempts: int, digest: str,
                        meta: dict[str, Any]) -> str:
    """The full trailer set (D-10.4): enough that `git log --grep`
    reconstructs a task's history with the store offline."""
    lines = [f"torve({task.id}): attempt {attempts} green", "",
             f"Torve-Task: {task.id}",
             f"Torve-Attempt: {attempts}",
             f"Torve-Agent: {_agent_identity(meta)}",
             f"Torve-Config: {digest}"]
    if task.decisions:
        graded = " ".join(f"{d.id}({d.grade})" for d in task.decisions)
        lines.append(f"Torve-Decisions: {graded}")
    return "\n".join(lines)


class RevertConflict(RuntimeError):
    """A dependent-commit conflict while reverting: escalates as
    merge_conflict (RFC 0010 §7) — Torve does not resolve it."""


_SHA = re.compile(r"[0-9a-f]{7,40}")


def _revert_targets(task: Task, vcs: Vcs, worktree: Path) -> list[str]:
    """Each target is a task id — resolved to its landed commits via the
    Torve-Task trailer — or an explicit sha. An unresolvable target is a
    contract error, raised before the first attempt dispatches."""
    shas: list[str] = []
    for target in task.targets:
        if _SHA.fullmatch(target):
            shas.append(target)
            continue
        landed = vcs.landed_shas(worktree, target)
        if not landed:
            raise ValueError(
                f"revert target {target!r} has no landed commits in this "
                "worktree's history — name a task that landed, or an "
                "explicit commit sha"
            )
        shas.extend(landed)
    return shas


def _write_revert_log(worktree: Path, task: Task, attempt: int,
                      shas: list[str]) -> None:
    """Every revert emits resolved entries against the inherited decisions
    (RFC 0010 §7): the reason work was undone reaches the next planning
    session as data, not folklore. Machine-written — a mechanical revert has
    no agent to write one."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    short = " ".join(sha[:10] for sha in shas)
    entries: list[dict[str, Any]] = [
        {"decision": d.id, "grade": str(d.grade), "kind": "resolved",
         "at": stamp, "attempt": attempt,
         "claim": f"the work under this decision was undone by {task.id}: "
                  f"{', '.join(task.targets)} reverted mechanically, "
                  "inverse tree staged for the landing commit",
         "evidence": f"`git revert --no-commit {short}` — clean",
         "action": "decided"}
        for d in task.decisions
    ]
    log_path = layout.log_file(worktree, task.id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(yaml.safe_dump(
        {"schema_version": 1, "task": task.id, "drift_count": 0,
         "entries": entries}, sort_keys=False), encoding="utf-8")


def real_hooks(
    root: Path, task: Task, config: RunnerConfig, deps: RunDeps, worktree: Path,
    shadow: bool = False, gates_base: str | None = None,
) -> AttemptHooks:
    tier = tier_for(config, task.tier)
    # What actually runs, not what the tier configured — an --agent fake
    # override must not masquerade as a model in the telemetry.
    kind = getattr(deps.agent, "kind", tier.adapter)
    real = kind != "fake"
    # The digest is the sandbox's identity (D-17.1): resolved once, at
    # dispatch; None is recorded as unresolved, never invented.
    image = image_for(config, tier)
    image_digest = deps.runtime.resolve_image(image)
    # Denormalised into every record this run appends (RFC 0004 §6): which
    # adapter and model did the work cannot be reconstructed later. Shadow
    # gate passes are marked so the measurement population stays separable
    # from live attempts in one stream.
    agent_meta: dict[str, Any] = {
        "tier": task.tier, "adapter": kind,
        "provider": (tier.provider or None) if real else None,
        "model": (tier.model or None) if real else None,
        "model_version": None, "cost_usd": None, "trace_ref": None,
        "image_digest": image_digest,
        "shadow": shadow,
        # Per-skill attribution (RFC 0009 §5): filled with what materialize
        # actually wrote, so cohorts group by skill regime from the record
        # alone.
        "skills": None,
    }

    async def attempt(state: RunState) -> AgentResult:
        # The runner composes the sandbox's context: the role's skill set is
        # written from package data at dispatch (A-3) — the agent does not
        # "have skills installed", and nothing is checked into the repository.
        # Vendored skills resolve from the worktree's committed vendor
        # directory beside package data (RFC 0009 §4a) — reviewed repository
        # content instructing the agent about the work.
        agent_meta["skills"] = materialize(
            task.role, worktree / ".torve" / "skills",
            config.skills.sets, layout.skills_vendor_dir(worktree))
        # The revision loop (RFC 0005 §4a, D-5.13): a retry's feedback
        # record travels into the sandbox beside the skills; the prompt
        # names it as untrusted review data.
        from torve.application.feedback import feedback_file

        captured = feedback_file(root, task.id)
        planted = worktree / ".torve" / "feedback.md"
        if captured.is_file():
            import shutil as _shutil

            planted.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(captured, planted)
        env_passthrough, volumes = (
            _sandbox_auth(tier, config.worker_slot) if real else ((), {})
        )
        infra_id = naming.shadow_id(task.id) if shadow else task.id
        spec = SandboxSpec(
            name=naming.sandbox_name(infra_id, state.run_id) + f"-a{state.attempts}",
            image=image,
            labels=naming.labels(infra_id, state.run_id),
            timeout_s=config.runtime.sandbox_timeout,
            env_passthrough=env_passthrough,
            volumes=volumes,
        )
        withheld = _withhold_never_send(worktree, config.providers.never_send)
        handle = deps.runtime.create(spec, worktree)
        state.sandbox_id = handle.id
        state.save()
        try:
            result = await asyncio.to_thread(deps.agent.run, AgentContext(
                task=task, attempt=state.attempts, workspace=worktree,
                handle=handle, runtime=deps.runtime, workdir=spec.workdir,
                timeout_s=config.runtime.agent_timeout,
            ))
            deps.runtime.sync_out(handle, worktree)
            agent_meta.update(model_version=result.model_version,
                              cost_usd=result.cost_usd, trace_ref=result.trace_ref)
            return result
        finally:
            # Synchronous on purpose: a cancelled task cannot await its own
            # cleanup, and the sandbox must die regardless (D-4).
            deps.runtime.destroy(handle)
            _restore_never_send(withheld)
            # The planted record was for this attempt's eyes (D-5.13,
            # T-0076): it leaves the tree before the gates measure it —
            # the feedback channel steers the attempt, never the candidate,
            # and a planted file the scope gate can see would fail every
            # revision against its own contract.
            planted.unlink(missing_ok=True)
            state.sandbox_id = None
            state.save()

    def halted() -> bool:
        return _log_has_halted_entry(worktree, task.id)

    # The last gate pass's results and patch, kept for the review's input —
    # the reviewer judges exactly what the gates judged.
    last_pass: dict[str, Any] = {"results": [], "patch": "", "digest": ""}

    async def gates(state: RunState) -> tuple[int, str, str]:
        exit_code, summary, digest, results, patch = await asyncio.to_thread(
            _run_gates_in_worktree, worktree, task.id, config, deps.runtime,
            state.run_id, root, agent_meta, gates_base, image, image_digest,
        )
        last_pass.update(results=results, patch=patch, digest=digest)
        return exit_code, summary, digest

    async def land(state: RunState, digest: str) -> str:
        # The commit is the runner's artefact (D-10.1), composed here where
        # the attempt's model_version is already known: author is the agent
        # identity (D-10.2), trailers complete (D-10.4), one commit per
        # attempt (D-10.8), signed outside the sandbox when a key is
        # configured (D-10.3).
        message = _provenance_message(task, state.attempts, digest, agent_meta)
        author = f"{_agent_identity(agent_meta)} <agents@torve.local>"
        sha = await asyncio.to_thread(deps.vcs.commit_all, worktree, message,
                                      author, config.vcs.signing_key)
        # The credential is resolved by NAME here, at the runner boundary
        # (D-4b): the value lives only in this process and the subprocess
        # environments the adapters compose.
        token = (os.environ.get(config.scm.token_env)
                 if config.scm.token_env else None)
        pushed = (await asyncio.to_thread(
            deps.vcs.push, worktree, naming.branch(task.id), token)
            if sha else False)
        pr_url = ""
        if pushed and config.scm.open_pr:
            title, pr_body = compose_pr(task, state.attempts, digest,
                                        agent_meta, list(last_pass["results"]),
                                        worktree)
            pr_url = await asyncio.to_thread(
                deps.scm.open_pr, worktree, naming.branch(task.id), title, pr_body)
        fact = (f"committed {sha[:10]}" if sha else "nothing to commit")
        fact += f"; pushed={pushed}" + (f"; pr={pr_url}" if pr_url else "; pr deferred")
        return fact

    attempt_hook = attempt
    if task.role == "revert":
        # Revert is mechanical (RFC 0010 §7, D-10.7): the runner executes
        # git revert itself — no agent, no attempt sandbox; the gates still
        # run in theirs and the landing carries the revert's own provenance.
        # Targets resolve before the first dispatch so an unresolvable one
        # fails loudly, like a misconfigured review.
        agent_meta.update(adapter="revert", provider=None, model=None)
        revert_shas = _revert_targets(task, deps.vcs, worktree)

        async def revert_attempt(state: RunState) -> AgentResult:
            done = await asyncio.to_thread(deps.vcs.revert, worktree, revert_shas)
            if not done:
                raise RevertConflict(
                    f"dependent-commit conflict reverting "
                    f"{', '.join(task.targets)} — revert aborted, worktree clean"
                )
            _write_revert_log(worktree, task, state.attempts, revert_shas)
            return AgentResult(exit_code=0,
                               output=f"reverted {len(revert_shas)} commit(s)")

        attempt_hook = revert_attempt

    review_hook = None
    if not shadow and task.role == "implement" and "task_gated" in config.review.on:
        # Review follows execution (D-5.11): minted here, never by the
        # planner. A shadow replay measures the harness, not the reviewer.
        if deps.review_agent is None:
            raise ValueError(
                "review is configured (review.on: task_gated) but no reviewer "
                "agent was provided"
            )
        reviewer_agent = deps.review_agent

        async def review_hook_fn(state: RunState) -> str | None:
            from torve.application.review import mint_review_task, run_review

            review_task = mint_review_task(root, task)
            outcome = await asyncio.to_thread(
                run_review, root, worktree, task, review_task, config,
                deps.runtime, reviewer_agent,
                str(last_pass["patch"]), list(last_pass["results"]),
                str(last_pass["digest"]),
            )
            if outcome.blockers:
                detail = "; ".join(f.claim for f in outcome.blockers)
                state.escalate(EscalationReason.BLOCKER_FINDING,
                               f"{outcome.review_id}: {detail[:300]}")
                return None
            return f"{outcome.fact} ({outcome.review_id})"

        review_hook = review_hook_fn

    return AttemptHooks(attempt=attempt_hook, halted=halted, gates=gates,
                        land=land, review=review_hook)


async def _run_task_async(
    root: Path, task: Task, config: RunnerConfig, deps: RunDeps, state: RunState
) -> RunState:
    worktree = deps.workspace.create(task.id, resolve_base(root, config.base))
    state.worktree = str(worktree)
    state.save()
    hooks = real_hooks(root, task, config, deps, worktree)

    async def body(_fctx: ExecutionContext, _input_json: JsonDict | None) -> JsonDict:
        bound = current_durable_run()
        if bound is not None:
            state.durable_run_id = bound.run_id
            state.save()
        try:
            final = await drive_attempts(state, task, config, hooks)
        except RevertConflict as exc:
            # RFC 0010 §7: a dependent-commit conflict escalates as
            # merge_conflict — the revert aborted and the worktree is clean.
            state.escalate(EscalationReason.MERGE_CONFLICT, str(exc))
            final = state
        except asyncio.CancelledError:
            if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
                state.escalate(EscalationReason.KILLED,
                               "cancellation observed via the lease heartbeat")
            raise
        return {
            "task_id": task.id,
            "state": str(final.state),
            "attempts": final.attempts,
            "escalation": final.escalation.reason if final.escalation else None,
        }

    store = await deps.store(config.store)
    taskstore = TaskStore(store, config.store)
    taskstore.register(body)
    record = await taskstore.run_now(
        {"task_id": task.id, "engine_run_id": state.run_id},
        idempotency_key=f"{task.id}:{state.run_id}",
    )

    if (record.status is DurableRunStatus.TIMED_OUT and state.escalation is None
            and state.state not in (TaskState.READY, TaskState.ABANDONED)):
        state.escalate(EscalationReason.BUDGET_EXHAUSTED,
                       "max run duration reached (store watchdog)")
    if record.status is DurableRunStatus.FAILED:
        raise RuntimeError(f"durable run failed: {record.error}")
    return state


class BlockedDispatch(RuntimeError):
    """Dispatch refused: another active run's scope intersects this task's
    (RFC 0006 §2 — prevention beats ordering). Never a silent wait: the
    cause is in the message and counted in telemetry (D-6.6)."""


def _blocking_overlap(root: Path, task: Task) -> tuple[str, str] | None:
    """(blocking task id, contended path) when an active run's allow-set
    intersects this task's; an empty allow-set is unconstrained and
    contends with everything."""
    from torve.application.planner import globs_intersect
    from torve.gates.context import load_task

    active = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}
    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        if state.task_id == task.id or state.state not in active:
            continue
        contract = root / layout.TORVE_DIR / "tasks" / state.task_id / "contract.yaml"
        if not contract.is_file():
            continue
        other = load_task(contract)
        if not task.scope.allow or not other.scope.allow:
            return state.task_id, "unconstrained scope"
        for mine in task.scope.allow:
            for theirs in other.scope.allow:
                if globs_intersect([mine], [theirs]):
                    return state.task_id, theirs
    return None


def run_task(root: Path, task: Task, config: RunnerConfig, deps: RunDeps) -> RunState:
    state_path = naming.state_file(root, task.id)
    if state_path.exists():
        previous = RunState.load(state_path)
        # QUEUED is a board re-queue (T-0059): the human act already
        # happened, and dispatch is exactly what it asked for.
        if previous.state not in (TaskState.READY, TaskState.ABANDONED,
                                  TaskState.QUEUED):
            raise RuntimeError(
                f"{task.id} has an existing run in state {previous.state} "
                f"(run {previous.run_id[:8]}); triage it or `torve reap` first"
            )

    blocked = _blocking_overlap(root, task)
    if blocked is not None:
        blocker, path = blocked
        engine_event(root, "blocked_dispatch",
                     {"task": task.id, "blocked_by": blocker, "path": path})
        raise BlockedDispatch(f"blocked_by_overlap: {blocker} on {path}")

    state = RunState(task_id=task.id, path=state_path)
    state.transition(TaskState.CLAIMED, "torve run: single synchronous claim")
    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state))
    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")
        return state
