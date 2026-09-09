"""What one attempt does inside its sandbox, and the mechanical leg that
does it without one (RFC 0046 §5.2).

Everything here is a step over a `Dispatch`: the regime advance a conviction
routes, the agent session itself, the revert leg — which is the same leg with
`git revert` where the agent would be — and the halted check the loop reads
after either.

The order of an attempt's endings is load-bearing (D-38.1). A broker refusal,
a halted divergence entry and a failed exit are inspected in the order the
loop acts on them, so the verdict on the row is the ending that actually
stopped the run; an attempt that goes on to a gate pass writes no row here,
because the gate record is the one that describes it.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pathspec import GitIgnoreSpec

from torve.application.dispatch import (
    Dispatch,
    attempt_row,
    cache_volumes,
    emit,
)
from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Broker,
    BrokerHandle,
    SandboxSpec,
    Vcs,
)
from torve.application.runstate import RunState
from torve.application.skills import materialize
from torve.application.telemetry import (
    TOKEN_FIELDS,
    agent_burn,
    agent_token_counts,
    broker_block,
    record_payload,
)
from torve.base import naming
from torve.config import layout
from torve.config.manifest import UNLABELED_AXIS, GateAxis, load_manifest
from torve.config.runconfig import (
    TierConfig,
    agent_timeout_for,
    effective_skill_sets,
    image_for,
    sandbox_timeout_for,
    tier_for,
    tier_name_for,
)
from torve.domain.attempt import GateResult
from torve.domain.states import EscalationReason
from torve.domain.task import Task

# ----------------------- #


# ....................... #


def previous_attempt_gate_red(state: RunState) -> bool:
    """D-27.11: whether the attempt about to dispatch follows a gate-red.
    `_attempt_loop` appends a "gates red: ..." fact without a transition
    (the state stays GATED, retried), so it sits one slot behind this
    attempt's own "attempt N dispatched" entry — never the last one."""

    return len(state.history) >= 2 and state.history[-2]["fact"].startswith("gates red:")


# ....................... #


# The fixed severity order of the gate axes, most severe first: retry
# selection resolves the rung of the most severe axis present among a red
# attempt's convictions (D-34.5). This order is this module's rule, not the
# vocabulary's — the manifest lists the same words in corpus order, and
# importing that list here would let a re-listing silently move the ladder.
AXIS_SEVERITY: tuple[GateAxis, ...] = ("functional", "boundary", "compliance", "form")


def retry_rung_for(
    tier: TierConfig,
    outcomes: Iterable[GateResult],
    gate_axes: Mapping[str, GateAxis],
) -> str:
    """The rung the red attempt's recorded gate outcomes resolve to (D-34.5):
    the seat's axis→rung mapping read at the most severe axis present among
    the attempt's convictions — outcome and state, the two fields every
    telemetry row carries; never a trace, a gate output or model text, so a
    replay of the rows reproduces this choice exactly.

    A conviction is a *blocking* fail or error: a shadow or quarantined
    failure reported beside the red never routes the retry (gate runner,
    §7.3 — measurement, not obstacle). A red whose record carries no
    conviction at all — the empty-diff refusal, an attempt that produced
    nothing — reads as functional, the fail-safe every unlabeled gate shares:
    route the retry up, never sideways.

    A boundary conviction resolves no rung (D-34.7), and its presence masks
    the lighter axes below compliance: a broken fence outranks the work's
    retry. The operator repairs it with a disclosed chore commit, not a
    heavier model. An axis the mapping names nothing for resolves no rung
    either — the attempt retries under the tier that just ran."""

    convicted = {
        gate_axes.get(result.name, UNLABELED_AXIS)
        for result in outcomes
        if result.outcome in ("fail", "error") and result.state == "blocking"
    }

    if not convicted:
        convicted = {UNLABELED_AXIS}

    rungs = tier.resolved_retry_variants()

    for axis in AXIS_SEVERITY:
        if axis in convicted:
            return "" if axis == "boundary" else rungs.get(axis, "")

    return ""  # unreachable: convicted holds only axes from the full AXIS_SEVERITY ladder


def _retry_gate_axes(worktree: Path) -> Mapping[str, GateAxis]:
    """Gate name → declared axis, read from the manifest of the tree that
    convicted — the same file the gate pass loaded, so selection classifies
    each conviction exactly as its declaration labels it. A missing or
    unreadable manifest maps nothing: every failing gate then reads as the
    unlabeled default, functional, the fail-safe that routes up (D-34.4)."""

    manifest_path = layout.gates_file(worktree)

    if not manifest_path.is_file():
        return {}

    try:
        resolved = load_manifest(manifest_path).resolved_gates()

    except (OSError, ValueError, yaml.YAMLError):
        return {}

    return {gate.name: gate.axis or UNLABELED_AXIS for gate in resolved}


# ....................... #


def advance_tier(run: Dispatch, state: RunState) -> Agent:
    """Resolve the regime this attempt runs under and return the Agent that
    will run it (D-27.11).

    One rung, routed by the conviction: the attempt after a gate-red resolves
    the tier the red attempt's recorded gate outcomes select — the seat's
    mapping at the most severe axis present, the scalar read as its
    functional sugar — instead of continuing under the tier that just ran.
    Any other attempt resolves the task's own tier, which is why an advance
    is never sticky: a green pass hands the next attempt back to the seat.

    Never fabricated. This only advances when the CLI wired an agent factory
    to actually build the resolved tier's Agent, so telemetry never stamps a
    tier that did not produce the work (D-27.1).
    """

    seat_name = tier_name_for(run.task)
    seat_tier = tier_for(run.config, seat_name)
    resolved_name, resolved_tier = seat_name, seat_tier
    retry_agent = run.deps.retry_agent

    if retry_agent is not None and previous_attempt_gate_red(state):
        rung = retry_rung_for(run.tier, run.convictions, _retry_gate_axes(run.worktree))

        if rung:
            resolved_name = rung
            resolved_tier = tier_for(run.config, resolved_name)

    if resolved_name != run.tier_name:
        resolved_image = image_for(run.config, resolved_tier)
        run.tier_name = resolved_name
        run.tier = resolved_tier
        run.image = resolved_image
        run.image_digest = run.deps.runtime.resolve_image(resolved_image)

    if resolved_name != seat_name and retry_agent is not None:
        return retry_agent(resolved_tier)

    return run.deps.agent


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


def _record_broker_usage(
    state: RunState, broker: Broker, broker_handle: BrokerHandle, agent_meta: dict[str, Any]
) -> None:
    """Stamps the attempt's broker usage into `agent_meta` and escalates on a
    budget refusal (D-21.6) — observed in progress, on the run that
    overspent, since the next request would be refused too."""

    usage = broker.usage(broker_handle)
    agent_meta["broker"] = broker_block(broker.name, usage)
    refused = usage.refusals.get("budget")

    if refused:
        state.escalate(
            EscalationReason.COST_ANOMALY,
            f"broker refused {refused} request(s) past the run's token budget",
        )


# ....................... #


async def run_agent_session(run: Dispatch, state: RunState) -> AgentResult:
    """One attempt: compose the sandbox, run the agent in it, record how it
    ended. The sandbox dies in the `finally` whatever happens — a cancelled
    task cannot await its own cleanup (D-4)."""

    deps, config, task = run.deps, run.config, run.task
    root, worktree, shadow, resume = run.root, run.worktree, run.shadow, run.resume

    run_agent = advance_tier(run, state)
    run_kind = getattr(run_agent, "kind", run.tier.adapter)
    run_real = run_kind != "fake"

    run.meta.update(
        tier=run.tier_name,
        # The attempt number, restamped where tier/adapter/model
        # already are (D-38.4): `attempts` incremented on entry to
        # running, so it names the attempt about to run.
        attempt=state.attempts,
        adapter=run_kind,
        provider=(run.tier.provider or None) if run_real else None,
        model=(run.tier.model or None) if run_real else None,
        image=run.image,
        image_digest=run.image_digest,
    )

    # The attempt's identity is settled here and nowhere earlier: the
    # tier a conviction routed to (D-27.11) is resolved above, so this
    # is the first moment the record would be true.
    emit(
        run,
        "attempt_started",
        state.attempts,
        tier=run.tier_name,
        agent=run_kind,
        image_digest=run.image_digest,
    )

    # The runner composes the sandbox's context: the role's skill set is
    # written from package data at dispatch (A-3) — the agent does not
    # "have skills installed", and nothing is checked into the repository.
    # Vendored skills resolve from the worktree's committed vendor
    # directory beside package data (RFC 0009 §4a) — reviewed repository
    # content instructing the agent about the work.
    #
    # RFC 0029 D-29.1/D-29.3: the resolved tier's `skills` — when set —
    # overrides the role-scoped set wholesale, for this role only; the
    # materializer's own resolution and refusals are untouched (D-29.2).
    run.meta["skills"] = materialize(
        task.role,
        worktree / ".torve" / "skills",
        effective_skill_sets(run.tier, task.role, config.skills.sets),
        layout.skills_vendor_dir(worktree),
    )

    # The context pack (RFC 0054 §5.6, D-54.10): the facts the corpus
    # cannot carry, written host-side from the record and the tree with no
    # model, beside the skills. A shadow run gets the time-invariant files
    # only, so a replay reads what the live attempt could have read and
    # never the rows written after it.
    from torve.application.contextpack import build as build_pack
    from torve.application.contextpack import materialize as materialize_pack

    materialize_pack(
        worktree,
        build_pack(
            root,
            root / config.rfcs.path,
            task,
            layout.gates_file(worktree),
            replay=shadow,
        ),
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

    env_passthrough, volumes = _sandbox_auth(run.tier, config.worker_slot) if run_real else ((), {})
    # The slot-suffixed derived cache this regime names — the runtime
    # adapter, not the agent adapter, is what makes a sandbox warm, so a
    # fake adapter's live sandbox carries it too. Empty under shadow, and
    # the gate battery reads the same function, so a pass can never judge
    # a warmer or colder tree than the attempt ran in (D-35.3).
    volumes = {**volumes, **cache_volumes(run)}
    infra_id = naming.shadow_id(task.id) if shadow else task.id

    spec = SandboxSpec(
        name=naming.sandbox_name(infra_id, state.run_id) + f"-a{state.attempts}",
        image=run.image,
        labels=naming.labels(infra_id, state.run_id, root),
        # The resolved tier's clock when it names one (RFC 0035 §5.3,
        # D-35.6): the heavy rung raises its own bound without touching
        # the global the gate passes and every untiered lane keep.
        timeout_s=sandbox_timeout_for(config, run.tier),
        env_passthrough=env_passthrough,
        volumes=volumes,
    )

    withheld = _withhold_never_send(worktree, config.providers.never_send)
    # The attempt's own clock, sandbox creation included — the broker's
    # wall_time_s spans the whole run and reads cumulative on retries.
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    attempt_started_at = _datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    attempt_clock = time.monotonic()
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
                # Same resolution as the sandbox bound above: one tier,
                # one clock, for both the agent and the platform over it
                # (D-35.6).
                timeout_s=agent_timeout_for(config, run.tier),
                broker=run.broker_handle,
                resume=resume,
            ),
        )

        deps.runtime.sync_out(handle, worktree)

        run.meta.update(
            model_version=result.model_version,
            cost_usd=result.cost_usd,
            trace_ref=result.trace_ref,
            started_at=attempt_started_at,
            ended_at=_datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            wall_time_s=round(time.monotonic() - attempt_clock, 3),
        )
        # The attempt's self-reported token counts ride the same block
        # (T-0186): only the counts the adapter reported — absent keys
        # stay absent, never zeroed (D-4.6's self-reported regime).
        #
        # Cleared first, because `run.meta` is one dict for the whole run
        # (T-0187): "absent stays absent" holds within an attempt and not
        # across them, so an attempt whose adapter reported nothing
        # inherited the previous attempt's counts and showed them beside
        # its own `cost_usd: null`. Unreported must read as unreported.
        for stale in (*TOKEN_FIELDS, "burn"):
            run.meta.pop(stale, None)

        run.meta.update(agent_token_counts(result))
        # The burn profile rides the block beside those totals (RFC 0039
        # §5.3): what the adapter derived at capture time from the
        # store's full bytes; a stream with no per-turn facts contributes
        # no key at all — no stream, no block (D-39.4).
        run.meta.update(agent_burn(result))

        # The broker's live counts ride the attempt record beside the
        # adapter's self-report (D-21.5). A budget refusal escalates in
        # progress, on the run that overspent (D-21.6): the next request
        # would be refused too, so the loop stops here.
        broker, wire = deps.broker, run.broker_handle

        if broker is not None and wire is not None:
            _record_broker_usage(state, broker, wire, run.meta)

        # Every path out of this hook ends in exactly one row (D-38.1).
        # The endings are inspected in the order the loop itself reads
        # them — escalation first, then the halted divergence entry,
        # then the agent's own failure — so the verdict on the row is
        # the ending the loop acts on. A clean attempt appends no row
        # here: the gates leg owns its verdict (green or gates_red).
        record: dict[str, Any] = {}

        if state.escalation is not None:
            # The broker refused the run's budget mid-attempt (D-21.6):
            # the spend happened, the gates will never run, and until
            # now this was the ending that recorded nothing at all.
            record = attempt_row(
                run,
                "broker_refused",
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                escalation=state.escalation.reason,
            )

        elif _log_has_halted_entry(worktree, task.id):
            # The halted divergence entry (RFC 0001 §4): terminal by
            # design, and today it ends the attempt silently.
            record = attempt_row(
                run,
                "halted",
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                escalation=str(EscalationReason.LOCKED_CONFLICT),
            )

        elif result.timed_out or result.exit_code != 0:
            # RFC 0004 §6: the spend happened even though the gates will
            # never run for this attempt — without a record here, a
            # budget-killed or timed-out attempt's cost vanishes from
            # every projection (four ~$4 first attempts were missing
            # from cost-and-iterations when this was found). This is
            # that record, now carrying its verdict (D-38.3).
            record = attempt_row(
                run,
                "agent_timeout" if result.timed_out else "agent_error",
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )

        # One record, both carriers (A-85). An ending that produced a
        # telemetry row emits that same row's content; an attempt that
        # goes on to a gate pass has no ending of its own to describe,
        # so it reports only what it spent and the gate's record is the
        # one that describes it.
        emit(
            run,
            "attempt_finished",
            state.attempts,
            **(
                record_payload(record, state.attempts)
                if record
                else {
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "wall_time_s": run.meta.get("wall_time_s"),
                    "cost_usd": result.cost_usd,
                }
            ),
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


def halted(run: Dispatch) -> bool:
    """A LOCKED conflict is written to the log as a halted entry; the loop
    reads the fact from the file, so the agent cannot cause the transition
    directly. The A-1 YAML log is parsed, not pattern-matched."""

    return _log_has_halted_entry(run.worktree, run.task.id)


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


def revert_leg(run: Dispatch) -> Callable[[RunState], Awaitable[AgentResult]]:
    """Revert is mechanical (RFC 0010 §7, D-10.7): the runner executes `git
    revert` itself — no agent, no attempt sandbox. The gates still run in
    theirs and the landing carries the revert's own provenance.

    Targets resolve here, before the first dispatch, so an unresolvable one
    fails loudly rather than at attempt three."""

    run.meta.update(adapter="revert", provider=None, model=None)
    shas = _revert_targets(run.task, run.deps.vcs, run.worktree)

    async def run_revert(state: RunState) -> AgentResult:
        # The mechanical attempt still stamps its number (D-38.4): its gate
        # record joins the trace convention like any other.
        run.meta["attempt"] = state.attempts
        done = await asyncio.to_thread(run.deps.vcs.revert, run.worktree, shas)

        if not done:
            raise RevertConflict(
                f"dependent-commit conflict reverting "
                f"{', '.join(run.task.targets)} — revert aborted, worktree clean"
            )

        _write_revert_log(run.worktree, run.task, state.attempts, shas)

        return AgentResult(exit_code=0, output=f"reverted {len(shas)} commit(s)")

    return run_revert
