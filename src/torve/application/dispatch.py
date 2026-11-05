"""One dispatch of one task: its ports, its regime, and the facts its steps
share (S-0046).

A dispatch is one `torve run` — a worktree, a resolved tier, and up to
`poison_ceiling` attempts inside it. Everything a step of that run needs to
read, and the few things it advances, live on one typed object here instead
of in the closure cells of a factory nothing could call a piece of.

The regime is the load-bearing part. `tier`, `image` and `image_digest` name
what is running *right now*, not what the contract asked for: a gate-red
hands off to a retry rung (S-0027/D-11) and the fields move with it, so the
record every step stamps names the tier that actually produced the work.

`open_dispatch` settles all of that and refuses what must be refused before
anything runs. `open_broker` is deliberately separate and deliberately last
(S-0046/D-4): the broker is a live credential route, and a setup failure after
it opened would leak one.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    JudgePort,
    RunChannel,
    Runtime,
    Scm,
    StoreFactory,
    Vcs,
    WorkspacePort,
)
from torve.application.telemetry import (
    append_record,
    attempt_cost,
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
from torve.gates.evidence_package import EvidencePackage

# ----------------------- #


@dataclass
class RunDeps:
    workspace: WorkspacePort
    runtime: Runtime
    agent: Agent
    vcs: Vcs
    scm: Scm
    store: StoreFactory
    # The reviewer's agent (S-0005), built by the CLI from the reviewer
    # tier when review is configured — cross-model by pointing the tier at
    # a different vendor (S-0005/D-1). None means review cannot run.
    review_agent: Agent | None = None
    # The egress broker adapter in force (S-0021): built by the CLI from
    # the configuration; None means the port was never wired (tests,
    # simulation). Under a configured broker the run opens it around the
    # attempts and closes it when the loop ends.
    broker: Broker | None = None
    # S-0027/D-11: builds the Agent for a tier resolved mid-run — the attempt
    # after a gate-red, when the tier that just ran names a retry_variant.
    # Building an Agent is a CLI-layer act (it reaches into adapters), so
    # the runner is handed a factory rather than importing one; None means
    # retry_variant never fires for this dispatch — `agent` above keeps
    # running every attempt, and telemetry never stamps a tier that did not
    # actually produce the work.
    retry_agent: Callable[[TierConfig], Agent] | None = None
    # S-0045 S-0045/D-4: where the broker's per-response metering goes. The
    # runner only hands it to the broker at open; what it does with a burn
    # — record it, count it, drop it — is the caller's, and None is the
    # unobserved run every test and simulation already assumes.
    sink: BurnSink | None = None
    # S-0044 S-0044/D-3: where each attempt's own facts go, as they become
    # true. A dispatch is up to `poison_ceiling` attempts, and the summary
    # of one is not the record of three.
    facts: AttemptSink | None = None
    # S-0044 S-0044/A-3: brings the store and the worktree's divergence log into
    # agreement before the gates read it, so the battery judges the record
    # rather than whatever the sandbox left behind. None keeps v1's
    # behaviour — the file the agent's intake wrote is the only carrier.
    journal: JournalSync | None = None
    # S-0045/the-intake-route: the run's route into the record, handed to the broker
    # at open. The sandbox reaches the record through the broker or not at
    # all — it never holds a store credential (S-0045/D-1).
    channel: RunChannel | None = None
    # S-0056/D-9: reads the task ids the record holds when a store holds the
    # tasks. A contract the run mints (the review at gated) numbers past them,
    # since a store-backed plan writes no file under `.torve/tasks/` to count.
    # Read when the contract is minted, not when the run began: another slot
    # may mint while an attempt runs. None is a run without a store.
    taken: Callable[[], Iterable[str]] | None = None


# ....................... #


# The arm axis (S-0074/D-1, S-0082/D-1): an arm is named by the apparatus it
# removes, and each removal is a property of the replay rather than an edit to
# the manifest or the contract. Two properties, one name — the prompt's removal
# and the battery's — so `gated` (the bare prompt with the battery still
# judging what comes back) is a combination of the two rather than a third
# mechanism.
BARE, GATED, CONFIGURED = "bare", "gated", "configured"


# ....................... #


@dataclass
class GatePass:
    """What the last gate pass produced. The reviewer judges exactly what the
    gates judged (S-0005), so the results, the patch and the configuration
    digest travel together or the review is judging something else."""

    results: list[GateResult] = field(default_factory=list)
    patch: str = ""
    digest: str = ""
    # The candidate's evidence package (S-0122/D-5), measured in the same
    # pass when the critic will read it; None when no critic will.
    evidence: EvidencePackage | None = None


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

    # The regime in force right now (S-0027/D-11): seeded from the task's own
    # tier, advanced only when a gate-red routes the next attempt to a retry
    # rung. A gate pass judges the same image the agent ran under (S-0003/D-8),
    # so both legs read these fields rather than re-resolving the tier.
    tier_name: str
    tier: TierConfig
    image: str
    image_digest: str | None

    # Denormalised into every record this run appends (S-0004/telemetry-staged): which
    # adapter and model did the work cannot be reconstructed later. Still a
    # dictionary because it is a telemetry row under construction, not a
    # domain object (S-0046/D-6).
    meta: dict[str, Any]

    # The run's broker route table and handle, opened last (S-0046/D-4) and
    # closed once however the loop ends.
    routing: BrokerRouting = field(default_factory=BrokerRouting)
    broker_handle: BrokerHandle | None = None

    # The most recent gate pass, restamped every pass: `convictions` is what
    # retry selection reads after a red (S-0034/D-5), `last_pass` is what the
    # review is handed.
    convictions: list[GateResult] = field(default_factory=list)
    last_pass: GatePass = field(default_factory=GatePass)
    # The package the current pass measured, before `last_pass` is restamped
    # with it; cleared at the start of every pass.
    evidence: EvidencePackage | None = None

    # The repair routing's own facts (S-0069/D-3, D-6), restamped by the gate
    # leg every red pass like `convictions`: `contract_acceptance` is what the
    # contract declared — sealed at open, the base a repair's acceptance is
    # extended from and an ordinary routing returns to, so no addition leaks
    # into an attempt that was not routed as one. `repaired_gates` is the once
    # bound: a second conviction on a gate already in it routes exactly where
    # it routes today.
    contract_acceptance: tuple[str, ...] = ()
    repaired_gates: set[str] = field(default_factory=set)

    # Which arm is running (S-0082/D-1). Both removals are read from this one
    # name, so the leg that composes the prompt and the leg that runs the
    # battery can never disagree about which arm produced the numbers.
    arm: str = CONFIGURED

    # Who opens the battery's judge checkout (S-0114/D-2) when it is not the
    # workspace: a shadow replay's clone is no worktree of the root, so the
    # replay hands its own.
    judge: JudgePort | None = None

    # ....................... #

    @property
    def prompt_removed(self) -> bool:
        """Whether this arm's session is handed a bare prompt — the task's
        intent and nothing else — instead of the composed one."""

        return self.arm in (BARE, GATED)

    # ....................... #

    @property
    def battery_removed(self) -> bool:
        """Whether this arm's gate pass runs nothing (S-0074/D-3)."""

        return self.arm == BARE

    # ....................... #

    @property
    def broker(self) -> Broker | None:
        """The broker only when it is actually open. Every caller needs both
        facts and checking one without the other is how a run reports usage
        from a handle that was never issued."""

        return self.deps.broker if self.broker_handle is not None else None


# ....................... #


def review_gated(config: RunnerConfig, task: Task, shadow: bool) -> bool:
    """Review follows execution (S-0005/D-11) only for live implement runs with
    the task-gated trigger configured — one predicate, shared by the review hook
    and the broker's routing derivation so they cannot disagree."""

    return not shadow and task.role == "implement" and "task_gated" in config.review.on


# ....................... #


def reachable_tiers(config: RunnerConfig, base_name: str) -> list[tuple[str, TierConfig]]:
    """S-0117/D-2: the seat and every tier a run can reach from it — its retry
    rungs and its `revision_variant`, followed transitively through each
    listed tier's own. Discovery order, each name once, so a rung naming the
    seat is never emitted twice."""

    found = [(base_name, tier_for(config, base_name))]
    seen = {base_name}
    index = 0

    while index < len(found):
        _, tier = found[index]
        index += 1

        for name in (*tier.resolved_retry_variants().values(), tier.revision_variant):
            if name and name not in seen:
                seen.add(name)
                found.append((name, tier_for(config, name)))

    return found


# ....................... #


def run_routing(
    config: RunnerConfig, task: Task, review_on: bool, include_retry: bool = False
) -> BrokerRouting:
    """The run's routing (S-0021/D-4): every provider the run's agents will use,
    resolved once and handed to the broker. Dispatch allowed them at the CLI;
    the broker enforces them at the wire. A provider the broker configuration
    does not route is a configuration error, never a quiet fallback.

    `include_retry` (S-0027/D-11, generalized by S-0034/D-6 and S-0117/D-2)
    also routes every tier the task's seat can reach: every axis of
    `retry_variants`, its `revision_variant`, and each listed tier's own.
    The broker opens once, before the first attempt, so a provider only a
    later conviction-routed retry or blocker revision reaches must already
    be on the route table.
    """

    routes: list[BrokerRoute] = []
    base_name = tier_name_for(task)
    tier_names = [base_name]

    if include_retry:
        tier_names = [name for name, _ in reachable_tiers(config, base_name)]

    if review_on and "reviewer" not in tier_names:
        tier_names.append("reviewer")

    for tier_name in tier_names:
        tier = tier_for(config, tier_name)

        if tier.adapter == "fake" or not tier.provider:
            continue

        # A route is a provider and a dialect together (S-0064/D-2), because a
        # provider may serve two and they are different upstreams. The seat
        # carries the name the broker knows its route by, so nothing here
        # re-derives it.
        route = tier.route or tier.provider
        provider = config.broker.providers.get(route)

        if provider is None and not broker_in_force(config):
            # The none adapter routes nothing at the wire: keys keep their
            # existing channel and an empty provider table is the named
            # default, not a configuration error (S-0021/D-9).
            continue

        if provider is None:
            raise ValueError(
                f"tier {tier_name!r} reaches {tier.provider!r} over "
                f"{tier.dialect or 'no named dialect'} and the broker routes "
                f"{sorted(config.broker.providers) or 'nothing'} — write "
                f".torve/providers/{tier.provider}.yaml, or name the dialect it serves"
            )

        routes.append(
            BrokerRoute(
                provider=route,
                upstream=provider.upstream,
                key_env=provider.key_env,
                via_proxy=provider.via_proxy,
            )
        )

    return BrokerRouting(routes=tuple(routes))


# ....................... #


def _seat_price(config: RunnerConfig, tier: TierConfig) -> tuple[bool, dict[str, Any] | None]:
    """The rate card the seat's attempts are priced from (S-0064/D-12), and
    whether the roster had anything to say at all.

    Three cases, and they are three different facts. A provider with no record
    or a model the roster does not list resolves nothing — the attempt keeps
    whatever the harness reported, because nothing here has an opinion yet and
    resolving a seat against the roster is the next phase's. A listed model
    with no price resolves to no price, which is a subscription seat saying it
    has no per-token cost. A listed model with one resolves to the number the
    engine computes from.
    """

    record = config.provider_records.get(tier.provider)
    entry = record.models.get(tier.model) if record is not None else None

    if entry is None:
        return False, None

    return True, None if entry.price is None else entry.price.model_dump()


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
    arm: str = CONFIGURED,
    judge: JudgePort | None = None,
) -> Dispatch:
    """Settle the regime this run starts under, refusing what must be
    refused first. Every fallible step of setup happens here; the broker
    opens afterwards, in `open_broker`, so a refusal can never leak a live
    credential route (S-0046/D-4)."""

    tier_name = tier_name_for(task)
    tier = tier_for(config, tier_name)

    # No credential refusal here any more. It refused a brokered tier that named
    # `api_key_env`, belt-and-braces against a programmatically-built configuration
    # slipping a key name past the validator into a sandbox's env. S-0064/D-9 closed
    # it structurally instead: a credential is the provider's, and `credential_names`
    # hands a sandbox nothing while a broker is in force, so there is nowhere left
    # for the mistake to be made.

    # What actually runs, not what the tier configured — an --agent fake
    # override must not masquerade as a model in the telemetry.
    kind = getattr(deps.agent, "kind", tier.adapter)
    real = kind != "fake"
    # The digest is the sandbox's identity (S-0017/D-1): resolved once, at
    # dispatch; None is recorded as unresolved, never invented.
    image = image_for(config, tier)
    image_digest = deps.runtime.resolve_image(image)
    # S-0064/D-12: the attempt's cost is computed from the record and the token
    # counts rather than believed from a harness that may not recognise the
    # model it was pointed at. Resolved once, for the task's own seat: the
    # attempt leg restamps provider and model for a conviction-routed rung,
    # and the price follows them when the seat itself resolves against the
    # roster (phase 2).
    priced, price = _seat_price(config, tier) if real else (False, None)

    return Dispatch(
        root=root,
        task=task,
        config=config,
        deps=deps,
        worktree=worktree,
        shadow=shadow,
        gates_base=gates_base,
        resume=resume,
        arm=arm,
        judge=judge,
        contract_acceptance=tuple(task.acceptance),
        tier_name=tier_name,
        tier=tier,
        image=image,
        image_digest=image_digest,
        # Shadow gate passes are marked so the measurement population stays
        # separable from live attempts in one stream. The attempt leg
        # restamps tier/adapter/provider/model/image_digest and the attempt
        # number (S-0038/the-attempt-number, S-0038/D-4) every call — this is only the
        # shape, so every record joins deterministically to its trace file
        # and S-0026's continuation chain.
        meta={
            "tier": tier_name,
            "attempt": None,
            "adapter": kind,
            "provider": (tier.provider or None) if real else None,
            "model": (tier.model or None) if real else None,
            "model_version": None,
            "cost_usd": None,
            # Present — null included — only where the roster listed the
            # model: a null price is the record saying this seat has no
            # per-token cost, which is not the same fact as a seat the roster
            # says nothing about (S-0064/D-12).
            **({"price": price} if priced else {}),
            "trace_ref": None,
            # The image tag beside its digest: harness identity is the image
            # (S-0017/D-4), and a projection labeling "which harness" reads the
            # tag.
            "image": image,
            "image_digest": image_digest,
            "shadow": shadow,
            # Per-skill attribution (S-0009/evals): filled with what
            # materialize actually wrote, so cohorts group by skill regime
            # from the record alone.
            "skills": None,
        },
    )


# ....................... #


def open_broker(run: Dispatch) -> None:
    """The broker's life spans the run (S-0021/the-port): one loopback route
    per routed provider, a run-scoped token, and the task's token budget held
    at the wire. `none` opens trivially and the record carries the adapter in
    force either way (S-0021/D-9: opting out is explicit).

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
    reports the authoritative usage (S-0021/D-5). Wire refusals become engine
    events — a refusal for a provider the run's routing carried is a defect
    report about the configuration reader (S-0021/D-4)."""

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

    # S-0064/D-12 repairs the comparison rather than adding a second one: it
    # weighed a harness's rate card against reality, and now it weighs torve's
    # arithmetic against the broker's metering — two numbers that are both
    # about this call. Where no price is written there is no arithmetic, and
    # the adapter's claim is still the only other number there is.
    adapter_cost = run.meta.get("cost_usd")
    engine_cost = attempt_cost(run.meta)
    ours = engine_cost if engine_cost is not None else adapter_cost

    if usage.cost_usd is not None and isinstance(ours, (int, float)):
        scale = max(abs(usage.cost_usd), abs(ours)) or 1.0

        if abs(usage.cost_usd - ours) / scale > run.config.broker.cost_tolerance:
            engine_event(
                run.root,
                "cost_divergence",
                {
                    "task": run.task.id,
                    "broker": broker.name,
                    "broker_cost_usd": usage.cost_usd,
                    "engine_cost_usd": engine_cost,
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


def cache_volumes(run: Dispatch, role: str = "") -> dict[str, str]:
    """The derived-cache volume this run's current regime mounts (S-0035
    §5.2, S-0035/D-4): named like the auth volume — base plus `-<slot>`, so two
    concurrent workers never share a cache — at the fixed address outside
    the workspace. An unnamed cache is no volume at all: cold exactly as
    before the field existed.

    Always empty under shadow (S-0035/D-3): a replay measures the cold truth
    even when the tier names a cache, so an eval comparing arms never
    compares caches. A *role* names a volume of its own: the gate battery's
    is never the attempt's, because candidate code a gate runs could leave
    files there for the next attempt to read (S-0122/D-3).
    """

    if run.shadow or not run.tier.cache_volume:
        return {}

    suffix = f"-{role}" if role else ""

    return {f"{run.tier.cache_volume}-{run.config.worker_slot}{suffix}": CACHE_MOUNT}


# ....................... #


def telemetry_target(run: Dispatch) -> Path:
    """The stream for records this run appends outside a gate pass. The
    record must never depend on the manifest existing — a worktree with no
    gates.yaml still burned the money."""

    manifest_file = layout.gates_file(run.worktree if run.shadow else run.root)
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
    (S-0038/D-1): the red-agent shape — the spend survives even though the gates
    never ran — with the engine-derived verdict naming how the attempt ended.
    The attempt's facts, not its prose: exec results, escalation state,
    whatever the adapter reported (S-0038/D-2).

    Rendered rather than appended directly, so the stream is provably a view
    of the record the event carries (S-0044/A-4) — the round trip is what a test
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
