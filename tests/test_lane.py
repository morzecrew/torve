"""RFC 0006 phase 2: the serialized lane over real git — fast-forward as
measured, rebase-and-regate when the base moved, conflict reported and left
for a human."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from torve.adapters.vcs.git import GitLane
from torve.application.lane import process_lane
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


def test_the_loop_disposes_of_a_conflict_through_the_revision_loop(lane_repo):
    # D-6.10 as amended by A-35, bounded by D-6.12: with a disposal wired
    # (the standing loop's), a conflict against a fresh base tip escalates
    # — the record and the queue-age alarm stand — and is re-queued in
    # place, the disposal capturing and dropping the branch; a repeat
    # conflict against the SAME base tip is a human's turn.
    candidate(lane_repo, "T-7005", "app.py", "candidate = 5\n")
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    base_tip = git(lane_repo, "rev-parse", "HEAD")
    dropped: list[str] = []

    def disposal(task_id: str) -> str:
        dropped.append(task_id)
        return "remote branch deleted; feedback captured"

    results = process_lane(lane_repo, GitLane(), on_conflict=disposal)
    assert [r.action for r in results] == ["conflict requeued"]
    assert dropped == ["T-7005"]
    state = RunState.load(naming.state_file(lane_repo, "T-7005"))
    assert state.state is TaskState.QUEUED
    assert state.conflict_base == base_tip
    facts = [event["fact"] for event in state.history]
    assert any("merge_conflict" in fact for fact in facts)
    assert any("auto-requeue" in fact for fact in facts)

    # Re-ready against the SAME base: the progress bound holds.
    state.state = TaskState.READY
    state.save()
    again = process_lane(lane_repo, GitLane(), on_conflict=disposal)
    assert [r.action for r in again] == ["conflict"]
    assert dropped == ["T-7005"]  # the disposal did not run again
    state = RunState.load(naming.state_file(lane_repo, "T-7005"))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "merge_conflict"


def test_a_refused_disposal_leaves_the_escalation_standing(lane_repo):
    # A disposal the forge refuses degrades to the un-amended behaviour:
    # the escalation stands for the human fork, nothing half-applied.
    candidate(lane_repo, "T-7006", "app.py", "candidate = 6\n")
    (lane_repo / "app.py").write_text("base = 3\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    def refusing(task_id: str) -> str:
        raise RuntimeError("origin unreachable")

    results = process_lane(lane_repo, GitLane(), on_conflict=refusing)
    assert [r.action for r in results] == ["conflict"]
    assert "refused" in results[0].detail
    state = RunState.load(naming.state_file(lane_repo, "T-7006"))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "merge_conflict"


def test_dry_run_previews_without_moving(lane_repo):
    candidate(lane_repo, "T-7004", "four.py", "four = 4\n")
    tip_before = git(lane_repo, "rev-parse", "HEAD")
    result = invoke_merge(lane_repo, "--dry-run")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "would land"
    assert git(lane_repo, "rev-parse", "HEAD") == tip_before


def test_a_dirty_checkout_refuses_the_lane_and_names_the_dirt(lane_repo):
    candidate(lane_repo, "T-7005", "five.py", "five = 5\n")
    (lane_repo / "app.py").write_text("dirty\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 4, result.output
    assert "app.py" in result.output


def test_engine_records_never_block_the_lane(lane_repo):
    # The papercut the first standing-team run surfaced: the runner-minted
    # review contract lands untracked in the root checkout, and the outbox
    # pair mutates with every tracker sync. Records, not landed content —
    # the candidate still lands.
    candidate(lane_repo, "T-7010", "ten.py", "ten = 10\n")
    contract_dir = lane_repo / ".torve" / "tasks" / "T-7011"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text("# runner-minted\n", encoding="utf-8")
    (lane_repo / ".torve" / "outbox.jsonl").write_text("{}\n", encoding="utf-8")
    (lane_repo / ".torve" / "outbox-ledger.jsonl").write_text("{}\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"


def test_engine_records_beside_real_dirt_still_refuse(lane_repo):
    candidate(lane_repo, "T-7012", "twelve.py", "twelve = 12\n")
    contract_dir = lane_repo / ".torve" / "tasks" / "T-7013"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text("# runner-minted\n", encoding="utf-8")
    (lane_repo / "app.py").write_text("dirty\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 4, result.output
    # The refusal names the content dirt, not the tolerated record.
    assert "app.py" in result.output and "T-7013" not in result.output


# ....................... #
# ci: green_on_current_head (RFC 0006 §3): with a CI port supplied, only a
# remote-green branch tip lands; anything else refuses without touching git.


class FakeCi:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.asked: list[str] = []

    def conclusion(self, sha: str) -> str:
        self.asked.append(sha)
        return self.verdict


def test_ci_not_green_refuses_the_landing_and_touches_nothing(lane_repo):
    from torve.adapters.vcs.git import GitLane
    from torve.application.lane import process_lane

    candidate(lane_repo, "T-7006", "six.py", "six = 6\n")
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7006"))
    base_tip = git(lane_repo, "rev-parse", "HEAD")

    ci = FakeCi("failure")
    results = process_lane(lane_repo, GitLane(), ci=ci)
    assert results[0].action == "ci not green"
    assert "failure" in results[0].detail
    assert ci.asked == [branch_tip]
    assert git(lane_repo, "rev-parse", "HEAD") == base_tip
    records = [json.loads(line) for line in
               (lane_repo / ".torve" / "telemetry.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "lane_ci_not_green" and r["verdict"] == "failure"
               for r in records)

    # The same candidate lands once the remote goes green.
    landed = process_lane(lane_repo, GitLane(), ci=FakeCi("success"))
    assert landed[0].action == "landed"


def test_the_lane_releases_the_engine_worktree_before_a_rebase(lane_repo):
    # An engine run leaves its worktree holding the task branch; git refuses
    # a second checkout, so the rebase path must release it first.
    candidate(lane_repo, "T-7008", "eight.py", "eight = 8\n")
    engine_wt = lane_repo / ".wt" / "T-7008"
    git(lane_repo, "worktree", "add", str(engine_wt), naming.branch("T-7008"))
    # The base moves, forcing the rebase path.
    (lane_repo / "other.py").write_text("other = 1\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["results"][0]["action"] == "landed"
    assert "rebased" in report["results"][0]["detail"]
    assert not engine_wt.exists()


def test_require_ci_without_a_repo_is_a_configuration_error(lane_repo):
    candidate(lane_repo, "T-7007", "seven.py", "seven = 7\n")
    (lane_repo / "torve.yaml").write_text(
        "schema_version: 1\npromotion:\n  require_ci: true\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 3, result.output


def test_the_ticks_own_lock_never_blocks_the_lane(lane_repo):
    # Found live in the first tick: the lane leg runs while the tick holds
    # its lock, and the lock file must not read as content dirt.
    candidate(lane_repo, "T-7014", "fourteen.py", "fourteen = 14\n")
    (lane_repo / ".torve" / "tick.lock").write_text('{"pid": 1}', encoding="utf-8")
    (lane_repo / ".torve" / "pr-reviews.jsonl").write_text("{}\n", encoding="utf-8")
    (lane_repo / ".torve" / "evals.jsonl").write_text("{}\n", encoding="utf-8")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"


def test_the_lane_adopts_identical_untracked_records_the_landing_carries(lane_repo):
    # D-19.11 (A-28): the provenance commit carries the task's contract;
    # an untracked byte-identical root copy must not refuse the landing.
    git(lane_repo, "checkout", "-q", "-b", naming.branch("T-7015"), "main")
    contract_dir = lane_repo / ".torve" / "tasks" / "T-7015"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text("id: T-7015\n", encoding="utf-8")
    (lane_repo / "fifteen.py").write_text("fifteen = 15\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "work (T-7015)")
    git(lane_repo, "checkout", "-q", "main")
    # The root holds the same contract, untracked, identical.
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "contract.yaml").write_text("id: T-7015\n", encoding="utf-8")
    state = RunState(task_id="T-7015", path=naming.state_file(lane_repo, "T-7015"))
    state.state = TaskState.READY
    state.save()
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"
    assert (contract_dir / "contract.yaml").read_text() == "id: T-7015\n"


def test_a_differing_untracked_record_still_refuses_the_landing(lane_repo):
    git(lane_repo, "checkout", "-q", "-b", naming.branch("T-7016"), "main")
    contract_dir = lane_repo / ".torve" / "tasks" / "T-7016"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text("id: T-7016\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "work (T-7016)")
    git(lane_repo, "checkout", "-q", "main")
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "contract.yaml").write_text("id: DIFFERENT\n", encoding="utf-8")
    state = RunState(task_id="T-7016", path=naming.state_file(lane_repo, "T-7016"))
    state.state = TaskState.READY
    state.save()
    result = invoke_merge(lane_repo)
    # git refuses to overwrite the differing file — the landing fails loudly
    # and the root copy is untouched.
    assert result.exit_code != 0
    assert (contract_dir / "contract.yaml").read_text() == "id: DIFFERENT\n"
    assert git(lane_repo, "log", "--oneline", "-1").endswith("init")


# Promotion approvals and the quiet window (RFC 0006 §3, T-0060).


def test_approvals_required_refuses_an_unapproved_candidate(lane_repo):
    candidate(lane_repo, "T-7020", "twenty.py", "twenty = 20\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8")
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: approvals")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)["results"][0]
    assert report["action"] == "approvals short"
    assert "0 of 1" in report["detail"]


def test_an_approval_of_the_current_tip_lands(lane_repo):
    from torve.application.lane import record_approval

    candidate(lane_repo, "T-7021", "twentyone.py", "t = 21\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8")
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: approvals")
    tip = git(lane_repo, "rev-parse", naming.branch("T-7021"))
    assert record_approval(lane_repo, "T-7021", "operator", tip) is True
    # The dedupe: the same actor approving the same tip is one approval.
    assert record_approval(lane_repo, "T-7021", "operator", tip) is False
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"


def test_an_approval_of_a_superseded_tip_counts_for_nothing(lane_repo):
    from torve.application.lane import record_approval

    candidate(lane_repo, "T-7022", "twentytwo.py", "t = 22\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8")
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: approvals")
    old_tip = git(lane_repo, "rev-parse", naming.branch("T-7022"))
    record_approval(lane_repo, "T-7022", "operator", old_tip)
    # The branch moves after the approval — D-6.3: review freshness is
    # relative to current head.
    git(lane_repo, "checkout", "-q", naming.branch("T-7022"))
    (lane_repo / "twentytwo.py").write_text("t = 23\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "pushed after approval")
    git(lane_repo, "checkout", "-q", "main")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "approvals short"


def test_the_quiet_window_refuses_a_fresh_tip_and_passes_an_old_one(lane_repo):
    candidate(lane_repo, "T-7023", "twentythree.py", "t = 23\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  quiet_window: 3600\n", encoding="utf-8")
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: quiet window")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "quiet window"

    # An old tip is quiet: re-commit the branch with an aged committer date.
    git(lane_repo, "checkout", "-q", naming.branch("T-7023"))
    (lane_repo / "twentythree.py").write_text("t = 24\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    subprocess.run(["git", "-C", str(lane_repo), "-c",
                    "user.name=Lane Operator", "-c",
                    "user.email=lane@example.invalid",
                    "commit", "-q", "--no-gpg-sign", "-m", "aged"],
                   env={**__import__("os").environ,
                        "GIT_COMMITTER_DATE": "2001-01-01T00:00:00",
                        "GIT_AUTHOR_DATE": "2001-01-01T00:00:00"},
                   check=True)
    git(lane_repo, "checkout", "-q", "main")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"
