"""Standing maintenance (S-0023): a committed contract under
`.torve/standing/`, a deterministic trigger the tick evaluates with no
agent — exit code or digest comparison as the answer (S-0023/D-2) — and
instantiation through S-0020/adoption's adoption path, unchanged (S-0023/D-4).
The tick never decides that work exists; it recognises a condition a
human already committed an answer to. Any predicate outcome that is not a
clean verdict mints nothing (S-0023/D-3): the leg fails closed toward not
creating work.

Phase 2 (T-0102) adds the `command` predicate's sibling — `path-digest`,
a content digest compared against the digest recorded at the job's last
firing, due when it differs — and the fourth of S-0023/D-6's bounds:
self-disable after `strike_limit` consecutive non-landings. S-0023/D-6's
config knob (`standing.strike_limit`) was assigned to
`src/torve/config/runconfig.py`, which sits outside this phase's own
scope (both the RFC's phasing block and this task's minted contract
restrict it to this file, `.torve/standing/**` and `tests/**`) — departed
per S-0023/D-6 (ASSUMED) to a per-job `strike_limit` field on
`StandingContract` instead, logged in this task's `log.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from torve.application.ports import Runtime, SandboxSpec
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.base.model import STRICT
from torve.config import layout
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import Scope

# ----------------------- #

STANDING_RECORD = "standing.json"
_SANDBOX_UNSAFE = re.compile(r"[^a-z0-9_.-]")


# ....................... #


class Trigger(BaseModel):
    """One predicate (S-0023/D-8, S-0023/A-1): `command` runs a shell line in the
    sandbox, exit code as the answer; `path-digest` digests every file
    `paths` reaches (gitwildmatch, the same dialect `Scope.allow` uses) and
    is due when that digest differs from the one recorded at the job's last
    firing; `flake-threshold` sums telemetry's `flaky_count_by_command` and
    is due when any command not already in the gate manifest's quarantine
    list crosses `threshold` — the engine reading its own records, where an
    inline script would have to re-parse them with less than the engine
    knows."""

    model_config = STRICT
    kind: Literal["command", "path-digest", "flake-threshold"] = "command"
    """Which predicate the tick evaluates: `command`, `path-digest` or `flake-threshold`
    (S-0023/D-8, S-0023/A-1)."""
    run: str = ""
    """The shell line a `command` predicate runs in the sandbox, exit code as the answer;
    a command predicate with none is refused."""
    paths: list[str] = Field(default_factory=list)
    """The gitwildmatch patterns a `path-digest` predicate digests — the same dialect
    `Scope.allow` uses; a path-digest predicate with none is refused."""
    threshold: int = 3
    """The count a `flake-threshold` predicate is due at: a command not already in the gate
    manifest's quarantine list whose summed `flaky_count_by_command` crosses this."""

    # ....................... #

    @model_validator(mode="after")
    def _command_names_a_line(self) -> Trigger:
        if self.kind == "command" and not self.run.strip():
            raise ValueError("trigger.run is empty — a command predicate needs a shell line")

        if self.kind == "path-digest" and not self.paths:
            raise ValueError(
                "trigger.paths is empty — a path-digest predicate needs at least one path"
            )

        if self.kind == "flake-threshold" and self.threshold < 1:
            raise ValueError("trigger.threshold must be at least 1")

        return self


# ....................... #


class StandingContract(BaseModel):
    """One recurring job (S-0023/the-standing-contract): a task contract minus its id,
    with a trigger where a phasing block would be. `name` keys the bounds
    below across firings, so two files must never share one (S-0023/D-5's
    comparability depends on it)."""

    model_config = STRICT
    name: str
    """The job's name, which keys the bounds below across firings, so two files must never
    share one (S-0023/D-5's comparability depends on it)."""
    trigger: Trigger
    """The deterministic predicate the tick evaluates to decide whether this job is due —
    where a phasing block would be."""
    intent: str = ""
    """The intent the minted contract carries, exactly as a task contract's does."""
    scope: Scope = Field(default_factory=Scope)
    """The scope the minted contract carries, exactly as a task contract's does."""
    acceptance: list[str] = Field(default_factory=list)
    """The acceptance commands the minted contract carries, exactly as a task contract's
    do."""
    decisions_from: str | None = None
    """The document whose rows this job's contracts inherit, by number or identifier."""
    source: str | None = None
    """What asked for this job's work (S-0060/D-5): a source identifier the tree
    holds, so the contracts a recurring job mints say which job minted them."""
    """The document the minted instance inherits its decisions from, by number or
    identifier; a job naming a document that is not in the corpus is refused before
    adoption."""
    cooldown_hours: float = 0.0
    """How long after the job's last firing it may fire again, in hours; 0 imposes no
    wait."""
    max_open: int = 1
    """How many of this job's instances may be unresolved — neither landed nor abandoned —
    before the tick stops minting more (S-0023/D-11)."""
    strike_limit: int = 3
    """S-0023/D-6's fourth bound: self-disable after this many consecutive non-landings.
    S-0023 names it as a global `standing.strike_limit` default; this phase's scope
    excludes src/torve/config/runconfig.py, so it lives here instead, per job (departed,
    S-0023/D-6, see log.yaml)."""

    # ....................... #

    @model_validator(mode="after")
    def _bounds_are_sane(self) -> StandingContract:
        if not self.name.strip():
            raise ValueError("name is empty")

        if self.max_open < 1:
            raise ValueError("max_open must be at least 1")

        if self.cooldown_hours < 0:
            raise ValueError("cooldown_hours must not be negative")

        if self.strike_limit < 1:
            raise ValueError("strike_limit must be at least 1")

        return self


# ....................... #


class PredicateError(RuntimeError):
    """A predicate outcome that is not a clean exit code (S-0023/D-3): the
    caller mints nothing and records an engine event — never a quiet
    'not due'."""


# ....................... #


def load_standing_contracts(root: Path) -> tuple[list[StandingContract], list[str]]:
    """Every committed job under `.torve/standing/` (S-0023/D-1), or none — an
    empty or absent directory is off, not misconfigured (S-0023/D-7)."""

    directory = layout.standing_dir(root)

    if not directory.is_dir():
        return [], []

    jobs: list[StandingContract] = []
    errors: list[str] = []

    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))

        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: not YAML ({exc})")
            continue

        if not isinstance(raw, dict):
            errors.append(f"{path.name}: not a mapping")
            continue

        try:
            jobs.append(StandingContract.model_validate(raw))

        except ValidationError as exc:
            errors.append(f"{path.name}: {exc.errors()[0]['msg']}")

    names = [job.name for job in jobs]

    if len(set(names)) != len(names):
        errors.append(
            "duplicate standing job name(s) — each name must be unique "
            "across .torve/standing/ (S-0023/D-5's comparability depends on it)"
        )

    return jobs, errors


# ....................... #


def lint_job_body(root: Path, job: StandingContract) -> list[str]:
    """S-0020's contract lint, unchanged (S-0023/D-9): the body below
    `trigger` is exactly a task contract minus its id, checked the same
    way a drafted contract is before a human ever sees it."""

    from torve.application.intake import Draft, DraftsDocument, lint_drafts

    document = DraftsDocument(
        drafts=[Draft(ref="DRAFT-1", intent=job.intent, scope=job.scope, acceptance=job.acceptance)]
    )

    return [error.replace("DRAFT-1", job.name) for error in lint_drafts(root, document, 1)]


# ....................... #


def _path_digest(root: Path, patterns: list[str]) -> str:
    """sha256 over every file `patterns` reaches (gitwildmatch, the same
    dialect `Scope.allow` already uses) — path plus content, so an edited
    file or a moved reference both change the digest without a tool
    having to exist to notice."""

    from torve.gates.contract import spec

    hasher = hashlib.sha256()

    for rel in sorted(spec(patterns).match_tree_files(root)):
        hasher.update(rel.encode("utf-8"))
        hasher.update((root / rel).read_bytes())

    return hasher.hexdigest()


# ....................... #


def _flake_over_threshold(root: Path, threshold: int) -> bool:
    """Any gate command whose accumulated `flaky_count_by_command` reaches
    *threshold* and is not already quarantined in the gate manifest (S-0023/A-1):
    read with the engine's own parsers — the manifest loader and the
    telemetry stream — never a regex over YAML."""

    from torve.config.manifest import load_manifest

    counts: dict[str, int] = {}
    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if telemetry.is_file():
        for line in telemetry.read_text(encoding="utf-8").splitlines():
            try:
                record: Any = json.loads(line)

            except json.JSONDecodeError:
                continue

            if not isinstance(record, dict):
                continue

            raw: Any = cast("dict[str, Any]", record).get("flaky_count_by_command")

            if not isinstance(raw, dict):
                continue

            for command, n in cast("dict[str, Any]", raw).items():
                if isinstance(n, int):
                    counts[command] = counts.get(command, 0) + n

    manifest_file = layout.gates_file(root)
    quarantined: set[str] = (
        set(load_manifest(manifest_file).quarantine) if manifest_file.is_file() else set()
    )

    return any(n >= threshold and command not in quarantined for command, n in counts.items())


# ....................... #


def evaluate_predicate(
    job: StandingContract, root: Path, config: RunnerConfig, runtime: Runtime
) -> bool:
    """True when due (S-0023/D-2): a sandbox and an exit code for `command`; a
    content digest compared against the last firing's, no sandbox needed,
    for `path-digest` — both read only committed inputs, no model, no
    network. Anything a `command` predicate cannot cleanly exit is a
    PredicateError (S-0023/D-3), never invented as 'not due'."""

    if job.trigger.kind == "path-digest":
        current = _path_digest(root, job.trigger.paths)
        last = _last_digest(_job_instances(root, job.name))

        # No prior firing is nothing to compare against, which reads as
        # 'differs': the job fires once to record a baseline, then only on
        # an actual change thereafter.
        return last is None or current != last

    if job.trigger.kind == "flake-threshold":
        return _flake_over_threshold(root, job.trigger.threshold)

    safe_name = _SANDBOX_UNSAFE.sub("-", job.name.lower()) or "job"

    spec = SandboxSpec(
        name=f"torve-standing-{safe_name}-{uuid.uuid4().hex[:8]}",
        image=config.runtime.image,
        labels=naming.labels(f"standing-{safe_name}", "predicate", root),
        timeout_s=config.runtime.sandbox_timeout,
        workspace_read_only=True,
    )

    handle = runtime.create(spec, root)

    try:
        result = runtime.exec(handle, job.trigger.run, config.runtime.sandbox_timeout)

    finally:
        runtime.destroy(handle)

    if result.timed_out:
        raise PredicateError(f"predicate timed out: {job.trigger.run!r}")

    return result.exit_code != 0


# ....................... #


def _resolve_document(root: Path, config: RunnerConfig, identifier: str) -> str:
    """`decisions_from` names a document (S-0023/the-standing-contract's
    `"0012"`), by number or identifier; the instance's contract carries the
    identifier (S-0059/D-1), and the corpus is asked first so a job naming a
    document that is not there fails here rather than at adoption."""

    from torve.config import spec
    from torve.domain.spec import document_id

    if spec.document_dir(root / config.specs.path, identifier) is None:
        raise ValueError(f"no document {identifier!r} under {config.specs.path}")

    return document_id(identifier)


# ....................... #


def instantiate(root: Path, job: StandingContract, config: RunnerConfig) -> str:
    """S-0020/adoption's adoption path, unchanged (S-0023/D-4): a scratch drafts
    file carries the job's fixed body through `intake.adopt`, so id
    assignment, the commit and `inherit_decisions` all run through the one
    path that already closes the id race under the tick lock. The new
    instance's sidecar records its origin (S-0023/D-10)."""

    from torve.application.intake import adopt, drafts_file

    scratch = f"standing-{job.name}-{uuid.uuid4().hex[:8]}"
    document = _resolve_document(root, config, job.decisions_from) if job.decisions_from else None

    source = drafts_file(root, scratch)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request": job.intent,
                "spec": document,
                "source": job.source,
                "rationale": "",
                "drafts": [
                    {
                        "ref": "DRAFT-1",
                        "intent": job.intent,
                        "scope": job.scope.model_dump(),
                        "acceptance": list(job.acceptance),
                        "depends_on": [],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # The tick that calls this leg already holds the lock (S-0019/D-2); adopt's
    # own acquire would deadlock against it.
    try:
        (new_id,) = adopt(root, scratch, config, assume_lock=True)

    except Exception:
        # A refused adoption never retries this scratch — the next
        # evaluation mints a fresh uuid — so leaving it behind grows one
        # directory per pass for as long as the job stays unadoptable.
        # `flake-quarantine` crosses locked decisions from four documents
        # and had left seven. A successful adoption keeps its directory:
        # `adopted.json` lands there and is what refuses a second adoption.
        shutil.rmtree(source.parent, ignore_errors=True)
        raise

    record: dict[str, Any] = {
        "schema_version": 1,
        "job": job.name,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if job.trigger.kind == "path-digest":
        # This firing's baseline: the next evaluation is due only once the
        # digest recorded here has changed.
        record["digest"] = _path_digest(root, job.trigger.paths)

    sidecar = layout.task_dir(root, new_id) / STANDING_RECORD
    sidecar.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    engine_event(root, "standing_fired", {"job": job.name, "task": new_id})

    return new_id


# ....................... #


def _job_instances(root: Path, name: str) -> list[tuple[str, datetime, dict[str, Any]]]:
    """(task_id, fired_at, sidecar record) for every instance this job has
    minted. The sidecar `standing.json` is the firing ledger (S-0023/D-11,
    decided): it sits under `.torve/tasks/`, an engine record exempt from
    the lane's dirty-tree check exactly like a minted contract, so
    cooldown, max_open and the path-digest baseline never depend on
    host-local telemetry that a fresh clone would lack."""

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return []

    found: list[tuple[str, datetime, dict[str, Any]]] = []

    for sidecar in sorted(tasks_dir.glob(f"*/{STANDING_RECORD}")):
        try:
            record = cast("dict[str, Any]", json.loads(sidecar.read_text(encoding="utf-8")))

        except json.JSONDecodeError:
            continue

        if record.get("job") != name:
            continue

        try:
            at = datetime.strptime(str(record["at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

        except (KeyError, ValueError):
            continue

        found.append((sidecar.parent.name, at, record))

    return found


# ....................... #


def _last_digest(instances: list[tuple[str, datetime, dict[str, Any]]]) -> str | None:
    """The digest recorded at the job's most recent firing — the
    `path-digest` baseline. None when the job has never fired."""

    if not instances:
        return None

    _task_id, _at, record = max(instances, key=lambda item: item[1])
    digest = record.get("digest")

    return digest if isinstance(digest, str) else None


# ....................... #


def _consecutive_non_landings(
    root: Path,
    instances: list[tuple[str, datetime, dict[str, Any]]],
    landed: Callable[[str], bool],
) -> int:
    """S-0023/D-6's fourth bound: the trailing streak of instances that
    concluded without landing. Walked newest-first — a landing ends the
    streak (the job is healthy again), an abandoned instance extends it,
    and an instance still open has not concluded either way, so it is
    skipped rather than counted."""

    streak = 0

    for task_id, _at, _record in sorted(instances, key=lambda item: item[1], reverse=True):
        if landed(task_id):
            break

        state_path = naming.state_file(root, task_id)

        if state_path.exists() and RunState.load(state_path).state is TaskState.ABANDONED:
            streak += 1

    return streak


# ....................... #


def _open_count(
    root: Path,
    instances: list[tuple[str, datetime, dict[str, Any]]],
    landed: Callable[[str], bool],
) -> int:
    """Instances neither landed nor abandoned (S-0023/D-11, decided): an
    escalated instance is unresolved work and counts toward `max_open` —
    the reading S-0023's unresolved question favoured."""

    count = 0

    for task_id, _at, _record in instances:
        if landed(task_id):
            continue

        state_path = naming.state_file(root, task_id)

        if state_path.exists() and RunState.load(state_path).state is TaskState.ABANDONED:
            continue

        count += 1

    return count


# ....................... #


def standing_leg(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    landed: Callable[[str], bool],
) -> tuple[str, bool]:
    """The tick's standing leg (S-0023/bounds-because-this-is-the-leg-that-can-grow): evaluate every committed
    job's predicate and mint at most `loop.standing_max_per_tick`
    instances, each bounded by its own cooldown, max_open and strike
    limit. The escalation pause (S-0023/D-6's first bound) is the caller's
    job — `run_tick` calls this leg from inside the same conditional that
    gates dispatch, so a paused tick evaluates no predicate at all."""

    jobs, load_errors = load_standing_contracts(root)

    for message in load_errors:
        engine_event(root, "standing_contract_invalid", {"error": message})

    fired: list[str] = []
    skipped: list[str] = []
    errors: list[str] = list(load_errors)

    for job in jobs:
        if len(fired) >= config.loop.standing_max_per_tick:
            skipped.append(f"{job.name} (standing_max_per_tick reached)")
            continue

        instances = _job_instances(root, job.name)
        last_fired = max((at for _tid, at, _rec in instances), default=None)

        if last_fired is not None and job.cooldown_hours > 0:
            age_h = (datetime.now(UTC) - last_fired).total_seconds() / 3600

            if age_h < job.cooldown_hours:
                skipped.append(f"{job.name} (cooldown {age_h:.1f}h/{job.cooldown_hours}h)")
                continue

        if _open_count(root, instances, landed) >= job.max_open:
            skipped.append(f"{job.name} (max_open reached)")
            continue

        strikes = _consecutive_non_landings(root, instances, landed)

        if strikes >= job.strike_limit:
            engine_event(root, "standing_self_disabled", {"job": job.name, "strikes": strikes})
            skipped.append(f"{job.name} (self-disabled: {strikes} consecutive non-landings)")
            continue

        try:
            due = evaluate_predicate(job, root, config, runtime)

        except PredicateError as exc:
            engine_event(root, "standing_predicate_error", {"job": job.name, "error": str(exc)})
            errors.append(f"{job.name}: {exc}")
            continue

        if not due:
            continue

        lint_errors = lint_job_body(root, job)

        if lint_errors:
            engine_event(
                root, "standing_predicate_error", {"job": job.name, "error": lint_errors[0]}
            )
            errors.append(f"{job.name}: contract lint red: {lint_errors[0]}")
            continue

        try:
            fired.append(f"{job.name}->{instantiate(root, job, config)}")

        except ValueError as exc:
            # S-0023/D-3's rule, one step later than it was written: the leg
            # fails closed toward not creating work, and an instantiation
            # a human would have to fix is exactly that. Found live — a job
            # whose body crosses four documents' locked decisions is
            # refused by the threshold (S-0030), and the refusal used to
            # leave the manager's whole pass dead (S-0044/A-11).
            engine_event(root, "standing_instantiate_refused", {"job": job.name, "error": str(exc)})
            errors.append(f"{job.name}: {exc}")

    parts: list[str] = []

    if fired:
        parts.append(f"fired {len(fired)}: {', '.join(fired)}")

    if skipped:
        parts.append(f"skipped {len(skipped)}: {', '.join(skipped)}")

    if errors:
        parts.append(f"{len(errors)} error(s): {', '.join(errors)}")

    detail = "; ".join(parts) if parts else "no standing jobs due"

    return detail, bool(fired)
