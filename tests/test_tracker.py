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
    def __init__(self, reflect_outcome: str = "applied") -> None:
        self.reflected: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []
        self.commands: list[TrackerCommand] = []
        self.reflect_outcome = reflect_outcome

    def reflect(self, task_id: str, state: str, title: str) -> ReflectResult:
        self.reflected.append((task_id, state))
        return ReflectResult(self.reflect_outcome, "faked")

    def comment(self, task_id: str, body: str, key: str) -> ReflectResult:
        self.comments.append((task_id, body, key))
        return ReflectResult("applied")

    def annotate(self, task_id: str, location: str, body: str, key: str) -> ReflectResult:
        return ReflectResult("unsupported", "no inline annotations")

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
    report = poll_and_apply(root, tracker)
    assert report.outcomes[0].applied is True
    assert RunState.load(naming.state_file(root, "T-6004")).state is TaskState.QUEUED
    assert tracker.comments[-1][2] == "cmd:c1"  # answered on its thread


def test_a_command_against_the_wrong_state_is_refused_and_answered(root):
    run_state(root, "T-6005", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6005", "misery7100", "c2")]
    report = poll_and_apply(root, tracker)
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
    outcome = poll_and_apply(root, tracker).outcomes[0]
    assert outcome.applied is False and "not a legal exit" in outcome.detail


def test_an_unknown_verb_is_refused_by_the_fixed_vocabulary(root):
    run_state(root, "T-6007", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("redeploy", "T-6007", "anyone", "c4")]
    outcome = poll_and_apply(root, tracker).outcomes[0]
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
