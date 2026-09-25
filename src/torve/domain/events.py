"""The event vocabulary (S-0044/the-event-log, §5.2): what the engine records and
who may record it.

The log is the system of record for intent and execution — every other view
of engine state is a projection rebuilt from it (S-0044/D-1). Events are written
at the moment the fact occurs and never derived afterwards (S-0044/D-3), which
is why the vocabulary is closed the way the escalation enum is closed: an
extensible kind list makes history incomparable across time, and a payload
that grew a field silently makes replay lie. Adding a kind is a document
amendment, not a code change.

Write authority is domain knowledge, not adapter policy (S-0044/D-2): the
authority table below is what makes the human signature S-0027 protected
survive the move of truth out of git. An agent cannot write an acceptance
whatever its prompt says, because the application service refuses the write
before any store sees it.

This module is deliberately storage-free. The record's envelope — identity,
timestamps, tenancy, ordering — belongs to the document aggregate the
persistence layer defines, and the service that enforces the rules below
sits above the document ports rather than beside them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from forze.domain.models import CreateDocumentCmd, Document, ReadDocument
from pydantic import BaseModel, Field

from torve.base.model import STRICT
from torve.domain.states import EscalationReason
from torve.domain.vocabulary import (
    EntryAction,
    EntryClass,
    EntryGrade,
    EntryKind,
    Grade,
    SourceKind,
)

# ----------------------- #

SCHEMA_VERSION = 1


# ....................... #


class ActorKind(StrEnum):
    """Who is writing. The operator is a human signature; the manager owns
    the queue; a worker executes; an agent speaks only through the intake
    its worker runs (S-0044/the-authority-rule)."""

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
    # S-0079/D-1: a night is a subject here rather than a file, a summary or
    # a counter — a manager killed at 04:00 has already written everything
    # the morning report reads, and an open with no close says unfinished.
    NIGHT = "night"


# ....................... #


class EventKind(StrEnum):
    SOURCE_IMPORTED = "source.imported"
    DECISION_RECORDED = "decision.recorded"
    DECISION_ACCEPTED = "decision.accepted"
    DECISION_RETIRED = "decision.retired"
    TASK_MINTED = "task.minted"
    TASK_ADOPTED = "task.adopted"
    TASK_CLAIMED = "task.claimed"
    TASK_RELEASED = "task.released"
    TASK_RETURNED = "task.returned"
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
    NOTIFICATION_SENT = "notification.sent"
    NIGHT_OPENED = "night.opened"
    NIGHT_CLOSED = "night.closed"


# ....................... #

# S-0044 §5.2. Acceptance and adoption are the human's alone — that pair is
# the whole of what the old git-holds-truth rule was protecting. Queue and
# landing facts belong to the manager because it is the only actor that can
# know them; execution facts belong to the worker for the same reason. An
# agent writes exactly two kinds, both through a validating intake, and both
# are statements about its own work rather than about the system's state.
AUTHORITY: dict[EventKind, frozenset[ActorKind]] = {
    EventKind.SOURCE_IMPORTED: frozenset({ActorKind.OPERATOR, ActorKind.MANAGER}),
    EventKind.DECISION_RECORDED: frozenset({ActorKind.OPERATOR, ActorKind.MANAGER}),
    EventKind.DECISION_ACCEPTED: frozenset({ActorKind.OPERATOR}),
    EventKind.DECISION_RETIRED: frozenset({ActorKind.OPERATOR, ActorKind.MANAGER}),
    EventKind.TASK_MINTED: frozenset({ActorKind.MANAGER}),
    EventKind.TASK_ADOPTED: frozenset({ActorKind.OPERATOR}),
    EventKind.TASK_CLAIMED: frozenset({ActorKind.MANAGER}),
    EventKind.TASK_RELEASED: frozenset({ActorKind.MANAGER}),
    # S-0044/A-13: sending a reviewed candidate back is the operator's alone, for
    # ESCALATION_RESOLVED's reason — an agent that could return its own
    # judged work could route around every verdict it disliked.
    EventKind.TASK_RETURNED: frozenset({ActorKind.OPERATOR}),
    EventKind.ATTEMPT_STARTED: frozenset({ActorKind.WORKER}),
    EventKind.ATTEMPT_FINISHED: frozenset({ActorKind.WORKER}),
    EventKind.GATES_EVALUATED: frozenset({ActorKind.WORKER}),
    EventKind.DIVERGENCE_RECORDED: frozenset({ActorKind.AGENT}),
    EventKind.REVIEW_RECORDED: frozenset({ActorKind.WORKER}),
    EventKind.BLOCKER_RAISED: frozenset({ActorKind.WORKER}),
    EventKind.LANDING_RECORDED: frozenset({ActorKind.MANAGER}),
    EventKind.ESCALATION_RAISED: frozenset({ActorKind.MANAGER, ActorKind.WORKER}),
    EventKind.ESCALATION_RESOLVED: frozenset({ActorKind.OPERATOR}),
    # S-0044/A-2: a note from the manager or the operator is the same record with
    # a different sender, and the sender is stamped rather than claimed.
    EventKind.MESSAGE_SENT: frozenset({ActorKind.AGENT, ActorKind.MANAGER, ActorKind.OPERATOR}),
    EventKind.SEAT_CONSUMED: frozenset({ActorKind.WORKER}),
    # S-0051/D-1: the relay runs inside the manager's pass, so the manager is
    # who delivered it. A worker never writes this — a page is the loop's
    # act, not an attempt's.
    EventKind.NOTIFICATION_SENT: frozenset({ActorKind.MANAGER}),
    # S-0079/D-1: opening and closing a night are the manager's acts, for the
    # queue facts' reason — the loop is the only actor that can know them.
    EventKind.NIGHT_OPENED: frozenset({ActorKind.MANAGER}),
    EventKind.NIGHT_CLOSED: frozenset({ActorKind.MANAGER}),
}


# ----------------------- #

# One payload model per kind (S-0044/D-1). Fields are what v1 already records in
# its telemetry and run states — transcription, not invention — and the
# record's own actor and subject are never repeated here.


class SourceImported(BaseModel):
    model_config = STRICT

    source_kind: SourceKind
    ref: str
    title: str = ""


# ....................... #


class DecisionRecorded(BaseModel):
    model_config = STRICT

    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)
    source_id: str
    supersedes: str | None = None
    # S-0054 S-0054/D-1: the reason the row exists and the command that
    # judges it travel with the row; a record written before carries neither.
    consequence: str = ""
    check: str | None = None


# ....................... #


class DecisionAccepted(BaseModel):
    model_config = STRICT

    note: str = ""


# ....................... #


class DecisionRetired(BaseModel):
    """S-0044/A-6: a decision leaving force is recorded, never inferred from a row
    that stopped appearing. Absence cannot tell a deliberate retirement from
    a table someone broke, and it means nothing at all for a source that is
    an incident rather than a file."""

    model_config = STRICT

    reason: str = ""
    superseded_by: str | None = None


# ....................... #


class TaskMinted(BaseModel):
    """S-0044/A-8: minting places a contract on a partition (§5.3), so the contract
    is what the event carries.

    `title` and `source_id` are the board's own derivations — a fallback
    chain and `spec or "operator"` (S-0059/D-3) — rather than contract fields. `phase` and
    `depends_on` are copies, kept because 192 mints written before this
    amendment carry them and nothing else, with a test pinning them equal to
    the contract's: the fold prefers the contract and falls back to them.
    """

    model_config = STRICT

    title: str
    source_id: str
    phase: int = 0
    depends_on: list[str] = Field(default_factory=list)
    # The `Task` model's own dump. Empty for a mint written before S-0044/A-8 —
    # readers treat that as a view with no contract rather than an error.
    contract: dict[str, Any] = Field(default_factory=dict)


# ....................... #


class TaskAdopted(BaseModel):
    model_config = STRICT

    note: str = ""


# ....................... #


class TaskClaimed(BaseModel):
    model_config = STRICT

    worker: str
    lease_seconds: int


# ....................... #


class TaskReleased(BaseModel):
    model_config = STRICT

    reason: str


# ....................... #


class TaskReturned(BaseModel):
    """A reviewed candidate sent back for revision (S-0044/A-13). Distinct from
    `TaskReleased`, which is a holder letting go of a lease: this is work
    that was done, judged and refused, and the two must stay tellable
    apart. `note` is what the next attempt is briefed with."""

    model_config = STRICT

    reason: str
    note: str = ""


# ....................... #


class AttemptStarted(BaseModel):
    model_config = STRICT

    attempt: int
    tier: str
    agent: str
    image_digest: str | None = None


# ....................... #


class AttemptFinished(BaseModel):
    """One attempt, as it ended (S-0044 S-0044/A-4).

    The fields below `cost_usd` are the attempt record the engine has always
    written to its telemetry stream, carried here because they are the same
    facts: what ran, what it cost, what it produced and how it ended. They
    are optional because an attempt that goes on to a gate pass has no
    ending of its own to describe — the gate's record is that attempt's —
    and because a stream written before this existed carries none of them.

    The interiors stay loose on purpose. `agent`, `transfer` and `results`
    have their own shapes elsewhere (the adapter's self-report, the
    runtime's booking, `GateResult`); restating them here would give the
    engine two definitions of one thing, which is the defect this phase
    exists to remove.
    """

    model_config = STRICT

    attempt: int
    exit_code: int | None = None
    timed_out: bool = False
    wall_time_s: float | None = None
    cost_usd: float | None = None

    # The attempt record, present on an ending that produced no gate pass.
    torve_version: str = ""
    config_hash: str | None = None
    verdict: str = ""
    escalation: str = ""
    gates_run: bool = True
    agent: dict[str, Any] = Field(default_factory=dict)
    transfer: dict[str, Any] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)


# ....................... #


class GatesEvaluated(BaseModel):
    """One gate pass, and the attempt it judged (S-0044 S-0044/A-4).

    Same rule as `AttemptFinished`: this is the record the telemetry stream
    has always carried for an attempt that reached its battery, written once
    and rendered into both carriers. `config_hash` is the regime the pass
    ran under — the identity every comparison between numbers keys on — and
    `results` is the battery's own output, not a summary of it.
    """

    model_config = STRICT

    attempt: int
    exit_code: int
    config_hash: str | None = None
    torve_version: str = ""
    verdict: str = ""
    base: str = ""
    merge_base: str = ""
    head: str = ""
    gates_run: bool = True
    agent: dict[str, Any] = Field(default_factory=dict)
    transfer: dict[str, Any] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    bypass_count_by_gate: dict[str, int] = Field(default_factory=dict)
    flaky_count_by_command: dict[str, int] = Field(default_factory=dict)


# ....................... #


def gate_outcomes(payload: Mapping[str, Any]) -> dict[str, str]:
    """`{gate: outcome}` from a gates.evaluated payload. Derived where it is
    read rather than stored beside `results`: a summary recorded next to
    what it summarises is a second copy that can disagree."""

    return {
        str(one.get("name") or ""): str(one.get("outcome") or "")
        for one in payload.get("results", [])
    }


# ....................... #

# The v1 divergence entry, transcribed (S-0001/decisions, the flag-dont-flip
# vocabulary the decisions-reported gate enforces) — the log's own words, from
# the one vocabulary (S-0059/D-5), so the record and the gate cannot drift.


class DivergenceRecorded(BaseModel):
    model_config = STRICT

    attempt: int
    decision_id: str
    grade: EntryGrade
    entry_kind: EntryKind
    entry_class: EntryClass
    claim: str
    evidence: str
    action: EntryAction
    proposal: str = ""
    notes: str = ""


# ....................... #


class ReviewRecorded(BaseModel):
    model_config = STRICT

    review_id: str
    findings: int = 0
    blockers: int = 0


# ....................... #


class BlockerRaised(BaseModel):
    model_config = STRICT

    review_id: str
    claim: str


# ....................... #


class LandingRecorded(BaseModel):
    model_config = STRICT

    sha: str
    attempt: int


# ....................... #


class EscalationRaised(BaseModel):
    model_config = STRICT

    reason: EscalationReason
    detail: str = ""


# ....................... #


class EscalationResolved(BaseModel):
    model_config = STRICT

    resolution: Literal["requeued", "abandoned", "landed"]
    note: str = ""
    # `landed`: the commit the hand finish landed as, which is what a
    # dependent's cut is checked against (S-0085/D-4) — a landing with no
    # sha satisfies no dependency.
    sha: str = ""


# ....................... #


class MessageSent(BaseModel):
    """S-0044 S-0044/D-4: an agent influences another agent only as a record a
    human can read. There is no channel that bypasses this model."""

    model_config = STRICT

    to_role: str
    topic: str
    body: str


# ....................... #


class SeatConsumed(BaseModel):
    model_config = STRICT

    seat: str
    tokens: int | None = None
    cost_usd: float | None = None


# ....................... #


class NotificationSent(BaseModel):
    """One delivery of one escalation (S-0051 S-0051/D-1).

    `subject` is the escalation event's own id, which is both what makes
    this a delivery *of* something and the idempotency key the destination
    dedups on (S-0051/D-6). `receipt` is the destination's own handle for what
    it accepted — a delivery nobody can point at afterwards is
    indistinguishable from one that never happened.

    Every attempt is recorded, not only the ones that worked (S-0051/A-1):
    `outcome` is `delivered`, `retrying` when the destination might take it
    later, or `failed` once the attempts are spent. The count of rows is
    what the retry ceiling is derived from, so a relay that recorded only
    successes could never reach it.

    `delivered` and `failed` both drain the queue (S-0051/D-7) — an escalation
    nobody could page about is still on the board where it always was.
    """

    model_config = STRICT

    subject: str
    destination: str
    outcome: Literal["delivered", "retrying", "failed"] = "delivered"
    attempt: int = 1
    receipt: str = ""
    detail: str = ""


# ....................... #


class NightOpened(BaseModel):
    """The night's terms, whole (S-0079/D-2).

    Read once at the open and never re-read: the report says what the night
    was started with even if the configuration was edited while it ran, and
    two nights are comparable because their terms are recorded rather than
    reconstructed from whatever the file says afterwards.

    `queue` is the ready queue as it stood — the task ids, not a count, so
    a night that drained can be told from one that never had the work.
    `budget_usd` and `budget_attempts` are the two axes; at most one may be
    absent, which the configuration refuses at load rather than here
    (S-0079/D-5). `stop_on` is the escalation classes the operator named
    (S-0079/D-7), and `knobs` is the resolved value of each night knob under
    whatever name its harness spells it (S-0079/D-9) — torve records these
    and interprets none of them.
    """

    model_config = STRICT

    queue: list[str] = Field(default_factory=list)
    # S-0079/D-11: recorded as a term of the night and today it is one; a
    # later night at width three is comparable against tonight's.
    width: int = 1
    budget_usd: float | None = None
    budget_attempts: int | None = None
    # The wall-clock end, in minutes from the open (S-0079/D-12): a term of the
    # night like the two budgets, absent when the night has no end but the queue.
    minutes: int | None = None
    stop_on: list[EscalationReason] = Field(default_factory=list)
    lease_seconds: int
    knobs: dict[str, str] = Field(default_factory=dict)


# ....................... #


class NightClosed(BaseModel):
    """A night that ended, and on which of its own terms (S-0079/D-1).

    Thin on purpose: the morning report is a projection of the window
    computed on every call (S-0079/D-4), so every count a close could carry
    would be a second copy of what the window already holds. What only the
    close can say is which bound was reached — and `wall_clock` is recorded
    as reached at the moment it is, however far inside a pass the end fell,
    because the end is a soft bound and the record has to say so
    (S-0079/D-12).
    """

    model_config = STRICT

    reason: Literal["drained", "budget_usd", "budget_attempts", "wall_clock", "escalation"]
    # The escalation class that stopped the night, for `escalation`.
    detail: str = ""
    # What the night did and how late it closed (S-0079/D-12): the attempts it
    # handled, and the seconds past its wall-clock end the close landed — the
    # end is soft, so an attempt in flight is never interrupted and the overrun
    # is the record of that.
    handled: int = 0
    overran_seconds: float = 0.0


# ....................... #

PAYLOADS: dict[EventKind, type[BaseModel]] = {
    EventKind.SOURCE_IMPORTED: SourceImported,
    EventKind.DECISION_RECORDED: DecisionRecorded,
    EventKind.DECISION_ACCEPTED: DecisionAccepted,
    EventKind.DECISION_RETIRED: DecisionRetired,
    EventKind.TASK_MINTED: TaskMinted,
    EventKind.TASK_ADOPTED: TaskAdopted,
    EventKind.TASK_CLAIMED: TaskClaimed,
    EventKind.TASK_RELEASED: TaskReleased,
    EventKind.TASK_RETURNED: TaskReturned,
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
    EventKind.NOTIFICATION_SENT: NotificationSent,
    EventKind.NIGHT_OPENED: NightOpened,
    EventKind.NIGHT_CLOSED: NightClosed,
}


# ----------------------- #

# The aggregate (S-0044/the-event-log). Identity, revision and timestamps come from
# the document base — `created_at` is the event's own clock, so the record
# carries no second one. There is deliberately no update command: a spec
# without one exposes no update port at all, which is how "append-only" is
# enforced here rather than by everyone remembering not to call it. A
# correction is another event.


class EventDoc(Document):
    schema_version: int = SCHEMA_VERSION
    kind: EventKind
    partition: str
    subject_type: SubjectType
    subject_id: str
    actor_kind: ActorKind
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None


# ....................... #


class CreateEventCmd(CreateDocumentCmd):
    schema_version: int = SCHEMA_VERSION
    kind: EventKind
    partition: str
    subject_type: SubjectType
    subject_id: str
    actor_kind: ActorKind
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None


# ....................... #


class EventRecord(ReadDocument):
    schema_version: int = SCHEMA_VERSION
    kind: EventKind
    partition: str
    subject_type: SubjectType
    subject_id: str
    actor_kind: ActorKind
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None

    # ....................... #

    def typed_payload(self) -> BaseModel:
        return validate_payload(self.kind, self.payload)


# ----------------------- #


class UnauthorizedWrite(Exception):
    def __init__(self, actor: ActorKind, kind: EventKind) -> None:
        allowed = ", ".join(sorted(str(one) for one in AUTHORITY[kind]))
        super().__init__(f"{actor} may not write {kind}; authorized: {allowed}")

        self.actor, self.kind = actor, kind


# ....................... #


def check_authority(actor: ActorKind, kind: EventKind) -> None:
    """S-0044 S-0044/D-2. The service calls this before it builds a record, so
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
