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
import json
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
    Broker,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    Runtime,
    SandboxHandle,
    SandboxSpec,
    Scm,
    StoreFactory,
    Vcs,
    WorkspacePort,
)
from torve.application.runstate import Escalation, RunState
from torve.application.skills import materialize
from torve.application.taskstore import TaskStore
from torve.application.telemetry import (
    append_record,
    broker_block,
    build_record,
    config_hash,
    engine_event,
)
from torve.base import naming
from torve.config import layout
from torve.config.manifest import load_manifest
from torve.config.runconfig import (
    RunnerConfig,
    TierConfig,
    broker_in_force,
    image_for,
    tier_for,
    tier_name_for,
)
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
    # The egress broker adapter in force (RFC 0021): built by the CLI from
    # the configuration; None means the port was never wired (tests,
    # simulation). Under a configured broker the run opens it around the
    # attempts and closes it when the loop ends.
    broker: Broker | None = None
    # D-27.11: builds the Agent for a tier resolved mid-run — the attempt
    # after a gate-red, when the tier that just ran names a retry_variant.
    # Building an Agent is a CLI-layer act (it reaches into adapters), so
    # the runner is handed a factory rather than importing one; None means
    # retry_variant never fires for this dispatch — `agent` above keeps
    # running every attempt, and telemetry never stamps a tier that did not
    # actually produce the work.
    retry_agent: Callable[[TierConfig], Agent] | None = None


# ....................... #


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
    # One close per run, whatever path the loop exits by: the broker's
    # revocation and final usage land here (RFC 0021 §5.1).
    close: Callable[[], None] | None = None
    # Called once, only when the run ends on budget exhaustion — wallclock or
    # tokens (RFC 0026 D-26.8/9): commits whatever the worktree holds so the
    # next dispatch has a candidate tip to continue from. Never called on a
    # convicted escalation — that restarts from base unchanged.
    checkpoint: Callable[[RunState], None] | None = None


# ....................... #


# Continuation eligibility's shared marker (RFC 0026 D-26.8): every detail
# string this module writes for a wallclock-caused budget_exhausted
# escalation starts with it, and `_continuable` is the only reader — so
# generation and detection can never drift apart.
_WALLCLOCK_MARKER = "wallclock budget exhausted"


def _continuable(escalation: Escalation) -> bool:
    """D-26.8 (LOCKED): continuation fires only on budget exhaustion —
    wallclock or tokens — never on a gate conviction, review blocker or any
    judged escalation. `iterations` exhaustion (like `poison_ceiling`) is
    excluded on purpose: repeated red gates is a judgement on the work, not
    a clock running out, so it restarts from base like any conviction."""

    if escalation.reason == str(EscalationReason.COST_ANOMALY):
        return True

    return escalation.reason == str(
        EscalationReason.BUDGET_EXHAUSTED
    ) and escalation.detail.startswith(_WALLCLOCK_MARKER)


# ....................... #


def _elapsed_minutes(state: RunState) -> float:
    started = datetime.strptime(state.history[0]["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)

    return (datetime.now(UTC) - started).total_seconds() / 60


# ....................... #


async def drive_attempts(
    state: RunState, task: Task, config: RunnerConfig, hooks: AttemptHooks
) -> RunState:
    ceiling = config.poison_ceiling
    iterations = task.budget.iterations
    wallclock_minutes = task.budget.wallclock_minutes

    try:
        result = await _attempt_loop(state, task, hooks, ceiling, iterations, wallclock_minutes)

        # D-26.9: the checkpoint is what gives the next dispatch a candidate
        # tip to cut from — taken once, right where the loop actually ended,
        # never for a convicted escalation.
        if (
            hooks.checkpoint is not None
            and result.escalation is not None
            and _continuable(result.escalation)
        ):
            hooks.checkpoint(result)

        return result

    finally:
        # One close per run, whatever path the loop exits by — a green
        # landing, an escalation, a cancellation. The broker's token is
        # revoked and its final usage recorded here (RFC 0021 §5.1).
        if hooks.close is not None:
            hooks.close()


# ....................... #


async def _attempt_loop(
    state: RunState,
    task: Task,
    hooks: AttemptHooks,
    ceiling: int,
    iterations: int | None,
    wallclock_minutes: int | None = None,
) -> RunState:
    while True:
        # Poison ceiling is checked before dispatch, never after (RFC 0001 §4).
        if state.attempts >= ceiling:
            state.escalate(
                EscalationReason.POISON_CEILING, f"{state.attempts} attempts, ceiling {ceiling}"
            )

            return state

        if iterations is not None and state.attempts >= iterations:
            state.escalate(
                EscalationReason.BUDGET_EXHAUSTED, f"{state.attempts} attempts, budget {iterations}"
            )

            return state

        if wallclock_minutes is not None:
            elapsed = _elapsed_minutes(state)

            if elapsed >= wallclock_minutes:
                state.escalate(
                    EscalationReason.BUDGET_EXHAUSTED,
                    f"{_WALLCLOCK_MARKER}: {elapsed:.1f}m elapsed, budget {wallclock_minutes}m",
                )

                return state

        state.transition(TaskState.RUNNING, f"attempt {state.attempts + 1} dispatched")
        state.save()
        result = await hooks.attempt(state)

        # An attempt hook that escalated (the broker refusing the run's
        # budget, D-21.6) stops the loop: continuing would burn attempts
        # against a refusal that will not lift.
        if state.escalation is not None:
            return state

        if hooks.halted():
            # Terminal by design, not an error: the one case where a task
            # stops on working code (RFC 0001 §4).
            state.escalate(
                EscalationReason.LOCKED_CONFLICT,
                f"halted divergence entry in the {task.id} execution log",
            )

            return state

        if result.timed_out or result.exit_code != 0:
            fact = (
                "agent hit the hard timeout"
                if result.timed_out
                else f"agent exited {result.exit_code}"
            ) + "; gates not run"

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
            state.history.append(
                {
                    "at": state.heartbeat,
                    "from": str(state.state),
                    "to": str(state.state),
                    "fact": f"gates red: {summary}",
                }
            )

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


# ....................... #


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


# ....................... #


def _previous_attempt_gate_red(state: RunState) -> bool:
    """D-27.11: whether the attempt about to dispatch follows a gate-red.
    `_attempt_loop` appends a "gates red: ..." fact without a transition
    (the state stays GATED, retried), so it sits one slot behind this
    attempt's own "attempt N dispatched" entry — never the last one."""

    return len(state.history) >= 2 and state.history[-2]["fact"].startswith("gates red:")


# ....................... #


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


# ....................... #


def _restore_never_send(withheld: dict[Path, bytes]) -> None:
    for path, content in withheld.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


# ....................... #


def _sandbox_auth(tier: TierConfig, worker_slot: int) -> tuple[tuple[str, ...], dict[str, str]]:
    """(env_passthrough, volumes) for the tier's authentication route (RFC
    0004 §1): key names for api and harness, a per-slot volume for
    subscription (D-4.2), nothing for fake."""

    if tier.adapter in ("api", "harness"):
        return tuple(tier.api_key_env), {}

    if tier.adapter == "subscription":
        return (), {f"{tier.auth_volume}-{worker_slot}": tier.auth_mount}

    return (), {}


# ....................... #


class _SandboxExecutor:
    """ExecuteOnce over a fresh sandbox, created lazily so gate passes with no
    shell gates cost nothing, destroyed by the caller when the pass ends."""

    def __init__(self, runtime: Runtime, spec: SandboxSpec, workspace: Path) -> None:
        self.runtime, self.spec, self.workspace = runtime, spec, workspace
        self.handle: SandboxHandle | None = None

    # ....................... #

    def __call__(self, command: str, timeout: float) -> tuple[int | None, str]:
        if self.handle is None:
            self.handle = self.runtime.create(self.spec, self.workspace)

        result = self.runtime.exec(self.handle, command, timeout)

        return result.exit_code, result.output

    # ....................... #

    def close(self) -> None:
        if self.handle is not None:
            self.runtime.sync_out(self.handle, self.workspace)
            self.runtime.destroy(self.handle)
            self.handle = None


# ....................... #


def _run_gates_in_worktree(
    worktree: Path,
    task_id: str,
    config: RunnerConfig,
    runtime: Runtime,
    run_id: str,
    root: Path,
    agent_meta: dict[str, Any] | None = None,
    base: str | None = None,
    image: str | None = None,
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
            labels=naming.labels(task_id, run_id, root),
            timeout_s=config.runtime.sandbox_timeout,
        ),
        worktree,
    )

    try:
        ctx = build_context(
            worktree,
            manifest,
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


# ....................... #


def _agent_identity(meta: dict[str, Any]) -> str:
    """The commit author and Torve-Agent trailer value (RFC 0010 §3):
    adapter/model@model_version, degrading gracefully — a fake or mechanical
    attempt is named for what it is, never invented."""

    adapter = str(meta.get("adapter") or "unknown")
    model = meta.get("model")
    ident = f"{adapter}/{model}" if model else adapter
    version = meta.get("model_version")

    return f"{ident}@{version}" if version else ident


# ....................... #


def _provenance_message(task: Task, attempts: int, digest: str, meta: dict[str, Any]) -> str:
    """The full trailer set (D-10.4): enough that `git log --grep`
    reconstructs a task's history with the store offline. The subject
    carries the intent's head (D-10.6: composed from the contract, never
    the agent's prose) — a history readable without opening the task."""

    head = task.intent.strip().splitlines()[0].strip() if task.intent.strip() else ""

    if len(head) > 46:
        head = head[:45].rstrip() + "…"

    what = f" {head} —" if head else ""

    lines = [
        f"torve({task.id}):{what} attempt {attempts} green",
        "",
        f"Torve-Task: {task.id}",
        f"Torve-Attempt: {attempts}",
        f"Torve-Agent: {_agent_identity(meta)}",
        f"Torve-Config: {digest}",
    ]

    if task.decisions:
        graded = " ".join(f"{d.id}({d.grade})" for d in task.decisions)
        lines.append(f"Torve-Decisions: {graded}")

    return "\n".join(lines)


# ....................... #


class RevertConflict(RuntimeError):
    """A dependent-commit conflict while reverting: escalates as
    merge_conflict (RFC 0010 §7) — Torve does not resolve it."""


# ....................... #

_SHA = re.compile(r"[0-9a-f]{7,40}")


# ....................... #


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


# ....................... #


def _write_revert_log(worktree: Path, task: Task, attempt: int, shas: list[str]) -> None:
    """Every revert emits resolved entries against the inherited decisions
    (RFC 0010 §7): the reason work was undone reaches the next planning
    session as data, not folklore. Machine-written — a mechanical revert has
    no agent to write one."""

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    short = " ".join(sha[:10] for sha in shas)

    entries: list[dict[str, Any]] = [
        {
            "decision": d.id,
            "grade": str(d.grade),
            "kind": "resolved",
            "at": stamp,
            "attempt": attempt,
            "claim": f"the work under this decision was undone by {task.id}: "
            f"{', '.join(task.targets)} reverted mechanically, "
            "inverse tree staged for the landing commit",
            "evidence": f"`git revert --no-commit {short}` — clean",
            "action": "decided",
        }
        for d in task.decisions
    ]

    log_path = layout.log_file(worktree, task.id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "task": task.id, "drift_count": 0, "entries": entries},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


# ....................... #


def _review_gated(config: RunnerConfig, task: Task, shadow: bool) -> bool:
    """Review follows execution (D-5.11) only for live implement runs with
    the task-gated trigger configured — one predicate, shared by the review
    hook and the broker's routing derivation so they cannot disagree."""

    return not shadow and task.role == "implement" and "task_gated" in config.review.on


# ....................... #


def run_routing(
    config: RunnerConfig, task: Task, review_on: bool, include_retry: bool = False
) -> BrokerRouting:
    """The run's routing (D-21.4): every provider the run's agents will use,
    resolved once and handed to the broker. Dispatch allowed them at the CLI;
    the broker enforces them at the wire. A provider the broker configuration
    does not route is a configuration error, never a quiet fallback.

    `include_retry` (D-27.11) also routes the task's tier's `retry_variant`
    when one is named: the broker opens once, before the first attempt, so a
    provider only a later retry reaches must already be on the route table.
    """

    routes: list[BrokerRoute] = []
    base_name = tier_name_for(task)
    base_tier = tier_for(config, base_name)
    tier_names = [base_name]

    if include_retry and base_tier.retry_variant:
        tier_names.append(base_tier.retry_variant)

    if review_on:
        tier_names.append("reviewer")

    for tier_name in tier_names:
        tier = tier_for(config, tier_name)

        if tier.adapter == "fake" or not tier.provider:
            continue

        provider = config.broker.providers.get(tier.provider)

        if provider is None:
            if not broker_in_force(config):
                # The none adapter routes nothing at the wire: keys keep
                # their existing channel and an empty provider table is the
                # named default, not a configuration error (D-21.9).
                continue

            raise ValueError(
                f"tier {tier_name!r} uses provider {tier.provider!r} but the broker "
                "configuration routes no such provider — add it under broker.providers"
            )

        routes.append(
            BrokerRoute(
                provider=tier.provider, upstream=provider.upstream, key_env=provider.key_env
            )
        )

    return BrokerRouting(routes=tuple(routes))


# ....................... #


def _measured_config_eval_digests(root: Path, tier_name: str) -> tuple[str, str] | None:
    """(incumbent, candidate) digests the eval ledger's most recent
    config-eval verdict citing `tier_name` measured (D-27.7), or None when no
    verdict cites it — nothing has been measured, so nothing can have been
    displaced from it. The ledger is append-only, so the last matching line
    is the most recent."""

    from torve.application.evals import EVAL_LEDGER

    ledger = root / layout.TORVE_DIR / EVAL_LEDGER

    if not ledger.is_file():
        return None

    latest: tuple[str, str] | None = None

    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast(dict[str, Any], record)

        if row.get("kind") != "config-eval" or row.get("tier") != tier_name:
            continue

        raw_digests: Any = row.get("digests")

        if not isinstance(raw_digests, dict):
            continue

        digests = cast(dict[str, Any], raw_digests)
        incumbent, candidate = digests.get("incumbent"), digests.get("candidate")

        if isinstance(incumbent, str) and isinstance(candidate, str):
            latest = (incumbent, candidate)

    return latest


# ....................... #


def real_hooks(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    worktree: Path,
    shadow: bool = False,
    gates_base: str | None = None,
    resume: bool = False,
) -> AttemptHooks:
    tier_name = tier_name_for(task)
    tier = tier_for(config, tier_name)

    def _refuse_credentialed_brokered_tier(name: str, candidate: TierConfig) -> None:
        # D-21.1's second line: the configuration validator already refuses a
        # brokered tier that names a credential; the runner refuses again so
        # a programmatically-built configuration cannot slip a key name past
        # the validator into the sandbox's env. Checked for the retry_variant
        # too (D-27.11) — a run never dispatches under a regime it hasn't
        # already validated (D-27.1's spirit, applied ahead of time).
        if broker_in_force(config) and candidate.api_key_env:
            raise ValueError(
                f"tier {name!r} names api_key_env {candidate.api_key_env} under broker "
                f"{config.broker.adapter!r} — a brokered tier names no credential"
            )

    _refuse_credentialed_brokered_tier(tier_name, tier)

    if deps.retry_agent is not None and tier.retry_variant:
        _refuse_credentialed_brokered_tier(tier.retry_variant, tier_for(config, tier.retry_variant))

    # What actually runs, not what the tier configured — an --agent fake
    # override must not masquerade as a model in the telemetry.
    kind = getattr(deps.agent, "kind", tier.adapter)
    real = kind != "fake"
    # The digest is the sandbox's identity (D-17.1): resolved once, at
    # dispatch; None is recorded as unresolved, never invented.
    image = image_for(config, tier)
    image_digest = deps.runtime.resolve_image(image)

    # D-27.7: a candidate configuration displaces the incumbent default only
    # through a paired replay verdict recorded in the eval ledger citing both
    # digests — never by a definition edit quietly changing what a tier's
    # image tag resolves to. Scoped to the live (non-shadow) dispatch of the
    # task's own seat, with no explicit tier_variant named: a variant is
    # naming and running a candidate on purpose (free, per D-27.3), and the
    # eval loop's own shadow arms (run_config_eval) must not trip on the very
    # candidate they exist to measure.
    if not shadow and not task.tier_variant and image_digest is not None:
        measured = _measured_config_eval_digests(root, tier_name)

        if measured is not None and image_digest not in measured:
            incumbent, candidate = measured

            raise ValueError(
                f"tier {tier_name!r} now resolves image digest {image_digest!r}, "
                "which the most recent recorded verdict for this tier never "
                f"measured (it measured {incumbent!r} as the running default and "
                f"{candidate!r} as the candidate) — the configured image changed "
                "since that measurement with no new paired verdict backing it; "
                "record a fresh replay verdict before this task can dispatch, or "
                "name an explicit tier_variant to run a named candidate freely"
            )

    # What this run is actually under right now (D-27.11): seeded from the
    # task's own tier, advanced by `attempt()` only when a gate-red hands off
    # to a retry_variant. `gates()` reads it too — a gate pass judges the
    # same image the agent just ran under (D-3.8).
    current: dict[str, Any] = {
        "name": tier_name,
        "tier": tier,
        "image": image,
        "image_digest": image_digest,
    }

    # Denormalised into every record this run appends (RFC 0004 §6): which
    # adapter and model did the work cannot be reconstructed later. Shadow
    # gate passes are marked so the measurement population stays separable
    # from live attempts in one stream. `attempt()` restamps tier/adapter/
    # provider/model/image_digest every call — this is only the shape.
    agent_meta: dict[str, Any] = {
        "tier": tier_name,
        "adapter": kind,
        "provider": (tier.provider or None) if real else None,
        "model": (tier.model or None) if real else None,
        "model_version": None,
        "cost_usd": None,
        "trace_ref": None,
        "image_digest": image_digest,
        "shadow": shadow,
        # Per-skill attribution (RFC 0009 §5): filled with what materialize
        # actually wrote, so cohorts group by skill regime from the record
        # alone.
        "skills": None,
    }

    async def attempt(state: RunState) -> AgentResult:
        # D-27.11: one rung. The attempt after a gate-red resolves the tier
        # that just ran's retry_variant instead of continuing under it; any
        # other attempt resolves the task's own tier. Never fabricated —
        # this only fires when the CLI wired an agent factory to actually
        # build the resolved tier's Agent, so telemetry never stamps a
        # tier that did not produce the work (D-27.1).
        resolved_name, resolved_tier = tier_name, tier
        retry_agent = deps.retry_agent
        running_tier: TierConfig = current["tier"]

        if retry_agent is not None and _previous_attempt_gate_red(state) and running_tier.retry_variant:
            resolved_name = running_tier.retry_variant
            resolved_tier = tier_for(config, resolved_name)

        if resolved_name != current["name"]:
            resolved_image = image_for(config, resolved_tier)
            current.update(
                name=resolved_name,
                tier=resolved_tier,
                image=resolved_image,
                image_digest=deps.runtime.resolve_image(resolved_image),
            )

        if resolved_name != tier_name and retry_agent is not None:
            run_agent = retry_agent(resolved_tier)
        else:
            run_agent = deps.agent

        run_kind = getattr(run_agent, "kind", resolved_tier.adapter)
        run_real = run_kind != "fake"

        agent_meta.update(
            tier=resolved_name,
            adapter=run_kind,
            provider=(resolved_tier.provider or None) if run_real else None,
            model=(resolved_tier.model or None) if run_real else None,
            image_digest=current["image_digest"],
        )

        # The runner composes the sandbox's context: the role's skill set is
        # written from package data at dispatch (A-3) — the agent does not
        # "have skills installed", and nothing is checked into the repository.
        # Vendored skills resolve from the worktree's committed vendor
        # directory beside package data (RFC 0009 §4a) — reviewed repository
        # content instructing the agent about the work.
        agent_meta["skills"] = materialize(
            task.role,
            worktree / ".torve" / "skills",
            config.skills.sets,
            layout.skills_vendor_dir(worktree),
        )

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
            _sandbox_auth(resolved_tier, config.worker_slot) if run_real else ((), {})
        )
        infra_id = naming.shadow_id(task.id) if shadow else task.id

        spec = SandboxSpec(
            name=naming.sandbox_name(infra_id, state.run_id) + f"-a{state.attempts}",
            image=current["image"],
            labels=naming.labels(infra_id, state.run_id, root),
            timeout_s=config.runtime.sandbox_timeout,
            env_passthrough=env_passthrough,
            volumes=volumes,
        )

        withheld = _withhold_never_send(worktree, config.providers.never_send)
        handle = deps.runtime.create(spec, worktree)
        state.sandbox_id = handle.id
        state.save()

        try:
            result = await asyncio.to_thread(
                run_agent.run,
                AgentContext(
                    task=task,
                    attempt=state.attempts,
                    workspace=worktree,
                    handle=handle,
                    runtime=deps.runtime,
                    workdir=spec.workdir,
                    timeout_s=config.runtime.agent_timeout,
                    broker=broker_handle,
                    resume=resume,
                ),
            )

            deps.runtime.sync_out(handle, worktree)

            agent_meta.update(
                model_version=result.model_version,
                cost_usd=result.cost_usd,
                trace_ref=result.trace_ref,
            )

            # The broker's live counts ride the attempt record beside the
            # adapter's self-report (D-21.5). A budget refusal escalates in
            # progress, on the run that overspent (D-21.6): the next request
            # would be refused too, so the loop stops here.
            if broker is not None and broker_handle is not None:
                usage = broker.usage(broker_handle)
                agent_meta["broker"] = broker_block(broker.name, usage)

                if usage.refusals.get("budget"):
                    state.escalate(
                        EscalationReason.COST_ANOMALY,
                        f"broker refused {usage.refusals['budget']} request(s) "
                        "past the run's token budget",
                    )

            if result.timed_out or result.exit_code != 0:
                # RFC 0004 §6: the spend happened even though the gates will
                # never run for this attempt — without a record here, a
                # budget-killed or timed-out attempt's cost vanishes from
                # every projection (four ~$4 first attempts were missing
                # from cost-and-iterations when this was found).
                import torve as _torve
                from torve.config.manifest import Manifest as _Manifest
                from torve.domain.task import SCHEMA_VERSION as _TELEMETRY_SCHEMA

                # The record must never depend on the manifest existing —
                # a worktree with no gates.yaml still burned the money.
                manifest_file = layout.gates_file(worktree)
                telemetry_rel = (
                    load_manifest(manifest_file).telemetry
                    if manifest_file.is_file()
                    else _Manifest().telemetry
                )

                append_record(
                    root / telemetry_rel,
                    {
                        "schema_version": _TELEMETRY_SCHEMA,
                        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "config_hash": None,  # gates never ran; no manifest pass
                        "torve_version": _torve.__version__,
                        "task_id": task.id,
                        "agent": dict(agent_meta),
                        "decisions": [d.model_dump() for d in task.decisions],
                        "results": [],
                        "exit_code": result.exit_code,
                        "gates_run": False,
                        "timed_out": result.timed_out,
                    },
                )

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

    def checkpoint(final: RunState) -> None:
        # D-26.9: local only — the branch already lives in this repository,
        # so a worktree cut on the next dispatch resolves it without a push;
        # publishing a WIP tip is the eventual `land()`'s job, unchanged.
        # A trailer of its own (never Torve-Task) keeps this commit from
        # ever being mistaken for a landed candidate (D-10.4's grep, the
        # revert leg's `landed_shas`).
        message = (
            f"torve checkpoint {task.id}: attempt {final.attempts} exhausted its budget"
            f"\n\nTorve-Checkpoint: {task.id}\nTorve-Attempt: {final.attempts}"
        )
        author = f"{_agent_identity(agent_meta)} <agents@torve.local>"
        deps.vcs.commit_all(worktree, message, author, config.vcs.signing_key)

    # The last gate pass's results and patch, kept for the review's input —
    # the reviewer judges exactly what the gates judged.
    last_pass: dict[str, Any] = {"results": [], "patch": "", "digest": ""}

    async def gates(state: RunState) -> tuple[int, str, str]:
        exit_code, summary, digest, results, patch = await asyncio.to_thread(
            _run_gates_in_worktree,
            worktree,
            task.id,
            config,
            deps.runtime,
            state.run_id,
            root,
            agent_meta,
            gates_base,
            current["image"],
            current["image_digest"],
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

        sha = await asyncio.to_thread(
            deps.vcs.commit_all, worktree, message, author, config.vcs.signing_key
        )

        # The credential is resolved by NAME here, at the runner boundary
        # (D-4b): the value lives only in this process and the subprocess
        # environments the adapters compose.
        token = os.environ.get(config.scm.token_env) if config.scm.token_env else None

        pushed = (
            await asyncio.to_thread(
                # supersede (D-10.10, A-37): the attempt owns the task's
                # persistent branch — a prior candidate there is superseded
                # under lease, its feedback captured at the requeue.
                deps.vcs.push,
                worktree,
                naming.branch(task.id),
                token,
                True,
            )
            # Publication follows the forge leg (D-10.11, A-58): with
            # open_pr off the candidate stays local — pushing a branch is
            # publishing, and on a repository whose base was never pushed
            # it publishes the entire history.
            if sha and config.scm.open_pr
            else False
        )

        pr_url = ""

        if pushed and config.scm.open_pr:
            title, pr_body = compose_pr(
                task,
                state.attempts,
                digest,
                agent_meta,
                list(last_pass["results"]),
                worktree,
                changed=deps.vcs.changed_names(worktree),
            )

            pr_url = await asyncio.to_thread(
                deps.scm.open_pr, worktree, naming.branch(task.id), title, pr_body
            )

        fact = f"committed {sha[:10]}" if sha else "nothing to commit"
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

            return AgentResult(exit_code=0, output=f"reverted {len(revert_shas)} commit(s)")

        attempt_hook = revert_attempt

    review_hook = None

    if _review_gated(config, task, shadow):
        # Review follows execution (D-5.11): minted here, never by the
        # planner. A shadow replay measures the harness, not the reviewer.
        if deps.review_agent is None:
            raise ValueError(
                "review is configured (review.on: task_gated) but no reviewer agent was provided"
            )

        reviewer_agent = deps.review_agent

        async def review_hook_fn(state: RunState) -> str | None:
            from torve.application.review import mint_review_task, run_review

            review_task = mint_review_task(root, task)

            outcome = await asyncio.to_thread(
                run_review,
                root,
                worktree,
                task,
                review_task,
                config,
                deps.runtime,
                reviewer_agent,
                str(last_pass["patch"]),
                list(last_pass["results"]),
                str(last_pass["digest"]),
                broker=broker,
                broker_handle=broker_handle,
            )

            # The reviewer spends the same run budget: a refusal there is the
            # same cost_anomaly, stopped on the run that overspent (D-21.6).
            if broker is not None and broker_handle is not None:
                usage = broker.usage(broker_handle)

                if usage.refusals.get("budget"):
                    state.escalate(
                        EscalationReason.COST_ANOMALY,
                        f"broker refused {usage.refusals['budget']} request(s) "
                        "past the run's token budget during review",
                    )

                    return None

            if outcome.blockers:
                detail = "; ".join(f.claim for f in outcome.blockers)

                state.escalate(
                    EscalationReason.BLOCKER_FINDING, f"{outcome.review_id}: {detail[:300]}"
                )

                return None

            # The verdict the lane's require_review predicate reads
            # (D-6.14, A-43); cleared on the next entry to running.
            state.reviewed_by = outcome.review_id

            return f"{outcome.fact} ({outcome.review_id})"

        review_hook = review_hook_fn

    # The broker's life spans the run (RFC 0021 §5.1): one loopback route
    # per routed provider, a run-scoped token, and the task's token budget
    # held at the wire. `none` opens trivially and the record carries the
    # adapter in force either way (D-21.9: opting out is explicit). Opened
    # last — after every fallible step above — so a setup failure cannot
    # leak a live broker; the close hook revokes it when the loop ends.
    review_on = _review_gated(config, task, shadow)
    broker = deps.broker
    broker_handle: BrokerHandle | None = None
    routing: BrokerRouting = BrokerRouting()

    if broker is not None:
        routing = run_routing(config, task, review_on, include_retry=deps.retry_agent is not None)
        broker_handle = broker.open(task.id, routing, BrokerBudget(tokens=task.budget.tokens))

    def close() -> None:
        # The run's one close: the broker revokes the run-scoped token and
        # reports the authoritative usage (D-21.5). Wire refusals become
        # engine events — a refusal for a provider the run's routing carried
        # is a defect report about the configuration reader (D-21.4).
        if broker is None or broker_handle is None:
            return

        usage = broker.close(broker_handle)
        agent_meta["broker"] = broker_block(broker.name, usage)

        for provider, count in sorted(usage.refused_providers.items()):
            engine_event(
                root,
                "wire_routing_refusal",
                {
                    "task": task.id,
                    "broker": broker.name,
                    "provider": provider,
                    "count": count,
                    "routed": routing.route_for(provider) is not None,
                },
            )

        adapter_cost = agent_meta.get("cost_usd")

        if usage.cost_usd is not None and isinstance(adapter_cost, (int, float)):
            scale = max(abs(usage.cost_usd), abs(adapter_cost)) or 1.0

            if abs(usage.cost_usd - adapter_cost) / scale > config.broker.cost_tolerance:
                engine_event(
                    root,
                    "cost_divergence",
                    {
                        "task": task.id,
                        "broker": broker.name,
                        "broker_cost_usd": usage.cost_usd,
                        "adapter_cost_usd": adapter_cost,
                        "tolerance": config.broker.cost_tolerance,
                    },
                )

    return AttemptHooks(
        attempt=attempt_hook,
        halted=halted,
        gates=gates,
        land=land,
        review=review_hook,
        close=close,
        checkpoint=checkpoint,
    )


# ....................... #


async def _run_task_async(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    state: RunState,
    resume: bool = False,
) -> RunState:
    worktree = deps.workspace.create(task.id, resolve_base(root, config.base), resume=resume)
    state.worktree = str(worktree)
    state.save()
    hooks = real_hooks(root, task, config, deps, worktree, resume=resume)

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
                state.escalate(
                    EscalationReason.KILLED, "cancellation observed via the lease heartbeat"
                )

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

    if (
        record.status is DurableRunStatus.TIMED_OUT
        and state.escalation is None
        and state.state not in (TaskState.READY, TaskState.ABANDONED)
    ):
        state.escalate(
            EscalationReason.BUDGET_EXHAUSTED,
            f"{_WALLCLOCK_MARKER}: max run duration reached (store watchdog)",
        )

    if record.status is DurableRunStatus.FAILED:
        raise RuntimeError(f"durable run failed: {record.error}")

    return state


# ....................... #


class BlockedDispatch(RuntimeError):
    """Dispatch refused: another active run's scope intersects this task's
    (RFC 0006 §2 — prevention beats ordering). Never a silent wait: the
    cause is in the message and counted in telemetry (D-6.6)."""


# ....................... #


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


# ....................... #


def _should_resume(previous: RunState) -> bool:
    """A continuation is legible only immediately off its own escalation
    (D-26.9): `escalation` is never cleared by design (RFC 0001 §4's history
    is append-only), so without this the field would resurrect a long-dead
    budget exhaustion on an unrelated re-queue — a lane conflict auto-requeue
    or a review `revise` both land on QUEUED too. Requiring the immediately
    preceding transition to be `escalated` pins the field to the same event."""

    if previous.state is not TaskState.QUEUED or previous.escalation is None:
        return False

    if len(previous.history) < 2 or previous.history[-2].get("to") != str(TaskState.ESCALATED):
        return False

    return _continuable(previous.escalation)


# ....................... #


def run_task(root: Path, task: Task, config: RunnerConfig, deps: RunDeps) -> RunState:
    state_path = naming.state_file(root, task.id)
    resume = False

    if state_path.exists():
        previous = RunState.load(state_path)

        # QUEUED is a board re-queue (T-0059): the human act already
        # happened, and dispatch is exactly what it asked for.
        if previous.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.QUEUED):
            raise RuntimeError(
                f"{task.id} has an existing run in state {previous.state} "
                f"(run {previous.run_id[:8]}); triage it or `torve reap` first"
            )

        resume = _should_resume(previous)

    blocked = _blocking_overlap(root, task)

    if blocked is not None:
        blocker, path = blocked

        engine_event(
            root, "blocked_dispatch", {"task": task.id, "blocked_by": blocker, "path": path}
        )

        raise BlockedDispatch(f"blocked_by_overlap: {blocker} on {path}")

    state = RunState(task_id=task.id, path=state_path)
    fact = "torve run: single synchronous claim"
    state.transition(TaskState.CLAIMED, f"{fact} (continuation)" if resume else fact)

    try:
        return asyncio.run(_run_task_async(root, task, config, deps, state, resume=resume))

    except KeyboardInterrupt:
        if state.state not in (TaskState.READY, TaskState.ABANDONED, TaskState.ESCALATED):
            state.escalate(EscalationReason.KILLED, "interrupted by operator")

        return state
