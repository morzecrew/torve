"""The event vocabulary (RFC 0044 §5.1, §5.2): what the engine records and
who may record it.

The log is the system of record for intent and execution — every other view
of engine state is a projection rebuilt from it (D-44.1). Events are written
at the moment the fact occurs and never derived afterwards (D-44.3), which
is why the vocabulary is closed the way the escalation enum is closed: an
extensible kind list makes history incomparable across time, and a payload
that grew a field silently makes replay lie. Adding a kind is an RFC
amendment, not a code change.

Write authority is domain knowledge, not adapter policy (D-44.2): the
authority table below is what makes the human signature RFC 0027 protected
survive the move of truth out of git. An agent cannot write an acceptance
whatever its prompt says, because the application service refuses the write
before any store sees it.

This module is deliberately storage-free. The record's envelope — identity,
timestamps, tenancy, ordering — belongs to the document aggregate the
persistence layer defines, and the service that enforces the rules below
sits above the document ports rather than beside them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from torve.domain.rfc import Grade
from torve.domain.states import EscalationReason

# ----------------------- #


class ActorKind(StrEnum):
    """Who is writing. The operator is a human signature; the manager owns
    the queue; a worker executes; an agent speaks only through the intake
    its worker runs (RFC 0044 §5.2)."""

    OPERATOR = "operator"
    MANAGER = "manager"
    WORKER = "worker"
    AGENT = "agent"


# ....................... #


class SubjectType(StrEnum):
    SOURCE = "source"
    DECISION = "decision"
    TASK = "task"
    SEAT = "seat"


# ....................... #


class EventKind(StrEnum):
    SOURCE_IMPORTED = "source.imported"
    DECISION_RECORDED = "decision.recorded"
    DECISION_ACCEPTED = "decision.accepted"
    TASK_MINTED = "task.minted"
    TASK_ADOPTED = "task.adopted"
    TASK_CLAIMED = "task.claimed"
    TASK_RELEASED = "task.released"
    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_FINISHED = "attempt.finished"
    GATES_EVALUATED = "gates.evaluated"
    DIVERGENCE_RECORDED = "divergence.recorded"
    REVIEW_RECORDED = "review.recorded"
    BLOCKER_RAISED = "blocker.raised"
    LANDING_RECORDED = "landing.recorded"
    ESCALATION_RAISED = "escalation.raised"
    ESCALATION_RESOLVED = "escalation.resolved"
    MESSAGE_SENT = "message.sent"
    SEAT_CONSUMED = "seat.consumed"


# ....................... #

# RFC 0044 §5.2. Acceptance and adoption are the human's alone — that pair is
# the whole of what the old git-holds-truth rule was protecting. Queue and
# landing facts belong to the manager because it is the only actor that can
# know them; execution facts belong to the worker for the same reason. An
# agent writes exactly two kinds, both through a validating intake, and both
# are statements about its own work rather than about the system's state.
AUTHORITY: dict[EventKind, frozenset[ActorKind]] = {
    EventKind.SOURCE_IMPORTED: frozenset({ActorKind.OPERATOR, ActorKind.MANAGER}),
    EventKind.DECISION_RECORDED: frozenset({ActorKind.OPERATOR, ActorKind.MANAGER}),
    EventKind.DECISION_ACCEPTED: frozenset({ActorKind.OPERATOR}),
    EventKind.TASK_MINTED: frozenset({ActorKind.MANAGER}),
    EventKind.TASK_ADOPTED: frozenset({ActorKind.OPERATOR}),
    EventKind.TASK_CLAIMED: frozenset({ActorKind.MANAGER}),
    EventKind.TASK_RELEASED: frozenset({ActorKind.MANAGER}),
    EventKind.ATTEMPT_STARTED: frozenset({ActorKind.WORKER}),
    EventKind.ATTEMPT_FINISHED: frozenset({ActorKind.WORKER}),
    EventKind.GATES_EVALUATED: frozenset({ActorKind.WORKER}),
    EventKind.DIVERGENCE_RECORDED: frozenset({ActorKind.AGENT}),
    EventKind.REVIEW_RECORDED: frozenset({ActorKind.WORKER}),
    EventKind.BLOCKER_RAISED: frozenset({ActorKind.WORKER}),
    EventKind.LANDING_RECORDED: frozenset({ActorKind.MANAGER}),
    EventKind.ESCALATION_RAISED: frozenset({ActorKind.MANAGER, ActorKind.WORKER}),
    EventKind.ESCALATION_RESOLVED: frozenset({ActorKind.OPERATOR}),
    EventKind.MESSAGE_SENT: frozenset({ActorKind.AGENT}),
    EventKind.SEAT_CONSUMED: frozenset({ActorKind.WORKER}),
}


# ----------------------- #

# One payload model per kind (D-44.1). Fields are what v1 already records in
# its telemetry and run states — transcription, not invention — and the
# record's own actor and subject are never repeated here.


class SourceImported(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["specification", "incident", "audit", "review", "operator"]
    ref: str
    title: str = ""


# ....................... #


class DecisionRecorded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)
    source_id: str
    supersedes: str | None = None


# ....................... #


class DecisionAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


# ....................... #


class TaskMinted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    source_id: str
    phase: int = 0
    depends_on: list[str] = Field(default_factory=list)


# ....................... #


class TaskAdopted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


# ....................... #


class TaskClaimed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    lease_seconds: int


# ....................... #


class TaskReleased(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


# ....................... #


class AttemptStarted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int
    tier: str
    agent: str
    image_digest: str | None = None


# ....................... #


class AttemptFinished(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int
    exit_code: int
    timed_out: bool = False
    wall_time_s: float | None = None
    cost_usd: float | None = None


# ....................... #


class GatesEvaluated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int
    exit_code: int
    outcomes: dict[str, str] = Field(default_factory=dict)
    digest: str = ""


# ....................... #

# The v1 divergence entry, transcribed (RFC 0001 §7, the flag-dont-flip
# vocabulary the decisions-reported gate enforces). The intake validates an
# entry against these same words at write time, and a parity test pins this
# vocabulary against the gate's own sets so the two cannot drift apart.
DivergenceKind = Literal["contradicted", "departed", "resolved", "blocked"]
DivergenceClass = Literal["discovery", "spec-gap", "drift", "irreducible"]
DivergenceAction = Literal["halted", "departed", "decided"]


class DivergenceRecorded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int
    decision_id: str
    grade: Grade
    entry_kind: DivergenceKind
    entry_class: DivergenceClass
    claim: str
    evidence: str
    action: DivergenceAction
    proposal: str = ""
    notes: str = ""


# ....................... #


class ReviewRecorded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    findings: int = 0
    blockers: int = 0


# ....................... #


class BlockerRaised(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    claim: str


# ....................... #


class LandingRecorded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha: str
    attempt: int


# ....................... #


class EscalationRaised(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: EscalationReason
    detail: str = ""


# ....................... #


class EscalationResolved(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["requeued", "abandoned", "landed"]
    note: str = ""


# ....................... #


class MessageSent(BaseModel):
    """RFC 0044 D-44.4: an agent influences another agent only as a record a
    human can read. There is no channel that bypasses this model."""

    model_config = ConfigDict(extra="forbid")

    to_role: str
    topic: str
    body: str


# ....................... #


class SeatConsumed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seat: str
    tokens: int | None = None
    cost_usd: float | None = None


# ....................... #

PAYLOADS: dict[EventKind, type[BaseModel]] = {
    EventKind.SOURCE_IMPORTED: SourceImported,
    EventKind.DECISION_RECORDED: DecisionRecorded,
    EventKind.DECISION_ACCEPTED: DecisionAccepted,
    EventKind.TASK_MINTED: TaskMinted,
    EventKind.TASK_ADOPTED: TaskAdopted,
    EventKind.TASK_CLAIMED: TaskClaimed,
    EventKind.TASK_RELEASED: TaskReleased,
    EventKind.ATTEMPT_STARTED: AttemptStarted,
    EventKind.ATTEMPT_FINISHED: AttemptFinished,
    EventKind.GATES_EVALUATED: GatesEvaluated,
    EventKind.DIVERGENCE_RECORDED: DivergenceRecorded,
    EventKind.REVIEW_RECORDED: ReviewRecorded,
    EventKind.BLOCKER_RAISED: BlockerRaised,
    EventKind.LANDING_RECORDED: LandingRecorded,
    EventKind.ESCALATION_RAISED: EscalationRaised,
    EventKind.ESCALATION_RESOLVED: EscalationResolved,
    EventKind.MESSAGE_SENT: MessageSent,
    EventKind.SEAT_CONSUMED: SeatConsumed,
}


# ----------------------- #


class UnauthorizedWrite(Exception):
    def __init__(self, actor: ActorKind, kind: EventKind) -> None:
        allowed = ", ".join(sorted(str(one) for one in AUTHORITY[kind]))
        super().__init__(f"{actor} may not write {kind}; authorized: {allowed}")

        self.actor, self.kind = actor, kind


# ....................... #


def check_authority(actor: ActorKind, kind: EventKind) -> None:
    """RFC 0044 D-44.2. The service calls this before it builds a record, so
    an unauthorized write never reaches a store to be refused there — the
    rule is the domain's, and no adapter can be permissive on its own."""

    if actor not in AUTHORITY[kind]:
        raise UnauthorizedWrite(actor, kind)


# ....................... #


def validate_payload(kind: EventKind, payload: dict[str, Any]) -> BaseModel:
    """The payload as its kind's model. A write whose payload does not
    validate is a defect in the writer, not a record to keep: replay reads
    these back as models, and a stored payload that cannot be one makes the
    history unreadable exactly where it matters most."""

    return PAYLOADS[kind].model_validate(payload)
