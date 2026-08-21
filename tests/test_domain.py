from __future__ import annotations

import itertools

import pytest

from torve.domain import (
    TRANSITIONS,
    EscalationReason,
    IllegalTransition,
    TaskState,
    check_transition,
)
from torve.runstate import RunState


def test_the_happy_path_is_legal():
    order = [TaskState.QUEUED, TaskState.CLAIMED, TaskState.RUNNING,
             TaskState.GATED, TaskState.REVIEWED, TaskState.READY]
    for current, to in itertools.pairwise(order):
        check_transition(current, to)


def test_terminal_states_have_no_exit():
    assert TRANSITIONS[TaskState.READY] == frozenset()
    assert TRANSITIONS[TaskState.ABANDONED] == frozenset()


def test_illegal_transitions_raise():
    with pytest.raises(IllegalTransition):
        check_transition(TaskState.QUEUED, TaskState.RUNNING)
    with pytest.raises(IllegalTransition):
        check_transition(TaskState.READY, TaskState.QUEUED)


def test_escalated_goes_back_through_a_human():
    check_transition(TaskState.ESCALATED, TaskState.QUEUED)
    check_transition(TaskState.ESCALATED, TaskState.ABANDONED)
    with pytest.raises(IllegalTransition):
        check_transition(TaskState.ESCALATED, TaskState.RUNNING)


def test_attempts_increment_only_on_entry_to_running(tmp_path):
    state = RunState(task_id="T-1", path=tmp_path / "T-1.state.json")
    state.transition(TaskState.CLAIMED, "claim")
    assert state.attempts == 0
    state.transition(TaskState.RUNNING, "dispatch")
    assert state.attempts == 1
    state.transition(TaskState.GATED, "agent done")
    state.transition(TaskState.RUNNING, "retry")
    assert state.attempts == 2


def test_state_file_roundtrip(tmp_path):
    path = tmp_path / "T-2.state.json"
    state = RunState(task_id="T-2", path=path)
    state.transition(TaskState.CLAIMED, "claim")
    state.transition(TaskState.RUNNING, "dispatch")
    state.escalate(EscalationReason.POISON_CEILING, "3 attempts, ceiling 3")

    loaded = RunState.load(path)
    assert loaded.state is TaskState.ESCALATED
    assert loaded.attempts == 1
    assert loaded.escalation is not None
    assert loaded.escalation.reason == "poison_ceiling"
    assert [event["to"] for event in loaded.history][-1] == "escalated"
    assert loaded.heartbeat_age_s() < 60


def test_illegal_transition_does_not_corrupt_state(tmp_path):
    state = RunState(task_id="T-3", path=tmp_path / "T-3.state.json")
    with pytest.raises(IllegalTransition):
        state.transition(TaskState.READY, "skip everything")
    assert state.state is TaskState.QUEUED
    assert state.history == []


def test_every_escalation_reason_has_an_exit_code():
    # D-11.4: one taxonomy, two views — a new reason without a code (or a
    # code without a reason) must fail here, not in a caller's script.
    from torve.domain import EXIT_BY_REASON, EscalationReason

    assert set(EXIT_BY_REASON) == set(EscalationReason)
    assert all(2 <= code <= 5 for code in EXIT_BY_REASON.values())
