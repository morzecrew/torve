"""The event vocabulary's own guards (RFC 0044 §5.1, §5.2).

Three properties are worth a test even before anything produces an event: the
vocabulary is closed and complete, write authority refuses, and the
divergence words here are the same words the gate enforces. The third is the
one that rots silently — the gate owns those sets today and the intake will
validate against these, so a parity test is what keeps a "fix" on one side
from quietly disagreeing with the other.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from torve.domain.events import (
    AUTHORITY,
    PAYLOADS,
    ActorKind,
    DivergenceAction,
    DivergenceClass,
    DivergenceGrade,
    DivergenceKind,
    EventKind,
    UnauthorizedWrite,
    check_authority,
    validate_payload,
)
from torve.gates.decisions_reported import ACTIONS, CLASSES, GRADES, KINDS


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
    # agent reading it must be able to act on it (RFC 0044 D-44.10).
    assert str(unauthorized) in str(caught.value)
    assert str(kind) in str(caught.value)


def test_an_agent_may_never_sign_for_a_human():
    for kind in (EventKind.DECISION_ACCEPTED, EventKind.TASK_ADOPTED):
        assert AUTHORITY[kind] == frozenset({ActorKind.OPERATOR})

        with pytest.raises(UnauthorizedWrite):
            check_authority(ActorKind.AGENT, kind)


def test_divergence_vocabulary_matches_the_gate():
    assert set(get_args(DivergenceGrade)) == GRADES
    assert set(get_args(DivergenceKind)) == KINDS
    assert set(get_args(DivergenceClass)) == CLASSES
    assert set(get_args(DivergenceAction)) == ACTIONS


def test_a_payload_is_validated_against_its_kind():
    payload = {
        "attempt": 1,
        "decision_id": "D-44.2",
        "grade": "LOCKED",
        "entry_kind": "resolved",
        "entry_class": "spec-gap",
        "claim": "the authority table is the human signature",
        "evidence": "src/torve/domain/events.py — the AUTHORITY mapping",
        "action": "decided",
    }
    recorded = validate_payload(EventKind.DIVERGENCE_RECORDED, payload)

    assert recorded.model_dump()["decision_id"] == "D-44.2"

    with pytest.raises(ValidationError):
        validate_payload(EventKind.DIVERGENCE_RECORDED, {**payload, "entry_kind": "invented"})

    with pytest.raises(ValidationError):
        validate_payload(EventKind.DIVERGENCE_RECORDED, {**payload, "surprise": "extra"})
