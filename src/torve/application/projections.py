"""`torve context` — a projection of accumulated facts into a form a
planning session can consume (S-0007/torve-context). Not a plan: tasks by state,
escalations by reason, execution-log divergences ready to become
decision-table rows, findings awaiting the operator, per-gate health, cost
against `config_hash`, the programme view of the RFC graph (S-0007/D-11),
asserted `implementation` beside derived per-phase progress with
disagreements flagged (S-0007/D-15), the character calibration — declared
character against the realized conviction profile, measurement only
(S-0034/D-8) — and the document-level half of the specification-quality report
(S-0022/document-level-report, S-0022/D-6): the same MCP surface that already exposes this
projection carries it to a planning session with no new tool.

Everything here is read from files the engine already writes — contracts,
run states, execution logs, the feedback and telemetry streams, the corpus.
Progress is computed on demand and stored nowhere (S-0016/D-22). The projection
emits data; judgement stays with the human reading it.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from torve.application.manager import IN_FLIGHT, Board, TaskView, current_shape, project
from torve.application.planner import document_of
from torve.application.runstate import RunState
from torve.application.specquality import operator_attention, read_tasks, render_operator_attention
from torve.application.telemetry import TOKEN_FIELDS, record_row
from torve.base import naming
from torve.base.clock import parse, stamp
from torve.config import layout, spec
from torve.config.manifest import UNLABELED_AXIS, Manifest, load_manifest
from torve.config.runconfig import RunnerConfig
from torve.domain.events import EventKind
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import DISPATCHABLE_ROLES, Task
from torve.domain.vocabulary import GATE_AXES

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from torve.domain.events import EventRecord

# ----------------------- #

ACTIVE = {TaskState.CLAIMED, TaskState.RUNNING, TaskState.GATED, TaskState.REVIEWED}

# S-0004/measurement-defects-to-fix-before-trusting-a-number, reproduced verbatim (S-0022/D-7, LOCKED: printed with the report,
# never paraphrased). `torve.cli.spec` owns and prints this same text for
# `torve spec health`; the layering contract puts `torve.cli` above
# `torve.application`, so this module cannot import it back and the string
# is copied rather than shared — a wording change updates both call sites.
QUASI_EXPERIMENT_CAVEAT = (
    "Baseline is a quasi-experiment, not an A/B: tasks before "
    "and after are different tasks, done under different conditions. This "
    'supports direction ("iterations fell") and not magnitude ("40% faster").'
)

# The two escalation reasons that indict a document rather than the code
# that executed it (charter S-0001/A-7, S-0001/A-8) — S-0022/document-level-report asks for these on
# their own line even when a document has never triggered either.
DOCUMENT_INDICTING_REASONS = (
    str(EscalationReason.UNDERSPECIFIED),
    str(EscalationReason.STALE_INHERITANCE),
)

# A table, not a dump — mirrors specquality's own bound on decided_claims.
SPEC_DRIFT_FINDINGS_LIMIT = 10


# ....................... #

# S-0065/D-7: three readers counted landings and disagreed — the record's
# `landing.recorded` events (263), the lane's `lane_landed` telemetry (49)
# and the landing files in the tree (58). The landing files are the
# carrier, and these two functions are how it is read. A landing file is
# only in the tree at this base because the commit carrying it landed
# here, so the file answers "did this task land" with no store, no
# telemetry and no git; the record's events are already minted from these
# same files by `decisions.landing_events`, and the lane's event is
# reconciled against them at the landing rather than tallied beside them.


def shipped_landings(root: Path, spec_dir: Path | None = None) -> dict[str, str]:
    """Task id to the commit that landed it, from the landings the tree
    holds (S-0059/D-12) — newest winning.

    A task with no run state is not necessarily unstarted: the engine did
    not run it, but a landing records that someone did. This read the
    commit history until the landing file carried the commit, and had to
    know three spellings of a shipped task to do it (S-0007/D-26, retired
    here); now it reads files, so a tree without git answers too.
    """

    from torve.application.decisions import landed_by_task

    return landed_by_task(root, spec_dir if spec_dir is not None else root / layout.SPECS_DIR)


def lane_landings(root: Path) -> dict[str, str]:
    """Task id to the commit the lane landed it as, from this host's own
    stream — the landings on a document branch that the base's tree does not
    yet hold. A phase run by hand and landed by `torve merge` is a landing the
    served manager would otherwise never hear of, and its dependents would
    wait on the board forever (bloomery S-0008, 2026-09-19). Newest wins.

    A document's landing is its tasks' landing (S-0085/D-3): the merge commit
    the base holds stamps every task the branch carried, so ancestry answers
    the same for a squash — whose branch commits the base never holds — as for
    a fast-forward. `shipped_landings` and the tree's landing files are
    unchanged."""

    landed: dict[str, str] = {}

    for row in stream_rows(root):
        event = row.get("event")

        if event == "lane_landed" and row.get("task") and row.get("sha"):
            landed[str(row["task"])] = str(row["sha"])

        elif event == "lane_document_landed" and row.get("sha"):
            for task_id in row.get("tasks") or []:
                landed[str(task_id)] = str(row["sha"])

    return landed


def shipped_ids(root: Path, spec_dir: Path | None = None) -> set[str]:
    """Task ids the tree records as landed, with or without a commit — the
    denominator every rate is divided by, whichever reader asks."""

    from torve.application.decisions import landed_task_ids

    return landed_task_ids(root, spec_dir if spec_dir is not None else root / layout.SPECS_DIR)


# ....................... #


def cross_document_waits(
    root: Path, landed: Collection[str] = ()
) -> dict[str, dict[str, list[str]]]:
    """Task id to the tasks of *another* document it is still waiting on,
    keyed by that document's id (S-0085/D-6). A person reading the board sees
    which document's landing the wait is on, and so which pull request to look
    at, rather than task ids to resolve by hand.

    *landed* is what has landed already; a dependency in it is no wait."""

    tasks_dir = root / layout.TORVE_DIR / "tasks"
    spec_of: dict[str, str] = {}
    depends_on: dict[str, list[str]] = {}

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")) if tasks_dir.is_dir() else []:
        record = _load_yaml_dict(contract)

        if record is None:
            continue

        task_id = str(record.get("id", contract.parent.name))
        spec_of[task_id] = str(record.get("spec") or "")
        depends_on[task_id] = [str(one) for one in record.get("depends_on") or []]

    waits: dict[str, dict[str, list[str]]] = {}

    for task_id, dependencies in depends_on.items():
        by_document: dict[str, list[str]] = {}

        for dependency in dependencies:
            document = spec_of.get(dependency, "")

            if dependency in landed or not document or document == spec_of[task_id]:
                continue

            by_document.setdefault(document, []).append(dependency)

        if by_document:
            waits[task_id] = {d: sorted(ids) for d, ids in sorted(by_document.items())}

    return waits


# ....................... #


def _load_yaml_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, yaml.YAMLError):
        return None

    if not isinstance(raw, dict):
        return None

    return cast("dict[str, Any]", raw)


# ....................... #


def _tasks(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return found

    shipped = shipped_ids(root)

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        record = _load_yaml_dict(contract)

        if record is None:
            continue

        task_id = str(record.get("id", contract.parent.name))

        entry: dict[str, Any] = {
            "id": task_id,
            "spec": record.get("spec"),
            # `shipped` is a projection-derived pseudo-state, not a member of
            # the engine's state machine: no run state exists, but a shipping
            # commit cites the task.
            "phase": record.get("phase", 0),
            "role": record.get("role", "implement"),
            # A drafting contract (intake, decompose) with no live run was
            # consumed by its adoption; a review contract with no live run
            # concluded with the landing its verdict gated — "unstarted"
            # would claim work is still owed. A run state below overrides.
            "state": (
                "shipped"
                if task_id in shipped
                else ("consumed" if record.get("role") in ("draft", "review") else "unstarted")
            ),
            "attempts": 0,
            "escalation": None,
            "escalated_at": None,
            # S-0026 S-0026/D-5: read by this projection only — dispatch, lane
            # and store never consult it.
            "parent": record.get("parent"),
            # A review contract's targets name the task under review — the
            # cross-reference a reader otherwise digs out of the intent.
            "targets": record.get("targets") or [],
        }

        state_path = naming.state_file(root, task_id)

        if state_path.exists():
            state = RunState.load(state_path)
            entry["state"] = str(state.state)
            entry["attempts"] = state.attempts

            if state.escalation is not None:
                entry["escalation"] = state.escalation.reason

                entry["escalated_at"] = next(
                    (
                        event["at"]
                        for event in reversed(state.history)
                        if event["to"] == "escalated"
                    ),
                    None,
                )

        found.append(entry)

    return found


# ....................... #


def _proposals(
    rfc_dir: Path, logs_by_task: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Divergence entries carrying a `proposal:` — data ready to become
    decision-table rows, with the entry that produced each (§4: amendments
    stop being copy-paste; append-only is preserved and nothing is retyped).

    A promoted row cites its task (`see .torve/tasks/T-nnnn`), so a proposal
    from a task the corpus already cites is marked `possibly_landed` — the
    log is append-only and cannot record acceptance, but the citation is
    evidence the author has been through that task's log."""

    cited = ""

    for path in rfc_dir.glob("*.md"):
        cited += path.read_text(encoding="utf-8")

    found: list[dict[str, Any]] = []

    for task_id, entries in sorted(logs_by_task.items()):
        for record in entries:
            proposal = record.get("proposal")

            if not proposal:
                continue

            found.append(
                {
                    "task": task_id,
                    "decision": record.get("decision"),
                    "grade": record.get("grade"),
                    "claim": record.get("claim"),
                    "proposal": proposal,
                    "evidence": record.get("evidence"),
                    # Any corpus mention of the task id is evidence the author
                    # has been through its log — the weaker claim "possibly
                    # landed", never "accepted". Logs promoted before the
                    # provenance convention carry no citation and stay visible;
                    # surfacing them is the feature, not a defect.
                    "possibly_landed": task_id in cited,
                }
            )

    return found


# ....................... #


def _contract_texts(root: Path) -> dict[str, str]:
    """Raw contract text per task id — the citation corpus `_findings`
    checks `possibly_addressed` against. Each review's own contract is
    excluded at the call site; its `id` would cite itself and mark every
    finding addressed."""

    texts: dict[str, str] = {}
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return texts

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw = contract.read_text(encoding="utf-8")

        except OSError:
            continue

        record = _load_yaml_dict(contract)
        task_id = (
            str(record["id"]) if record is not None and record.get("id") else contract.parent.name
        )
        texts[task_id] = raw

    return texts


# ....................... #


def _findings(root: Path) -> list[dict[str, Any]]:
    """The findings ledger (S-0005/D-15, S-0005/A-1): every kept non-blocking finding
    from a landed target's review, read from the review records telemetry
    already carries — review id, severity and claim, with
    `possibly_addressed` marking. The weak-citation discipline is S-0007/D-24's
    `possibly_landed` applied to findings: a contract whose text cites the
    review's task id is evidence the author has been through that review —
    evidence, never proof. The engine mints nothing from a finding; the
    operator triages this ledger in batch."""

    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if not telemetry.is_file():
        return []

    landed = shipped_ids(root)
    contract_texts = _contract_texts(root)
    found: list[dict[str, Any]] = []

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)

        if row.get("kind") != "review":
            continue

        review_id = str(row.get("task_id") or "")
        target = str(row.get("target") or "")
        findings = row.get("findings")

        if not review_id or not target or target not in landed or not isinstance(findings, list):
            continue

        # Any contract text citing the review id is evidence a follow-up
        # exists — the review's own contract excluded, whose id would
        # cite itself and mark every finding addressed.
        cited = "".join(text for task_id, text in contract_texts.items() if task_id != review_id)

        for finding in cast("list[object]", findings):
            if not isinstance(finding, dict):
                continue

            item = cast("dict[str, Any]", finding)

            # S-0005/D-15 excluded blockers because "a blocker escalates its
            # target and never lands beside it". True on the task-gated
            # path, where a blocker is either revised in-run or escalates;
            # false on the pull-request path, which reports and never
            # touches task state, so a blocker there escalates nothing and
            # its target lands like any other. The trigger is what says
            # which lifecycle applied — an absent one is the task-gated
            # default, so a record written before the marker existed stays
            # out (S-0005/A-7, corrected by S-0005/A-9).
            if item.get("severity") == "blocker" and row.get("trigger") != "pull_request":
                continue

            found.append(
                {
                    "review": review_id,
                    "target": target,
                    "severity": str(item.get("severity") or ""),
                    "claim": str(item.get("claim") or ""),
                    "evidence": str(item.get("evidence") or ""),
                    "possibly_addressed": review_id in cited,
                }
            )

    return found


# ....................... #


# How many of a gate's most recent runs the recency counters cover. Small
# enough that a gate calibrated this week reads differently from its own
# lifetime, large enough not to swing on one attempt (S-0004/A-4).
RECENT_RUNS = 20


def _gate_health(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-gate counters from the attempt rows (§4: what gate to write comes
    from data rather than recollection).

    Lifetime *and* the last `RECENT_RUNS`, because the lifetime figure alone
    hides the thing worth seeing. S-0004's shadow-run reading found
    `coverage-delta` at 59% failures over its whole life and 100%, 76%, 0%
    over the three days that life consists of: a gate being calibrated, and
    an aggregate that reads as a broken gate (S-0004/A-4). A rate that is moving
    is a different fact from a rate that is high.
    """

    stats: dict[str, dict[str, Any]] = {}
    recent: dict[str, list[str]] = {}

    for record in rows:
        results: Any = record.get("results")

        if not isinstance(results, list):
            continue

        for result in cast("list[object]", results):
            if not isinstance(result, dict):
                continue

            row = cast("dict[str, Any]", result)
            name = str(row.get("name", "?"))

            gate = stats.setdefault(
                name,
                {
                    "runs": 0,
                    "failures": 0,
                    "flaky": 0,
                    "bypassed": 0,
                    "total_duration_s": 0.0,
                    "max_duration_s": 0.0,
                },
            )

            gate["runs"] += 1
            outcome = str(row.get("outcome", ""))
            recent.setdefault(name, []).append(outcome)

            if outcome in ("fail", "error"):
                gate["failures"] += 1
            elif outcome == "flaky":
                gate["flaky"] += 1
            elif outcome == "bypassed":
                gate["bypassed"] += 1

            duration = float(row.get("duration_s", 0.0) or 0.0)
            gate["total_duration_s"] += duration
            gate["max_duration_s"] = max(gate["max_duration_s"], duration)

    for name, gate in stats.items():
        runs = gate["runs"] or 1
        gate["mean_duration_s"] = round(gate["total_duration_s"] / runs, 2)
        gate["total_duration_s"] = round(gate["total_duration_s"], 2)

        # The rows arrive oldest first, so the tail is the recent window.
        window = recent[name][-RECENT_RUNS:]
        gate["recent_runs"] = len(window)
        gate["recent_failures"] = sum(1 for one in window if one in ("fail", "error"))

    return stats


# ....................... #


def _harness_label(agent: dict[str, Any]) -> str | None:
    """Which harness did the work: the image reference verbatim, as
    configured and recorded at dispatch — no name is derived from it (a
    tag is operator-supplied; the digest beside it is the identity).
    Records from before the field existed fall back to the adapter kind."""

    image = agent.get("image")

    if isinstance(image, str) and image:
        return image

    adapter = agent.get("adapter")
    return str(adapter) if adapter else None


def _costs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cost and iterations by task against config_hash (§4) — every real
    agent attempt and every shadow summary. An attempt whose harness reported
    no usage still appears, costless: an uncontrolled regime is a fact worth
    seeing (S-0004/D-6), and a hidden run reads as a run that never happened.
    Fake-agent attempts stay out — simulation is not spend."""

    found: list[dict[str, Any]] = []

    for row in rows:
        if row.get("kind") == "shadow":
            found.append(
                {
                    "kind": "shadow",
                    "at": row.get("at"),
                    "task": row.get("task_id"),
                    "config_hash": row.get("config_hash"),
                    "cost_usd": row.get("cost_usd_total"),
                    "attempts": row.get("attempts"),
                    "state": row.get("state"),
                    # The eval loop's arm annotation, when this replay was
                    # one side of a paired measurement (S-0027/D-7).
                    **(
                        {"arm": cast("dict[str, Any]", row["eval"]).get("arm")}
                        if isinstance(row.get("eval"), dict)
                        else {}
                    ),
                }
            )

            continue

        agent: Any = row.get("agent")

        if isinstance(agent, dict) and cast("dict[str, Any]", agent).get("adapter") != "fake":
            block = cast("dict[str, Any]", agent)
            broker: Any = block.get("broker")
            # The attempt's own clock when the record has one; older records
            # fall back to the broker's run clock, which reads cumulative on
            # retries — flagged as an estimate so no reader trusts it flat.
            wall_time_s = block.get("wall_time_s")
            wall_estimated = wall_time_s is None

            if wall_time_s is None and isinstance(broker, dict):
                wall_time_s = cast("dict[str, Any]", broker).get("wall_time_s")

            found.append(
                {
                    "kind": "attempt",
                    "at": row.get("at"),
                    "task": row.get("task_id"),
                    "config_hash": row.get("config_hash"),
                    "cost_usd": block.get("cost_usd"),
                    "adapter": block.get("adapter"),
                    # Which seat ran and whether it was a replay: the two
                    # facts a reader needs to tell an execution attempt from
                    # a review run or a shadow arm without a join.
                    "tier": block.get("tier"),
                    **({"shadow": True} if block.get("shadow") else {}),
                    # Which harness did the work: identity is the image
                    # (S-0017/D-4) — a torve-agent:<name> tag labels by name;
                    # records from before the tag was stamped fall back to
                    # the adapter kind.
                    "harness": _harness_label(block),
                    "model": block.get("model"),
                    "provider": block.get("provider"),
                    "model_version": block.get("model_version"),
                    # Wall clock — time is spend too, and absent stays
                    # absent like the token shape.
                    **(
                        {
                            "wall_time_s": wall_time_s,
                            **({"wall_est": True} if wall_estimated else {}),
                        }
                        if wall_time_s is not None
                        else {}
                    ),
                    # Token shape (S-0004/D-6 self-reported regime): absent keys
                    # stay absent — a harness that reported nothing must not
                    # read as zero.
                    **{
                        key: block[key]
                        for key in (
                            "input_tokens",
                            "cache_read_tokens",
                            "cache_creation_tokens",
                            "output_tokens",
                        )
                        if key in block
                    },
                }
            )

    # Newest first: the reader's question is "what just ran", and the
    # stream on disk is append-ordered.
    found.sort(key=lambda r: str(r.get("at") or ""), reverse=True)

    return found


# ....................... #

# Kinds a harness population does not count as "a run under this tier":
# shadow replays are the measurement machinery comparing two regimes, not
# spend under either one, skill evals are S-0009's own population, and
# engine events carry no agent block at all (S-0027 S-0027/D-5).
_HARNESS_EXCLUDED_KINDS = {"shadow", "skill-eval", "engine"}


def _task_tier_name(record: dict[str, Any]) -> str:
    """The contract's own declared tier, dotted (S-0027/D-3) — read straight off
    the committed YAML, no `Task` validation needed for a population join."""

    tier = str(record.get("tier") or "")
    variant = record.get("tier_variant")

    return f"{tier}.{variant}" if variant else tier


def harness_populations(
    root: Path, config: RunnerConfig, rows: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """S-0027 S-0027/D-5's fact-feed widening: per-tier runs, cost (S-0021/D-5's
    broker-measured-preferred, self-reported-labelled split), escalations by
    reason, unparseable-review counts, and the most recently recorded image
    digest — every *configured* tier present with its denominator even at
    zero, so a variant nothing uses is visible (S-0027/risks's variant-sprawl
    mitigation). All from existing records: attempt rows for runs, cost,
    digest and unparseable reviews; contracts and run state for escalations.

    `rows` is where those attempt rows come from, and a caller that has them
    already — from the stream or rendered from the record — passes them
    rather than making this read the stream again."""

    buckets: dict[str, dict[str, Any]] = {
        name: {
            "tier": name,
            "attempts": 0,
            "cost_usd_broker": 0.0,
            "cost_usd_broker_n": 0,
            "cost_usd_self_reported": 0.0,
            "cost_usd_self_reported_n": 0,
            "escalations_by_reason": {},
            "unparseable_reviews": 0,
            "current_digest": None,
        }
        for name in sorted(config.tiers)
    }

    for row in rows if rows is not None else stream_rows(root):
        if str(row.get("kind", "")) in _HARNESS_EXCLUDED_KINDS:
            continue

        agent = row.get("agent")

        if not isinstance(agent, dict) or cast("dict[str, Any]", agent).get("adapter") == "fake":
            continue

        block = cast("dict[str, Any]", agent)
        bucket = buckets.get(str(block.get("tier") or ""))

        if bucket is None:
            continue

        bucket["attempts"] += 1
        broker = block.get("broker")

        if isinstance(broker, dict) and isinstance(
            cast("dict[str, Any]", broker).get("cost_usd"), int | float
        ):
            bucket["cost_usd_broker"] += float(cast("dict[str, Any]", broker)["cost_usd"])
            bucket["cost_usd_broker_n"] += 1
        elif isinstance(block.get("cost_usd"), int | float):
            bucket["cost_usd_self_reported"] += float(block["cost_usd"])
            bucket["cost_usd_self_reported_n"] += 1

        digest = block.get("image_digest")

        if digest:
            bucket["current_digest"] = digest

        if row.get("kind") == "review" and row.get("unparseable"):
            bucket["unparseable_reviews"] += 1

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if tasks_dir.is_dir():
        for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
            contract_record = _load_yaml_dict(contract)

            if contract_record is None:
                continue

            bucket = buckets.get(_task_tier_name(contract_record))

            if bucket is None:
                continue

            task_id = str(contract_record.get("id", contract.parent.name))
            state_path = naming.state_file(root, task_id)

            if not state_path.exists():
                continue

            state = RunState.load(state_path)

            if state.state is TaskState.ESCALATED and state.escalation:
                reason = str(state.escalation.reason)
                bucket["escalations_by_reason"][reason] = (
                    bucket["escalations_by_reason"].get(reason, 0) + 1
                )

    return [buckets[name] for name in sorted(buckets)]


# ....................... #

# What enters the calibration profile as a conviction: a red result from a
# blocking gate. `error` is kept out the way the outcome model keeps it out
# of "red" — a broken fence escalates as gate-infrastructure failure, it does
# not convict the work; shadow and quarantined gates run and report but
# convict nobody. Records from streams whose gate rows carry no `state` key
# at all read as the blocking default rather than vanish from measurement.
_CONVICTION_OUTCOMES = frozenset({"fail"})
_CONVICTION_STATES = frozenset({"blocking", ""})


def _gate_axes_and_stream(root: Path) -> tuple[dict[str, str], str]:
    """The gate-name to axis map the conviction profile groups on, and the
    telemetry stream's configured location — both resolved through the
    repository's gate manifest. No manifest: no labels, every gate reads on
    the unlabeled default (`functional`), and the stream sits at the shipped
    default path (same resolution as `specquality.telemetry_file`)."""

    manifest_path = layout.gates_file(root)
    manifest = load_manifest(manifest_path) if manifest_path.is_file() else Manifest(gates=[])

    return (
        {gate.name: gate.axis or UNLABELED_AXIS for gate in manifest.resolved_gates()},
        manifest.telemetry,
    )


def _character_calibration(
    root: Path, tasks: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Character calibration (S-0034/D-8): one row per task carrying a declaration
    or a conviction — declared character, realized conviction profile grouped
    by gate axis, attempts and token shape. The costs section already exposes
    the per-attempt gate results and token shape and the gate-health section
    already counts failures per gate name; this section is the join, which is
    the shape that makes a lying declaration visible.

    Measurement, never enforcement. The declared value is copied from the
    committed contract as the planner wrote it — vocabulary validation lives
    where character is declared, not here — and absence renders as
    `undeclared` and is never inferred (S-0034/D-1, S-0034/D-2: compliance is the
    axis nobody may declare, so it appears here only as a measured count).
    A wrong declaration is corrected in the document by its author, the way
    sizing estimates already earn observations."""

    axis_by_gate, _ = _gate_axes_and_stream(root)

    declared: dict[str, str] = {}
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if tasks_dir.is_dir():
        for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
            contract_record = _load_yaml_dict(contract)

            if contract_record is None:
                continue

            character = contract_record.get("character")

            if character:
                declared[str(contract_record.get("id", contract.parent.name))] = str(character)

    convictions: dict[str, dict[str, int]] = {}
    tokens: dict[str, dict[str, int]] = {}

    for row in rows:
        task_id = str(row.get("task_id") or "")

        if not task_id or str(row.get("kind", "")) in _HARNESS_EXCLUDED_KINDS:
            continue

        agent: Any = row.get("agent")

        if isinstance(agent, dict) and cast("dict[str, Any]", agent).get("adapter") == "fake":
            continue  # simulation is neither spend nor conviction (S-0004/D-6)

        results: Any = row.get("results")

        if isinstance(results, list):
            for result in cast("list[object]", results):
                if not isinstance(result, dict):
                    continue

                gate = cast("dict[str, Any]", result)

                if (
                    str(gate.get("outcome", "")) in _CONVICTION_OUTCOMES
                    and str(gate.get("state", "")) in _CONVICTION_STATES
                ):
                    axis = axis_by_gate.get(str(gate.get("name", "")), UNLABELED_AXIS)
                    profile = convictions.setdefault(task_id, {})
                    profile[axis] = profile.get(axis, 0) + 1

        if isinstance(agent, dict):
            block = cast("dict[str, Any]", agent)

            for key in TOKEN_FIELDS:
                value = block.get(key)

                if isinstance(value, int):
                    shape = tokens.setdefault(task_id, {})
                    shape[key] = shape.get(key, 0) + value

    attempts = {str(task["id"]): task.get("attempts") for task in tasks}
    found: list[dict[str, Any]] = []

    for task_id in sorted(set(declared) | set(convictions)):
        profile = convictions.get(task_id, {})
        shape = tokens.get(task_id, {})

        found.append(
            {
                "task": task_id,
                "character": declared.get(task_id, "undeclared"),
                # Vocabulary-ordered so the join reads the same in every
                # renderer and in serve's verbatim re-exposure.
                "convictions": {axis: profile[axis] for axis in GATE_AXES if axis in profile},
                "attempts": attempts.get(task_id),
                "tokens": {key: shape[key] for key in TOKEN_FIELDS if key in shape},
            }
        )

    return found


# ....................... #


def conviction_profile_text(convictions: dict[str, int]) -> str:
    """One prose cell for a profile — `functional 2, compliance 5`; the empty
    profile reads `none`, because a clean row is a fact, not an absence."""

    return ", ".join(f"{axis} {count}" for axis, count in convictions.items()) or "none"


def token_shape_text(tokens: dict[str, int]) -> str:
    """One prose cell for a token shape — `input 1200, cache-read 9000,
    output 300`; unreported keys stay out and an all-absent shape says so."""

    readable = {
        key.removesuffix("_tokens").replace("_", "-"): count for key, count in tokens.items()
    }

    return ", ".join(f"{label} {count}" for label, count in readable.items()) or "unreported"


# ....................... #


def feedback_records(root: Path) -> dict[str, dict[str, Any]]:
    """The latest `torve feedback` record per task id — the stream is
    append-only and keyed by task id, latest wins at analysis time
    (S-0022/current-state). Public: `specquality.operator_attention` reads this
    corpus-wide, the same lazy-import-to-avoid-a-cycle shape as
    `specquality._landed_task_ids` already uses for `shipped_ids`."""

    found: dict[str, dict[str, Any]] = {}
    path = layout.feedback_file(root)

    if not path.is_file():
        return found

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)
        task_id = row.get("task_id")

        if task_id:
            found[str(task_id)] = row  # later lines overwrite earlier ones

    return found


# ....................... #


def _document_signals(
    root: Path, tasks: list[dict[str, Any]], logs_by_task: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """S-0022/document-level-report, the document-level half of the specification-quality
    report: tasks minted, attempts to green (median, over tasks that landed
    — a task that never went green has none to count), escalations by
    reason with the two document-indicting reasons always present, spec-drift
    findings and their count (`class: drift` log entries — the same field
    the `decisions-reported` gate checks its declared `drift_count` against),
    human_minutes and rework rate from `torve feedback`. Tasks without a
    `spec` have no document to indict and are excluded (S-0022/D-9's reading, one
    level up from the decision join).

    Reuses `specquality.read_tasks` for the log join rather than parsing
    `log.yaml` a second way — one parser, one place the two reports could
    disagree, same reasoning specquality gives for reusing the gate's own
    `parse_log`."""

    feedback = feedback_records(root)
    by_document: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["spec"]:
            by_document.setdefault(str(task["spec"]), []).append(task)

    signals: list[dict[str, Any]] = []

    for document, entries in sorted(by_document.items()):
        attempts = [int(t["attempts"]) for t in entries if t["state"] == str(TaskState.READY)]
        escalations = dict.fromkeys(DOCUMENT_INDICTING_REASONS, 0)

        for task in entries:
            if task["escalation"]:
                reason = str(task["escalation"])
                escalations[reason] = escalations.get(reason, 0) + 1

        drift_findings = [
            {"task": task["id"], "claim": str(log_entry.get("claim") or "")}
            for task in entries
            for log_entry in logs_by_task.get(task["id"], [])
            if log_entry.get("class") == "drift"
        ]

        minutes: list[int] = []
        reworked = 0
        with_feedback = 0

        for task in entries:
            row = feedback.get(str(task["id"]))

            if row is None:
                continue

            with_feedback += 1
            human_minutes = row.get("human_minutes")

            if isinstance(human_minutes, int):
                minutes.append(human_minutes)

            if row.get("rework_after_review"):
                reworked += 1

        signals.append(
            {
                "spec": document,
                "minted": len(entries),
                "attempts_to_green_median": statistics.median(attempts) if attempts else None,
                "attempts_to_green_n": len(attempts),
                "escalations_by_reason": dict(sorted(escalations.items())),
                "drift_count": len(drift_findings),
                "spec_drift_findings": drift_findings[:SPEC_DRIFT_FINDINGS_LIMIT],
                "human_minutes_median": statistics.median(minutes) if minutes else None,
                "human_minutes_n": len(minutes),
                "rework_rate": reworked / with_feedback if with_feedback else None,
                "rework_n": with_feedback,
            }
        )

    return signals


# ....................... #


def _phase_progress(states: list[str]) -> str:
    """planned | in_flight | blocked | shipped, derived per phase (S-0007/D-15) —
    phase-level because that is the granularity at which decisions get made."""

    if states and all(state in ("ready", "shipped") for state in states):
        return "shipped"

    if any(state == "escalated" for state in states):
        return "blocked"

    if any(state in {str(s) for s in ACTIVE} for state in states):
        return "in_flight"

    return "planned"


# ....................... #


def _programme(root: Path, rfc_dir: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The RFC graph rendered for humans (S-0007/D-11): what is accepted, what
    shipped, what became plannable, and where assertion and derivation
    disagree (S-0007/D-15 — the disagreement is the informative part)."""

    by_document: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["spec"]:
            by_document.setdefault(str(task["spec"]), []).append(task)

    view: list[dict[str, Any]] = []

    try:
        corpus = spec.load_corpus(rfc_dir)
    except spec.SpecError:
        return view

    documents = {doc.id: doc for doc in corpus.documents if not doc.archived}
    statuses = {number: doc.status for number, doc in documents.items()}

    for number, doc in sorted(documents.items()):
        path = Path(doc.path)
        fm = {
            "title": doc.title,
            "kind": doc.kind,
            "implementation": doc.implementation,
            "depends_on": list(doc.depends_on),
        }
        phasing = doc.phasing
        document = str(path.resolve().relative_to(root.resolve()))
        minted = by_document.get(document_of(document), [])
        phases: dict[int, list[str]] = {}

        for task in minted:
            # A drafting task (intake, decompose) or a runner-minted review
            # cites the document that asked for it, but neither is a phase of
            # the document's implementation — one consumed contract otherwise
            # holds a completed RFC at "P0: planned" forever.
            if task.get("role") in ("draft", "review"):
                continue

            phases.setdefault(int(task["phase"]), []).append(str(task["state"]))

        progress = {phase: _phase_progress(states) for phase, states in sorted(phases.items())}

        status = statuses[number]
        implementation = str(fm.get("implementation") or "none")

        unsatisfied = [
            dep for dep in _list_field(fm, "depends_on") if statuses.get(dep, "") != "accepted"
        ]

        declared_phases: set[int] = {entry.phase for entry in phasing or []}
        unminted = sorted(declared_phases - set(phases))
        plannable = status == "accepted" and not unsatisfied and bool(unminted)

        disagreement: str | None = None

        if (
            implementation == "complete"
            and progress
            and any(p != "shipped" for p in progress.values())
        ):
            disagreement = "asserted complete, but a phase is not shipped"
        elif implementation == "none" and any(p == "shipped" for p in progress.values()):
            disagreement = "a phase shipped, but the assertion still says none"
        elif (
            implementation == "partial"
            and progress
            and not unminted
            and all(p == "shipped" for p in progress.values())
        ):
            # The assertion nothing could contradict (S-0007/A-4). `partial` was
            # the one value with no failing shape: a document could ship
            # every phase it declared and keep asserting there was more to
            # do, for months, and this projection agreed with it. It says
            # nothing about *scope* — a document may describe more than it
            # ever minted — which is why this reads as a disagreement for a
            # person to settle rather than a correction.
            disagreement = "asserted partial, but every declared phase shipped"

        view.append(
            {
                "spec": number,
                "title": str(fm.get("title", "")),
                "status": status,
                "kind": fm.get("kind") or "design",
                "implementation": implementation,
                "unsatisfied_depends_on": unsatisfied,
                "declared_phases": sorted(declared_phases),
                "minted_phases": {str(k): len(v) for k, v in sorted(phases.items())},
                "progress": {str(k): v for k, v in progress.items()},
                "plannable": plannable,
                "disagreement": disagreement,
            }
        )

    return view


# ....................... #


def _list_field(fm: dict[str, Any], name: str) -> list[str]:
    value = fm.get(name)

    if not isinstance(value, list):
        return []

    return [str(item) for item in cast("list[object]", value)]


# ....................... #

# Attention routing (S-0006/human-attention-is-the-scarce-resource, S-0006/D-4): blockers and locked conflicts
# interrupt, infrastructure pages the harness owner, the rest batches into
# review windows. The projection carries the class; policy stays with people.
ROUTE_NOTIFY = {"blocker_finding", "locked_conflict"}
ROUTE_HARNESS = {"gate_infrastructure_failure"}


# ....................... #


def escalation_route(reason: str) -> str:
    if reason in ROUTE_NOTIFY:
        return "notify"

    if reason in ROUTE_HARNESS:
        return "harness owner"

    return "batch"


# ....................... #


def _age_seconds(at: object) -> float | None:
    if not isinstance(at, str):
        return None

    try:
        parsed = parse(at)

    except ValueError:
        return None

    return max(0.0, (datetime.now(UTC) - parsed).total_seconds())


# ....................... #


def _decompositions(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Children grouped under their parent (S-0026 S-0026/D-5, S-0026/D-6): a
    projection convenience over the `parent` field — dispatch, the lane and
    the store never read it, so this grouping exists nowhere else."""

    groups: dict[str, list[str]] = {}

    for task in tasks:
        parent = task.get("parent")

        if parent:
            groups.setdefault(str(parent), []).append(str(task["id"]))

    return dict(sorted(groups.items()))


# ....................... #


def _entry_state(view: TaskView, task: Task) -> str:
    """One board row in the task vocabulary this projection reports in.

    The two vocabularies overlap everywhere a run happened and differ only
    at the start: the record has one word for a task nothing has happened
    to yet, and this projection has three. A queued row with no attempt
    behind it is `consumed` for the roles a run mints mid-flight and
    concludes with (S-0005/D-2, S-0020/D-2), and `unstarted` for the roles a worker
    takes. `shipped` has no record equivalent and needs none — it means
    landed, and a landed task on the board reads `ready`, which every
    consumer of this key already accepts alongside it.
    """

    if view.state is TaskState.QUEUED and not view.attempts:
        return "consumed" if task.role not in DISPATCHABLE_ROLES else "unstarted"

    return str(view.state)


# ....................... #


def divergences_from_events(events: Sequence[EventRecord]) -> dict[str, list[dict[str, Any]]]:
    """The log entries a partition's divergence records render to, by task.

    The rendering is the one the engine already writes a worktree's
    `log.yaml` with (S-0044/A-3), reused rather than restated: the log file is a
    projection of these events, so a reader that folds the events directly
    and one that parses the file it wrote must not be able to disagree.
    """

    from torve.application.divergence import entry_of

    found: dict[str, list[dict[str, Any]]] = {}

    for event in events:
        if event.kind is EventKind.DIVERGENCE_RECORDED:
            found.setdefault(event.subject_id, []).append(entry_of(event))

    return found


# ....................... #


def _stream_divergences(root: Path) -> dict[str, list[dict[str, Any]]]:
    """The same map from the worktree's own log files. One parser for both
    halves — `read_tasks` reuses the `decisions-reported` gate's parser, so
    the report and the gate cannot disagree about what an entry is."""

    return {task.id: task.log_entries for task in read_tasks(root)}


# ....................... #


def tasks_from_events(events: Sequence[EventRecord]) -> list[dict[str, Any]]:
    """The task entries a partition's record renders to (S-0050/order-and-why-context-is-last).

    Every contract the record holds, whatever its role — which is what
    S-0049/A-1 made true, and the reason this is a fold rather than a subset. A
    row whose contract the record does not carry is skipped: there is
    nothing to report about it beyond its state, and an entry with no
    document, phase or role is one every downstream block would have to
    special-case.
    """

    board = project(events)
    escalated_at = {
        event.subject_id: stamp(event.created_at)
        for event in events
        if event.kind is EventKind.ESCALATION_RAISED
    }

    return [
        {
            "id": task_id,
            "spec": view.contract.spec,
            "phase": view.contract.phase,
            "role": view.contract.role,
            "state": _entry_state(view, view.contract),
            "attempts": view.attempts,
            "escalation": view.escalation,
            "escalated_at": escalated_at.get(task_id) if view.escalation else None,
            "parent": view.contract.parent,
            "targets": list(view.contract.targets),
        }
        for task_id, view in sorted(board.tasks.items())
        if view.contract is not None
    ]


# ....................... #


def context_report(
    root: Path, rfc_dir: Path, *, recorded: Sequence[EventRecord] | None = None
) -> dict[str, Any]:
    """The planning projection (S-0007/torve-context).

    The task block reads the record when a partition was named and the
    record holds contracts; every other block still reads files, because
    the corpus is a file and the telemetry stream is this host's (S-0050
    phase 3). A record carrying no contract falls back wholesale, never
    key by key — a report assembled from two populations would compare
    counts that were never measured over the same tasks.
    """

    tasks = (tasks_from_events(recorded) if recorded else []) or _tasks(root)
    # One parse for every block that counts attempts, rendered from the
    # record when a partition was named (S-0044/A-4, S-0050/A-3). The `or` is the same
    # fallback the task block takes and for the same reason: a record with
    # no attempt in it is a record that was not watching, not a repository
    # where nothing ran.
    rows = (rows_from_events(recorded) if recorded else []) or stream_rows(root)
    logs = (divergences_from_events(recorded) if recorded else {}) or _stream_divergences(root)
    escalations: dict[str, list[dict[str, Any]]] = {}

    for task in tasks:
        if task["escalation"]:
            # The queue's age is the primary signal (S-0006/D-8): a queue nobody
            # triages looks identical to success from inside the runner.
            escalations.setdefault(str(task["escalation"]), []).append(
                {
                    "task": task["id"],
                    "at": task["escalated_at"],
                    "spec": task["spec"],
                    "age_s": _age_seconds(task["escalated_at"]),
                    "route": escalation_route(str(task["escalation"])),
                }
            )

    return {
        "schema_version": 1,
        "at": stamp(),
        # Which carrier answered, per block, and how much it had to answer
        # with (S-0050/A-3). A record that was not watching a run holds nothing
        # about it, and every count below is then correct about the record
        # and wrong about the repository — a reader has no way to tell those
        # apart from the numbers, so the report says which it is.
        "sources": {
            "tasks": "record" if recorded and tasks_from_events(recorded) else "files",
            "attempts": "record" if recorded and rows_from_events(recorded) else "files",
            "divergences": "record" if recorded and divergences_from_events(recorded) else "files",
            # No event kind carries operator feedback — human minutes and
            # rework are a `torve feedback` file and nothing else — so the
            # readings that join them stay on files whatever was selected.
            "feedback": "files",
            "attempt_rows": len(rows),
            # The corpus is files, and the findings ledger stays on the
            # stream for its claims whatever else was selected.
            "corpus": "files",
            "findings": "files",
        },
        "tasks": tasks,
        "decompositions": _decompositions(tasks),
        "escalations": escalations,
        "proposals": _proposals(rfc_dir, logs),
        # The findings ledger stays on the stream whatever the caller
        # selected: it reports each finding's severity *and its claim*, and
        # the record carries a claim only for a blocker (`blocker.raised`).
        # Rendering it from the record would silently drop the text an
        # operator triages by, which is a vocabulary change rather than a
        # source swap.
        "findings": _findings(root),
        "gates": _gate_health(rows),
        "costs": _costs(rows),
        "character": _character_calibration(root, tasks, rows),
        "programme": _programme(root, rfc_dir, tasks),
        "spec_quality": {
            "caveat": QUASI_EXPERIMENT_CAVEAT,
            "documents": _document_signals(root, tasks, logs),
            # S-0022/D-12, S-0022/A-5: the corpus-wide reading, independent of any
            # one document's population.
            "operator_attention": operator_attention(root),
        },
    }


# ....................... #


# What a run record carries that the board does not (S-0050/parity-is-the-acceptance). These
# are host facts — where the run put its worktree, which sandbox it held,
# which store run it executed under — and the record holds facts about the
# work rather than about the machine that did it. They are rendered empty
# rather than omitted: one envelope, two sources, and a key that appears
# only under one source is a key no reader can rely on.
_HOST_ONLY_KEYS = ("run_id", "sandbox_id", "durable_run_id", "worktree", "conflict_base")


def run_from_view(view: TaskView) -> dict[str, Any]:
    """One board row rendered as the run record `status` reports.

    The two vocabularies are the same `TaskState` reached by different
    paths, so nothing is translated here; what differs is depth. The record
    carries the escalation's reason and not its detail, and carries no
    history, because a board is a fold and the transitions it folded are
    the log itself rather than a list it keeps.
    """

    heartbeat = view.last_event_at or view.claimed_at

    return {
        "task_id": view.task_id,
        "schema_version": 1,
        "state": str(view.state),
        "attempts": view.attempts,
        "heartbeat": stamp(heartbeat),
        "escalation": ({"reason": view.escalation, "detail": ""} if view.escalation else None),
        "history": [],
        "approvals": [],
        "landed_sha": view.landed_sha,
        "reviewed_by": None,
        **dict.fromkeys(_HOST_ONLY_KEYS, None),
    }


def runs_from_board(board: Board) -> list[dict[str, Any]]:
    """The run records a partition's board renders to (S-0050/parity-is-the-acceptance).

    A run exists where the record shows an attempt, or where the engine is
    holding the task right now. Everything else the board carries is on the
    board and was never a run here: a queued contract waiting for a worker,
    and — the population that dwarfs the rest on a first pass — a task
    minted straight to `ready` from the repository's own landing trailer
    (S-0049/D-1), which is history the board imported rather than work this
    record watched happen.
    """

    return [
        run_from_view(view)
        for _, view in sorted(board.tasks.items())
        if view.attempts or view.state in IN_FLIGHT or view.state is TaskState.ESCALATED
    ]


def status_report(root: Path, *, board: Board | None = None) -> dict[str, Any]:
    """The `torve status` projection (S-0032/endpoints): live run states, one
    record per task, in the same envelope the CLI's --format json emits.
    One reader, two renderers (S-0032/D-1): the CLI and the serve endpoint both
    consume this, so the browser and the terminal can never disagree.

    With a board, the records come from the log the board was folded from
    (S-0050 S-0050/D-2); without one, from this host's state files. A board
    that turns out to hold no run falls back to the files, never the other
    way round — the direction that is safe when only one of the two
    carriers was ever written (S-0044/A-5).
    """

    if board is not None:
        runs = runs_from_board(board)

        if runs:
            return {"schema_version": 1, "runs": runs}

    states = RunState.load_all(root.resolve() / naming.WORKTREE_DIR)
    return {"schema_version": 1, "runs": [s.to_record() for s in states]}


# ....................... #

# The attempt population: real agent rows only. Shadow replays measure a
# regime, reviews are the reviewer's spend under another task id, engine
# events carry no agent block, and a fake adapter is simulation, neither
# spend nor conviction (S-0004/D-6) — the same exclusions the harness
# populations and the costs section already draw.
# `intake` joins them for the same reason one step earlier (T-0273): a
# drafting run is how the contract came to exist, not an attempt at the
# work it describes. Counted as one it added a phantom attempt to every
# drafted task's `why`, and folded its cost into the same-regime median
# and p90 that `context` reports for every task sharing the config hash.
_ATTEMPT_EXCLUDED_KINDS = _HARNESS_EXCLUDED_KINDS | {"review", "intake"}


def stream_rows(root: Path) -> list[dict[str, Any]]:
    """The telemetry stream, parsed — from the location the writer appends
    to, which a repository may relocate by configuration. Unparseable and
    non-object lines are skipped as everywhere else in this module: the
    stream is append-only and a projection reader is not a repair shop."""

    from torve.application.specquality import telemetry_file

    found: list[dict[str, Any]] = []
    telemetry = telemetry_file(root)

    if not telemetry.is_file():
        return found

    for line in telemetry.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if isinstance(record, dict):
            found.append(cast("dict[str, Any]", record))

    return found


def _is_attempt_row(row: dict[str, Any]) -> bool:
    """One attempt of this task as the engine recorded it: an agent block
    from a real adapter, not one of the measured or narrating kinds."""

    if str(row.get("kind", "")) in _ATTEMPT_EXCLUDED_KINDS:
        return False

    agent = row.get("agent")

    return isinstance(agent, dict) and cast("dict[str, Any]", agent).get("adapter") != "fake"


def _row_agent(row: dict[str, Any]) -> dict[str, Any]:
    agent: Any = row.get("agent")

    return cast("dict[str, Any]", agent) if isinstance(agent, dict) else {}


def _attempt_entry(rows: list[dict[str, Any]], attempt: int | None, root: Path) -> dict[str, Any]:
    """One attempt's envelope entry, folded from the rows carrying its stamp.
    S-0038/D-1 guarantees exactly one row per attempt, so the fold is defensive
    shape only: scalars take the later row's value, gate convictions
    accumulate. Absent stays absent — a harness that reported nothing reads
    unreported, never zero (S-0004/D-6)."""

    entry: dict[str, Any] = {"attempt": attempt, "at": rows[0].get("at")}

    if attempt is None:
        # Unstamped history (pre-0038) is rendered as what it is and never
        # retrofitted with a number the stream does not hold.
        entry["pre_verdict"] = True

    verdicts = [row.get("verdict") for row in rows if row.get("verdict")]

    if verdicts:
        entry["verdict"] = str(verdicts[-1])

    convictions: list[str] = []

    for row in rows:
        results = row.get("results")

        if isinstance(results, list):
            for result in cast("list[object]", results):
                if not isinstance(result, dict):
                    continue

                gate = cast("dict[str, Any]", result)

                if (
                    str(gate.get("outcome", "")) in _CONVICTION_OUTCOMES
                    and str(gate.get("state", "")) in _CONVICTION_STATES
                ):
                    name = str(gate.get("name", "?"))

                    if name not in convictions:
                        convictions.append(name)

    entry["convictions"] = convictions

    agent: dict[str, Any] = {}

    for row in rows:
        agent = {**agent, **_row_agent(row)}

    for key in ("tier", "model", "cost_usd"):
        if agent.get(key) is not None:
            entry[key] = agent[key]

    for key in TOKEN_FIELDS:
        if key in agent:
            entry[key] = agent[key]

    # Wall clock, estimated where the attempt's own span was never recorded
    # — the same fallback and the same honesty flag the costs section uses.
    wall_time_s = agent.get("wall_time_s")
    broker: Any = agent.get("broker")

    if wall_time_s is None and isinstance(broker, dict):
        wall_time_s = cast("dict[str, Any]", broker).get("wall_time_s")

        if wall_time_s is not None:
            entry["wall_est"] = True

    if wall_time_s is not None:
        entry["wall_time_s"] = wall_time_s

    if any(row.get("gates_run") is False for row in rows):
        entry["gates_run"] = False

    escalation = next((row["escalation"] for row in reversed(rows) if row.get("escalation")), None)

    if escalation is not None:
        entry["escalation"] = escalation

    # The trace is displayed, never opened (S-0040/D-2): the ref rides as
    # recorded and presence is a stat, not a read.
    trace_ref = agent.get("trace_ref")

    if isinstance(trace_ref, str) and trace_ref:
        entry["trace_ref"] = trace_ref
        entry["trace_present"] = (root / trace_ref).is_file()
    else:
        entry["trace_ref"] = None
        entry["trace_present"] = False

    return entry


def _group_attempts(rows: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    """Attempts grouped by the 0038 `attempt` stamp; unstamped rows each keep
    their own place in timestamp order (the fallback grouping, never a
    retrofitted number). The list runs chronologically — the timeline's own
    order."""

    by_stamp: dict[int, list[dict[str, Any]]] = {}
    unstamped: list[dict[str, Any]] = []

    for row in sorted(rows, key=lambda r: str(r.get("at") or "")):
        stamp = _row_agent(row).get("attempt")

        if isinstance(stamp, int) and not isinstance(stamp, bool):
            by_stamp.setdefault(stamp, []).append(row)
        else:
            unstamped.append(row)

    groups: list[tuple[int | None, list[dict[str, Any]]]] = [
        *((stamp, group) for stamp, group in by_stamp.items()),
        *((None, [row]) for row in unstamped),
    ]

    entries = [_attempt_entry(group, stamp, root) for stamp, group in groups]

    return sorted(entries, key=lambda entry: str(entry.get("at") or ""))


def _why_events(rows: list[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    """The engine's own words about this task — escalations, blocked and
    oversize dispatches, lane outcomes — chronologically. The record's
    payload passes through verbatim (the join keys excepted): the projection
    adds no interpretation (the envelope is data, never judgement)."""

    found: list[dict[str, Any]] = []

    for row in rows:
        if row.get("kind") != "engine" or row.get("task") != task_id:
            continue

        entry: dict[str, Any] = {"at": row.get("at"), "event": row.get("event")}
        entry.update(
            {
                key: value
                for key, value in row.items()
                if key not in {"schema_version", "kind", "at", "event"}
            }
        )
        found.append(entry)

    return sorted(found, key=lambda entry: str(entry.get("at") or ""))


def _why_reviews(rows: list[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    """Reviews conducted *of* this task, by their durable records: how many
    findings the review kept and how many of them were blockers. The review's
    contract is a different task; its id rides along so a reader can open
    the record."""

    found: list[dict[str, Any]] = []

    for row in rows:
        if row.get("kind") != "review" or row.get("target") != task_id:
            continue

        raw_findings: Any = row.get("findings")
        findings = cast("list[object]", raw_findings) if isinstance(raw_findings, list) else []
        kept = [cast("dict[str, Any]", f) for f in findings if isinstance(f, dict)]

        entry: dict[str, Any] = {
            "at": row.get("at"),
            "review": row.get("task_id"),
            "verdict_findings": len(kept),
            "blockers": sum(1 for f in kept if f.get("severity") == "blocker"),
        }

        if row.get("unparseable"):
            entry["unparseable"] = True

        found.append(entry)

    return sorted(found, key=lambda entry: str(entry.get("at") or ""))


def _why_totals(attempts: list[dict[str, Any]], human_minutes: int | None) -> dict[str, Any]:
    """Arithmetic, nothing else: sums over the attempts that reported, None
    where nothing did — an unreported total reads unreported."""

    def reported(key: str) -> list[float]:
        return [float(entry[key]) for entry in attempts if isinstance(entry.get(key), int | float)]

    def summed(key: str) -> float | int | None:
        values = reported(key)

        if not values:
            return None

        total = sum(values)
        return int(total) if key.endswith("_tokens") else round(total, 3)

    return {
        "attempts": len(attempts),
        "cost_usd": summed("cost_usd"),
        "input_tokens": summed("input_tokens"),
        "output_tokens": summed("output_tokens"),
        "wall_time_s": summed("wall_time_s"),
        "human_minutes": human_minutes,
    }


def _why_regime(task_rows: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The same-regime cost comparator. The anchor is the task's newest
    attempt row that recorded a config_hash — red-path rows record none, the
    gates never ran under a manifest for them — and the population is every
    real attempt row in the stream carrying that hash, this task's included.
    A comparator, never a verdict: the quasi-experiment caveat rides inside
    the envelope itself so no renderer can shed it."""

    anchor = next(
        (
            row["config_hash"]
            for row in sorted(task_rows, key=lambda r: str(r.get("at") or ""), reverse=True)
            if row.get("config_hash")
        ),
        None,
    )

    costs: list[float] = []

    if anchor:
        for row in all_rows:
            if not _is_attempt_row(row) or row.get("config_hash") != anchor:
                continue

            cost = _row_agent(row).get("cost_usd")

            if isinstance(cost, int | float):
                costs.append(float(cost))

    if costs:
        costs.sort()
        median: float | None = round(statistics.median(costs), 3)
        p90: float | None = round(costs[math.ceil(0.9 * len(costs)) - 1], 3)
        n: int | None = len(costs)
    else:
        median = None
        p90 = None
        n = len(costs) if anchor else None

    return {
        "config_hash": anchor,
        "attempt_cost_median_usd": median,
        "attempt_cost_p90_usd": p90,
        "attempt_cost_n": n,
        "caveat": QUASI_EXPERIMENT_CAVEAT,
    }


def _stream_state(attempts: list[dict[str, Any]], events: list[dict[str, Any]]) -> str | None:
    """Where the durable record leaves the task — not the engine's live
    state, which lives in run-state files this projection must never read.
    None is the honest answer for a history that ended red without
    escalating: the stream says nothing about where such a task sits now,
    and a guess dressed as a state would say more."""

    if not attempts:
        return "unstarted"

    last_at = str(attempts[-1].get("at") or "")

    if any(
        event.get("event") == "escalation" and str(event.get("at") or "") >= last_at
        for event in events
    ):
        return str(TaskState.ESCALATED)

    if attempts[-1].get("verdict") == "green":
        return str(TaskState.READY)

    return None


def _minted_contract(events: Sequence[EventRecord]) -> dict[str, Any] | None:
    """The contract the task was last minted with, as a plain mapping — the
    envelope wants `spec` and nothing else from it (S-0049 S-0049/D-1).

    None means the record does not hold this task, which is the one signal
    `why_report` falls back to the files on.
    """

    for event in reversed(events):
        if event.kind is not EventKind.TASK_MINTED:
            continue

        contract = event.payload.get("contract")

        if isinstance(contract, dict) and contract:
            return current_shape(cast("dict[str, Any]", contract))  # S-0059/D-3

    return None


# ....................... #


def rows_from_events(events: Iterable[EventRecord]) -> list[dict[str, Any]]:
    """The telemetry rows a task's events render to (S-0050/the-sources-swap-under-a-stable-envelope).

    S-0044/A-4 made the telemetry row a *rendering* of the event payload, so this
    is that rule read backwards: rather than re-implementing five joins
    against a second vocabulary, the record is rendered into the rows those
    joins already read. Parity then holds because the two sides are the same
    object rendered by the same function, and a difference is a defect in
    this rendering rather than a disagreement between two readings.

    Only the kinds the why envelope reads are rendered. An event this
    projection has nothing to say about produces no row, which is the same
    silence the stream keeps for a fact nobody wrote down.
    """

    rows: list[dict[str, Any]] = []

    for event in events:
        at = stamp(event.created_at)
        payload = event.payload

        if event.kind in (EventKind.GATES_EVALUATED, EventKind.ATTEMPT_FINISHED):
            rows.append(record_row(payload, task_id=event.subject_id, at=at))

        elif event.kind is EventKind.ESCALATION_RAISED:
            rows.append(
                {
                    "kind": "engine",
                    "at": at,
                    "event": "escalation",
                    "task": event.subject_id,
                    "reason": str(payload.get("reason") or ""),
                    "detail": str(payload.get("detail") or ""),
                }
            )

        elif event.kind is EventKind.REVIEW_RECORDED:
            # The findings count is what the record carries; the file row
            # carries the findings themselves and the join counts them. The
            # envelope wants the counts, so both arrive at the same place
            # from different depths.
            rows.append(
                {
                    "kind": "review",
                    "at": at,
                    "task_id": str(payload.get("review_id") or ""),
                    "target": event.subject_id,
                    "findings": [
                        {"severity": "blocker"} for _ in range(int(payload.get("blockers") or 0))
                    ]
                    + [
                        {"severity": "finding"}
                        for _ in range(
                            max(
                                0,
                                int(payload.get("findings") or 0)
                                - int(payload.get("blockers") or 0),
                            )
                        )
                    ],
                }
            )

    return rows


# ....................... #


def why_report(
    root: Path, task_id: str, *, recorded: Sequence[EventRecord] | None = None
) -> dict[str, Any]:
    """The per-task history envelope (the whole point of this projection):
    one task's attempts, the engine's events around them, the reviews of it,
    its totals and its regime comparator — joined from the durable streams
    on demand and stored nowhere. The CLI, the MCP tool and the serve
    endpoint all re-expose this one envelope verbatim, so the three surfaces
    can never disagree.

    The sources are the telemetry stream, the feedback stream and the task's
    contract head — never run-state files (overwritten per dispatch and
    swept at reap) and never trace content (the ref is displayed, not
    opened). An unknown task id is a `found: false` envelope, not a taskless
    history a typo could fake.

    `recorded` is one task's own events, when the caller has a log holding
    them (S-0050 S-0050/D-2): selection is the caller's, because only the call
    site knows whether a partition was named. A record that turns out not to
    hold the task falls back to the files — never the reverse, since an
    empty log must read as "ask the files" and a populated one must not be
    second-guessed by a stale file.
    """

    minted = _minted_contract(recorded) if recorded else None
    contract = minted if minted is not None else _load_yaml_dict(layout.task_file(root, task_id))

    if contract is None:
        return {"schema_version": 1, "task": task_id, "found": False}

    rows = rows_from_events(recorded) if minted is not None and recorded else stream_rows(root)
    task_rows = [row for row in rows if row.get("task_id") == task_id and _is_attempt_row(row)]
    attempts = _group_attempts(task_rows, root)
    events = _why_events(rows, task_id)
    feedback = feedback_records(root).get(task_id)
    human_minutes = (
        feedback.get("human_minutes")
        if isinstance(feedback, dict) and isinstance(feedback.get("human_minutes"), int)
        else None
    )

    return {
        "schema_version": 1,
        "task": task_id,
        "found": True,
        "spec": contract.get("spec"),
        "state": _stream_state(attempts, events),
        "attempts": attempts,
        "events": events,
        "reviews": _why_reviews(rows, task_id),
        "totals": _why_totals(attempts, human_minutes),
        "regime": _why_regime(task_rows, rows),
    }


# ....................... #


def render_markdown(report: dict[str, Any]) -> str:
    """The human-facing projection (S-0007/D-4: format decided by use — markdown
    for pasting into a planning session, JSON for machines, both from one
    report)."""

    lines: list[str] = [f"# torve context — {report['at']}", ""]
    sources = report.get("sources")

    if isinstance(sources, dict):
        # First, before any count: a planning session reading these numbers
        # has to know which carrier produced them (S-0050/A-3).
        read_from = (
            f"Read from — tasks: {sources['tasks']}, attempts: "
            f"{sources['attempts']} ({sources['attempt_rows']} row(s)), "
            "findings and corpus: files."
        )
        lines.extend([read_from, ""])

    lines.append("## Programme")
    lines.append("")

    for doc in report["programme"]:
        marks: list[str] = []

        if doc["plannable"]:
            marks.append("**plannable**")

        if doc["disagreement"]:
            marks.append(f"⚠ {doc['disagreement']}")

        if doc["unsatisfied_depends_on"]:
            marks.append(f"waits on {', '.join(doc['unsatisfied_depends_on'])}")

        progress = ", ".join(f"P{k}: {v}" for k, v in doc["progress"].items()) or "no tasks"

        lines.append(
            f"- **{doc['spec']}** {doc['title']} — {doc['status']}, "
            f"impl {doc['implementation']} · {progress}"
            + (" · " + " · ".join(marks) if marks else "")
        )

    lines.append("")

    lines.append("## Tasks by state")
    lines.append("")
    by_state: dict[str, list[str]] = {}

    for task in report["tasks"]:
        by_state.setdefault(str(task["state"]), []).append(str(task["id"]))

    for state, ids in sorted(by_state.items()):
        lines.append(f"- {state}: {', '.join(ids)}")

    lines.append("")

    if report["decompositions"]:
        lines.append("## Decompositions")
        lines.append("")

        for parent, children in report["decompositions"].items():
            lines.append(f"- {parent} (integration task) -> {', '.join(children)}")

        lines.append("")

    if report["escalations"]:
        lines.append("## Escalations by reason")
        lines.append("")

        for reason, items in sorted(report["escalations"].items()):
            names = ", ".join(str(item["task"]) for item in items)
            lines.append(f"- {reason} ({len(items)}): {names}")

        lines.append("")

    if report["proposals"]:
        fresh = [p for p in report["proposals"] if not p.get("possibly_landed")]
        landed = len(report["proposals"]) - len(fresh)
        lines.append("## Proposals awaiting the author")
        lines.append("")

        for item in fresh:
            lines.append(
                f"- `{item['decision']}` ({item['grade']}) from {item['task']}: "
                f"{str(item['proposal']).strip()}"
            )

        if landed:
            lines.append(
                f"- …plus {landed} proposal(s) from tasks the decision tables already "
                "cite — likely landed; the JSON report carries them all"
            )

        lines.append("")

    if report["findings"]:
        fresh = [f for f in report["findings"] if not f.get("possibly_addressed")]
        addressed = len(report["findings"]) - len(fresh)
        lines.append("## Findings awaiting the operator")
        lines.append("")

        for item in fresh:
            lines.append(f"- [{item['severity']}] {item['review']}: {str(item['claim']).strip()}")

        if addressed:
            lines.append(
                f"- …plus {addressed} finding(s) from reviews later contracts cite — "
                "possibly addressed; the JSON report carries them all"
            )

        lines.append("")

    if report["gates"]:
        lines.append("## Gate health")
        lines.append("")

        for name, gate in sorted(report["gates"].items()):
            lines.append(
                f"- {name}: {gate['runs']} run(s), {gate['failures']} failure(s), "
                f"{gate['flaky']} flaky, {gate['bypassed']} bypassed, "
                f"mean {gate['mean_duration_s']}s, max {gate['max_duration_s']}s"
            )

        lines.append("")

    if report["character"]:
        lines.append("## Character calibration")
        lines.append("")

        for row in report["character"]:
            attempts = (
                f"{row['attempts']} attempt(s)" if row["attempts"] is not None else "no run state"
            )
            lines.append(
                f"- **{row['task']}** {row['character']} — convictions: "
                f"{conviction_profile_text(row['convictions'])} — {attempts}, "
                f"tokens: {token_shape_text(row['tokens'])}"
            )

        lines.append("")

    if report["costs"]:
        lines.append("## Cost and iterations")
        lines.append("")

        for row in report["costs"]:
            cost = row.get("cost_usd")
            shown = f"${cost:.4f}" if isinstance(cost, (int, float)) else "unrecorded"

            if row["kind"] == "shadow":
                extra = f", attempts {row['attempts']}, {row['state']}"
            else:
                agent_bits = " · ".join(
                    str(part)
                    for part in (row.get("harness"), row.get("model_version") or row.get("model"))
                    if part
                )
                extra = f", {agent_bits}" if agent_bits else ""

            stamp = f"{row['at']} · " if row.get("at") else ""
            lines.append(
                f"- {stamp}{row['kind']} {row['task']} @ {row.get('config_hash')}: {shown}{extra}"
            )

        lines.append("")

    lines.append("## Specification quality")
    lines.append("")
    lines.append(report["spec_quality"]["caveat"])
    lines.append("")
    # S-0022/D-12: a corpus-wide fact, printed here even when no document below
    # has anything to say yet.
    lines.append(render_operator_attention(report["spec_quality"]["operator_attention"]))
    lines.append("")

    for doc in report["spec_quality"]["documents"]:
        attempts = (
            f"{doc['attempts_to_green_median']:.1f} attempt(s) to green "
            f"(n={doc['attempts_to_green_n']})"
            if doc["attempts_to_green_median"] is not None
            else "no landed tasks yet"
        )
        minutes = (
            f"{doc['human_minutes_median']:.0f}m human effort (n={doc['human_minutes_n']})"
            if doc["human_minutes_median"] is not None
            else "no feedback recorded"
        )
        rework = (
            f"{doc['rework_rate']:.0%} rework (n={doc['rework_n']})"
            if doc["rework_rate"] is not None
            else "no feedback recorded"
        )
        escalations = (
            ", ".join(
                f"{reason} ({count})"
                for reason, count in doc["escalations_by_reason"].items()
                if count
            )
            or "none"
        )

        lines.append(
            f"- **{doc['spec']}** — {doc['minted']} minted, {attempts}, {minutes}, {rework}, "
            f"{doc['drift_count']} spec-drift finding(s), escalations: {escalations}"
        )

    lines.append("")

    return "\n".join(lines)
