"""Run state, v1: a JSON file beside the worktree (RFC 0003 §2).

This is the RFC-sanctioned first stage, not a hand-rolled TaskStore — the
durable run store facade (D-5) arrives with T-0004. Until then liveness is a
heartbeat stamped at each phase boundary, which is what lets the reaper tell
an orphan from a live run after `kill -9` (a decision logged in T-0003).

Writes are atomic (tmp + rename): a crash mid-write must not leave a state
file that parses halfway.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from torve.base.naming import WORKTREE_DIR
from torve.domain.states import EscalationReason, TaskState, check_transition
from torve.domain.task import SCHEMA_VERSION

# ----------------------- #


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ....................... #


@dataclass
class Escalation:
    reason: str
    detail: str


# ....................... #


@dataclass
class RunState:
    task_id: str
    path: Path
    schema_version: int = SCHEMA_VERSION
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    heartbeat: str = field(default_factory=_now)
    sandbox_id: str | None = None
    durable_run_id: str | None = None  # the store's run this engine run executes under
    worktree: str | None = None
    escalation: Escalation | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    # Sha-bound promotion approvals (RFC 0006 §3): {actor, sha, at} — an
    # approval that predates the last push approves nothing, so the lane
    # counts only entries matching the current branch tip.
    approvals: list[dict[str, str]] = field(default_factory=list)

    # The base tip this run last conflicted against (D-6.12, A-35): the
    # lane's automatic conflict disposal re-queues only against a base
    # that has moved since — a repeat against this tip is a human's turn.
    conflict_base: str | None = None

    # The review task that concluded over this candidate without a
    # surviving blocker (D-6.14, A-43) — the lane's require_review
    # predicate. The unconfigured-review bridge never sets it.
    reviewed_by: str | None = None

    # ....................... #

    def transition(self, to: TaskState, fact: str) -> None:
        """Transitions are executed from facts; the fact is recorded with the
        transition so the history explains itself."""

        check_transition(self.state, to)
        self.history.append({"at": _now(), "from": str(self.state), "to": str(to), "fact": fact})

        if to is TaskState.RUNNING:
            # Attempts increment on entry to running (RFC 0001 §4), and no
            # review verdict outlives the attempt it judged (D-6.14).
            self.attempts += 1
            self.reviewed_by = None

        self.state = to
        self.touch()

    # ....................... #

    def escalate(self, reason: EscalationReason, detail: str) -> None:
        self.transition(TaskState.ESCALATED, f"{reason}: {detail}")
        self.escalation = Escalation(reason=str(reason), detail=detail)
        self.save()
        # The durable landing of the fact, after the state-file write that
        # gates correctness (RFC 0038 §5.3, D-38.5).
        self._append_escalation_event(str(reason), detail)

    # ....................... #

    def _host_root(self) -> Path:
        """The repository root the escalation event rides, derived
        structurally from the state-file location: a state file lives at
        `<host>/.wt/<task>.state.json` (naming.state_file), so the parent
        of the `.wt` directory is the host root — the same worktree-parent
        walk `_write_regime_preimage` performs. A state file outside any
        `.wt` names its own directory's root: best-effort is all this
        claims."""

        for parent in self.path.parents:
            if parent.name == WORKTREE_DIR:
                return parent.parent

        return self.path.parent

    # ....................... #

    def _append_escalation_event(self, reason: str, detail: str) -> None:
        """One durable record of the escalation, from the single place the
        field is set — one call site, not twenty-two (D-38.5): the state
        file is overwritten by the next dispatch and deleted at the
        terminal sweep, and without this the reason a task needed a human
        is unrecoverable precisely after the human is done with it
        (RFC 0038 §2). `reason` is the existing EscalationReason value
        verbatim — no new taxonomy.

        Best-effort: an unwritable stream must not turn an escalation into
        a crash — the state-file write above is the one that gates
        correctness, this one records a fact."""

        from torve.application.telemetry import engine_event  # local: keep runstate light on import

        try:
            engine_event(
                self._host_root(),
                "escalation",
                {
                    "task": self.task_id,
                    "reason": reason,
                    "detail": detail,
                    "run_id": self.run_id,
                },
            )

        # Best-effort by contract: a lost event must never escalate itself
        # into a crash, so nothing an unwritable stream can raise escapes.
        except Exception:
            return

    # ....................... #

    def touch(self) -> None:
        self.heartbeat = _now()

    # ....................... #

    def heartbeat_age_s(self, now: datetime | None = None) -> float:
        stamp = datetime.strptime(self.heartbeat, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        return ((now or datetime.now(UTC)) - stamp).total_seconds()

    # ....................... #

    def to_record(self) -> dict[str, object]:
        """The persisted shape — also what `--format json` emits (D-11.3):
        one record, no parallel CLI-only schema."""

        data = asdict(self)
        data.pop("path")
        data["state"] = str(self.state)

        return data

    # ....................... #

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_record(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # ....................... #

    @classmethod
    def load(cls, path: Path) -> RunState:
        data = json.loads(path.read_text(encoding="utf-8"))
        escalation = data.pop("escalation", None)

        return cls(
            path=path,
            task_id=data["task_id"],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            run_id=data["run_id"],
            state=TaskState(data["state"]),
            attempts=data["attempts"],
            heartbeat=data["heartbeat"],
            sandbox_id=data.get("sandbox_id"),
            durable_run_id=data.get("durable_run_id"),
            worktree=data.get("worktree"),
            escalation=Escalation(**escalation) if escalation else None,
            history=data.get("history", []),
            approvals=data.get("approvals", []),
            conflict_base=data.get("conflict_base"),
            reviewed_by=data.get("reviewed_by"),
        )

    # ....................... #

    @classmethod
    def load_all(cls, wt_dir: Path) -> list[RunState]:
        if not wt_dir.is_dir():
            return []

        return [cls.load(p) for p in sorted(wt_dir.glob("*.state.json"))]
