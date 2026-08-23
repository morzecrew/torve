"""RFC 0008 phase 2: the projection — effects staged by (task_id, state,
attempt), a rerun delivering nothing, refusals as logged divergences, and
inbound commands as validated intents answered on their thread."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import torve.adapters.tracker.github as gh_module
from torve.adapters.tracker.github import GithubIssues
from torve.application.ports import ReflectResult, TrackerCommand
from torve.application.runstate import RunState
from torve.application.tracker import poll_and_apply, project, relay_to_tracker
from torve.base import naming
from torve.domain.states import TaskState


class FakeTracker:
    def __init__(self, reflect_outcome: str = "applied",
                 notify_outcome: str = "applied") -> None:
        self.reflected: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []
        self.notified: list[tuple[str, str, str, str]] = []
        self.commands: list[TrackerCommand] = []
        self.reflect_outcome = reflect_outcome
        self.notify_outcome = notify_outcome

    def reflect(self, task_id: str, state: str, title: str) -> ReflectResult:
        self.reflected.append((task_id, state))
        return ReflectResult(self.reflect_outcome, "faked")

    def comment(self, task_id: str, body: str, key: str) -> ReflectResult:
        self.comments.append((task_id, body, key))
        return ReflectResult("applied")

    def annotate(self, task_id: str, location: str, body: str, key: str) -> ReflectResult:
        return ReflectResult("unsupported", "no inline annotations")

    def notify(self, task_id: str, login: str, body: str, key: str) -> ReflectResult:
        self.notified.append((task_id, login, body, key))
        return ReflectResult(self.notify_outcome, "faked notify")

    def poll_commands(self) -> list[TrackerCommand]:
        return self.commands


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".torve").mkdir()
    return tmp_path


def run_state(root: Path, task_id: str, state: TaskState) -> RunState:
    record = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    record.state = state
    record.save()
    return record


def telemetry_records(root: Path) -> list[dict[str, object]]:
    path = root / ".torve" / "telemetry.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_projection_stages_by_key_and_a_rerun_delivers_nothing(root):
    run_state(root, "T-6001", TaskState.READY)
    tracker = FakeTracker()

    assert project(root) == 2  # created + current state
    first = relay_to_tracker(root, tracker)
    assert len(first.delivered) == 2
    assert ("T-6001", "created") in tracker.reflected
    assert ("T-6001", "ready") in tracker.reflected

    # Unchanged state: nothing new staged, nothing redelivered (§9's
    # deliberate replay of the relay).
    assert project(root) == 0
    again = relay_to_tracker(root, tracker)
    assert again.delivered == [] and len(again.skipped) == 2
    assert len(tracker.reflected) == 2


def test_an_escalation_projects_its_reason_and_detail(root):
    state = run_state(root, "T-6002", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.MERGE_CONFLICT, "rebase conflicts")
    tracker = FakeTracker()
    project(root)
    relay_to_tracker(root, tracker)
    assert ("T-6002", "escalated:merge_conflict") in tracker.reflected
    assert any("rebase conflicts" in body for _, body, _ in tracker.comments)


def test_a_refused_reflection_is_a_logged_divergence_not_an_exception(root):
    run_state(root, "T-6003", TaskState.READY)
    tracker = FakeTracker(reflect_outcome="refused")
    project(root)
    report = relay_to_tracker(root, tracker)
    # Delivered, not failed: the engine stays right whether or not the
    # board accepted it, and a refusal is never retried forever.
    assert len(report.delivered) == 2 and report.failed == {}
    events = [r for r in telemetry_records(root)
              if r.get("event") == "tracker_divergence"]
    assert len(events) == 2
    # Engine state untouched by the board's opinion.
    assert RunState.load(naming.state_file(root, "T-6003")).state is TaskState.READY


def test_retry_applies_only_to_an_escalated_run(root):
    state = run_state(root, "T-6004", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.POISON_CEILING, "3 attempts")
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6004", "misery7100", "c1")]
    report = poll_and_apply(root, tracker, ("misery7100",))
    assert report.outcomes[0].applied is True
    assert RunState.load(naming.state_file(root, "T-6004")).state is TaskState.QUEUED
    assert tracker.comments[-1][2] == "cmd:c1"  # answered on its thread


def test_a_command_against_the_wrong_state_is_refused_and_answered(root):
    run_state(root, "T-6005", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6005", "misery7100", "c2")]
    report = poll_and_apply(root, tracker, ("misery7100",))
    outcome = report.outcomes[0]
    assert outcome.applied is False and "escalated" in outcome.detail
    task_id, body, key = tracker.comments[-1]
    assert task_id == "T-6005" and body.startswith("refused") and key == "cmd:c2"
    # The board is a projection; the store did not move.
    assert RunState.load(naming.state_file(root, "T-6005")).state is TaskState.READY


def test_abandon_respects_the_transition_table(root):
    run_state(root, "T-6006", TaskState.RUNNING)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("abandon", "T-6006", "misery7100", "c3")]
    outcome = poll_and_apply(root, tracker, ("misery7100",)).outcomes[0]
    assert outcome.applied is False and "not a legal exit" in outcome.detail


def test_an_unknown_verb_is_refused_by_the_fixed_vocabulary(root):
    run_state(root, "T-6007", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("redeploy", "T-6007", "anyone", "c4")]
    outcome = poll_and_apply(root, tracker, ("anyone",)).outcomes[0]
    assert outcome.applied is False and "vocabulary" in outcome.detail


# ....................... #
# The GitHub adapter's untrusted-text parsing, over scripted gh output.


def scripted_gh(monkeypatch, responses: dict[str, str]):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        for marker, body in responses.items():
            if marker in " ".join(str(part) for part in command):
                return subprocess.CompletedProcess(command, 0, stdout=body, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    return calls


def test_github_poll_parses_allow_listed_commands_and_skips_answered(monkeypatch):
    issues = json.dumps([{"number": 7, "title": "T-6008: something"}])
    comments = json.dumps({"comments": [
        {"body": "/torve retry", "author": {"login": "misery7100"},
         "url": "https://x/#issuecomment-101"},
        {"body": "please redeploy everything now", "author": {"login": "rando"},
         "url": "https://x/#issuecomment-102"},
        {"body": "/torve abandon", "author": {"login": "misery7100"},
         "url": "https://x/#issuecomment-103"},
        {"body": "refused: abandon — no\n\n<!-- torve-key:cmd:103 -->",
         "author": {"login": "torve"}, "url": "https://x/#issuecomment-104"},
    ]})
    scripted_gh(monkeypatch, {"issue list": issues, "issue view": comments})

    commands = GithubIssues("example/lab", token_env=None).poll_commands()
    # The free-text plea is not a command (D-8.5); the answered abandon is
    # not returned again; the fresh retry is.
    assert [(c.verb, c.task_id, c.actor, c.source) for c in commands] == [
        ("retry", "T-6008", "misery7100", "101")]


# The notifier (RFC 0003 D-3.18, landed with 0006's policy): interrupt-class
# escalations page a person through the same outbox; batch stays a board
# comment. The invariant under test: every escalated task has exactly one
# delivered notification, whatever crashes and replays between state write
# and relay.


def test_an_interrupt_class_escalation_notifies_exactly_once(root):
    from torve.domain.states import EscalationReason

    state = run_state(root, "T-6101", TaskState.RUNNING)
    state.escalate(EscalationReason.BLOCKER_FINDING, "greet() modified")
    tracker = FakeTracker()
    project(root, notify_login="operator")
    relay_to_tracker(root, tracker)
    assert len(tracker.notified) == 1
    task_id, login, body, key = tracker.notified[0]
    assert (task_id, login) == ("T-6101", "operator")
    assert "blocker_finding" in body and "route: notify" in body
    assert ":notify:" in key

    # The crash landed between state write and relay: everything replays —
    # projection re-derives, the ledger absorbs the redelivery.
    project(root, notify_login="operator")
    relay_to_tracker(root, tracker)
    assert len(tracker.notified) == 1


def test_gate_infrastructure_pages_the_harness_owner(root):
    from torve.domain.states import EscalationReason

    state = run_state(root, "T-6102", TaskState.RUNNING)
    state.escalate(EscalationReason.GATE_INFRASTRUCTURE_FAILURE, "daemon gone")
    tracker = FakeTracker()
    project(root, notify_login="operator")
    relay_to_tracker(root, tracker)
    assert len(tracker.notified) == 1
    assert "route: harness owner" in tracker.notified[0][2]


def test_a_batch_route_escalation_stays_board_visible_only(root):
    from torve.domain.states import EscalationReason

    state = run_state(root, "T-6103", TaskState.RUNNING)
    state.escalate(EscalationReason.BUDGET_EXHAUSTED, "3 attempts spent")
    tracker = FakeTracker()
    project(root, notify_login="operator")
    relay_to_tracker(root, tracker)
    assert tracker.notified == []
    # Still on the board as an escalation comment (D-6.4: the rest batches).
    assert any("budget_exhausted" in body for _, body, _ in tracker.comments)


def test_an_empty_login_keeps_the_notifier_inert(root):
    from torve.domain.states import EscalationReason

    state = run_state(root, "T-6104", TaskState.RUNNING)
    state.escalate(EscalationReason.BLOCKER_FINDING, "blocker")
    tracker = FakeTracker()
    project(root)
    relay_to_tracker(root, tracker)
    assert tracker.notified == []


def test_a_refused_notification_is_a_logged_divergence(root):
    from torve.domain.states import EscalationReason

    state = run_state(root, "T-6105", TaskState.RUNNING)
    state.escalate(EscalationReason.LOCKED_CONFLICT, "contradicts a LOCKED row")
    tracker = FakeTracker(notify_outcome="refused")
    project(root, notify_login="operator")
    report = relay_to_tracker(root, tracker)
    # Done, not pending forever: the divergence is the record.
    assert report.failed == {}
    events = [r for r in telemetry_records(root)
              if r.get("event") == "tracker_divergence"]
    assert len(events) == 1 and ":notify:" in str(events[0].get("key"))


def test_github_notify_mentions_even_when_assignment_fails(monkeypatch):
    issues = json.dumps([{"number": 9, "title": "T-6009: task"}])
    calls: list[str] = []

    def fake_run(command, **kwargs):
        cmd = " ".join(str(part) for part in command)
        calls.append(cmd)
        if "--add-assignee" in cmd:
            return subprocess.CompletedProcess(command, 1, stdout="",
                                               stderr="could not assign")
        if "issue list" in cmd:
            return subprocess.CompletedProcess(command, 0, stdout=issues, stderr="")
        if "issue view" in cmd:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"comments": []}), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    result = GithubIssues("example/lab", token_env=None).notify(
        "T-6009", "operator", "escalated: blocker_finding — x",
        "T-6009:notify:1:blocker_finding")
    # The mention is the notification; a login the forge cannot assign must
    # not leave it pending forever.
    assert result.outcome == "applied" and "assign failed" in result.detail
    assert any("issue comment 9" in c and "@operator" in c for c in calls)


# Authorization precedes validation (T-0054): the board is an unattended
# command channel once the loop polls it.


def test_an_unconfigured_actor_is_refused_before_validation(root):
    state = run_state(root, "T-6110", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.POISON_CEILING, "3 attempts")
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6110", "rando", "c10")]
    outcome = poll_and_apply(root, tracker, ("misery7100",)).outcomes[0]
    # The retry would have validated — the actor did not.
    assert outcome.applied is False and "not a configured commander" in outcome.detail
    assert tracker.comments[-1][2] == "cmd:c10"  # still answered on-thread
    assert RunState.load(naming.state_file(root, "T-6110")).state is TaskState.ESCALATED


def test_an_empty_commander_list_refuses_everyone(root):
    state = run_state(root, "T-6111", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.POISON_CEILING, "3 attempts")
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6111", "misery7100", "c11")]
    outcome = poll_and_apply(root, tracker).outcomes[0]
    # Configuring nothing decides nothing — even the repo owner is refused
    # until named.
    assert outcome.applied is False and "not a configured commander" in outcome.detail


def test_a_transient_gh_failure_retries_exactly_once(monkeypatch):
    # T-0058: the scheduled ticks hit TLS handshake timeouts through the
    # local proxy — transient, never twice. One retry; the destination's
    # marker dedupe absorbs any at-least-once duplicate.
    outcomes = iter([
        subprocess.CompletedProcess([], 1, stdout="",
                                    stderr='Post "https://api.github.com/graphql": '
                                           "net/http: TLS handshake timeout"),
        subprocess.CompletedProcess([], 0, stdout="[]", stderr=""),
    ])
    monkeypatch.setattr(gh_module.subprocess, "run",
                        lambda *a, **k: next(outcomes))
    naps: list[float] = []
    board = GithubIssues("example/lab", token_env=None, sleeper=naps.append)
    assert board._gh("issue", "list") == "[]"
    assert naps == [2.0]


def test_a_real_gh_failure_raises_without_retry(monkeypatch):
    calls: list[int] = []

    def fake_run(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess([], 1, stdout="",
                                           stderr="GraphQL: Could not resolve issue")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    naps: list[float] = []
    board = GithubIssues("example/lab", token_env=None, sleeper=naps.append)
    with pytest.raises(RuntimeError, match="Could not resolve"):
        board._gh("issue", "view", "1")
    assert len(calls) == 1 and naps == []


def test_a_twice_transient_failure_raises_after_the_single_retry(monkeypatch):
    calls: list[int] = []

    def fake_run(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess([], 1, stdout="",
                                           stderr="connection reset by peer")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    board = GithubIssues("example/lab", token_env=None, sleeper=lambda _s: None)
    with pytest.raises(RuntimeError, match="connection reset"):
        board._gh("issue", "list")
    assert len(calls) == 2
