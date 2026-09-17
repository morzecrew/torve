"""S-0006 phase 2: the serialized lane over real git — fast-forward as
measured, rebase-and-regate when the base moved, conflict reported and left
for a human."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from torve.adapters.vcs.git import GitLane
from torve.application.feedback import feedback_file, threads_file
from torve.application.lane import conflict_disposal, process_lane
from torve.application.runstate import RunState
from torve.base import naming
from torve.cli.main import app
from torve.cli.manager import _lane_leg
from torve.config.runconfig import PromotionConfig, RunnerConfig
from torve.domain.states import TaskState


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def lane_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "config", "user.name", "Lane Operator")
    git(root, "config", "user.email", "lane@example.invalid")
    (root / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
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
    return CliRunner().invoke(app, ["merge", "--root", str(root), "--format", "json", *extra])


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
    records = [
        json.loads(line)
        for line in (lane_repo / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    landed = [r for r in records if r.get("event") == "lane_landed"]
    assert {r["mode"] for r in landed} == {"fast-forward", "rebased"}
    assert all(r["approver"] == "Lane Operator" for r in landed)


def test_a_red_rebase_puts_the_branch_back_so_the_next_pass_regates(lane_repo):
    """T-0391 landed on a battery that had gone red, in two passes.

    `git rebase` runs in a worktree checked out on the branch, so it moves the
    ref. When the battery then failed, the branch was left sitting on the new
    base — and the next pass read that as "the base has not moved under this
    branch", took the fast-forward, and skipped the battery that had just
    failed. One `rebase (finish)` in the reflog for two merges is what it looks
    like from outside.
    """

    candidate(lane_repo, "T-7020", "twenty.py", "twenty = 20\n")
    # Move the base under it, so landing needs a rebase and a re-gate.
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    before = git(lane_repo, "rev-parse", naming.branch("T-7020"))

    # A gate that cannot pass, so the re-gate after the rebase is red.
    (lane_repo / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\n"
        "gates:\n"
        "  - name: refuses\n"
        "    run: 'false'\n"
        "    state: blocking\n"
        "    origin: structural\n"
        "    input: worktree\n"
        "    timeout: 30\n",
        encoding="utf-8",
    )
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "a gate that refuses")

    # A red candidate is a non-zero lane, which is the exit code this asserts on
    # rather than around.
    first = invoke_merge(lane_repo)
    assert first.exit_code == 1, first.output
    assert json.loads(first.stdout)["results"][0]["action"] == "gates red"

    # The ref is back where it started: the next pass must rebase and re-gate
    # rather than read a rebased branch as an unmoved base.
    assert git(lane_repo, "rev-parse", naming.branch("T-7020")) == before

    second = invoke_merge(lane_repo)
    assert second.exit_code == 1, second.output
    assert json.loads(second.stdout)["results"][0]["action"] == "gates red"
    assert (lane_repo / "twenty.py").exists() is False


def test_the_lane_event_is_reconciled_against_the_landing_files(lane_repo):
    # S-0065/D-7: the landing files in the tree are the carrier the ledger
    # divides by, and the lane's own event is stamped with the carrier's
    # verdict rather than counted beside it. A landing the carrier does not
    # hold then reads as a disagreement instead of a second, different,
    # number.
    candidate(lane_repo, "T-7010", "ten.py", "ten = 10\n")
    candidate(lane_repo, "T-7011", "eleven.py", "eleven = 11\n")
    # Only T-7011's branch carries a landing file.
    git(lane_repo, "checkout", "-q", naming.branch("T-7011"))
    execution = lane_repo / ".torve" / "execution"
    execution.mkdir(parents=True, exist_ok=True)
    (execution / "T-7011-1-20260101T000000Z.yaml").write_text(
        "task: T-7011\nattempt: 1\nat: '2026-01-01T00:00:00Z'\n", encoding="utf-8"
    )
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "landing (T-7011)")
    git(lane_repo, "checkout", "-q", "main")

    results = process_lane(lane_repo, GitLane())
    assert [r.action for r in results] == ["landed", "landed"]

    records = [
        json.loads(line)
        for line in (lane_repo / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    landed = {r["task"]: r for r in records if r.get("event") == "lane_landed"}
    # The fast-forward landed no landing file; the rebased one did.
    assert landed["T-7010"]["mode"] == "fast-forward" and landed["T-7010"]["carried"] is False
    assert landed["T-7011"]["mode"] == "rebased" and landed["T-7011"]["carried"] is True


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
    # S-0006/D-10 as amended by A-35, bounded by S-0006/D-12: with a disposal wired
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


# ----------------------- #


def conflicting_candidate(root: Path, task_id: str) -> str:
    """A READY candidate whose base moved under it onto the same line.
    Returns the base tip it now collides with."""

    candidate(root, task_id, "app.py", f"candidate = {task_id[-2:]}\n")
    (root / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    return git(root, "rev-parse", "HEAD")


def test_the_wired_disposal_captures_the_collided_diff_before_requeuing(lane_repo):
    # The restored disposal (S-0052/a-conflict-disposes-of-itself) is the loop's minus the forge
    # half: the next attempt's feedback record holds the superseded
    # candidate's diff, the thread section says "none captured" rather
    # than implying the capture was complete, and the branch is kept.
    base_tip = conflicting_candidate(lane_repo, "T-7007")
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7007"))

    results = process_lane(
        lane_repo, GitLane(), on_conflict=conflict_disposal(lane_repo, GitLane())
    )
    assert [r.action for r in results] == ["conflict requeued"]

    text = feedback_file(lane_repo, "T-7007").read_text(encoding="utf-8")
    assert "+candidate = 07" in text  # the candidate's own diff, three-dot
    assert "base = 2" not in text  # not the base's drift
    assert "- none captured." in text  # the absent forge half, said honestly
    assert not threads_file(lane_repo, "T-7007").exists()

    state = RunState.load(naming.state_file(lane_repo, "T-7007"))
    assert state.state is TaskState.QUEUED
    assert state.conflict_base == base_tip
    assert git(lane_repo, "rev-parse", "HEAD") == base_tip  # the base stands
    assert git(lane_repo, "rev-parse", naming.branch("T-7007")) == branch_tip  # branch kept


def test_a_candidate_with_undecodable_bytes_still_disposes_and_the_pass_goes_on(lane_repo):
    # T-0285: `_superseded_diff` decoded git's output strictly, so a
    # candidate holding any non-UTF-8 byte raised UnicodeDecodeError —
    # which is not the RuntimeError the disposal is built on. It escaped
    # `process_lane` entirely: the conflicting candidate stayed escalated
    # and un-requeued, and every candidate behind it was abandoned.
    git(lane_repo, "checkout", "-q", "-b", naming.branch("T-7021"), "main")
    (lane_repo / "app.py").write_bytes(b"# caf\xe9\ncandidate = 21\n")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "work (T-7021)")
    git(lane_repo, "checkout", "-q", "main")
    state = RunState(task_id="T-7021", path=naming.state_file(lane_repo, "T-7021"))
    state.state = TaskState.READY
    state.save()

    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    # A second, innocent candidate queued behind the undecodable one.
    candidate(lane_repo, "T-7022", "other.py", "other = 22\n")

    results = process_lane(
        lane_repo, GitLane(), on_conflict=conflict_disposal(lane_repo, GitLane())
    )

    # The conflicting candidate was disposed of, not abandoned...
    assert results[0].action == "conflict requeued"
    assert RunState.load(naming.state_file(lane_repo, "T-7021")).state is TaskState.QUEUED

    # ...the undecodable byte reached the record as a replacement rather
    # than as an exception...
    assert "candidate = 21" in feedback_file(lane_repo, "T-7021").read_text(encoding="utf-8")

    # ...and the pass reached the candidate behind it, which is the
    # invariant the escaping decode error actually broke.
    assert [r.task for r in results] == ["T-7021", "T-7022"]


def test_the_wired_disposal_still_requeues_only_on_a_moved_base(lane_repo):
    # S-0006/D-12 is the lane's bound, not the disposal's — wiring the real
    # one must not loosen it: a second conflict against the SAME base
    # tip escalates for the human and re-captures nothing.
    conflicting_candidate(lane_repo, "T-7008")
    disposal = conflict_disposal(lane_repo, GitLane())

    assert [r.action for r in process_lane(lane_repo, GitLane(), on_conflict=disposal)] == [
        "conflict requeued"
    ]
    captured = feedback_file(lane_repo, "T-7008").read_text(encoding="utf-8")

    state = RunState.load(naming.state_file(lane_repo, "T-7008"))
    state.state = TaskState.READY
    state.save()
    assert [r.action for r in process_lane(lane_repo, GitLane(), on_conflict=disposal)] == [
        "conflict"
    ]
    state = RunState.load(naming.state_file(lane_repo, "T-7008"))
    assert state.state is TaskState.ESCALATED
    assert feedback_file(lane_repo, "T-7008").read_text(encoding="utf-8") == captured


def test_the_manual_lane_captures_nothing(lane_repo):
    # What must not change: `torve merge` passes no disposal, so a
    # conflict escalates and stays escalated with no feedback record —
    # capture is the unattended lane's, not the operator's.
    conflicting_candidate(lane_repo, "T-7009")

    result = invoke_merge(lane_repo)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "conflict"
    assert not feedback_file(lane_repo, "T-7009").exists()


def test_the_served_leg_disposes_of_the_conflict_the_manual_verb_escalates(lane_repo):
    # S-0079/D-10: the pass's landing leg is handed `conflict_disposal`, so an
    # overnight conflict is captured and re-queued under the `conflict_base`
    # bound instead of waiting for a person. The disposal is the one
    # argument where the leg and `torve merge` differ.
    base_tip = conflicting_candidate(lane_repo, "T-7010")
    leg = _lane_leg(lane_repo, RunnerConfig(promotion=PromotionConfig(auto_merge=True)), only=None)
    assert leg is not None
    assert asyncio.run(leg()) == []  # nothing landed — the conflict was disposed of

    state = RunState.load(naming.state_file(lane_repo, "T-7010"))
    assert state.state is TaskState.QUEUED
    assert state.conflict_base == base_tip
    assert "+candidate = 10" in feedback_file(lane_repo, "T-7010").read_text(encoding="utf-8")


def test_a_disposal_with_no_branch_to_read_refuses_cleanly(lane_repo):
    # The failure mode of a capture is the refused-cleanup path the lane
    # already has: nothing is re-queued on top of a missing record.
    dispose = conflict_disposal(lane_repo, GitLane())

    with pytest.raises(RuntimeError, match="cannot resolve"):
        dispose("T-7099")


def test_a_disposal_of_an_unchanged_tip_captures_nothing(lane_repo):
    # A branch that carries no diff of its own has nothing worth
    # carrying forward; the record stays absent, honestly — it is never
    # written empty to look captured.
    git(lane_repo, "branch", naming.branch("T-7100"), "main")
    dispose = conflict_disposal(lane_repo, GitLane())

    assert dispose("T-7100") == "nothing to capture"
    assert not feedback_file(lane_repo, "T-7100").exists()


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
    # review contract lands untracked in the root checkout, and the engine's
    # own ledgers mutate as it runs. Records, not landed content — the
    # candidate still lands.
    candidate(lane_repo, "T-7010", "ten.py", "ten = 10\n")
    contract_dir = lane_repo / ".torve" / "tasks" / "T-7011"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text("# runner-minted\n", encoding="utf-8")
    (lane_repo / ".torve" / "pr-reviews.jsonl").write_text("{}\n", encoding="utf-8")
    (lane_repo / ".torve" / "evals.jsonl").write_text("{}\n", encoding="utf-8")
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
# ci: green_on_current_head (S-0006/promotion): with a CI port supplied, only a
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
    records = [
        json.loads(line)
        for line in (lane_repo / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    assert any(r.get("event") == "lane_ci_not_green" and r["verdict"] == "failure" for r in records)

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
    (lane_repo / ".torve").mkdir(exist_ok=True)
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  require_ci: true\n", encoding="utf-8"
    )
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
    # S-0019/D-11 (A-28): the provenance commit carries the task's contract;
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


# Promotion approvals and the quiet window (S-0006/promotion, T-0060).


def test_a_conflicting_tip_is_never_offered_for_approval(lane_repo):
    # S-0006/D-13 (A-42): the probe precedes the prompt — with a disposal
    # wired and the base moved conflictingly, an unapproved candidate
    # re-queues at probe time; no approval is requested, none can burn.
    candidate(lane_repo, "T-7030", "app.py", "candidate = 30\n")
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7030"))
    captured: list[str] = []

    def disposal(task_id: str) -> str:
        captured.append(task_id)
        return "branch kept; feedback captured"

    results = process_lane(lane_repo, GitLane(), approvals_required=1, on_conflict=disposal)
    assert results[0].action == "conflict requeued"
    assert "probe" in results[0].detail
    assert captured == ["T-7030"]
    assert RunState.load(naming.state_file(lane_repo, "T-7030")).state is TaskState.QUEUED
    # The probe is read-only: the branch tip never moved.
    assert git(lane_repo, "rev-parse", naming.branch("T-7030")) == branch_tip
    events = [
        json.loads(line)
        for line in (lane_repo / ".torve" / "telemetry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert not any(e.get("event") == "lane_approvals_short" for e in events)


def test_a_clean_probe_still_prompts_for_approval(lane_repo):
    # A moved base with no conflict changes nothing: the prompt goes out
    # and the approval is honoured through the landing's rebase (S-0006/D-3).
    candidate(lane_repo, "T-7031", "other.py", "o = 31\n")
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    results = process_lane(
        lane_repo, GitLane(), approvals_required=1, on_conflict=lambda _t: "unused"
    )
    assert results[0].action == "approvals short"


def test_the_manual_lane_never_probes(lane_repo):
    # S-0006/D-12: without a wired disposal (the operator's lane), the probe
    # stays off — a conflicting unapproved candidate just reports short.
    candidate(lane_repo, "T-7032", "app.py", "candidate = 32\n")
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    results = process_lane(lane_repo, GitLane(), approvals_required=1)
    assert results[0].action == "approvals short"
    assert RunState.load(naming.state_file(lane_repo, "T-7032")).state is TaskState.READY


def test_approvals_required_refuses_an_unapproved_candidate(lane_repo):
    candidate(lane_repo, "T-7020", "twenty.py", "twenty = 20\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8"
    )
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
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8"
    )
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
        "schema_version: 1\npromotion:\n  approvals: 1\n", encoding="utf-8"
    )
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: approvals")
    old_tip = git(lane_repo, "rev-parse", naming.branch("T-7022"))
    record_approval(lane_repo, "T-7022", "operator", old_tip)
    # The branch moves after the approval — S-0006/D-3: review freshness is
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
        "schema_version: 1\npromotion:\n  quiet_window: 3600\n", encoding="utf-8"
    )
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: quiet window")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "quiet window"

    # An old tip is quiet: re-commit the branch with an aged committer date.
    git(lane_repo, "checkout", "-q", naming.branch("T-7023"))
    (lane_repo / "twentythree.py").write_text("t = 24\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    subprocess.run(
        [
            "git",
            "-C",
            str(lane_repo),
            "-c",
            "user.name=Lane Operator",
            "-c",
            "user.email=lane@example.invalid",
            "commit",
            "-q",
            "--no-gpg-sign",
            "-m",
            "aged",
        ],
        env={
            **__import__("os").environ,
            "GIT_COMMITTER_DATE": "2001-01-01T00:00:00",
            "GIT_AUTHOR_DATE": "2001-01-01T00:00:00",
        },
        check=True,
    )
    git(lane_repo, "checkout", "-q", "main")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "landed"


# The review predicate (S-0006/promotion, S-0006/D-14, A-43).


def _events(root: Path) -> list[dict]:
    path = root / ".torve" / "telemetry.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_require_review_refuses_a_candidate_without_a_verdict(lane_repo):
    candidate(lane_repo, "T-7040", "forty.py", "f = 40\n")
    results = process_lane(lane_repo, GitLane(), require_review=True)
    assert results[0].action == "review missing"
    assert "promotion.require_review" in results[0].detail
    # A standing refusal, like ci-not-green: the candidate stays READY.
    assert RunState.load(naming.state_file(lane_repo, "T-7040")).state is TaskState.READY
    assert any(e.get("event") == "lane_review_missing" for e in _events(lane_repo))


def test_a_recorded_review_verdict_lands(lane_repo):
    candidate(lane_repo, "T-7041", "fortyone.py", "f = 41\n")
    state = RunState.load(naming.state_file(lane_repo, "T-7041"))
    state.reviewed_by = "T-7999"
    state.save()
    results = process_lane(lane_repo, GitLane(), require_review=True)
    assert results[0].action == "landed"


def test_review_missing_precedes_the_approvals_prompt(lane_repo):
    # S-0006/D-14: a candidate the policy cannot land is never offered for
    # approval — the refusal fires before the approvals check.
    candidate(lane_repo, "T-7042", "fortytwo.py", "f = 42\n")
    results = process_lane(lane_repo, GitLane(), require_review=True, approvals_required=1)
    assert results[0].action == "review missing"
    assert not any(e.get("event") == "lane_approvals_short" for e in _events(lane_repo))


def test_require_review_flows_from_configuration(lane_repo):
    candidate(lane_repo, "T-7043", "fortythree.py", "f = 43\n")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  require_review: true\n", encoding="utf-8"
    )
    git(lane_repo, "add", ".torve/config.yaml")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "config: review")
    result = invoke_merge(lane_repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["results"][0]["action"] == "review missing"


def test_reviewed_by_round_trips_and_dies_with_the_next_attempt(tmp_path):
    # S-0006/D-14: no verdict outlives the attempt it judged — entry to
    # running clears it, exactly where attempts increment.
    state = RunState(task_id="T-7044", path=tmp_path / "T-7044.state.json")
    state.transition(TaskState.CLAIMED, "claimed")
    state.transition(TaskState.RUNNING, "attempt 1")
    state.reviewed_by = "T-7999"
    state.save()
    assert RunState.load(state.path).reviewed_by == "T-7999"
    state.transition(TaskState.GATED, "gates red")
    state.transition(TaskState.RUNNING, "attempt 2")
    assert state.reviewed_by is None


# ....................... #


def test_the_approve_verb_records_the_tip_and_dedupes(lane_repo):
    """The tracker's `/torve approve` was the only surface that recorded an
    approval, so `promotion.approvals` had become a knob that could never
    be satisfied — set it and the lane waited forever (A-110). The verb is
    the surface now."""

    from typer.testing import CliRunner

    from torve.cli.main import app

    candidate(lane_repo, "T-7099", "ninetynine.py", "n = 99\n")
    tip = git(lane_repo, "rev-parse", naming.branch("T-7099"))

    first = CliRunner().invoke(
        app, ["approve", "T-7099", "--root", str(lane_repo), "--format", "json"]
    )
    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert payload["sha"] == tip and payload["recorded"] is True

    # The same tip twice is one approval, and the verb says so rather than
    # counting it again.
    again = CliRunner().invoke(
        app, ["approve", "T-7099", "--root", str(lane_repo), "--format", "json"]
    )
    assert json.loads(again.stdout)["recorded"] is False


def test_approving_a_task_with_no_run_state_is_a_configuration_error(lane_repo):
    from typer.testing import CliRunner

    from torve.cli.main import app

    result = CliRunner().invoke(app, ["approve", "T-9999", "--root", str(lane_repo)])
    assert result.exit_code == 3
    assert "no run state" in result.stderr


# `pull_request` mode: the landing act is a publication (S-0080/D-3, S-0080/D-11).


def _recording_publisher(published: list[tuple[str, str]], url: str = "https://forge/pr/7"):
    def publish(task_id: str, branch: str) -> str:
        published.append((task_id, branch))
        return url

    return publish


def test_pull_request_mode_publishes_the_candidate_and_never_moves_the_base(lane_repo):
    candidate(lane_repo, "T-7101", "one.py", "one = 1\n")
    base_before = git(lane_repo, "rev-parse", "main")
    published: list[tuple[str, str]] = []

    results = process_lane(lane_repo, GitLane(), publish=_recording_publisher(published))

    assert [r.action for r in results] == ["pull request"]
    assert results[0].detail == "https://forge/pr/7"
    assert published == [("T-7101", naming.branch("T-7101"))]
    # The base is exactly where it was and the candidate's file never arrived:
    # the engine opened a pull request, it did not merge one.
    assert git(lane_repo, "rev-parse", "main") == base_before
    assert (lane_repo / "one.py").exists() is False

    opened = [e for e in _events(lane_repo) if e.get("event") == "lane_pr_opened"]
    assert opened and opened[0]["mode"] == "fast-forward"
    assert opened[0]["pr"] == "https://forge/pr/7"
    assert not [e for e in _events(lane_repo) if e.get("event") == "lane_landed"]
    # A standing candidate: nothing landed, so the run stays READY.
    assert RunState.load(naming.state_file(lane_repo, "T-7101")).state is TaskState.READY


def test_pull_request_mode_rebases_and_regates_before_it_publishes(lane_repo):
    candidate(lane_repo, "T-7102", "two.py", "two = 2\n")
    (lane_repo / "app.py").write_text("base = 2\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")

    base_before = git(lane_repo, "rev-parse", "main")
    published: list[tuple[str, str]] = []

    results = process_lane(lane_repo, GitLane(), publish=_recording_publisher(published))

    assert [r.action for r in results] == ["pull request"]
    assert published == [("T-7102", naming.branch("T-7102"))]
    # The branch was rebased onto the moved base and the battery re-run over
    # it, exactly as the local mode does — only the last step differs.
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7102"))
    assert results[0].sha == branch_tip
    assert git(lane_repo, "merge-base", "--is-ancestor", base_before, branch_tip) == ""
    assert git(lane_repo, "rev-parse", "main") == base_before

    opened = [e for e in _events(lane_repo) if e.get("event") == "lane_pr_opened"]
    assert opened and opened[0]["mode"] == "rebased"


def test_a_refused_publication_is_one_candidates_refusal_and_not_the_passs(lane_repo):
    candidate(lane_repo, "T-7103", "three.py", "three = 3\n")
    candidate(lane_repo, "T-7104", "four.py", "four = 4\n")
    base_before = git(lane_repo, "rev-parse", "main")

    def publish(task_id: str, branch: str) -> str:
        if task_id == "T-7103":
            raise RuntimeError("gh pr create failed")
        return "https://forge/pr/8"

    results = {r.task: r for r in process_lane(lane_repo, GitLane(), publish=publish)}

    assert results["T-7103"].action == "pr refused"
    assert "gh pr create failed" in results["T-7103"].detail
    assert results["T-7104"].action == "pull request"
    assert git(lane_repo, "rev-parse", "main") == base_before
    assert any(e.get("event") == "lane_pr_refused" for e in _events(lane_repo))


def test_a_dry_run_in_pull_request_mode_moves_nothing_and_asks_the_forge_nothing(lane_repo):
    candidate(lane_repo, "T-7105", "five.py", "five = 5\n")
    published: list[tuple[str, str]] = []

    results = process_lane(
        lane_repo, GitLane(), dry_run=True, publish=_recording_publisher(published)
    )

    assert [r.action for r in results] == ["would open pull request"]
    assert published == []


def test_the_landing_act_follows_the_configured_mode_and_nothing_else(lane_repo):
    # S-0080/D-1: the mode is a term of configuration, so the verb builds a
    # publisher exactly when the configuration names one.
    from torve.cli.merge import _publisher
    from torve.cli.options import load_config

    config_file = lane_repo / ".torve" / "config.yaml"
    config_file.write_text("schema_version: 1\n", encoding="utf-8")
    assert _publisher(lane_repo, load_config(lane_repo, None)) is None

    config_file.write_text(
        "schema_version: 1\n"
        "promotion:\n"
        "  landing: pull_request\n"
        "scm:\n"
        "  open_pr: true\n"
        "  repo: owner/name\n",
        encoding="utf-8",
    )
    assert _publisher(lane_repo, load_config(lane_repo, None)) is not None


# The later pass reads the verdict back (S-0080/the-landing-arrives-as-an-answer, S-0080/D-7,
# S-0080/D-8, S-0080/D-9, S-0080/D-12).


def _pr(number=7, state="open", merge_commit="", head_sha=""):
    from torve.application.ports import PrInfo

    return PrInfo(
        number=number,
        title="a candidate",
        author="a person",
        draft=False,
        head_sha=head_sha,
        base_ref="main",
        changed_files=1,
        state=state,
        merge_commit=merge_commit,
    )


def _forge(answer, asked: list[str]):
    def ask(branch: str):
        asked.append(branch)
        return answer

    return ask


def _opened(root: Path, task_id: str, filename: str, content: str, published: list) -> None:
    """A candidate the lane has already published: the pass that opened the
    pull request is the record the later pass reads."""
    candidate(root, task_id, filename, content)
    process_lane(root, GitLane(), only=task_id, publish=_recording_publisher(published))


def test_a_merged_pull_request_is_recorded_as_the_landing_with_the_merge_commit(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7110", "ten.py", "ten = 10\n", published)
    base_before = git(lane_repo, "rev-parse", "main")
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=11, state="merged", merge_commit="a" * 40), asked),
    )

    assert asked == [naming.branch("T-7110")]
    assert results[0].action == "landed"
    assert results[0].sha == "a" * 40
    # The engine recorded a landing; it performed none — the local base is
    # untouched and nothing was pushed a second time.
    assert git(lane_repo, "rev-parse", "main") == base_before
    assert len(published) == 1

    landed = [e for e in _events(lane_repo) if e.get("event") == "lane_landed"]
    assert landed and landed[0]["mode"] == "pull-request"
    assert landed[0]["sha"] == "a" * 40
    assert landed[0]["pr"] == 11
    # S-0080/D-12: the branch is kept, so the attempt's commits stay reachable.
    assert git(lane_repo, "rev-parse", naming.branch("T-7110"))

    # A later pass reads its own record back rather than the forge's.
    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(state="merged", merge_commit="a" * 40), asked),
    )
    assert again[0].action == "already landed"
    assert asked == [naming.branch("T-7110")]
    assert len(published) == 1


def test_a_closed_pull_request_is_an_abandonment_and_is_never_re_opened(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7111", "eleven.py", "eleven = 11\n", published)
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=12, state="closed"), asked),
    )

    assert results[0].action == "abandoned"
    assert "closed without merging" in results[0].detail
    closed = [e for e in _events(lane_repo) if e.get("event") == "lane_pr_closed"]
    assert closed and closed[0]["pr"] == 12
    # Not escalated for triage and not re-queued: the person declined it.
    state = RunState.load(naming.state_file(lane_repo, "T-7111"))
    assert state.state is TaskState.READY
    assert state.escalation is None
    assert len(published) == 1

    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=12, state="closed"), asked),
    )
    assert again[0].action == "abandoned"
    # The verdict is terminal: no second forge call and no second pull request.
    assert asked == [naming.branch("T-7111")]
    assert len(published) == 1


def test_an_open_pull_request_on_a_moved_base_rebases_regates_and_republishes(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7112", "twelve.py", "twelve = 12\n", published)

    (lane_repo / "app.py").write_text("base = 12\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "base moves")
    moved = git(lane_repo, "rev-parse", "main")
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(state="open"), asked),
    )

    assert asked == [naming.branch("T-7112")]
    assert results[0].action == "pull request"
    # The branch the person is looking at is the one the battery measured.
    branch_tip = git(lane_repo, "rev-parse", naming.branch("T-7112"))
    assert results[0].sha == branch_tip
    assert git(lane_repo, "merge-base", "--is-ancestor", moved, branch_tip) == ""
    assert len(published) == 2


def test_an_open_pull_request_on_an_unmoved_base_spends_no_push(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7113", "thirteen.py", "thirteen = 13\n", published)
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=13, state="open"), asked),
    )

    assert asked == [naming.branch("T-7113")]
    assert results[0].action == "pull request open"
    assert "#13" in results[0].detail
    assert len(published) == 1


def test_a_pass_with_nothing_open_asks_the_forge_nothing(lane_repo):
    candidate(lane_repo, "T-7114", "fourteen.py", "fourteen = 14\n")
    published: list[tuple[str, str]] = []
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(state="merged", merge_commit="b" * 40), asked),
    )

    # S-0080/D-16: nothing is open, so the credential is not spent at all.
    assert asked == []
    assert results[0].action == "pull request"
    assert len(published) == 1


def test_a_branch_the_forge_cannot_resolve_is_recorded_and_not_re_opened(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7115", "fifteen.py", "fifteen = 15\n", published)
    asked: list[str] = []

    results = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), forge=_forge(None, asked)
    )

    assert results[0].action == "pr unresolved"
    assert any(e.get("event") == "lane_pr_unresolved" for e in _events(lane_repo))
    assert len(published) == 1

    again = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), forge=_forge(None, asked)
    )
    assert again[0].action == "pr unresolved"
    assert asked == [naming.branch("T-7115")]
    assert len(published) == 1


def test_a_dry_run_asks_the_forge_nothing(lane_repo):
    published: list[tuple[str, str]] = []
    _opened(lane_repo, "T-7116", "sixteen.py", "sixteen = 16\n", published)
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        dry_run=True,
        publish=_recording_publisher(published),
        forge=_forge(_pr(state="merged", merge_commit="c" * 40), asked),
    )

    assert asked == []
    assert [r.action for r in results] == ["would open pull request"]


# `unit: document`: one branch and one pull request per document (S-0083/D-4,
# S-0083/D-5, S-0083/D-6, S-0083/D-7, S-0083/D-18).


def _origin(root: Path, tmp_path: Path) -> Path:
    """A remote to cut the document branch from — `unit: document` cuts from
    the remote's `main` after a fetch, never from the local copy."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    return remote


def _contract(root: Path, task_id: str, spec: str | None) -> None:
    """The contract is what names the document — written after every
    candidate, because `candidate` stages the whole tree onto its branch."""
    path = root / ".torve" / "tasks" / task_id / "contract.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"schema_version: 2\nid: {task_id}\ndecisions: []\n"
    path.write_text(body + (f"spec: {spec}\n" if spec else ""), encoding="utf-8")


def test_the_first_phase_cuts_the_document_branch_and_lands_onto_it(lane_repo, tmp_path):
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7201", "one.py", "one = 1\n")
    _contract(lane_repo, "T-7201", "S-0900")
    base_before = git(lane_repo, "rev-parse", "main")
    published: list[tuple[str, str]] = []

    results = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), unit="document"
    )

    document = naming.document_branch("S-0900")
    # The landing is onto the document's branch, and what is published is that
    # branch — the checkout's base is untouched, as in any pull-request mode.
    assert [r.action for r in results] == ["landed"]
    assert results[0].branch == document
    assert published == [("T-7201", document)]
    assert git(lane_repo, "rev-parse", document) == git(
        lane_repo, "rev-parse", naming.branch("T-7201")
    )
    assert git(lane_repo, "rev-parse", "main") == base_before

    landed = [e for e in _events(lane_repo) if e.get("event") == "lane_landed"]
    assert landed and landed[0]["unit"] == "document" and landed[0]["branch"] == document
    assert landed[0]["mode"] == "fast-forward"
    assert landed[0]["sha"] == git(lane_repo, "rev-parse", document)

    # The branch is cut once: a later pass finds the phase already landed on
    # it and spends no second publication.
    again = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), unit="document"
    )
    assert [r.action for r in again] == ["already landed"]
    assert len(published) == 1


def test_a_second_phase_rebases_onto_the_document_branch_and_lands_onto_the_same_ref(
    lane_repo, tmp_path
):
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7202", "two.py", "two = 2\n")
    candidate(lane_repo, "T-7203", "three.py", "three = 3\n")
    _contract(lane_repo, "T-7202", "S-0901")
    _contract(lane_repo, "T-7203", "S-0901")
    published: list[tuple[str, str]] = []

    results = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), unit="document"
    )

    document = naming.document_branch("S-0901")
    # Both phases landed onto the one branch: the first fast-forwards it as
    # measured, the second was rebased onto it and the battery re-run first.
    assert [r.action for r in results] == ["landed", "landed"]
    assert published == [("T-7202", document), ("T-7203", document)]
    assert [e["mode"] for e in _events(lane_repo) if e.get("event") == "lane_landed"] == [
        "fast-forward",
        "rebased",
    ]

    tip = git(lane_repo, "rev-parse", document)
    assert results[1].sha == tip
    # One branch, carrying both phases' work.
    assert git(lane_repo, "show", f"{document}:two.py") == "two = 2"
    assert git(lane_repo, "show", f"{document}:three.py") == "three = 3"
    assert git(lane_repo, "rev-parse", "main") != tip


def test_a_contract_naming_no_document_lands_by_the_task_unit(lane_repo, tmp_path):
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7204", "four.py", "four = 4\n")
    # A candidate with no contract at all is the same case: nothing infers a
    # document for it.
    candidate(lane_repo, "T-7205", "five.py", "five = 5\n")
    _contract(lane_repo, "T-7204", None)
    published: list[tuple[str, str]] = []

    results = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), unit="document"
    )

    assert [r.action for r in results] == ["pull request", "pull request"]
    assert published == [
        ("T-7204", naming.branch("T-7204")),
        ("T-7205", naming.branch("T-7205")),
    ]
    assert not [e for e in _events(lane_repo) if e.get("event") == "lane_landed"]


def test_a_refused_publication_puts_the_document_branch_back(lane_repo, tmp_path):
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7206", "six.py", "six = 6\n")
    _contract(lane_repo, "T-7206", "S-0902")

    def refuse(task_id: str, branch: str) -> str:
        raise RuntimeError("gh pr create failed")

    results = process_lane(lane_repo, GitLane(), publish=refuse, unit="document")

    document = naming.document_branch("S-0902")
    assert [r.action for r in results] == ["pr refused"]
    # The landing is the publication too, so the branch stays where it was cut
    # and the next pass lands the phase again.
    assert git(lane_repo, "rev-parse", document) == git(lane_repo, "rev-parse", "main")
    assert not [e for e in _events(lane_repo) if e.get("event") == "lane_landed"]

    published: list[tuple[str, str]] = []
    again = process_lane(
        lane_repo, GitLane(), publish=_recording_publisher(published), unit="document"
    )
    assert [r.action for r in again] == ["landed"]
    assert published == [("T-7206", document)]


def test_the_local_landing_ignores_the_unit(lane_repo, tmp_path):
    # S-0083/D-2: no publisher is a local landing, which has no pull request to
    # be one per — the unit is inert rather than refused.
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7207", "seven.py", "seven = 7\n")
    _contract(lane_repo, "T-7207", "S-0903")

    results = process_lane(lane_repo, GitLane(), unit="document")

    assert [r.action for r in results] == ["landed"]
    assert results[0].detail == "fast-forward"
    assert git(lane_repo, "rev-parse", "main") == git(
        lane_repo, "rev-parse", naming.branch("T-7207")
    )
    assert GitLane().tip(lane_repo, naming.document_branch("S-0903")) is None


def test_a_dry_run_cuts_no_document_branch(lane_repo, tmp_path):
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7208", "eight.py", "eight = 8\n")
    _contract(lane_repo, "T-7208", "S-0904")
    published: list[tuple[str, str]] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        dry_run=True,
        publish=_recording_publisher(published),
        unit="document",
    )

    assert published == []
    assert GitLane().tip(lane_repo, naming.document_branch("S-0904")) is None
    assert [r.action for r in results] == ["would rebase"]


def test_the_configured_unit_reaches_the_lane(lane_repo, tmp_path):
    # S-0083/D-1: a term of configuration, never inferred from whether the
    # ready candidates happen to share a document.
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7209", "nine.py", "nine = 9\n")
    _contract(lane_repo, "T-7209", "S-0905")
    (lane_repo / ".torve" / "config.yaml").write_text(
        "schema_version: 1\npromotion:\n  landing: pull_request\n  unit: document\n"
        "scm:\n  open_pr: true\n  repo: owner/name\n",
        encoding="utf-8",
    )
    # Committed, or the lane refuses the pass on a dirty checkout.
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "configure the unit")

    seen: list[tuple[str, str]] = []
    import torve.cli.merge as merge_module

    original = merge_module._publisher
    merge_module._publisher = lambda root, config: _recording_publisher(seen)

    try:
        result = invoke_merge(lane_repo)
    finally:
        merge_module._publisher = original

    assert result.exit_code == 0, result.output
    assert seen == [("T-7209", naming.document_branch("S-0905"))]


# The later pass at the document unit (S-0083/D-10, S-0083/D-11, S-0083/D-12,
# S-0083/D-13, S-0083/D-14): one question per open document, and the verdict a
# person gave recorded once for the whole branch.


def _landed_document(
    root: Path, tmp_path: Path, spec: str, phases: dict[str, str], published: list
) -> str:
    """A document branch carrying one landing per phase — the record a later
    pass reads its own open pull request back from."""

    _origin(root, tmp_path)

    for task_id, filename in phases.items():
        candidate(root, task_id, filename, f"# {task_id}\n")

    for task_id in phases:
        _contract(root, task_id, spec)

    process_lane(root, GitLane(), publish=_recording_publisher(published), unit="document")

    return naming.document_branch(spec)


def test_a_merged_document_pull_request_is_one_landing_naming_every_task(lane_repo, tmp_path):
    published: list[tuple[str, str]] = []
    document = _landed_document(
        lane_repo, tmp_path, "S-0910", {"T-7301": "one.py", "T-7302": "two.py"}, published
    )
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=21, state="merged", merge_commit="d" * 40), asked),
        unit="document",
    )

    # One question for the document, not one per phase (S-0083/D-12).
    assert asked == [document]
    assert [r.action for r in results] == ["landed", "already landed", "already landed"]
    assert results[0].sha == "d" * 40
    assert "T-7301, T-7302" in results[0].detail

    landing = [e for e in _events(lane_repo) if e.get("event") == "lane_document_landed"]
    assert len(landing) == 1
    assert landing[0]["sha"] == "d" * 40
    assert landing[0]["pr"] == 21
    assert landing[0]["tasks"] == ["T-7301", "T-7302"]
    # Not a second landing per task: the two on the stream are the two the
    # phases wrote when they landed on the branch (S-0083/D-10).
    assert [e["task"] for e in _events(lane_repo) if e.get("event") == "lane_landed"] == [
        "T-7301",
        "T-7302",
    ]
    # S-0083/D-14: nobody deletes the branch, so the commits the squash
    # rewrote stay reachable task by task.
    assert git(lane_repo, "show", f"{document}:two.py")

    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=21, state="merged", merge_commit="d" * 40), asked),
        unit="document",
    )
    # The verdict is terminal: no second question and no second pull request.
    assert asked == [document]
    assert [r.action for r in again] == ["already landed", "already landed"]
    assert len(published) == 2


def test_a_closed_document_pull_request_abandons_every_task_and_keeps_the_branch(
    lane_repo, tmp_path
):
    published: list[tuple[str, str]] = []
    document = _landed_document(
        lane_repo, tmp_path, "S-0911", {"T-7303": "three.py", "T-7304": "four.py"}, published
    )
    tip = git(lane_repo, "rev-parse", document)
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=22, state="closed"), asked),
        unit="document",
    )

    assert [r.action for r in results] == ["abandoned", "abandoned", "abandoned"]
    assert "T-7303, T-7304" in results[0].detail
    closed = [e for e in _events(lane_repo) if e.get("event") == "lane_document_closed"]
    assert len(closed) == 1
    assert closed[0]["tasks"] == ["T-7303", "T-7304"]
    assert closed[0]["pr"] == 22
    # A person declined the design, not a triage queue: nothing is escalated
    # and nothing is re-queued (S-0083/D-11).
    for task_id in ("T-7303", "T-7304"):
        state = RunState.load(naming.state_file(lane_repo, task_id))
        assert state.state is TaskState.READY
        assert state.escalation is None

    # The branch is kept, and the work on it is never offered a second pull
    # request.
    assert git(lane_repo, "rev-parse", document) == tip

    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=22, state="closed"), asked),
        unit="document",
    )
    assert [r.action for r in again] == ["abandoned", "abandoned"]
    assert asked == [document]
    assert len(published) == 2


def test_an_open_document_on_a_moved_base_rebases_regates_and_republishes(lane_repo, tmp_path):
    published: list[tuple[str, str]] = []
    document = _landed_document(lane_repo, tmp_path, "S-0912", {"T-7305": "five.py"}, published)

    (lane_repo / "app.py").write_text("base = 5\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "the base moves")
    git(lane_repo, "push", "-q", "origin", "main")
    moved = git(lane_repo, "rev-parse", "origin/main")
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=23, state="open"), asked),
        unit="document",
    )

    assert asked == [document]
    assert [r.action for r in results] == ["pull request", "already landed"]
    # The branch a person is looking at is the one the battery just judged,
    # sitting on the remote's `main` (S-0083/D-13).
    tip = git(lane_repo, "rev-parse", document)
    assert results[0].sha == tip
    assert git(lane_repo, "merge-base", "--is-ancestor", moved, tip) == ""
    assert git(lane_repo, "show", f"{document}:five.py")
    assert published[-1] == ("T-7305", document)

    # Rebased onto the tip it now sits on: the next pass has nothing to do.
    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=23, state="open"), asked),
        unit="document",
    )
    assert [r.action for r in again] == ["pull request open", "already landed"]
    assert len(published) == 2


def test_an_open_document_on_an_unmoved_base_spends_no_push(lane_repo, tmp_path):
    published: list[tuple[str, str]] = []
    document = _landed_document(lane_repo, tmp_path, "S-0913", {"T-7306": "six.py"}, published)
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=24, state="open"), asked),
        unit="document",
    )

    assert asked == [document]
    assert [r.action for r in results] == ["pull request open", "already landed"]
    assert "#24" in results[0].detail
    assert len(published) == 1


def test_a_document_that_no_longer_rebases_escalates_once_per_base_tip(lane_repo, tmp_path):
    published: list[tuple[str, str]] = []
    # The phase and the base both write `app.py`, so no rebase of the branch
    # onto the moved remote can be clean.
    document = _landed_document(lane_repo, tmp_path, "S-0914", {"T-7307": "app.py"}, published)
    tip = git(lane_repo, "rev-parse", document)

    (lane_repo / "app.py").write_text("base = 7\n", encoding="utf-8")
    git(lane_repo, "add", "-A")
    git(lane_repo, "commit", "-q", "--no-gpg-sign", "-m", "the base moves onto the same line")
    git(lane_repo, "push", "-q", "origin", "main")
    base_tip = git(lane_repo, "rev-parse", "origin/main")
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=25, state="open"), asked),
        unit="document",
    )

    assert [r.action for r in results] == ["conflict"]
    conflicts = [e for e in _events(lane_repo) if e.get("event") == "lane_document_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0]["base_tip"] == base_tip
    assert conflicts[0]["tasks"] == ["T-7307"]
    # The engine never resolves one: the branch stands exactly as measured
    # and the work it carries waits on a person.
    assert git(lane_repo, "rev-parse", document) == tip
    state = RunState.load(naming.state_file(lane_repo, "T-7307"))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None

    again = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(number=25, state="open"), asked),
        unit="document",
    )

    # Bounded once per base tip: the same collision against the same base is
    # reported, never rebased a second time.
    assert [r.action for r in again] == ["conflict"]
    assert len([e for e in _events(lane_repo) if e.get("event") == "lane_document_conflict"]) == 1
    assert git(lane_repo, "rev-parse", document) == tip
    assert len(published) == 1


def test_a_pass_holding_no_document_asks_the_forge_nothing(lane_repo, tmp_path):
    # S-0083/D-12: the lane's own records are what it holds open, so an idle
    # night spends no forge call at all.
    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7308", "eight.py", "eight = 8\n")
    _contract(lane_repo, "T-7308", "S-0915")
    published: list[tuple[str, str]] = []
    asked: list[str] = []

    results = process_lane(
        lane_repo,
        GitLane(),
        publish=_recording_publisher(published),
        forge=_forge(_pr(state="merged", merge_commit="e" * 40), asked),
        unit="document",
    )

    assert asked == []
    assert [r.action for r in results] == ["landed"]


def test_the_served_leg_publishes_and_reads_back_as_the_manual_verb_does(lane_repo, tmp_path):
    # S-0083/D-15: handed neither, an armed pass under `landing: pull_request`
    # fast-forwards the checkout's base locally — the one act the mode exists
    # to avoid.
    import torve.cli.merge as merge_module

    _origin(lane_repo, tmp_path)
    candidate(lane_repo, "T-7309", "nine.py", "nine = 9\n")
    _contract(lane_repo, "T-7309", "S-0916")
    base_before = git(lane_repo, "rev-parse", "main")

    config = RunnerConfig(
        promotion=PromotionConfig(auto_merge=True, landing="pull_request", unit="document")
    )
    published: list[tuple[str, str]] = []
    asked: list[str] = []
    original = (merge_module._publisher, merge_module._forge)
    merge_module._publisher = lambda root, config: _recording_publisher(published)
    merge_module._forge = lambda config: _forge(_pr(number=26, state="open"), asked)

    try:
        leg = _lane_leg(lane_repo, config, only=None)
        assert leg is not None
        assert asyncio.run(leg()) == ["T-7309"]
        assert asyncio.run(leg()) == []
    finally:
        merge_module._publisher, merge_module._forge = original

    document = naming.document_branch("S-0916")
    assert published == [("T-7309", document)]
    # The second pass asked about the document the first left open, and the
    # checkout's base was never moved by either.
    assert asked == [document]
    assert git(lane_repo, "rev-parse", "main") == base_before
