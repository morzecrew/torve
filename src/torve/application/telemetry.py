"""One JSONL record per run (RFC 0002 §8). Three fields must be right from the
first line because none can be reconstructed later: `schema_version`,
`config_hash`, and decisions denormalised into the record, not referenced.

`config_hash` lives here rather than beside the manifest model: digesting the
regime needs the forze pin from `application.migrate`, and `config` may not
import `application` (RFC 0015 §2.1).
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import torve
from torve.application.ports import AgentResult, BrokerUsage
from torve.base.naming import WORKTREE_DIR
from torve.config import layout
from torve.config.runconfig import RunnerConfig
from torve.domain.task import SCHEMA_VERSION, Task
from torve.gates.context import GateContext
from torve.gates.runner import RunReport

# ----------------------- #

_APPEND_LOCK = threading.Lock()
_REGIME_LOCK = threading.Lock()


# ....................... #


def config_hash(
    manifest_path: Path,
    root: Path,
    config: RunnerConfig | None = None,
    image_digest: str | None = None,
) -> str:
    """Digest of the regime a run belongs to (RFC 0002 §8, D-9.8): gates.yaml,
    the agent-skills lockfile, the Torve package version (its gates and
    shipped skills change behavior — A-3), the pinned forze version (a
    substrate upgrade is a regime change, and possibly a migration — A-6),
    and — when the runner configuration is at hand — the tier mapping and
    provider policy (RFC 0004 §6, D-4.3): which adapter executed and where
    contents were allowed to go are part of what a number was measured under.
    The sandbox image digest joins when the caller resolved one (RFC 0017 §2,
    D-17.1): two runs under one tag but different digests are two regimes.
    """

    from torve.application.migrate import forze_pin

    parts: dict[str, str] = {
        "gates.yaml": manifest_path.read_text(encoding="utf-8"),
        "torve": torve.__version__,
        # The substrate pin, not the installed version: the pin names the
        # schema regime the migrations were written against (rfcs/0012-migrations.md §6);
        # torve doctor is what compares it to the installed version.
        "forze": forze_pin(),
    }

    if config is not None:
        parts["tiers"] = json.dumps(
            {name: tier.model_dump() for name, tier in sorted(config.tiers.items())},
            sort_keys=True,
        )

        parts["providers"] = json.dumps(config.providers.model_dump(), sort_keys=True)

        # The egress regime (RFC 0021 §5.5, D-21.8): the broker adapter and
        # the run's routing are part of what a number was measured under —
        # two runs under different egress regimes are two regimes.
        parts["broker"] = json.dumps(config.broker.model_dump(), sort_keys=True)

    if image_digest is not None:
        parts["image"] = image_digest

    lock = root / "skills-lock.json"

    if lock.is_file():
        parts["skills-lock.json"] = lock.read_text(encoding="utf-8")

    # The vendored skills tree (RFC 0009 §4a, D-9.13): an edited vendored
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
    """The `parts` a `config_hash` was computed over (D-4.19, A-72): written
    once, only if absent, so `config_hash` names a regime someone can open
    rather than a bare hex string. Content-addressed by the hash it produced
    — a write racing an identical write lands the same bytes either way, so
    the existence check is the only guard that matters.

    Lands beside the telemetry stream, not the tree that was hashed: a
    worktree is always `<host>/.wt/<name>` (`naming.worktree`, D-3.4) and is
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
    (D-21.5): both are recorded, the broker's is authoritative. Counts and
    metadata only — the broker never keeps bodies (D-21.7)."""

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
# keys are omitted, never zeroed (D-4.6's self-reported regime) — a harness
# that reports nothing stays visibly unreported.
TOKEN_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
)


def agent_token_counts(result: AgentResult) -> dict[str, int]:
    """The token counts an adapter self-reported, as the agent block's record
    keys — only the counts that are present: absent stays absent (D-4.6). A
    plain AgentResult (no token fields) contributes nothing."""

    counts: dict[str, int] = {}

    for name in TOKEN_FIELDS:
        value: Any = getattr(result, name, None)

        if value is not None:
            counts[name] = int(value)

    return counts


def agent_burn(result: AgentResult) -> dict[str, Any]:
    """The burn profile a harness adapter derived at capture time from the
    durable store's own bytes, as the agent block's one nested key beside the
    token totals (RFC 0039 §5.3) — present only when the stream carried
    per-turn facts. No stream, no block: absence stays visible and is never
    zeroed or inferred (D-4.6). A plain AgentResult carries no burn attribute
    and contributes nothing; the block is recorded data, read by no control
    flow."""

    profile: Any = getattr(result, "burn", None)

    if profile is None:
        return {}

    return {"burn": cast("dict[str, Any]", profile.as_block())}


# ....................... #


# The closed vocabulary of attempt verdicts (RFC 0038 §5.2, D-38.3): one
# word per way an attempt can end, derived by the engine from facts it
# already holds at attempt end — exec results, gate report, escalation
# state — never from model output (D-38.2). It grows by amendment when a
# reader needs a distinction; no router reads it (D-34.5 stands).
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


def build_record(
    ctx: GateContext,
    report: RunReport,
    config_hash: str,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_hash": config_hash,
        "torve_version": torve.__version__,  # toolchain, recorded beside the regime hash
        "base": ctx.base,
        "merge_base": ctx.merge_base,
        "head": ctx.head_sha,
        "task_id": ctx.task.id if ctx.task else None,
        # Which adapter, model and provider version produced the work under
        # gate (RFC 0004 §6, D-4.6) — None on runs with no agent (human PRs,
        # bare `torve gates run`). model_version None inside the block marks
        # an uncontrolled regime.
        "agent": agent,
        "decisions": [d.model_dump() for d in ctx.task.decisions] if ctx.task else [],
        "results": [r.model_dump() for r in report.results],
        "exit_code": report.exit_code,
        # The engine's one-word ending beside the gate report's exit code
        # (D-38.2, D-38.3) — present only on rows that recorded an agent: a
        # bare gate run over a human PR is not an attempt, and absence reads
        # as pre-0038 exactly as it does for every other additive key
        # (D-38.6).
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
    """The record of an attempt that ended without a gate pass (D-38.1) —
    the shape the red-path record has carried since RFC 0004 §6 (the spend
    happened even though the gates will never run for this attempt; without
    a record here, a budget-killed or timed-out attempt's cost vanishes from
    every projection — four ~$4 first attempts were missing from
    cost-and-iterations when this was found), now the one shape for every
    such ending: `results: []`, `gates_run: false`, the agent block with
    whatever the adapter reported, and the engine-derived verdict naming
    how it ended. `escalation` carries the escalation reason verbatim on
    the endings the loop stops on, so a reader re-derives the verdict from
    the row's own fields — the vocabulary adds convenience, never
    information (D-38.2's determinism argument)."""

    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_hash": None,  # gates never ran; no manifest pass
        "torve_version": torve.__version__,
        "task_id": task.id,
        "agent": dict(agent),
        "decisions": [d.model_dump() for d in task.decisions],
        "results": [],
        "exit_code": exit_code,
        "gates_run": False,
        "timed_out": timed_out,
        "verdict": verdict,
        **({} if escalation is None else {"escalation": escalation}),
    }


# ....................... #


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # One line per write, whole, under a dispatch batch (D-19.14, A-39):
    # concurrent attempts share this stream in one process.
    with _APPEND_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ....................... #


def engine_event(root: Path, event: str, details: dict[str, Any]) -> None:
    """Engine health rides the existing telemetry path (RFC 0006 §5b,
    D-6.7): one stream, a `kind: engine` record — a second observability
    system would be a second system to operate. Blocked dispatches, kills
    and lane outcomes land here so contention and triage lag are queries,
    not hunches."""

    from datetime import UTC, datetime

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
            "schema_version": SCHEMA_VERSION,
            "kind": "engine",
            "event": event,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **details,
        },
    )


# ....................... #


def feedback_record(task_id: str, human_minutes: int, rework_after_review: bool) -> dict[str, Any]:
    """The two hand-entered fields (RFC 0004 §6), keyed by task id in their
    own append-only stream — appending is easy, updating a row in an
    append-only store is not."""

    return {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": task_id,
        "human_minutes": human_minutes,
        "rework_after_review": rework_after_review,
    }
