"""One JSONL record per run (S-0002/telemetry-from-day-one). Three fields must be right from the
first line because none can be reconstructed later: `schema_version`,
`config_hash`, and decisions denormalised into the record, not referenced.

`config_hash` lives here rather than beside the manifest model: digesting the
regime needs the forze pin from `application.migrate`, and `config` may not
import `application` (S-0015/permitted-imports).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import torve
from torve.application.ports import AgentResult, BrokerUsage
from torve.base.clock import stamp
from torve.base.naming import WORKTREE_DIR, shadow_id
from torve.config import layout
from torve.config.providers import Price
from torve.config.runconfig import RunnerConfig
from torve.domain.attempt import GateResult
from torve.domain.task import InheritedDecision, Task
from torve.gates.context import GateContext
from torve.gates.runner import RunReport

# ----------------------- #

# The telemetry envelope's own shape version (T-0321) — every record
# appended to the stream declares it, wherever it was built.
RECORD_SCHEMA_VERSION = 1

_APPEND_LOCK = threading.Lock()
_REGIME_LOCK = threading.Lock()


# ....................... #


def config_hash(
    manifest_path: Path,
    root: Path,
    config: RunnerConfig | None = None,
    image_digest: str | None = None,
) -> str:
    """Digest of the regime a run belongs to (S-0002/telemetry-from-day-one, S-0009/D-8):
    gates.yaml, the Torve package version (its gates and shipped skills change
    behavior — S-0009/A-1), the pinned forze version (a substrate upgrade is a regime
    change, and possibly a migration — S-0003/A-1), and — when the runner
    configuration is at hand — the seats, the role equipment and the provider
    policy (S-0004/telemetry-staged, S-0004/D-3): which agent ran under which
    harness, with what, and where contents were allowed to go are all part of what
    a number was measured under. The sandbox image digest joins when the caller
    resolved one (S-0017/the-image-is-an-input-not-an-environment, S-0017/D-1): two
    runs under one tag but different digests are two regimes.

    The parts are what was *resolved*, never a file some other tool keeps
    (S-0061/D-7). Each seat carries its harness and profile merged onto it, so
    editing either moves the digest; the role equipment joins beside them,
    because since S-0061/D-11 a role's skills are a file rather than a default
    written in code, and a hash that missed it would call two regimes one.
    """

    from torve.application.migrate import forze_pin

    parts: dict[str, str] = {
        "gates.yaml": manifest_path.read_text(encoding="utf-8"),
        "torve": torve.__version__,
        # The substrate pin, not the installed version: the pin names the
        # schema regime the migrations were written against (S-0012);
        # torve doctor is what compares it to the installed version.
        "forze": forze_pin(),
    }

    if config is not None:
        parts["tiers"] = json.dumps(
            {name: tier.model_dump() for name, tier in sorted(config.tiers.items())},
            sort_keys=True,
        )

        # The equipment a role loads, resolved from the role profiles at load
        # (S-0061/D-11). It rode the `torve` version while it was a default in
        # code; a file needs its own part.
        parts["skills"] = json.dumps(config.skills.model_dump(), sort_keys=True)

        # The lower of the two equipment layers, by key and never by contents
        # (S-0062/D-8): the key is the declaration, and the declaration is what
        # an operator chose — hashing bytes would make a regime depend on when a
        # fetch happened, so two checkouts of one tree would disagree until both
        # had warmed. The seat's own layer rides `tiers` above, where the same
        # rule holds because a declaration is all a seat carries.
        from torve.application.equipment import regime_keys
        from torve.config.agents import role_equipment

        parts["equipment"] = json.dumps(
            {role: regime_keys(items) for role, items in sorted(role_equipment(root).items())},
            sort_keys=True,
        )

        parts["providers"] = json.dumps(config.providers.model_dump(), sort_keys=True)

        # The provider records beside the policy over them (S-0064/D-1): a
        # window, a cap, a route's compat facts and a price are all part of
        # what a number was measured under. Keyed by the id that reaches the
        # provider rather than by the roster's local shorthand (S-0064/D-3) —
        # reproducibility is a property of what was sent, so renaming a
        # shorthand must not move a digest.
        parts["provider-records"] = json.dumps(
            {
                name: {
                    **record.model_dump(exclude={"models", "name", "schema_version"}),
                    "models": {
                        record.id_for(key): entry.model_dump(exclude={"id"})
                        for key, entry in record.models.items()
                    },
                }
                for name, record in sorted(config.provider_records.items())
            },
            sort_keys=True,
        )

        # The egress regime (S-0021/what-this-does-not-change, S-0021/D-8): the broker adapter and
        # the run's routing are part of what a number was measured under —
        # two runs under different egress regimes are two regimes.
        parts["broker"] = json.dumps(config.broker.model_dump(), sort_keys=True)

    if image_digest is not None:
        parts["image"] = image_digest

    # `skills-lock.json` was here and is not (S-0061/D-7). It is the `skills`
    # CLI's file, written and read by that tool for the operator's own use; this
    # engine installs nothing from it and read it for nothing else. Hashing it
    # meant a reformat by another tool changed the regime while a change to the
    # equipment this engine actually resolves did not.

    # The vendored skills tree (S-0009/vendored-skills, S-0009/D-13): an edited vendored
    # skill is a regime change — the image-digest doctrine applied to
    # prompt-side inputs.
    vendor = layout.skills_vendor_dir(root)

    if vendor.is_dir():
        tree = hashlib.sha256()

        for file in sorted(p for p in vendor.rglob("*") if p.is_file()):
            tree.update(str(file.relative_to(vendor)).encode("utf-8"))
            tree.update(file.read_bytes())

        parts["skills-vendor"] = tree.hexdigest()

    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8"))
    hexdigest = digest.hexdigest()[:12]

    _write_regime_preimage(root, hexdigest, parts)

    return hexdigest


# ....................... #


def _write_regime_preimage(root: Path, digest: str, parts: dict[str, str]) -> None:
    """The `parts` a `config_hash` was computed over (S-0004/D-19, S-0004/A-1): written
    once, only if absent, so `config_hash` names a regime someone can open
    rather than a bare hex string. Content-addressed by the hash it produced
    — a write racing an identical write lands the same bytes either way, so
    the existence check is the only guard that matters.

    Lands beside the telemetry stream, not the tree that was hashed: a
    worktree is always `<host>/.wt/<name>` (`naming.worktree`, S-0003/D-4) and is
    destroyed at reap, while the telemetry row citing this digest lives in
    the host root's telemetry.jsonl. Detected structurally from `root`
    itself so no caller needs to change.

    Best-effort: an unwritable `.torve` must not fail a run over a record
    that exists purely for human triage — a missing preimage is strictly
    better than a dead run."""

    for parent in root.parents:
        if parent.name == WORKTREE_DIR:
            root = parent.parent
            break

    path = root / layout.TORVE_DIR / "regimes" / f"{digest}.json"

    try:
        if path.exists():
            return

        with _REGIME_LOCK:
            if path.exists():
                return

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(parts, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    except OSError:
        return


# ....................... #


def broker_block(name: str, usage: BrokerUsage) -> dict[str, Any]:
    """The broker's counts as a JSON block beside the adapter's own report
    (S-0021/D-5): both are recorded, the broker's is authoritative. Counts and
    metadata only — the broker never keeps bodies (S-0021/D-7)."""

    return {
        "adapter": name,
        "requests": usage.requests,
        "tokens_per_provider": usage.tokens_per_provider,
        "cost_usd": usage.cost_usd,
        "wall_time_s": round(usage.wall_time_s, 3),
        "refusals": usage.refusals,
    }


# ....................... #


# The agent block's token fields (T-0186): the four counts the harness
# adapter's parse_metadata extracts from the shapes the harnesses emit (the
# claude envelope's snake_case usage block; the dsh reporter's camelCase
# usage object). Keys ride flat beside cost_usd and model_version. Absent
# keys are omitted, never zeroed (S-0004/D-6's self-reported regime) — a harness
# that reports nothing stays visibly unreported.
TOKEN_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
)


def agent_token_counts(result: AgentResult) -> dict[str, int]:
    """The token counts an adapter self-reported, as the agent block's record
    keys — only the counts that are present: absent stays absent (S-0004/D-6). A
    plain AgentResult (no token fields) contributes nothing."""

    counts: dict[str, int] = {}

    for name in TOKEN_FIELDS:
        value: Any = getattr(result, name, None)

        if value is not None:
            counts[name] = int(value)

    return counts


def attempt_cost(agent: Mapping[str, Any]) -> float | None:
    """What the attempt cost by torve's own arithmetic (S-0064/D-12): the price
    the dispatch resolved from the provider record, applied to the counts the
    adapter reported.

    None where the seat resolved no price — a subscription genuinely has no
    per-token cost — and None where the adapter reported no counts. Both are
    unreported, and unreported is never zero (S-0004/D-6).
    """

    price: Any = agent.get("price")

    if not isinstance(price, dict):
        return None

    return Price.model_validate(price).cost(agent)


def priced(agent: Mapping[str, Any]) -> dict[str, Any]:
    """The agent block with the cost the engine computed (S-0064/D-12), keeping
    the harness's own number beside it as the adapter's claim.

    The claim is never the cost: measured, claude emits `unrecognized_model`
    for qwen3.8-flash and then prices the attempt off its own Anthropic table.
    A block the roster said nothing about is passed through untouched —
    resolving a seat against the roster is the next phase's, and until then a
    seat with no record keeps reporting what its harness reported.
    """

    block = dict(agent)

    if "price" not in block:
        return block

    claim = block.get("cost_usd")

    if claim is not None:
        block["adapter_cost_usd"] = claim

    block["cost_usd"] = attempt_cost(block)

    return block


def agent_burn(result: AgentResult) -> dict[str, Any]:
    """The burn profile a harness adapter derived at capture time from the
    durable store's own bytes, as the agent block's one nested key beside the
    token totals (S-0039/the-burn-profile) — present only when the stream carried
    per-turn facts. No stream, no block: absence stays visible and is never
    zeroed or inferred (S-0004/D-6). A plain AgentResult carries no burn attribute
    and contributes nothing; the block is recorded data, read by no control
    flow."""

    profile: Any = getattr(result, "burn", None)

    if profile is None:
        return {}

    return {"burn": cast("dict[str, Any]", profile.as_block())}


# ....................... #


# The closed vocabulary of attempt verdicts (S-0038/the-verdict, S-0038/D-3): one
# word per way an attempt can end, derived by the engine from facts it
# already holds at attempt end — exec results, gate report, escalation
# state — never from model output (S-0038/D-2). It grows by amendment when a
# reader needs a distinction; no router reads it (S-0034/D-5 stands).
ATTEMPT_VERDICTS: frozenset[str] = frozenset(
    {
        "green",
        "gates_red",
        "agent_timeout",
        "agent_error",
        "broker_refused",
        "halted",
        "gate_infrastructure",
    }
)


def gate_verdict(report_exit_code: int) -> str:
    """The verdict of an attempt whose gates ran: the agent exited 0 (the
    loop reaches the gate pass no other way), the report's exit code
    settles green against gates_red."""

    return "green" if report_exit_code == 0 else "gates_red"


# ....................... #

# The transfer ledger (S-0041/the-transfer-measured, S-0041/D-5). A runtime that carries a
# workspace over the wire — the OpenSandbox adapter's tar seed in and base64
# pipe out — books each leg's bytes and seconds against the task its sandbox
# spec is labelled with; the attempt-row builders drain the booking into a
# `transfer` block beside the agent block. A runtime that mounts and transfers
# nothing never writes here, so its rows lack the key outright — absent stays
# absent (S-0004/D-6), and an attempt that synced nothing is told apart from one
# whose sync-out moved zero bytes. The ledger is process-local and dies with
# the orchestrator's one process (S-0041/D-1).

_TRANSFER_LOCK = threading.Lock()
_pending_transfers: dict[str, dict[str, float]] = {}


def record_transfer(
    task_id: str,
    *,
    seed_bytes: int | None = None,
    seed_seconds: float | None = None,
    sync_out_bytes: int | None = None,
    sync_out_seconds: float | None = None,
) -> None:
    """Book one transfer leg's cost against a task. Legs accumulate, because
    an attempt moves two sandboxes (the agent's and its `-gates` battery,
    S-0003/D-8) and the row wants their sum; seconds are wall-clock cost of the
    leg including host-side packing, bytes are the wire bytes that crossed
    the API."""

    legs = {
        key: value
        for key, value in {
            "seed_bytes": seed_bytes,
            "seed_seconds": seed_seconds,
            "sync_out_bytes": sync_out_bytes,
            "sync_out_seconds": sync_out_seconds,
        }.items()
        if value is not None
    }

    with _TRANSFER_LOCK:
        booked = _pending_transfers.setdefault(task_id, {})

        for key, value in legs.items():
            booked[key] = booked.get(key, 0.0) + value


def _drain_transfer(task_id: str | None) -> dict[str, Any]:
    """Pop the booking for a task as the row's `transfer` block — once only,
    which is what makes it per-attempt. Attempt rows key by task id while
    sandbox labels key by the infrastructure id, so a shadow replay's booking
    (whose label reads `shadow-<task id>`) drains into the row of the task it
    shadows."""

    if task_id is None:
        return {}

    with _TRANSFER_LOCK:
        booked = _pending_transfers.pop(task_id, {})

        for key, value in _pending_transfers.pop(shadow_id(task_id), {}).items():
            booked[key] = booked.get(key, 0.0) + value

    return {
        key: round(value, 3) if key.endswith("_seconds") else int(value)
        for key, value in sorted(booked.items())
    }


# ....................... #

# The harness receipt's own account of the attempt (S-0065/D-6), booked by the
# adapter that read it and drained by whichever row ends the attempt — the
# transfer ledger's route above, for the same reason: the receipt is parsed
# where the harness output lives, and the record is built three modules away.
# A harness whose receipt names neither field books nothing, so the row lacks
# the keys outright — absent stays absent (S-0004/D-6), and an ending the engine
# was not told is never invented. Process-local, like the transfer ledger.

_RECEIPT_LOCK = threading.Lock()
_pending_receipts: dict[str, dict[str, str]] = {}


def record_receipt(
    task_id: str,
    *,
    terminal_reason: str | None = None,
    session_id: str | None = None,
) -> None:
    """Book what a harness receipt said against a task: how the harness says
    the run ended, and the session it ran under. Either may be missing, and a
    missing one is not recorded."""

    named = {
        key: value
        for key, value in {"terminal_reason": terminal_reason, "session_id": session_id}.items()
        if value
    }

    if not named:
        return

    with _RECEIPT_LOCK:
        _pending_receipts.setdefault(task_id, {}).update(named)


def _drain_receipt(task_id: str | None) -> dict[str, str]:
    """Pop a task's booking as the agent block's extra keys — once only,
    which is what keeps one attempt's ending off the next attempt's row."""

    if task_id is None:
        return {}

    with _RECEIPT_LOCK:
        return _pending_receipts.pop(task_id, {})


# ....................... #

# What the stream keeps of a gate's output and a contract's rows. Both are
# already written down once — the gate's own output rides the attempt to the
# operator's terminal and the task log, the row is in the contract — and a
# second copy in a record nobody reads it from is the duplication S-0059/D-10
# removed from commit trailers, unfixed here. So: a passing gate's output is
# dropped (the one reader, the retry's feedback pack, takes only a red gate's
# tail), a red gate's is kept to a bound comfortably over what that reader
# asks for, and a row is recorded as what identifies and governs it.
RECORDED_OUTPUT = 4000
_RED = ("fail", "error")


def _recorded_result(result: GateResult) -> dict[str, Any]:
    row = result.model_dump()
    output = str(row.get("output") or "")

    if result.outcome not in _RED:
        row["output"] = ""
    elif len(output) > RECORDED_OUTPUT:
        row["output"] = output[-RECORDED_OUTPUT:]

    return row


def _recorded_decision(row: InheritedDecision) -> dict[str, Any]:
    """The row as the stream keeps it: what names it, what grade it carries
    and what it governs — the three fields every reader of this stream asks
    for. Its text is the contract's and the corpus's."""

    return {"id": row.id, "grade": row.grade, "paths": list(row.paths)}


# ....................... #


def build_record(
    ctx: GateContext,
    report: RunReport,
    config_hash: str,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = ctx.task.id if ctx.task else None
    transfer = _drain_transfer(task_id)

    # S-0065/D-4: an attempt row that names no sha at all can never be joined
    # to a contract, a diff or a landing — it is a cost and a duration attached
    # to nothing, and it costs more in the record than it would have as an
    # absence, because it is counted. The eighteen unjoinable rows in the
    # stream are exactly this shape: merge_base and head both empty. Either sha
    # is enough for the join, so the refusal fires only when both are missing,
    # which is the caller handing over a context it never resolved. A bare gate
    # run over a human PR is not an attempt and has no agent block.
    if agent is not None and not ctx.merge_base and not ctx.head_sha:
        raise ValueError(
            f"attempt record for {task_id or 'no task'} names no base sha "
            "(merge_base and head are both empty): such a row can never be "
            "joined to a contract, a diff or a landing — resolve the base "
            "before the attempt runs"
        )

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "at": stamp(),
        "config_hash": config_hash,
        "torve_version": torve.__version__,  # toolchain, recorded beside the regime hash
        "base": ctx.base,
        "merge_base": ctx.merge_base,
        "head": ctx.head_sha,
        "task_id": task_id,
        # Which adapter, model and provider version produced the work under
        # gate (S-0004/telemetry-staged, S-0004/D-6) — None on runs with no agent (human PRs,
        # bare `torve gates run`). model_version None inside the block marks
        # an uncontrolled regime. What the harness receipt said about the
        # ending joins it (S-0065/D-6), where the harness returned it.
        "agent": None if agent is None else priced({**agent, **_drain_receipt(task_id)}),
        # The workspace transfer's cost, booked by a transferring runtime for
        # this attempt (S-0041/the-transfer-measured) — a sibling of the agent block because
        # it is the runtime's measurement, not the agent's self-report.
        **({} if not transfer else {"transfer": transfer}),
        "decisions": [_recorded_decision(d) for d in ctx.task.decisions] if ctx.task else [],
        "results": [_recorded_result(r) for r in report.results],
        "exit_code": report.exit_code,
        # The engine's one-word ending beside the gate report's exit code
        # (S-0038/D-2, S-0038/D-3) — present only on rows that recorded an agent: a
        # bare gate run over a human PR is not an attempt, and absence reads
        # as pre-0038 exactly as it does for every other additive key
        # (S-0038/D-6).
        **({} if agent is None else {"verdict": gate_verdict(report.exit_code)}),
        "bypass_count_by_gate": report.bypass_count_by_gate,
        "flaky_count_by_command": report.flaky_count_by_command,
    }


# ....................... #


def build_attempt_row(
    task: Task,
    agent: dict[str, Any],
    *,
    verdict: str,
    exit_code: int | None,
    timed_out: bool,
    escalation: str | None = None,
) -> dict[str, Any]:
    """The record of an attempt that ended without a gate pass (S-0038/D-1) —
    the shape the red-path record has carried since S-0004/telemetry-staged (the spend
    happened even though the gates will never run for this attempt; without
    a record here, a budget-killed or timed-out attempt's cost vanishes from
    every projection — four ~$4 first attempts were missing from
    cost-and-iterations when this was found), now the one shape for every
    such ending: `results: []`, `gates_run: false`, the agent block with
    whatever the adapter reported, and the engine-derived verdict naming
    how it ended. `escalation` carries the escalation reason verbatim on
    the endings the loop stops on, so a reader re-derives the verdict from
    the row's own fields — the vocabulary adds convenience, never
    information (S-0038/D-2's determinism argument)."""

    transfer = _drain_transfer(task.id)

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "at": stamp(),
        "config_hash": None,  # gates never ran; no manifest pass
        "torve_version": torve.__version__,
        "task_id": task.id,
        # The receipt's account of the ending rides the block here too
        # (S-0065/D-6) — the endings this row describes are exactly the ones a
        # terminal reason tells apart.
        "agent": priced({**agent, **_drain_receipt(task.id)}),
        # The runtime's booked transfer legs ride beside the agent block
        # exactly as on the gate-pass row (S-0041/the-transfer-measured): the spend on
        # moving the workspace happened even if nothing else did.
        **({} if not transfer else {"transfer": transfer}),
        "decisions": [_recorded_decision(d) for d in task.decisions],
        "results": [],
        "exit_code": exit_code,
        "gates_run": False,
        "timed_out": timed_out,
        "verdict": verdict,
        **({} if escalation is None else {"escalation": escalation}),
    }


# ....................... #


# The fields the event envelope already carries, so the record does not
# repeat them: a record is what happened, and who and when it happened to
# are the envelope's (S-0044 S-0044/A-4).
ENVELOPE_FIELDS = ("schema_version", "at", "task_id")


def record_payload(record: dict[str, Any], attempt: int) -> dict[str, Any]:
    """The attempt record as an event payload.

    Absence is preserved rather than defaulted: the stream's rule is that a
    missing key reads as "written before this key existed" (S-0038/D-6), and a
    payload that helpfully fills one in destroys that reading.
    """

    payload = {key: value for key, value in record.items() if key not in ENVELOPE_FIELDS}
    payload["attempt"] = attempt

    return payload


# ....................... #


def record_row(payload: Mapping[str, Any], *, task_id: str | None, at: str) -> dict[str, Any]:
    """The same record as a telemetry row — the carrier every projection
    reads. Rendered from the payload rather than built beside it, which is
    what makes the two carriers incapable of disagreeing."""

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "at": at,
        "task_id": task_id,
        **{key: value for key, value in payload.items() if key != "attempt"},
    }


# ....................... #


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # One line per write, whole, under a dispatch batch (S-0019/D-14, S-0019/A-6):
    # concurrent attempts share this stream in one process.
    with _APPEND_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ....................... #


def engine_event(root: Path, event: str, details: dict[str, Any]) -> None:
    """Engine health rides the existing telemetry path (S-0006/engine-health,
    S-0006/D-7): one stream, a `kind: engine` record — a second observability
    system would be a second system to operate. Blocked dispatches, kills
    and lane outcomes land here so contention and triage lag are queries,
    not hunches."""

    from torve.config import layout
    from torve.config.manifest import Manifest, load_manifest

    manifest_path = layout.gates_file(root)

    telemetry_rel = (
        load_manifest(manifest_path).telemetry
        if manifest_path.is_file()
        else Manifest(gates=[]).telemetry
    )

    append_record(
        root / telemetry_rel,
        {
            "schema_version": RECORD_SCHEMA_VERSION,
            "kind": "engine",
            "event": event,
            "at": stamp(),
            **details,
        },
    )


# ....................... #


def feedback_record(task_id: str, human_minutes: int, rework_after_review: bool) -> dict[str, Any]:
    """The two hand-entered fields (S-0004/telemetry-staged), keyed by task id in their
    own append-only stream — appending is easy, updating a row in an
    append-only store is not."""

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "at": stamp(),
        "task_id": task_id,
        "human_minutes": human_minutes,
        "rework_after_review": rework_after_review,
    }
