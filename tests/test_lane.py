"""RFC 0006 phase 2: the serialized lane over real git — fast-forward as
measured, rebase-and-regate when the base moved, conflict reported and left
for a human."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from torve.application.runstate import RunState
from torve.base import naming
from torve.cli.main import app
from torve.domain.states import TaskState


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def lane_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "config", "user.name", "Lane Operator")
    git(root, "config", "user.email", "lane@example.invalid")
    (root / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8")
    (root / ".gitignore").write_text(".wt/\n.torve/telemetry.jsonl\n", encoding="utf-8")
    (root / "app.py").write_text("base = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", "init")
    return root


def candidate(root: Path, task_id: str, filename: str, content: str) -> None:
    """A green task branch plus its terminal READY run state."""
    git(root, "checkout", "-q", "-b", naming.branch(task_id), "main")
    (root / filename).write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", f"work ({task_id})")
    git(root, "checkout", "-q", "main")
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.state = TaskState.READY
    state.save()


def invoke_merge(root: Path, *extra: str):
    return CliRunner().invoke(app, ["merge", "--root", str(root),
                                    "--format", "json", *extra])


def test_two_candidates_land_serially_first_ff_then_rebased(lane_repo):
    candidate(lane_repo, "T-7001", "one.py", "one = 1\n")
    candidate(lane_repo, "T-7002", "two.py", "two = 2\n")

    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    actions = {r["task"]: r for r in report["results"]}
    # The first candidate fast-forwards (base unmoved); its landing moves
    # the base, so the second is rebased and re-gated before landing.
    assert actions["T-7001"]["action"] == "landed"
    assert actions["T-7001"]["detail"] == "fast-forward"
    assert actions["T-7002"]["action"] == "landed"
    assert "rebased" in actions["T-7002"]["detail"]
    # Both files are on main, linear history.
    assert (lane_repo / "one.py").is_file() and (lane_repo / "two.py").is_file()

    # The lane's outcomes rode the telemetry stream.
    records = [json.loads(line) for line in
               (lane_repo / ".torve" / "telemetry.jsonl").read_text().splitlines()]
    landed = [r for r in records if r.get("event") == "lane_landed"]
    assert {r["mode"] for r in landed} == {"fast-forward", "rebased"}
    assert all(r["approver"] == "Lane Operator" for r in landed)


def test_a_conflict_escalates_the_run_and_leaves_the_branch_for_a_human(lane_repo):
    candidate(lane_repo, "T-7003", "app.py", "candidate = 3\n")
    # The base moves under the candidate, touching the same line.
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    base_tip = git(lane_repo, "rev-parse", "HEAD")
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7003"))

    result = invoke_merge(lane_repo)
    assert result.exit_code == 2, result.output
    report = json.loads(result.stdout)
    assert report["results"][0]["action"] == "conflict"
    # Nothing moved: the base stands, the branch is untouched.
    assert git(lane_repo, "rev-parse", "HEAD") == base_tip
    assert git(lane_repo, "rev-parse", naming.branch("T-7003")) == branch_tip
    # And no stray lane worktree remains.
    assert "lane-" not in git(lane_repo, "worktree", "list")
    # The run escalated (ready -> escalated, charter A-26): the failed
    # landing enters the escalation queue rather than sitting green in a
    # report nobody reads.
    state = RunState.load(naming.state_file(lane_repo, "T-7003"))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "merge_conflict"
    # An escalated candidate has left the lane: the next invocation does
    # not retry it.
    again = invoke_merge(lane_repo)
    assert again.exit_code == 0, again.output
    assert json.loads(again.stdout)["results"] == []


def test_dry_run_previews_without_moving(lane_repo):
    candidate(lane_repo, "T-7004", "four.py", "four = 4\n")
    tip_before = git(lane_repo, "rev-parse", "HEAD")
    result = invoke_merge(lane_repo, "--dry-run")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "would land"
    assert git(lane_repo, "rev-parse", "HEAD") == tip_before


def test_a_dirty_checkout_refuses_the_lane(lane_repo):
    candidate(lane_repo, "T-7005", "five.py", "five = 5\n")
    (lane_repo / "app.py").write_text("dirty\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 4, result.output
