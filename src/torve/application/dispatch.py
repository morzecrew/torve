"""One dispatch of one task: its ports, its regime, and the facts its steps
share (RFC 0046).

A dispatch is one `torve run` — a worktree, a resolved tier, and up to
`poison_ceiling` attempts inside it. Everything a step of that run needs to
read, and the few things it advances, live on one typed object here instead
of in the closure cells of a factory nothing could call a piece of.

The regime is the load-bearing part. `tier`, `image` and `image_digest` name
what is running *right now*, not what the contract asked for: a gate-red
hands off to a retry rung (D-27.11) and the fields move with it, so the
record every step stamps names the tier that actually produced the work.

`open_dispatch` settles all of that and refuses what must be refused before
anything runs. `open_broker` is deliberately separate and deliberately last
(D-46.4): the broker is a live credential route, and a setup failure after
it opened would leak one.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from torve.application.ports import (
    Agent,
    AttemptFact,
    AttemptSink,
    Broker,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    BurnSink,
    JournalSync,
    RunChannel,
    Runtime,
    Scm,
    StoreFactory,
    Vcs,
    WorkspacePort,
)
from torve.application.telemetry import (
    append_record,
    broker_block,
    build_attempt_row,
    engine_event,
    record_payload,
    record_row,
)
from torve.config import layout
from torve.config.manifest import Manifest, load_manifest
from torve.config.runconfig import (
    CACHE_MOUNT,
    RunnerConfig,
    TierConfig,
    broker_in_force,
    image_for,
    tier_for,
    tier_name_for,
)
from torve.domain.attempt import GateResult
from torve.domain.task import Task

# ----------------------- #


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
    # RFC 0045 D-45.4: where the broker's per-response metering goes. The
    # runner only hands it to the broker at open; what it does with a burn
    # — record it, count it, drop it — is the caller's, and None is the
    # unobserved run every test and simulation already assumes.
    sink: BurnSink | None = None
    # RFC 0044 D-44.3: where each attempt's own facts go, as they become
    # true. A dispatch is up to `poison_ceiling` attempts, and the summary
    # of one is not the record of three.
    facts: AttemptSink | None = None
    # RFC 0044 A-82: brings the store and the worktree's divergence log into
    # agreement before the gates read it, so the battery judges the record
    # rather than whatever the sandbox left behind. None keeps v1's
    # behaviour — the file the agent's intake wrote is the only carrier.
    journal: JournalSync | None = None
    # RFC 0045 §5.2: the run's route into the record, handed to the broker
    # at open. The sandbox reaches the record through the broker or not at
    # all — it never holds a store credential (D-45.1).
    channel: RunChannel | None = None


# ....................... #


@dataclass
class GatePass:
    """What the last gate pass produced. The reviewer judges exactly what the
    gates judged (RFC 0005), so the results, the patch and the configuration
    digest travel together or the review is judging something else."""

    results: list[GateResult] = field(default_factory=list)
    patch: str = ""
    digest: str = ""


# ....................... #


@dataclass
class Dispatch:
    """One task, one worktree, one regime — the facts every step of a run
    reads, and the few it advances."""

    # Settled at open, never reassigned.
    root: Path
    task: Task
    config: RunnerConfig
    deps: RunDeps
    worktree: Path
    shadow: bool
    gates_base: str | None
    resume: bool

    # The regime in force right now (D-27.11): seeded from the task's own
    # tier, advanced only when a gate-red routes the next attempt to a retry
    # rung. A gate pass judges the same image the agent ran under (D-3.8),
    # so both legs read these fields rather than re-resolving the tier.
    tier_name: str
    tier: TierConfig
    image: str
    image_digest: str | None

    # Denormalised into every record this run appends (RFC 0004 §6): which
    # adapter and model did the work cannot be reconstructed later. Still a
    # dictionary because it is a telemetry row under construction, not a
    # domain object (D-46.6).
    meta: dict[str, Any]

    # The commits a `revert` role undoes, resolved at open so an
    # unresolvable target fails before anything runs. Empty for every other
    # role.
    revert_shas: list[str] = field(default_factory=list)

    # The run's broker route table and handle, opened last (D-46.4) and
    # closed once however the loop ends.
    routing: BrokerRouting = field(default_factory=BrokerRouting)
    broker_handle: BrokerHandle | None = None

    # The most recent gate pass, restamped every pass: `convictions` is what
    # retry selection reads after a red (D-34.5), `last_pass` is what the
    # review is handed.
    convictions: list[GateResult] = field(default_factory=list)
    last_pass: GatePass = field(default_factory=GatePass)

    # ....................... #

    @property
    def broker(self) -> Broker | None:
        """The broker only when it is actually open. Every caller needs both
        facts and checking one without the other is how a run reports usage
        from a handle that was never issued."""

        return self.deps.broker if self.broker_handle is not None else None


# ....................... #


def review_gated(config: RunnerConfig, task: Task, shadow: bool) -> bool:
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

    `include_retry` (D-27.11, generalized by D-34.6) also routes every rung
    the task's tier resolves for a retry — every axis of `retry_variants`,
    not only the scalar's functional one: the broker opens once, before the
    first attempt, so a provider only a later conviction-routed retry
    reaches must already be on the route table.
    """

    routes: list[BrokerRoute] = []
    base_name = tier_name_for(task)
    base_tier = tier_for(config, base_name)
    tier_names = [base_name]

    if include_retry:
        rung_names = base_tier.resolved_retry_variants().values()
        tier_names = list(dict.fromkeys([*tier_names, *rung_names]))

    if review_on and "reviewer" not in tier_names:
        tier_names.append("reviewer")

    for tier_name in tier_names:
        tier = tier_for(config, tier_name)

        if tier.adapter == "fake" or not tier.provider:
            continue

        provider = config.broker.providers.get(tier.provider)

        if provider is None and not broker_in_force(config):
            # The none adapter routes nothing at the wire: keys keep their
            # existing channel and an empty provider table is the named
            # default, not a configuration error (D-21.9).
            continue

        if provider is None:
            raise ValueError(
                f"tier {tier_name!r} uses provider {tier.provider!r} but the broker "
                "configuration routes no such provider — add it under broker.providers"
            )

        routes.append(
            BrokerRoute(
                provider=tier.provider,
                upstream=provider.upstream,
                key_env=provider.key_env,
                via_proxy=provider.via_proxy,
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


def _refuse_credentialed_tier(config: RunnerConfig, name: str, candidate: TierConfig) -> None:
    # D-21.1's second line: the configuration validator already refuses a
    # brokered tier that names a credential; the runner refuses again so
    # a programmatically-built configuration cannot slip a key name past
    # the validator into the sandbox's env. Checked for every retry rung
    # too (D-27.11, D-34.6) — a run never dispatches under a regime it
    # hasn't already validated (D-27.1's spirit, applied ahead of time).
    if broker_in_force(config) and candidate.api_key_env:
        raise ValueError(
            f"tier {name!r} names api_key_env {candidate.api_key_env} under broker "
            f"{config.broker.adapter!r} — a brokered tier names no credential"
        )


# ....................... #


def open_dispatch(
    root: Path,
    task: Task,
    config: RunnerConfig,
    deps: RunDeps,
    worktree: Path,
    *,
    shadow: bool = False,
    gates_base: str | None = None,
    resume: bool = False,
) -> Dispatch:
    """Settle the regime this run starts under, refusing what must be
    refused first. Every fallible step of setup happens here; the broker
    opens afterwards, in `open_broker`, so a refusal can never leak a live
    credential route (D-46.4)."""

    tier_name = tier_name_for(task)
    tier = tier_for(config, tier_name)

    _refuse_credentialed_tier(config, tier_name, tier)

    if deps.retry_agent is not None:
        for rung in tier.resolved_retry_variants().values():
            _refuse_credentialed_tier(config, rung, tier_for(config, rung))

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

            if config.unmeasured_images == "allow":
                # The rebuild escape hatch (A-88): dispatch proceeds and the
                # unmeasured regime is recorded rather than assumed. What
                # the rule protects — comparing numbers from regimes nobody
                # measured — is protected by the record saying so, not by
                # the refusal.
                engine_event(
                    root,
                    "unmeasured_dispatch",
                    {
                        "task": task.id,
                        "tier": tier_name,
                        "digest": image_digest,
                        "measured": [incumbent, candidate],
                    },
                )

            else:
                raise ValueError(
                    f"tier {tier_name!r} now resolves image digest {image_digest!r}, "
                    "which the most recent recorded verdict for this tier never "
                    f"measured (it measured {incumbent!r} as the running default and "
                    f"{candidate!r} as the candidate) — the configured image changed "
                    "since that measurement with no new paired verdict backing it; "
                    "record a fresh replay verdict before this task can dispatch, "
                    "name an explicit tier_variant to run a named candidate freely, "
                    "or set unmeasured_images: allow while the images are being "
                    "rebuilt"
                )

    return Dispatch(
        root=root,
        task=task,
        config=config,
        deps=deps,
        worktree=worktree,
        shadow=shadow,
        gates_base=gates_base,
        resume=resume,
        tier_name=tier_name,
        tier=tier,
        image=image,
        image_digest=image_digest,
        # Shadow gate passes are marked so the measurement population stays
        # separable from live attempts in one stream. The attempt leg
        # restamps tier/adapter/provider/model/image_digest and the attempt
        # number (RFC 0038 §5.1, D-38.4) every call — this is only the
        # shape, so every record joins deterministically to its trace file
        # and RFC 0026's continuation chain.
        meta={
            "tier": tier_name,
            "attempt": None,
            "adapter": kind,
            "provider": (tier.provider or None) if real else None,
            "model": (tier.model or None) if real else None,
            "model_version": None,
            "cost_usd": None,
            "trace_ref": None,
            # The image tag beside its digest: harness identity is the image
            # (D-17.4), and a projection labeling "which harness" reads the
            # tag.
            "image": image,
            "image_digest": image_digest,
            "shadow": shadow,
            # Per-skill attribution (RFC 0009 §5): filled with what
            # materialize actually wrote, so cohorts group by skill regime
            # from the record alone.
            "skills": None,
        },
    )


# ....................... #


def open_broker(run: Dispatch) -> None:
    """The broker's life spans the run (RFC 0021 §5.1): one loopback route
    per routed provider, a run-scoped token, and the task's token budget held
    at the wire. `none` opens trivially and the record carries the adapter in
    force either way (D-21.9: opting out is explicit).

    Called last, after every fallible step of setup, so a setup failure
    cannot leak a live broker; `close_dispatch` revokes it when the loop
    ends."""

    broker = run.deps.broker

    if broker is None:
        return

    run.routing = run_routing(
        run.config,
        run.task,
        review_gated(run.config, run.task, run.shadow),
        include_retry=run.deps.retry_agent is not None,
    )
    run.broker_handle = broker.open(
        run.task.id,
        run.routing,
        BrokerBudget(tokens=run.task.budget.tokens),
        sink=run.deps.sink,
        channel=run.deps.channel,
    )


# ....................... #


def close_dispatch(run: Dispatch) -> None:
    """The run's one close: the broker revokes the run-scoped token and
    reports the authoritative usage (D-21.5). Wire refusals become engine
    events — a refusal for a provider the run's routing carried is a defect
    report about the configuration reader (D-21.4)."""

    broker, handle = run.broker, run.broker_handle

    if broker is None or handle is None:
        return

    usage = broker.close(handle)
    run.meta["broker"] = broker_block(broker.name, usage)

    for provider, count in sorted(usage.refused_providers.items()):
        engine_event(
            run.root,
            "wire_routing_refusal",
            {
                "task": run.task.id,
                "broker": broker.name,
                "provider": provider,
                "count": count,
                "routed": run.routing.route_for(provider) is not None,
            },
        )

    adapter_cost = run.meta.get("cost_usd")

    if usage.cost_usd is not None and isinstance(adapter_cost, (int, float)):
        scale = max(abs(usage.cost_usd), abs(adapter_cost)) or 1.0

        if abs(usage.cost_usd - adapter_cost) / scale > run.config.broker.cost_tolerance:
            engine_event(
                run.root,
                "cost_divergence",
                {
                    "task": run.task.id,
                    "broker": broker.name,
                    "broker_cost_usd": usage.cost_usd,
                    "adapter_cost_usd": adapter_cost,
                    "tolerance": run.config.broker.cost_tolerance,
                },
            )


# ....................... #


def emit(run: Dispatch, kind: str, attempt: int, /, **payload: object) -> None:
    """Hand one attempt fact to whoever is observing this run.

    Failures are swallowed on purpose, the same rule the burn sink follows:
    an observer that can break a run is not an observer. There is no
    ordering guarantee to protect either — each fact is emitted where it
    becomes true, so the sequence is the run's own.
    """

    sink = run.deps.facts

    if sink is None:
        return

    with contextlib.suppress(Exception):
        sink(AttemptFact(kind=kind, attempt=attempt, payload=dict(payload)))  # type: ignore[arg-type]


# ....................... #


def cache_volumes(run: Dispatch) -> dict[str, str]:
    """The derived-cache volume this run's current regime mounts (RFC 0035
    §5.2, D-35.4): named like the auth volume — base plus `-<slot>`, so two
    concurrent workers never share a cache — at the fixed address outside
    the workspace. An unnamed cache is no volume at all: cold exactly as
    before the field existed.

    Always empty under shadow (D-35.3): a replay measures the cold truth
    even when the tier names a cache, so an eval comparing arms never
    compares caches. Both the agent's sandbox and the gate battery's read
    it here, because a pass that judged a different cache than the attempt
    ran under would be judging a different regime.
    """

    if run.shadow or not run.tier.cache_volume:
        return {}

    return {f"{run.tier.cache_volume}-{run.config.worker_slot}": CACHE_MOUNT}


# ....................... #


def telemetry_target(run: Dispatch) -> Path:
    """The stream for records this run appends outside a gate pass. The
    record must never depend on the manifest existing — a worktree with no
    gates.yaml still burned the money."""

    manifest_file = layout.gates_file(run.worktree)
    telemetry_rel = (
        load_manifest(manifest_file).telemetry if manifest_file.is_file() else Manifest().telemetry
    )

    return run.root / telemetry_rel


# ....................... #


def attempt_row(
    run: Dispatch,
    verdict: str,
    *,
    exit_code: int | None,
    timed_out: bool,
    escalation: str | None = None,
) -> dict[str, Any]:
    """One row for one ending of an attempt that produces no gate record
    (D-38.1): the red-agent shape — the spend survives even though the gates
    never ran — with the engine-derived verdict naming how the attempt ended.
    The attempt's facts, not its prose: exec results, escalation state,
    whatever the adapter reported (D-38.2).

    Rendered rather than appended directly, so the stream is provably a view
    of the record the event carries (A-85) — the round trip is what a test
    can pin, and a field added to one carrier and not the other stops being
    possible."""

    record = build_attempt_row(
        run.task,
        run.meta,
        verdict=verdict,
        exit_code=exit_code,
        timed_out=timed_out,
        escalation=escalation,
    )
    payload = record_payload(record, int(run.meta.get("attempt") or 0))

    append_record(
        telemetry_target(run),
        record_row(payload, task_id=record["task_id"], at=record["at"]),
    )

    return record
