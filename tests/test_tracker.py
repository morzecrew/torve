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
from torve.application.tracker import (
    poll_and_apply,
    project,
    project_landings,
    relay_to_tracker,
)
from torve.base import naming
from torve.domain.states import TaskState


class FakeTracker:
    def __init__(self, reflect_outcome: str = "applied", notify_outcome: str = "applied") -> None:
        self.reflected: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []
        self.unlabelled: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []
        self.notified: list[tuple[str, str, str, str]] = []
        self.commands: list[TrackerCommand] = []
        self.reflect_outcome = reflect_outcome
        self.notify_outcome = notify_outcome

    def reflect(self, task_id: str, state: str, title: str) -> ReflectResult:
        self.reflected.append((task_id, state))
        return ReflectResult(self.reflect_outcome, "faked")

    def label(self, task_id: str, name: str) -> ReflectResult:
        self.labelled.append((task_id, name))
        return ReflectResult("applied", "faked label")

    def unlabel(self, task_id: str, name: str) -> ReflectResult:
        self.unlabelled.append((task_id, name))
        return ReflectResult("applied", "faked unlabel")

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


def contract(
    root: Path, task_id: str, role: str = "implement", targets: list[str] | None = None
) -> None:
    folder = root / ".torve" / "tasks" / task_id
    folder.mkdir(parents=True)
    lines = [
        f"schema_version: 1\nid: {task_id}\nrole: {role}\n",
        "intent: close-out probe\nscope:\n  allow: ['src/**']\n  deny: []\n",
        "decisions: []\n",
    ]
    if targets:
        lines.append(f"targets: [{', '.join(targets)}]\n")
    (folder / "contract.yaml").write_text("".join(lines), encoding="utf-8")


def test_a_landed_task_with_no_state_closes_its_issue_once(root):
    # D-8.11: the run state is swept after a landing, so the landings
    # pass consults the trailer — one close-out effect per task, ever.
    contract(root, "T-6101")
    tracker = FakeTracker()

    assert project_landings(root, lambda t: t == "T-6101") == 1
    relay_to_tracker(root, tracker)
    assert ("T-6101", "landed") in tracker.reflected

    assert project_landings(root, lambda t: t == "T-6101") == 0  # ever
    again = relay_to_tracker(root, tracker)
    assert again.delivered == []


def test_a_live_or_unlanded_task_is_not_closed(root):
    contract(root, "T-6102")
    run_state(root, "T-6102", TaskState.READY)  # live state owns it
    contract(root, "T-6103")  # no landing trailer
    assert project_landings(root, lambda t: t == "T-6102") == 0


def test_an_approvals_short_candidate_prompts_once_per_tip(root):
    # D-8.13: the board says where the human is needed — one prompt per
    # tip, and a superseded tip prompts afresh.
    from torve.application.tracker import project_approval_gap

    contract(root, "T-6120")
    tracker = FakeTracker()
    assert project_approval_gap(root, "T-6120", "a" * 40, 1) is True
    assert project_approval_gap(root, "T-6120", "a" * 40, 1) is False  # same tip
    relay_to_tracker(root, tracker)
    assert ("T-6120", "needs:approval") in tracker.labelled
    prompts = [b for t, b, _ in tracker.comments if t == "T-6120"]
    assert len(prompts) == 1 and "/torve approve" in prompts[0]

    assert project_approval_gap(root, "T-6120", "b" * 40, 1) is True  # new tip
    relay_to_tracker(root, tracker)
    assert len([b for t, b, _ in tracker.comments if t == "T-6120"]) == 2


def test_an_applied_approval_retires_the_needs_approval_label(root):
    # D-8.17 (A-36): the approval the prompt asked for arrived — the
    # label's removal rides the same outbox at once.
    from torve.application.tracker import project_approval_gap

    contract(root, "T-6140")
    run_state(root, "T-6140", TaskState.READY)
    project_approval_gap(root, "T-6140", "a" * 40, 1)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("approve", "T-6140", "misery7100", "c40")]
    poll_and_apply(root, tracker, ("misery7100",), approve_tip=lambda _t: "a" * 40)
    relay_to_tracker(root, tracker)
    assert ("T-6140", "needs:approval") in tracker.unlabelled


def test_a_run_outside_ready_clears_a_worn_prompt_only(root):
    # D-8.17 (A-36): a re-queued run has no approval to want — cleared
    # once per transition, and never staged for tasks never prompted.
    from torve.application.tracker import project_approval_gap

    contract(root, "T-6141")
    project_approval_gap(root, "T-6141", "a" * 40, 1)
    run_state(root, "T-6141", TaskState.QUEUED)
    contract(root, "T-6142")  # never prompted
    run_state(root, "T-6142", TaskState.QUEUED)
    tracker = FakeTracker()
    project(root)
    relay_to_tracker(root, tracker)
    assert ("T-6141", "needs:approval") in tracker.unlabelled
    assert all(t != "T-6142" for t, _ in tracker.unlabelled)


def test_the_landings_pass_clears_prompts_retroactively(root):
    # D-8.17 (A-36): the backstop — a task that landed before the
    # amendment still wears its label; the ledger remembers the prompt.
    from torve.application.outbox import Effect, stage
    from torve.application.tracker import project_approval_gap

    contract(root, "T-6143")
    project_approval_gap(root, "T-6143", "a" * 40, 1)
    stage(
        root,
        Effect(
            key="T-6143:landed", kind="landed", payload={"task": "T-6143", "title": "T-6143: t"}
        ),
    )
    tracker = FakeTracker()
    assert project_landings(root, lambda _t: True) == 1
    relay_to_tracker(root, tracker)
    assert ("T-6143", "needs:approval") in tracker.unlabelled


def test_github_unlabel_absorbs_an_absent_label(monkeypatch):
    # D-8.17: removal is idempotent — an absent label, like an absent
    # issue, is the postcondition already holding.
    issue = json.dumps([{"number": 5, "title": "T-6144: probe"}])
    calls = scripted_gh(monkeypatch, {"issue list": issue})
    result = GithubIssues("o/r", token_env=None).unlabel("T-6144", "needs:approval")
    assert result.outcome == "applied"
    assert any("--remove-label" in " ".join(c) for c in calls)

    bare = scripted_gh(monkeypatch, {"issue list": "[]"})
    missing = GithubIssues("o/r", token_env=None).unlabel("T-6145", "x")
    assert missing.outcome == "applied"
    assert missing.detail == "no issue to unlabel"
    assert not any("--remove-label" in " ".join(c) for c in bare)

    # A label the issue never wore: the forge's refusal is absorbed.
    adapter = GithubIssues("o/r", token_env=None)
    adapter._issues["T-6146"] = 6
    monkeypatch.setattr(adapter, "_gh", _raise_not_found)
    worn_off = adapter.unlabel("T-6146", "needs:approval")
    assert worn_off.outcome == "applied" and "already absent" in worn_off.detail


def _raise_not_found(*_args: str) -> str:
    raise RuntimeError("label 'needs:approval' not found")


def test_a_review_task_projects_no_issue(root):
    # D-8.16 (A-33): a board row exists to solicit human input, and a
    # review never needs any — no created, no state label, nothing.
    contract(root, "T-6121", role="review", targets=["T-6120"])
    run_state(root, "T-6121", TaskState.RUNNING)
    tracker = FakeTracker()
    project(root)
    relay_to_tracker(root, tracker)
    assert not any(t == "T-6121" for t, _ in tracker.reflected)
    assert tracker.labelled == []


def test_a_review_milestone_reaches_the_targets_thread(root):
    # The review's escalation notifies where the retry/abandon decision
    # lives — on the work's issue, attributed to the review.
    contract(root, "T-6130")
    contract(root, "T-6131", role="review", targets=["T-6130"])
    state = run_state(root, "T-6131", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.BLOCKER_FINDING, "unlocatable evidence")
    tracker = FakeTracker()
    project(root, notify_login="misery7100")
    relay_to_tracker(root, tracker)
    assert ("T-6130", "escalated:blocker_finding") in tracker.reflected
    assert not any(t == "T-6131" for t, _ in tracker.reflected)
    assert any(t == "T-6130" and "review T-6131" in b for t, _login, b, _k in tracker.notified)


def test_a_revisited_state_is_reflected_again(root):
    # A-30: ready → escalated → ready at the same attempt must re-reflect
    # — the board would otherwise wear yesterday's escalation label over
    # a ready candidate — while replays between transitions stay silent.
    state = run_state(root, "T-6140", TaskState.READY)
    tracker = FakeTracker()
    project(root)
    relay_to_tracker(root, tracker)
    project(root)
    relay_to_tracker(root, tracker)  # replay: nothing new
    assert tracker.reflected.count(("T-6140", "ready")) == 1

    # The revisit is a longer history at the same state and attempt —
    # the shape a retry's excursion leaves behind.
    state.history.append({"at": "t", "from": "ready", "to": "escalated", "fact": "conflict"})
    state.history.append({"at": "t", "from": "escalated", "to": "queued", "fact": "retry"})
    state.save()
    project(root)
    relay_to_tracker(root, tracker)
    assert tracker.reflected.count(("T-6140", "ready")) == 2


def test_approve_refuses_a_review_task(root):
    contract(root, "T-6123", role="review", targets=["T-6120"])
    run_state(root, "T-6123", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("approve", "T-6123", "misery7100", "c9")]
    outcome = poll_and_apply(
        root, tracker, ("misery7100",), approve_tip=lambda _t: "c" * 40
    ).outcomes[0]
    assert outcome.applied is False and "review" in outcome.detail
    assert RunState.load(naming.state_file(root, "T-6123")).approvals == []


def test_a_review_is_discharged_by_its_targets_landing(root):
    # T-0066: a review never lands; its repo-recorded discharge is the
    # landing of what it reviewed — every target, or it stays open.
    contract(root, "T-6110", role="review", targets=["T-6101"])
    contract(root, "T-6111", role="review", targets=["T-6101", "T-6103"])
    contract(root, "T-6112", role="review")  # no targets: never closed
    tracker = FakeTracker()
    project_landings(root, lambda t: t == "T-6101")
    relay_to_tracker(root, tracker)
    assert tracker.reflected == [("T-6110", "landed")]  # 6111/6112 stay open
    assert project_landings(root, lambda t: t == "T-6101") == 0  # replay


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
    events = [r for r in telemetry_records(root) if r.get("event") == "tracker_divergence"]
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
    comments = json.dumps(
        {
            "comments": [
                {
                    "body": "/torve retry",
                    "author": {"login": "misery7100"},
                    "url": "https://x/#issuecomment-101",
                },
                {
                    "body": "please redeploy everything now",
                    "author": {"login": "rando"},
                    "url": "https://x/#issuecomment-102",
                },
                {
                    "body": "/torve abandon",
                    "author": {"login": "misery7100"},
                    "url": "https://x/#issuecomment-103",
                },
                {
                    "body": "refused: abandon — no\n\n<!-- torve-key:cmd:103 -->",
                    "author": {"login": "torve"},
                    "url": "https://x/#issuecomment-104",
                },
            ]
        }
    )
    scripted_gh(monkeypatch, {"issue list": issues, "issue view": comments})

    commands = GithubIssues("example/lab", token_env=None).poll_commands()
    # The free-text plea is not a command (D-8.5); the answered abandon is
    # not returned again; the fresh retry is.
    assert [(c.verb, c.task_id, c.actor, c.source) for c in commands] == [
        ("retry", "T-6008", "misery7100", "101")
    ]


# The notifier (RFC 0003 D-3.18, landed with 0006's policy): interrupt-class
# escalations page a person through the same outbox; batch stays a board
# comment. The invariant under test: every escalated task has exactly one
# delivered notification, whatever crashes and replays between state write
# and relay.


def test_github_reflect_landed_closes_but_never_creates(monkeypatch):
    # With an issue: labelled state:landed and closed. Without one: the
    # close-out is a no-op — an issue is never created just to be closed.
    issue = json.dumps([{"number": 9, "title": "T-6104: probe"}])
    labels = json.dumps({"labels": [{"name": "state:ready"}]})
    calls = scripted_gh(monkeypatch, {"issue list": issue, "issue view": labels})
    adapter = GithubIssues("o/r", token_env=None)
    result = adapter.reflect("T-6104", "landed", "T-6104: probe")
    assert result.outcome == "applied" and "closed" in result.detail
    joined = [" ".join(c) for c in calls]
    assert any("issue close 9" in c for c in joined)

    bare = scripted_gh(monkeypatch, {"issue list": "[]"})
    missing = GithubIssues("o/r", token_env=None).reflect("T-6105", "landed", "t")
    assert missing.detail == "no issue to close"
    assert not any("issue create" in " ".join(c) for c in bare)


def test_github_reflect_retires_stale_state_labels(monkeypatch):
    # D-8.12: the board wears one state label at a time; non-state labels
    # are untouched.
    issue = json.dumps([{"number": 7, "title": "T-6106: probe"}])
    labels = json.dumps(
        {
            "labels": [
                {"name": "state:escalated"},
                {"name": "state:escalated:poison_ceiling"},
                {"name": "priority"},
            ]
        }
    )
    calls = scripted_gh(monkeypatch, {"issue list": issue, "issue view": labels})
    GithubIssues("o/r", token_env=None).reflect("T-6106", "ready", "T-6106: probe")
    joined = [" ".join(c) for c in calls]
    assert any("--remove-label state:escalated " in c + " " for c in joined)
    assert any("--remove-label state:escalated:poison_ceiling" in c for c in joined)
    assert any("--add-label state:ready" in c for c in joined)
    assert not any("--remove-label priority" in c for c in joined)


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
    events = [r for r in telemetry_records(root) if r.get("event") == "tracker_divergence"]
    assert len(events) == 1 and ":notify:" in str(events[0].get("key"))


def test_github_notify_mentions_even_when_assignment_fails(monkeypatch):
    issues = json.dumps([{"number": 9, "title": "T-6009: task"}])
    calls: list[str] = []

    def fake_run(command, **kwargs):
        cmd = " ".join(str(part) for part in command)
        calls.append(cmd)
        if "--add-assignee" in cmd:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="could not assign")
        if "issue list" in cmd:
            return subprocess.CompletedProcess(command, 0, stdout=issues, stderr="")
        if "issue view" in cmd:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"comments": []}), stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    result = GithubIssues("example/lab", token_env=None).notify(
        "T-6009", "operator", "escalated: blocker_finding — x", "T-6009:notify:1:blocker_finding"
    )
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
    outcomes = iter(
        [
            subprocess.CompletedProcess(
                [],
                1,
                stdout="",
                stderr='Post "https://api.github.com/graphql": net/http: TLS handshake timeout',
            ),
            subprocess.CompletedProcess([], 0, stdout="[]", stderr=""),
        ]
    )
    monkeypatch.setattr(gh_module.subprocess, "run", lambda *a, **k: next(outcomes))
    naps: list[float] = []
    board = GithubIssues("example/lab", token_env=None, sleeper=naps.append)
    assert board._gh("issue", "list") == "[]"
    assert naps == [2.0]


def test_a_real_gh_failure_raises_without_retry(monkeypatch):
    calls: list[int] = []

    def fake_run(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess(
            [], 1, stdout="", stderr="GraphQL: Could not resolve issue"
        )

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
        return subprocess.CompletedProcess([], 1, stdout="", stderr="connection reset by peer")

    monkeypatch.setattr(gh_module.subprocess, "run", fake_run)
    board = GithubIssues("example/lab", token_env=None, sleeper=lambda _s: None)
    with pytest.raises(RuntimeError, match="connection reset"):
        board._gh("issue", "list")
    assert len(calls) == 2


# The retry command completes its own re-queue (T-0059): the stale remote
# branch goes at apply time, before the state moves.


def test_retry_runs_the_requeue_cleanup_before_the_transition(root):
    state = run_state(root, "T-6120", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.MERGE_CONFLICT, "rebase conflicts")
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6120", "misery7100", "c20")]
    cleaned: list[str] = []

    def requeue(task_id: str) -> str:
        # The state must still be escalated when cleanup runs.
        assert RunState.load(naming.state_file(root, task_id)).state is TaskState.ESCALATED
        cleaned.append(task_id)
        return "remote branch deleted"

    outcome = poll_and_apply(root, tracker, ("misery7100",), requeue).outcomes[0]
    assert outcome.applied and "remote branch deleted" in outcome.detail
    assert cleaned == ["T-6120"]
    assert RunState.load(naming.state_file(root, "T-6120")).state is TaskState.QUEUED


def test_a_failed_requeue_cleanup_refuses_and_leaves_the_escalation(root):
    state = run_state(root, "T-6121", TaskState.RUNNING)
    from torve.domain.states import EscalationReason

    state.escalate(EscalationReason.MERGE_CONFLICT, "rebase conflicts")
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("retry", "T-6121", "misery7100", "c21")]

    def requeue(task_id: str) -> str:
        raise RuntimeError("origin unreachable")

    outcome = poll_and_apply(root, tracker, ("misery7100",), requeue).outcomes[0]
    assert not outcome.applied and "origin unreachable" in outcome.detail
    # The escalation stands; the command is retryable.
    assert RunState.load(naming.state_file(root, "T-6121")).state is TaskState.ESCALATED


# approve joins the vocabulary (T-0061): sha-bound, ready-only, idempotent.


def test_revise_requeues_a_ready_candidate_with_capture_first(root):
    # D-8.18 (A-40): a review finding worth another attempt — the same
    # capture-first cleanup as retry, then the ready → queued edge.
    contract(root, "T-6150")
    run_state(root, "T-6150", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("revise", "T-6150", "misery7100", "c50")]
    captured: list[str] = []

    def requeue(task_id: str) -> str:
        captured.append(task_id)
        return "branch kept; feedback captured"

    outcome = poll_and_apply(root, tracker, ("misery7100",), requeue=requeue).outcomes[0]
    assert outcome.applied and "feedback captured" in outcome.detail
    assert captured == ["T-6150"]
    assert RunState.load(naming.state_file(root, "T-6150")).state is TaskState.QUEUED


def test_revise_refuses_off_ready_and_review_roles(root):
    contract(root, "T-6151")
    run_state(root, "T-6151", TaskState.RUNNING)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("revise", "T-6151", "misery7100", "c51")]
    outcome = poll_and_apply(root, tracker, ("misery7100",)).outcomes[0]
    assert not outcome.applied and "ready candidate" in outcome.detail

    contract(root, "T-6152", role="review", targets=["T-6150"])
    run_state(root, "T-6152", TaskState.READY)
    tracker.commands = [TrackerCommand("revise", "T-6152", "misery7100", "c52")]
    outcome = poll_and_apply(root, tracker, ("misery7100",)).outcomes[0]
    assert not outcome.applied and "never revised" in outcome.detail


def test_revise_leaves_the_run_ready_when_capture_refuses(root):
    contract(root, "T-6153")
    run_state(root, "T-6153", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("revise", "T-6153", "misery7100", "c53")]

    def refusing(_task_id: str) -> str:
        raise RuntimeError("forge unreachable")

    outcome = poll_and_apply(root, tracker, ("misery7100",), requeue=refusing).outcomes[0]
    assert not outcome.applied and "capture failed" in outcome.detail
    assert RunState.load(naming.state_file(root, "T-6153")).state is TaskState.READY


def test_approve_records_a_sha_bound_approval_on_a_ready_candidate(root):
    run_state(root, "T-6130", TaskState.READY)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("approve", "T-6130", "misery7100", "c30")]
    outcome = poll_and_apply(
        root, tracker, ("misery7100",), approve_tip=lambda _t: "a" * 40
    ).outcomes[0]
    assert outcome.applied and "approved aaaaaaaaaa" in outcome.detail
    state = RunState.load(naming.state_file(root, "T-6130"))
    assert state.approvals == [
        {"actor": "misery7100", "sha": "a" * 40, "at": state.approvals[0]["at"]}
    ]
    # Idempotent: the same actor approving the same tip again.
    tracker.commands = [TrackerCommand("approve", "T-6130", "misery7100", "c31")]
    again = poll_and_apply(
        root, tracker, ("misery7100",), approve_tip=lambda _t: "a" * 40
    ).outcomes[0]
    assert again.applied and "already approved" in again.detail
    assert len(RunState.load(naming.state_file(root, "T-6130")).approvals) == 1


def test_approve_refuses_off_ready_and_branchless_candidates(root):
    run_state(root, "T-6131", TaskState.RUNNING)
    tracker = FakeTracker()
    tracker.commands = [TrackerCommand("approve", "T-6131", "misery7100", "c32")]
    outcome = poll_and_apply(
        root, tracker, ("misery7100",), approve_tip=lambda _t: "a" * 40
    ).outcomes[0]
    assert not outcome.applied and "ready candidate" in outcome.detail

    run_state(root, "T-6132", TaskState.READY)
    tracker.commands = [TrackerCommand("approve", "T-6132", "misery7100", "c33")]
    outcome = poll_and_apply(root, tracker, ("misery7100",), approve_tip=lambda _t: None).outcomes[
        0
    ]
    assert not outcome.applied and "no branch to approve" in outcome.detail


def test_shadow_runs_never_project(root):
    """Shadow runs are measurement, never work: a replay's state file must
    not become a board issue — eight 'shadow-T-nnnn: task' issues on the
    live board taught this."""
    run_state(root, "shadow-T-6002", TaskState.RUNNING)

    assert project(root) == 0


def test_retry_on_a_ready_run_routes_to_revise(root):
    """A ready candidate whose tip cannot land is not a dead end — /torve
    revise is its re-entry (D-8.18) — and the retry refusal must say so
    (D-A.18: a refusal routes, it never merely refuses). Found when a
    candidate with permanently-red tip CI sat ready with no visible exit."""
    from torve.application.tracker import _apply

    run_state(root, "T-6003", TaskState.READY)

    outcome = _apply(
        root, TrackerCommand(verb="retry", task_id="T-6003", actor="cmd", source="s1", text="")
    )

    assert not outcome.applied
    assert "/torve revise" in outcome.detail
