"""The divergence intake (RFC 0044 §5.6, D-44.10): the agent tells the
engine, and the engine writes the log.

Every poison ceiling in the measurement window that produced RFC 0044 was a
defect in a hand-written file, not in the work it described — a scalar that
made the YAML unparseable, an evidence line in the wrong grammar, a log
never staged and so invisible to the gate that judged the diff. All three
are artifacts of one arrangement: the agent authoring a machine-read file
and learning hours later, from a gate, that it was malformed.

So the agent stops writing the file. It states an entry; the intake checks
it with the gate's own per-entry checks and refuses in the words the gate
would have used, while the agent can still act; and on acceptance the
engine serializes the document, keeps its bookkeeping true, and stages it.
An unparseable log, a malformed evidence line and an unstaged log all stop
being reachable states rather than becoming rarer ones.

The recorded event is written host-side (`ingest`). An agent runs inside a
sandbox and has no route to the store — nor should it, which is what D-44.2
means by an agent writing only through a validating intake: this module is
the agent-facing half, and the worker holds the other.
"""

from __future__ import annotations

import json
import subprocess
from asyncio import run_coroutine_threadsafe
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from torve.config import layout
from torve.domain.events import ActorKind, EventKind, SubjectType
from torve.gates.decisions_reported import check_entry, check_pin

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

    from torve.application.eventlog import EventLog
    from torve.application.ports import JournalSync
    from torve.domain.events import EventRecord

# ----------------------- #

# The order the format reads in (RFC 0001 §6), so a projected log looks like
# the logs the corpus already carries rather than like a serializer's idea
# of one.
ENTRY_ORDER = (
    "decision",
    "grade",
    "kind",
    "class",
    "at",
    "attempt",
    "claim",
    "evidence",
    "action",
    "proposal",
    "notes",
)
DOCUMENT_ORDER = ("schema_version", "task", "repo", "base_sha", "drift_count", "entries")
# Engine scratch, generated and never committed (RFC 0013 §5).
PIN_FILE = "pin.json"
SCHEMA_VERSION = 1


# ....................... #


class IntakeRefused(Exception):
    """The entry is not one the gate would accept. Carries the gate's own
    problems, unedited: the agent reads the same sentences it would have
    read from a red battery, only sooner."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))

        self.problems = problems


# ....................... #


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )

    return result.stdout.strip() if result.returncode == 0 else ""


# ....................... #


def _pin(root: Path) -> dict[str, str]:
    """The log's opening pin (D-A.7): the repository its evidence resolves
    against, and the commit the work started from. Derived here because an
    agent transcribing it is one more thing that can be wrong — and was.

    A pin the engine left in the worktree wins over derivation: inside a
    sandbox git resolves nothing, and the dropped file is the only source
    there is."""

    dropped = root / layout.TORVE_DIR / "tmp" / PIN_FILE

    if dropped.is_file():
        loaded = json.loads(dropped.read_text() or "{}")

        if isinstance(loaded, dict):
            carried = cast(dict[str, Any], loaded)

            return {
                "repo": str(carried.get("repo") or ""),
                "base_sha": str(carried.get("base_sha") or ""),
            }

    remote = _git(root, "config", "--get", "remote.origin.url")
    repo = ""

    if remote:
        trimmed = remote.removesuffix(".git").rstrip("/")
        parts = trimmed.replace(":", "/").split("/")
        repo = "/".join(parts[-2:]) if len(parts) >= 2 else ""

    return {"repo": repo, "base_sha": _git(root, "rev-parse", "HEAD")}


# ....................... #


def open_log(root: Path, task_id: str) -> dict[str, Any]:
    """The task's log as a document — the file when it exists, an empty one
    pinned to this worktree when it does not (A-13, D-3.21)."""

    path = layout.log_file(root, task_id)

    if path.is_file() and path.read_text().strip():
        loaded = yaml.safe_load(path.read_text())

        if isinstance(loaded, dict):
            # The one boundary where a parsed document becomes typed.
            document = cast(dict[str, Any], loaded)
            document.setdefault("entries", [])

            return document

    return {
        "schema_version": SCHEMA_VERSION,
        "task": task_id,
        **_pin(root),
        "drift_count": 0,
        "entries": [],
    }


def render(document: dict[str, Any]) -> str:
    """The document as YAML the gate can read back. `safe_dump` decides the
    quoting, which is the whole point: the scalar that ended three attempts
    of T-0245 was a hand-written one carrying `key: value` inside backticks,
    and a serializer never writes that unquoted."""

    ordered = {key: document[key] for key in DOCUMENT_ORDER if key in document}
    ordered.update({key: value for key, value in document.items() if key not in ordered})
    ordered["entries"] = [
        {key: entry[key] for key in ENTRY_ORDER if key in entry}
        | {key: value for key, value in entry.items() if key not in ENTRY_ORDER}
        for entry in document["entries"]
    ]

    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=88)


# ....................... #


def seed(root: Path, task_id: str, *, base_sha: str | None = None) -> Path:
    """Write the log's pin where the intake can read it, before the agent
    runs.

    A sandbox sees a `.git` pointer into a host tree it cannot follow, so
    nothing inside it can resolve the commit its evidence must cite. The
    engine knows it, drops it here, and the intake reads it back — which is
    why the agent is no longer asked to copy a pin it has no way to check.

    It is a pin, not a log: an empty log is not the same as no log (A-13,
    D-3.21), and a run with nothing to report must still leave no file
    behind. The pin lives under the engine's own scratch directory, which
    is generated and never committed.
    """

    pin = dict(_pin(root))

    if base_sha:
        pin["base_sha"] = base_sha

    path = root / layout.TORVE_DIR / "tmp" / PIN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pin, indent=2) + "\n")

    return path


# ....................... #


def stage(root: Path, path: Path) -> bool:
    """Stage the log the engine just wrote. The gate judges a diff, so a
    log outside it is a log that does not exist — three escalations in one
    week were exactly this, and no prompt fixes it as reliably as the
    writer staging its own output."""

    relative = path.relative_to(root)
    result = subprocess.run(
        ["git", "-C", str(root), "add", "--", str(relative)],
        capture_output=True,
        text=True,
        check=False,
    )

    return result.returncode == 0


# ....................... #


# ....................... #


def compose(
    root: Path,
    *,
    decision: str,
    grade: str,
    claim: str,
    evidence: str,
    action: str,
    attempt: int,
    kind: str = "",
    klass: str = "",
    proposal: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """One entry, checked. Refuses before anything is written or sent: the
    checks are the gate's own, so a refusal here is the conviction the
    battery would have produced, delivered while the agent can still act on
    it."""

    entry: dict[str, Any] = {
        "decision": decision,
        "grade": grade,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attempt": attempt,
        "claim": claim,
        "evidence": evidence,
        "action": action,
    }

    for key, value in (("kind", kind), ("class", klass), ("proposal", proposal), ("notes", notes)):
        if value:
            entry[key] = value

    problems = check_entry(entry, root)

    if problems:
        raise IntakeRefused(problems)

    return entry


# ....................... #


def payload_of(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry as the `divergence.recorded` payload — the shape the store
    holds, and what the channel posts. The record carries no timestamp of
    its own: the store stamps when it accepted the entry, which is the only
    clock anybody can check."""

    return {
        "attempt": int(entry["attempt"]),
        "decision_id": str(entry["decision"]),
        "grade": str(entry["grade"]),
        "entry_kind": str(entry.get("kind") or "resolved"),
        "entry_class": str(entry.get("class") or "discovery"),
        "claim": str(entry["claim"]),
        "evidence": str(entry["evidence"]),
        "action": str(entry["action"]),
        "proposal": str(entry.get("proposal") or ""),
        "notes": str(entry.get("notes") or ""),
    }


# ....................... #


def append(root: Path, task_id: str, entry: dict[str, Any]) -> tuple[Path, dict[str, Any], bool]:
    """Serialize the entry into the worktree's log and stage it — because a
    write the caller must remember to stage is the failure this verb exists
    to remove."""

    document = open_log(root, task_id)

    # A log written before the intake existed may carry no pin, and the
    # worktree can supply what is missing — repair what is derivable, then
    # report what is not, rather than letting the battery find it later.
    for key, value in _pin(root).items():
        if not str(document.get(key) or "").strip():
            document[key] = value

    pin_problems = check_pin(document)

    if pin_problems:
        raise IntakeRefused(pin_problems)

    document["entries"].append(entry)
    # The count the gate compares against is derived, never declared: an
    # agent counting its own drift entries was a conviction waiting to
    # happen, and nothing is learned by making it count.
    document["drift_count"] = sum(
        1 for one in document["entries"] if str(one.get("class") or "") == "drift"
    )

    path = layout.log_file(root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(document))

    return path, document, stage(root, path)


# ....................... #


def record(
    root: Path,
    task_id: str,
    *,
    decision: str,
    grade: str,
    claim: str,
    evidence: str,
    action: str,
    attempt: int,
    kind: str = "",
    klass: str = "",
    proposal: str = "",
    notes: str = "",
) -> tuple[Path, dict[str, Any], bool]:
    """Check, append, serialize, stage — the file path, for a run with no
    channel to post through (D-45.6). A rejected entry leaves the log
    exactly as it was."""

    return append(
        root,
        task_id,
        compose(
            root,
            decision=decision,
            grade=grade,
            claim=claim,
            evidence=evidence,
            action=action,
            attempt=attempt,
            kind=kind,
            klass=klass,
            proposal=proposal,
            notes=notes,
        ),
    )


# ....................... #


async def ingest(
    log: EventLog,
    root: Path,
    task_id: str,
    *,
    partition: str,
    actor_id: str,
    correlation_id: str | None = None,
    after: int = 0,
) -> list[EventRecord]:
    """Record the worktree's entries into the system of record — the
    worker's half of the intake, run where the store is reachable.

    Entries already in the log are recorded as they stand: they passed the
    same checks on the way in, and re-validating a landed fact would only
    let a later schema refuse history it cannot change.

    `after` is how many of this log's entries are already recorded. A run
    ingests between attempts, and the file is cumulative, so without it
    attempt three would record attempt one's entries for the third time.
    """

    document = open_log(root, task_id)
    recorded: list[EventRecord] = []

    for entry in document["entries"][after:]:
        recorded.append(
            await log.record(
                EventKind.DIVERGENCE_RECORDED,
                partition=partition,
                subject_type=SubjectType.TASK,
                subject_id=task_id,
                actor_kind=ActorKind.AGENT,
                actor_id=actor_id,
                payload=payload_of(entry),
                correlation_id=correlation_id,
            )
        )

    return recorded


# ....................... #


def entry_of(event: EventRecord) -> dict[str, Any]:
    """One recorded divergence as the log format writes it. The event's own
    clock supplies `at`: the record's time is when the engine accepted it,
    which is the only timestamp anybody can check."""

    payload = event.payload
    entry: dict[str, Any] = {
        "decision": str(payload.get("decision_id") or ""),
        "grade": str(payload.get("grade") or ""),
        "at": event.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attempt": int(payload.get("attempt") or 1),
        "claim": str(payload.get("claim") or ""),
        "evidence": str(payload.get("evidence") or ""),
        "action": str(payload.get("action") or ""),
    }

    for key, source in (("kind", "entry_kind"), ("class", "entry_class")):
        if value := str(payload.get(source) or ""):
            entry[key] = value

    for key in ("proposal", "notes"):
        if value := str(payload.get(key) or ""):
            entry[key] = value

    return entry


# ....................... #


async def recorded_entries(
    log: EventLog, task_id: str, *, partition: str | None = None
) -> list[EventRecord]:
    """Every divergence the record holds for this task, oldest first."""

    return [
        event
        for event in await log.history(task_id, partition=partition)
        if event.kind is EventKind.DIVERGENCE_RECORDED
    ]


# ....................... #


async def project(log: EventLog, root: Path, task_id: str, *, partition: str | None = None) -> int:
    """Rewrite the worktree's log from what the store holds (RFC 0044
    D-44.10, A-82).

    The gate runs inside a sandbox and cannot reach the store, so the store
    is made authoritative the only way it can be: the engine writes the file
    the gate reads, from the record, before the gate reads it. An entry the
    store refused is an entry the gate never sees, and one the store holds
    appears whether or not the worktree's copy survived.

    A task with nothing recorded leaves no file — a missing log is an empty
    log (A-13, D-3.21), and writing an empty one would turn that into a
    claim nobody made.
    """

    recorded = await recorded_entries(log, task_id, partition=partition)

    if not recorded:
        return 0

    document = open_log(root, task_id)

    # The pin is the worktree's, not the record's: whatever the file carried
    # is kept, and anything missing is derived here rather than left for the
    # gate to convict — the same repair the intake performs on write.
    for key, value in _pin(root).items():
        if not str(document.get(key) or "").strip():
            document[key] = value

    document["entries"] = [entry_of(event) for event in recorded]
    document["drift_count"] = sum(
        1 for one in document["entries"] if str(one.get("class") or "") == "drift"
    )

    path = layout.log_file(root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(document))
    stage(root, path)

    return len(recorded)


# ....................... #


def journal_sync(
    log: EventLog,
    loop: AbstractEventLoop,
    *,
    partition: str,
    task_id: str,
    seat: str,
) -> JournalSync:
    """The runner's hook between an attempt and its gate pass (A-82).

    Two halves in one call, and the order matters: record what the attempt
    wrote, then rewrite the file from the record. After it, the log in the
    worktree is what the store holds and nothing else — an entry the store
    refused never reaches the battery, and one it accepted survives whatever
    the sandbox did to the file afterwards.

    The runner is synchronous and lives on another thread, so this blocks on
    the loop that owns the store rather than starting one of its own. It is
    allowed to block, and allowed to raise: the gates are fail-closed, and a
    battery that cannot verify what it judges must not run.
    """

    ingested = -1

    def sync(worktree: Path) -> None:
        nonlocal ingested

        async def once() -> None:
            nonlocal ingested

            if ingested < 0:
                # What the record already holds for this task. A re-dispatch
                # cuts a fresh worktree from base, and base may carry a log
                # landed by an earlier dispatch — starting the offset at zero
                # would record every one of those entries a second time.
                ingested = len(await recorded_entries(log, task_id, partition=partition))

            fresh = await ingest(
                log,
                worktree,
                task_id,
                partition=partition,
                actor_id=seat,
                after=ingested,
            )
            ingested += len(fresh)
            await project(log, worktree, task_id, partition=partition)

        run_coroutine_threadsafe(once(), loop).result()

    return sync
