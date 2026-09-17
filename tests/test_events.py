"""The event vocabulary's own guards (S-0044/the-event-log, §5.2).

Three properties are worth a test even before anything produces an event: the
vocabulary is closed and complete, write authority refuses, and the
divergence words here are the same words the gate enforces. The third is the
one that rots silently — the gate owns those sets today and the intake will
validate against these, so a parity test is what keeps a "fix" on one side
from quietly disagreeing with the other.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.domain.events import (
    AUTHORITY,
    PAYLOADS,
    ActorKind,
    EventKind,
    UnauthorizedWrite,
    check_authority,
    validate_payload,
)


def test_every_kind_has_an_authority_row_and_a_payload_model():
    assert set(EventKind) == set(AUTHORITY)
    assert set(EventKind) == set(PAYLOADS)


def test_no_kind_is_writable_by_everyone():
    # A row authorizing every actor is a row that decided nothing.
    for kind, allowed in AUTHORITY.items():
        assert allowed, f"{kind} authorizes nobody"
        assert allowed != set(ActorKind), f"{kind} authorizes everyone"


@pytest.mark.parametrize("kind", list(EventKind))
def test_an_unauthorized_actor_is_refused(kind):
    unauthorized = next(actor for actor in ActorKind if actor not in AUTHORITY[kind])

    with pytest.raises(UnauthorizedWrite) as caught:
        check_authority(unauthorized, kind)

    # The refusal names the actor and what would have been allowed — an
    # agent reading it must be able to act on it (S-0044 S-0044/D-10).
    assert str(unauthorized) in str(caught.value)
    assert str(kind) in str(caught.value)


def test_an_agent_may_never_sign_for_a_human():
    for kind in (EventKind.DECISION_ACCEPTED, EventKind.TASK_ADOPTED):
        assert AUTHORITY[kind] == frozenset({ActorKind.OPERATOR})

        with pytest.raises(UnauthorizedWrite):
            check_authority(ActorKind.AGENT, kind)


def test_a_payload_is_validated_against_its_kind():
    payload = {
        "attempt": 1,
        "decision_id": "S-0044/D-2",
        "grade": "LOCKED",
        "entry_kind": "resolved",
        "entry_class": "spec-gap",
        "claim": "the authority table is the human signature",
        "evidence": "src/torve/domain/events.py — the AUTHORITY mapping",
        "action": "decided",
    }
    recorded = validate_payload(EventKind.DIVERGENCE_RECORDED, payload)

    assert recorded.model_dump()["decision_id"] == "S-0044/D-2"

    with pytest.raises(ValidationError):
        validate_payload(EventKind.DIVERGENCE_RECORDED, {**payload, "entry_kind": "invented"})

    with pytest.raises(ValidationError):
        validate_payload(EventKind.DIVERGENCE_RECORDED, {**payload, "surprise": "extra"})


def test_decision_recorded_carries_consequence_and_check_and_defaults_them():
    """S-0054 S-0054/D-1: the payload gains the reason and the command; a
    record written before carries neither and still loads."""

    from torve.domain.events import DecisionRecorded

    old = DecisionRecorded.model_validate(
        {"grade": "LOCKED", "text": "x", "paths": [], "source_id": "S-0001"}
    )
    new = DecisionRecorded.model_validate(
        {
            "grade": "LOCKED",
            "text": "x",
            "paths": [],
            "source_id": "S-0001",
            "consequence": "why",
            "check": "true",
        }
    )

    assert old.consequence == "" and old.check is None
    assert new.consequence == "why" and new.check == "true"


def test_night_opened_carries_the_terms_whole():
    """S-0079/D-2: the queue as it stood, the width, both budget axes, the
    stop conditions, the lease and the resolved knobs — nothing else."""

    from torve.domain.events import AUTHORITY, ActorKind, NightOpened

    terms = NightOpened.model_validate(
        {
            "queue": ["T-0001", "T-0002"],
            "width": 1,
            "budget_usd": 40.0,
            "budget_attempts": 60,
            "stop_on": ["locked_conflict"],
            "lease_seconds": 3600,
            "knobs": {"TORVE_NIGHT_FALLBACK_MODEL": "claude-sonnet-5"},
        }
    )

    assert terms.queue == ["T-0001", "T-0002"]
    assert (terms.budget_usd, terms.budget_attempts) == (40.0, 60)
    assert [str(one) for one in terms.stop_on] == ["locked_conflict"]
    assert terms.knobs["TORVE_NIGHT_FALLBACK_MODEL"] == "claude-sonnet-5"

    # Both night kinds are the manager's alone (S-0079/D-1).
    for kind in (EventKind.NIGHT_OPENED, EventKind.NIGHT_CLOSED):
        assert AUTHORITY[kind] == frozenset({ActorKind.MANAGER})


def test_a_night_stop_class_must_be_one_the_engine_escalates():
    """A misspelt class is refused at the write, as it is at load
    (S-0079/D-5) — a stop condition naming nothing never fires."""

    payload = {"lease_seconds": 3600, "stop_on": ["locked_conflict"]}

    assert validate_payload(EventKind.NIGHT_OPENED, payload)

    with pytest.raises(ValidationError):
        validate_payload(EventKind.NIGHT_OPENED, {**payload, "stop_on": ["locked_confilct"]})

    assert validate_payload(EventKind.NIGHT_CLOSED, {"reason": "drained"})

    with pytest.raises(ValidationError):
        validate_payload(EventKind.NIGHT_CLOSED, {"reason": "morning"})
