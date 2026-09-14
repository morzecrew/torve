"""One JSONL record per run (S-0002/telemetry-from-day-one). Three fields must be right from the
first line because none can be reconstructed later: `schema_version`,
`config_hash`, and decisions denormalised into the record, not referenced.

`config_hash` lives here rather than beside the manifest model: digesting the
regime needs the forze pin from `application.migrate`, and `config` may not
import `application` (S-0015/permitted-imports).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any, cast

import torve
from torve.application.ports import AgentResult, BrokerUsage, SandboxSpec
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
# A harness whose receipt names no field books nothing, so the row lacks
# the keys outright — absent stays absent (S-0004/D-6), and an ending the engine
# was not told is never invented. The turn count, the refused calls and the
# subagent counters ride the same booking under the same rule (S-0073/D-3):
# recorded when the receipt carries them, and never invented when it does not.
# Process-local, like the transfer ledger.

_RECEIPT_LOCK = threading.Lock()
_pending_receipts: dict[str, dict[str, Any]] = {}


def record_receipt(
    task_id: str,
    *,
    terminal_reason: str | None = None,
    session_id: str | None = None,
    num_turns: int | None = None,
    permission_denials: list[Any] | None = None,
    subagent_stats: dict[str, Any] | None = None,
) -> None:
    """Book what a harness receipt said against a task: how the harness says
    the run ended, the session it ran under, how many turns it took, the calls
    it was refused and what it spawned. Any of them may be missing, and a
    missing one is not recorded.

    A reported emptiness is not a missing field: no denials and a subagent
    counter reading zero are answers, and the row keeps them (S-0073/D-3).
    """

    named = {
        key: value
        for key, value in {
            "terminal_reason": terminal_reason,
            "session_id": session_id,
            "num_turns": num_turns,
            "permission_denials": permission_denials,
            "subagent_stats": subagent_stats,
        }.items()
        if value is not None
    }

    if not named:
        return

    with _RECEIPT_LOCK:
        _pending_receipts.setdefault(task_id, {}).update(named)


def _drain_receipt(task_id: str | None) -> dict[str, Any]:
    """Pop a task's booking as the agent block's extra keys — once only,
    which is what keeps one attempt's ending off the next attempt's row."""

    if task_id is None:
        return {}

    with _RECEIPT_LOCK:
        return _pending_receipts.pop(task_id, {})


# ....................... #

# The per-request context curve (S-0075/D-1), on the receipt's route and for
# its reason: the curve is reconstructed where the trace lives — the harness
# adapter scans the store's own bytes — and the record is built three modules
# away. The booking is the agent block's one nested `context` key, and a task
# whose row never drains reads as pre-D-1, exactly like a missing receipt key.

_CONTEXT_LOCK = threading.Lock()
_pending_context: dict[str, dict[str, Any]] = {}


def record_context(task_id: str, block: dict[str, Any]) -> None:
    """Book an attempt's context-curve block against its task: the shape that
    produced it and the statistics, with the sum checked against the receipt's
    own total. Whole and per-attempt — a later row of the same task drains the
    previous attempt's booking only as the receipt's is drained (pop-once)."""

    with _CONTEXT_LOCK:
        _pending_context[task_id] = block


def _drain_context(task_id: str | None) -> dict[str, Any]:
    """Pop a task's booking as the agent block's `context` key — once only,
    which is what keeps one attempt's curve off the next attempt's row."""

    if task_id is None:
        return {}

    with _CONTEXT_LOCK:
        block = _pending_context.pop(task_id, {})

    return {"context": block} if block else {}


# ....................... #

# The burn classifier (S-0075/D-2): what an attempt's turns were for. The
# harness's own draft of the stream is the `burn` block — per-turn counts,
# no classes. Naming what a call was for is the engine's, applied at record
# time to the per-call facts a scanner emits from the same bytes. Application
# code, because every mitigation is judged by one of these classes and a
# vocabulary the adapters disagreed on would be a fact nobody could quote.

BURN_CLASSES: tuple[str, ...] = (
    "pack_read",
    "orientation",
    "in_scope_read",
    "edit",
    "test_run",
    "lint_run",
    "bookkeeping",
    "other",
)

# The vocabulary a class reads (S-0075's own risk clause — a heuristic, and
# a wrong class is worse than none, so the fixture that asserts one per call
# is what keeps it honest). Tool names and command fragments are the engine's
# own, settled by reading the calls they claim; the pack root is the context
# the engine wrote for the attempt, and a call these do not name is `other`.
_EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
_PACK_READ_TOOLS = frozenset({"Read", "Grep"})
_ORIENTATION_TOOLS = frozenset({"Glob"})
_BOOKKEEPING_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"})
_BASH = "Bash"
_PACK_ROOT = ".torve/"

_TEST_RUN_RE = re.compile(r"\b(pytest|just test)\b")
_LINT_RUN_RE = re.compile(r"\b(ruff|mypy|basedpyright|black|lint-imports)\b")
_BOOKKEEPING_RE = re.compile(r"\b(torve log|git (commit|add|push))\b")
# A rerun is a test command differing from the previous only in output
# filtering (S-0075): the pipe leg and the quiet and redirect flags are the
# filtering, the rest is the run.
_RERUN_NOISE_RE = re.compile(r"\s*(-q(?:uiet)?|>/dev/null|2>&1)\b")

# The same stream's two non-call line facts, bookkept beside the calls: a
# compaction event is counted as one, and the init line's inventories become
# the counts of what every request re-read (S-0075/D-4).
_SESSION_COMPACT = "SessionStart:compact"
_INIT_LINE = "init"
_INIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("tools", "init_tools"),
    ("skills", "init_skills"),
    ("mcp_servers", "init_mcp_servers"),
    ("plugins", "init_plugins"),
    ("agents", "init_agents"),
)


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _targets(input_: Mapping[str, Any] | None) -> list[str]:
    """The paths a read names, in the shapes the engine's tools spell them."""

    if input_ is None:
        return []

    found: list[str] = []

    for key in ("file_path", "path"):
        value: Any = input_.get(key)

        if isinstance(value, str) and value:
            found.append(value)

    return found


def _classify_call(
    name: str,
    input_: Mapping[str, Any] | None,
    scope: Sequence[str] | None,
) -> str:
    """One call's class. Edits are calls that change files; a read targets the
    pack when its path is under the engine's own context and an in-scope file
    when it matches the task's allow globs; Bash is what it runs. Anything a
    name does not claim is `other` — the class a silently-swallowed call lands
    on, which the fixture asserts against."""

    if name in _EDIT_TOOLS:
        return "edit"

    if name in _PACK_READ_TOOLS:
        targets = _targets(input_)

        if any(target.startswith(_PACK_ROOT) for target in targets):
            return "pack_read"

        if scope and any(
            fnmatch.fnmatch(target, pattern) for target in targets for pattern in scope
        ):
            return "in_scope_read"

        return "orientation"

    if name == _BASH:
        command = _str((input_ or {}).get("command"))

        if _TEST_RUN_RE.search(command):
            return "test_run"

        if _LINT_RUN_RE.search(command):
            return "lint_run"

        if _BOOKKEEPING_RE.search(command):
            return "bookkeeping"

        return "orientation"

    if name in _ORIENTATION_TOOLS:
        return "orientation"

    if name in _BOOKKEEPING_TOOLS:
        return "bookkeeping"

    return "other"


def _normalise_command(command: str) -> str:
    """A test command reduced to what it actually runs, for rerun detection."""

    head = command.split("|", 1)[0]

    return _RERUN_NOISE_RE.sub("", head).strip()


def classify_tool_calls(
    calls: Sequence[Mapping[str, Any]],
    *,
    scope: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The classified half of the burn profile (S-0075/D-2), as the `burn`
    block's `profile`: each call by class — pack reads, orientation,
    in-scope reads, edits, test runs, lint runs, bookkeeping, other — beside
    the counts that go with it: calls before the first edit, reruns (a test
    run repeating an earlier one modulo output filtering), calls per message,
    result bytes by class, compaction events and the latency medians.

    The input is the stream's line facts as a scanner would emit them: each
    tool call as `{"name", "input", "message", "bytes", "latency_ms"}`, the
    opening init line's inventories, and a compaction event (S-0075's
    out-of-scope clause, counted as an event only). `message` is the call's
    0-based message ordinal; a call without one counts as its own message,
    which is the 1.00-per-message every measured attempt already shows.
    `scope` is the task's allow globs, the thing that tells an in-scope read
    from an orientation read; without it every non-pack read is orientation.

    Empty input yields `{}`: no stream, no block (S-0039/D-4), and absent
    counts stay absent, never zero (S-0004/D-6)."""

    classes: dict[str, int] = {}
    bytes_by_class: dict[str, int] = {}
    latency_by_class: dict[str, list[float]] = {}
    messages: set[int] = set()
    init_counts: dict[str, int] = {}
    compaction_events = 0
    reruns = 0
    n_calls = 0
    first_edit: int | None = None
    seen_commands: set[str] = set()

    for fact in calls:
        name = _str(fact.get("name"))

        if name == _SESSION_COMPACT:
            compaction_events += 1
            continue

        if name == _INIT_LINE:
            for field, key in _INIT_FIELDS:
                value: Any = fact.get(field)

                if isinstance(value, list) and value:
                    init_counts[key] = len(value)

            continue

        if not name:
            continue

        message: Any = fact.get("message")
        message_id = (
            message
            if isinstance(message, int) and message >= 0
            else (max(messages) + 1 if messages else 0)
        )
        messages.add(message_id)

        raw_input: Any = fact.get("input")
        input_ = cast("Mapping[str, Any]", raw_input) if isinstance(raw_input, Mapping) else None
        cls = _classify_call(name, input_, scope)
        classes[cls] = classes.get(cls, 0) + 1

        if first_edit is None and cls == "edit":
            first_edit = n_calls

        n_calls += 1

        size: Any = fact.get("bytes")

        if isinstance(size, (int, float)) and not isinstance(size, bool):
            bytes_by_class[cls] = bytes_by_class.get(cls, 0) + int(size)

        latency: Any = fact.get("latency_ms")

        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latency_by_class.setdefault(cls, []).append(float(latency))

        if cls == "test_run":
            command = _str((input_ or {}).get("command"))
            normalised = _normalise_command(command) if command else ""

            if normalised:
                if normalised in seen_commands:
                    reruns += 1
                else:
                    seen_commands.add(normalised)

    if not n_calls:
        return {}

    block: dict[str, Any] = {
        "calls": n_calls,
        "messages": len(messages),
        "calls_per_message": round(n_calls / len(messages), 3),
        "classes": {class_: count for class_, count in sorted(classes.items()) if count > 0},
    }

    if first_edit is not None:
        block["calls_before_first_edit"] = first_edit

    if reruns:
        block["reruns"] = reruns

    if bytes_by_class:
        block["bytes_by_class"] = {
            class_: bytes_by_class[class_] for class_ in BURN_CLASSES if class_ in bytes_by_class
        }

    if compaction_events:
        block["compaction_events"] = compaction_events

    if latency_by_class:
        block["latency_medians"] = {
            class_: median(latency_by_class[class_])
            for class_ in BURN_CLASSES
            if class_ in latency_by_class
        }

    block.update(init_counts)

    return block


# ....................... #

# The classified profile's booking (S-0075/D-2), on the receipt's route and
# for its reason: the per-call facts live where the trace lives, and the row
# is built three modules away. Drained by the same attach that rides the
# context curve's booking, and applied to the row under the harness's own
# `burn` block — the profile is the same profile's account of what the calls
# were for, and a row whose task never booked reads as pre-D-2.

_BURN_PROFILE_LOCK = threading.Lock()
_pending_burn_profiles: dict[str, dict[str, Any]] = {}


def record_burn_profile(task_id: str, block: dict[str, Any]) -> None:
    """Book an attempt's classified burn profile against its task: whole and
    per-attempt, and a later row of the same task drains the previous
    attempt's profile only as the context curve's is — pop-once."""

    with _BURN_PROFILE_LOCK:
        _pending_burn_profiles[task_id] = block


def _drain_burn_profile(task_id: str | None) -> dict[str, Any]:
    """Pop a task's booking as the `burn` block's `profile` key — once only,
    which is what keeps one attempt's classification off the next one's."""

    if task_id is None:
        return {}

    with _BURN_PROFILE_LOCK:
        return _pending_burn_profiles.pop(task_id, {})


# ....................... #

# The profile a reader derives rather than the one an attempt left behind
# (S-0075/D-6): the recorded block is a cache of this derivation, and the
# retained trace is what settles a disagreement between them. So every
# attempt whose trace is still on disk has a profile — including the ones
# that ran before the classifier existed — and a corrected class
# reclassifies the history instead of leaving the history wrong.
#
# The scanner that turns a trace into per-call facts belongs to the harness
# adapter and is handed in: an application module may not import one
# (S-0055/D-23), and the classification is the engine's word either way.

TraceScan = Callable[[Path, str], list[dict[str, Any]]]

# What an attempt was fenced by, by task and by the sha the attempt built on
# — the ledger's own reader, handed in for the same reason the scanner is:
# it reaches git, and the module that reads contracts out of history sits
# above the stream this one writes.
ContractReader = Callable[[str, str], Mapping[str, Any] | None]

# Where one attempt's profile came from, in the order a reader trusts them:
# the trace it was derived from now, the block the attempt recorded when the
# trace has been retained away, neither.
BURN_SOURCES: tuple[str, ...] = ("trace", "recorded", "absent")

# The mount a trace spells its paths against. The recorded profile was
# classified against the workspace the attempt ran in, so a derivation that
# read the same bytes from the host would call every in-scope read an
# orientation read and disagree with its own cache for no reason.
_TRACE_WORKDIR = SandboxSpec.workdir


def _agent_block(row: Mapping[str, Any]) -> Mapping[str, Any]:
    block: Any = row.get("agent")

    return cast("Mapping[str, Any]", block) if isinstance(block, Mapping) else {}


def recorded_burn_profile(row: Mapping[str, Any]) -> dict[str, Any]:
    """The classification the attempt itself recorded — the cache, empty
    where the attempt ran before the classifier or named no call."""

    burn: Any = _agent_block(row).get("burn")

    if not isinstance(burn, Mapping):
        return {}

    profile: Any = cast("Mapping[str, Any]", burn).get("profile")

    return dict(cast("Mapping[str, Any]", profile)) if isinstance(profile, Mapping) else {}


def derive_burn_profile(
    row: Mapping[str, Any],
    root: Path,
    scan: TraceScan,
    *,
    scope: Sequence[str] | None = None,
) -> tuple[dict[str, Any], str]:
    """One attempt's burn profile as a reader sees it (S-0075/D-6), beside
    the source that answered: `trace` where the attempt's retained trace was
    read and classified again, `recorded` where the trace is gone and the
    cached block answers, `absent` where neither does.

    The trace wins whenever it is still on disk — it is the evidence, the
    recorded block is a copy of an older reading of it."""

    ref: Any = _agent_block(row).get("trace_ref")

    if isinstance(ref, str) and ref:
        trace = root / ref

        if trace.is_file():
            derived = classify_tool_calls(scan(trace, _TRACE_WORKDIR), scope=scope)

            if derived:
                return derived, "trace"

    recorded = recorded_burn_profile(row)

    return (recorded, "recorded") if recorded else ({}, "absent")


def _scope_allow(contract: Mapping[str, Any] | None) -> Sequence[str] | None:
    """The allow globs the attempt was fenced by, which is what tells an
    in-scope read from an orientation read. None where the contract has left
    both the tree and the history: without it every read outside the pack is
    orientation, and saying so is better than guessing a scope."""

    scope: Any = (contract or {}).get("scope")

    if not isinstance(scope, Mapping):
        return None

    allow: Any = cast("Mapping[str, Any]", scope).get("allow")

    return (
        [str(entry) for entry in cast("list[object]", allow)] if isinstance(allow, list) else None
    )


def _measured(value: Any) -> float | None:
    """A recorded number, or None. `bool` is an int in Python and is never a
    measurement here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    return float(value)


def _share(part: Any, whole: Any) -> float | None:
    """A share of a measured whole, None where either side is unmeasured or
    the whole is zero — a change that read nothing has no share, not a zero
    and not an infinity (S-0004/D-6)."""

    measured = _measured(part)
    total = _measured(whole)

    if measured is None or total is None or total <= 0:
        return None

    return measured / total


def burn_population(
    rows: Sequence[Mapping[str, Any]],
    root: Path,
    scan: TraceScan,
    *,
    contracts: ContractReader | None = None,
) -> dict[str, Any]:
    """The corpus's burn profiles, derived at read time (S-0075/D-6): how
    many attempts have a profile at all and from where, how many the
    recording alone would have produced, how many the trace reclassifies, and
    the two baselines a mitigation is judged against — the median share of
    read bytes that went to orientation, and the median share of an attempt's
    calls that came before its first edit.

    `rows` is the population the caller counts, so this and the rates beside
    it exclude the same attempts. `contracts` answers what an attempt was
    fenced by, read at its own sha; the answer is memoised here because one
    sha answers for every attempt built on it. Without one, no attempt has a
    scope and every read outside the pack reads as orientation."""

    resolved: dict[tuple[str, str], Mapping[str, Any] | None] = {}
    sources = dict.fromkeys(BURN_SOURCES, 0)
    cached = 0
    reclassified = 0
    with_edit = 0
    orientation_shares: list[float] = []
    before_edit_shares: list[float] = []

    for row in rows:
        key = (str(row.get("task_id") or ""), str(row.get("merge_base") or row.get("head") or ""))

        if contracts is not None and key[0] and key not in resolved:
            resolved[key] = contracts(*key)

        profile, source = derive_burn_profile(
            row, root, scan, scope=_scope_allow(resolved.get(key))
        )
        sources[source] += 1
        recorded = recorded_burn_profile(row)

        if recorded:
            cached += 1

        if source == "trace" and recorded and profile != recorded:
            reclassified += 1

        if not profile:
            continue

        by_class: Any = profile.get("bytes_by_class")

        if isinstance(by_class, Mapping):
            read = cast("Mapping[str, Any]", by_class)
            share = _share(
                read.get("orientation"),
                sum(_measured(size) or 0.0 for size in read.values()),
            )

            if share is not None:
                orientation_shares.append(share)

        classes: Any = profile.get("classes")

        if isinstance(classes, Mapping) and cast("Mapping[str, Any]", classes).get("edit"):
            with_edit += 1
            share = _share(profile.get("calls_before_first_edit"), profile.get("calls"))

            if share is not None:
                before_edit_shares.append(share)

    return {
        "attempts": len(rows),
        "profiled": sources["trace"] + sources["recorded"],
        "sources": sources,
        "cached": cached,
        "reclassified": reclassified,
        "with_edit": with_edit,
        "median_orientation_share_of_bytes": median(orientation_shares)
        if orientation_shares
        else None,
        "median_calls_before_first_edit_share": median(before_edit_shares)
        if before_edit_shares
        else None,
    }


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


def _attach_prefix(
    agent: Mapping[str, Any],
    task_id: str | None,
    task: Task | None,
) -> dict[str, Any]:
    """The attempt's account of everything every request re-read, beside the
    receipt's own account of the ending (S-0065/D-6): the context curve's
    booking (S-0075/D-1), the classified burn profile (S-0075/D-2) merged
    under the harness's own `burn` block, and the contract's row count and
    prose size (S-0075/D-4). Pop-once like every booking the row drains; a
    task that booked nothing, and a contract with no rows, are not recorded —
    absent stays absent (S-0004/D-6)."""

    block = {**agent, **_drain_receipt(task_id), **_drain_context(task_id)}
    profile = _drain_burn_profile(task_id)

    if profile:
        burn = dict(block.get("burn") or {})
        burn["profile"] = profile
        block["burn"] = burn

    if task is not None and task.decisions:
        block["contract"] = {
            "rows": len(task.decisions),
            "characters": sum(len(decision.text) for decision in task.decisions)
            + len(task.intent or ""),
        }

    return block


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
        "agent": None if agent is None else priced(_attach_prefix(agent, task_id, ctx.task)),
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
        # terminal reason tells apart. The context curve's booking rides beside
        # it on the same route (S-0075/D-1), and the classified burn profile and
        # the contract's facts follow it (S-0075/D-2, S-0075/D-4): the spend
        # happened even if nothing else did, and the row must be able to say so.
        "agent": priced(_attach_prefix(agent, task.id, task)),
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
