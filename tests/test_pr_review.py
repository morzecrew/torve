"""RFC 0005 §4, the forge leg: review on pull-request open and update,
including pull requests no agent wrote — skip rules, one review per head
(the pull regime's debounce), degraded mode without a task contract told
so explicitly (D-5.8), and the runner posting the findings comment while
the reviewer holds no forge credential (D-5.2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_run_loop import MockRuntime

import torve.adapters.vcs.git as git_module
from torve.adapters.vcs.git import GhScm, GitVcs
from torve.application.ports import AgentResult, PrInfo
from torve.application.review import PR_LEDGER, review_pull_request
from torve.config.runconfig import RunnerConfig

# ----------------------- #


class RecordingAgent:
    def __init__(self, output: str = '{"findings": []}') -> None:
        self.output = output
        self.prompts: list[str] = []

    def run(self, ctx):
        self.prompts.append(ctx.prompt)
        return AgentResult(exit_code=0, output=self.output)


class FakePrScm:
    def __init__(self, info: PrInfo) -> None:
        self.info = info
        self.comments: list[tuple[int, str, str]] = []

    def pr_info(self, number: int) -> PrInfo:
        return self.info

    def comment(self, number: int, body: str, key: str) -> str:
        self.comments.append((number, body, key))
        return f"https://example.invalid/pr/{number}#comment"


class FakePrVcs:
    def __init__(self, trailers: list[str] | None = None,
                 refuse_fetch: bool = False) -> None:
        self.trailers = trailers or []
        self.refuse_fetch = refuse_fetch
        self.removed: list[Path] = []

    def fetch_pr(self, root, number, base_ref, token=None):
        if self.refuse_fetch:
            raise AssertionError("a skipped pull request must not be fetched")
        return ("b" * 40, "a" * 40)

    def worktree_at(self, root, sha, workdir):
        Path(workdir).mkdir(parents=True, exist_ok=True)

    def remove_worktree(self, root, workdir):
        self.removed.append(Path(workdir))

    def diff(self, root, base, head):
        return "diff --git a/x b/x\n+organic change\n"

    def task_trailers(self, root, base, head):
        return list(self.trailers)


def pr_info(**overrides) -> PrInfo:
    values = {
        "number": 7, "title": "an organic change", "author": "misery7100",
        "draft": False, "head_sha": "a" * 40, "base_ref": "main",
        "changed_files": 2, "state": "open",
    }
    values.update(overrides)
    return PrInfo(**values)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8")
    return tmp_path


def config(**review) -> RunnerConfig:
    return RunnerConfig.model_validate(
        {"review": {"on": ["pr_opened", "pr_synchronized"], **review}})


def run(root: Path, scm: FakePrScm, vcs: FakePrVcs, cfg: RunnerConfig | None = None,
        agent: RecordingAgent | None = None):
    return review_pull_request(
        root, cfg or config(), MockRuntime(), agent or RecordingAgent(),
        scm, vcs, 7)


def test_skip_rules_run_before_any_fetch(root):
    vcs = FakePrVcs(refuse_fetch=True)
    for info, why in [
        (pr_info(draft=True), "draft"),
        (pr_info(changed_files=0), "no changed files"),
        (pr_info(state="merged"), "pull request is merged"),
    ]:
        outcome = run(root, FakePrScm(info), vcs)
        assert (outcome.action, outcome.detail) == ("skipped", why)


def test_a_configured_author_is_skipped(root):
    cfg = config(skip_authors=["dependabot"])
    outcome = run(root, FakePrScm(pr_info(author="dependabot")),
                  FakePrVcs(refuse_fetch=True), cfg)
    assert outcome.action == "skipped" and "dependabot" in outcome.detail


def test_an_organic_pr_reviews_degraded_and_posts_one_comment(root):
    agent = RecordingAgent()
    scm = FakePrScm(pr_info())
    vcs = FakePrVcs()
    outcome = run(root, scm, vcs, agent=agent)

    assert outcome.action == "reviewed" and outcome.review_id is not None
    # D-5.8: told so explicitly, never an invented specification.
    assert "degraded mode" in agent.prompts[0]
    # The runner posted, keyed to pr and head.
    number, body, key = scm.comments[0]
    assert (number, key) == (7, f"review:7:{'a' * 12}")
    assert "degraded input" in body
    # The minted review contract targets the pull request, not a task.
    contract = (root / ".torve" / "tasks" / outcome.review_id /
                "contract.yaml").read_text()
    assert "PR-7" in contract
    # The disposable worktree is gone.
    assert vcs.removed


def test_a_head_reviews_at_most_once(root):
    scm = FakePrScm(pr_info())
    first = run(root, scm, FakePrVcs())
    again = run(root, scm, FakePrVcs())
    assert first.action == "reviewed"
    assert again.action == "already reviewed"
    assert len(scm.comments) == 1
    rows = [json.loads(line) for line in
            (root / ".torve" / PR_LEDGER).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["pr"] == 7


def test_a_new_head_reviews_again(root):
    scm = FakePrScm(pr_info())
    run(root, scm, FakePrVcs())
    scm.info = pr_info(head_sha="c" * 40)
    outcome = run(root, scm, FakePrVcs())
    assert outcome.action == "reviewed"
    assert len(scm.comments) == 2


def test_a_torve_task_trailer_maps_to_its_contract(root):
    contract_dir = root / ".torve" / "tasks" / "T-0101"
    contract_dir.mkdir(parents=True)
    (contract_dir / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0101\nrole: implement\n"
        "intent: the mapped task intent sentence\ndecisions: []\n",
        encoding="utf-8")
    agent = RecordingAgent()
    outcome = run(root, FakePrScm(pr_info()), FakePrVcs(trailers=["T-0101"]),
                  agent=agent)
    assert outcome.action == "reviewed"
    # Task-informed, not degraded: the contract's intent reaches the prompt.
    assert "the mapped task intent sentence" in agent.prompts[0]
    assert "degraded mode" not in agent.prompts[0]


def test_findings_reach_the_comment_blockers_first(root):
    output = json.dumps({"findings": [
        {"severity": "minor", "claim": "a nit", "evidence": "x.py:1 — minor"},
        {"severity": "blocker", "claim": "wrong", "evidence": "x.py:2 — bad"},
    ]})
    root_x = root / "x.py"
    root_x.write_text("line1\nline2\n", encoding="utf-8")
    agent = RecordingAgent(output=output)

    class TreeVcs(FakePrVcs):
        def worktree_at(self, _root, sha, workdir):
            Path(workdir).mkdir(parents=True, exist_ok=True)
            (Path(workdir) / "x.py").write_text("line1\nline2\n", encoding="utf-8")

    scm = FakePrScm(pr_info())
    outcome = run(root, scm, TreeVcs(), agent=agent)
    assert (outcome.findings, outcome.blockers) == (2, 1)
    body = scm.comments[0][1]
    assert body.index("[blocker]") < body.index("[minor]")


def test_pr_triggers_join_the_config_vocabulary():
    assert config().review.on == ["pr_opened", "pr_synchronized"]
    with pytest.raises(ValueError, match="unsupported review trigger"):
        RunnerConfig.model_validate({"review": {"on": ["pr_closed"]}})


# ....................... #
# The GitHub adapter surface, over scripted gh output.


def scripted_gh(monkeypatch, responses: dict[str, str]):
    calls: list[str] = []

    def fake_run(command, **kwargs):
        cmd = " ".join(str(part) for part in command)
        calls.append(cmd)
        for marker, body in responses.items():
            if marker in cmd:
                return subprocess.CompletedProcess(command, 0, stdout=body, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    return calls


def test_ghscm_pr_info_parses_the_forge_shape(monkeypatch):
    view = json.dumps({
        "number": 12, "title": "t", "author": {"login": "someone"},
        "isDraft": False, "headRefOid": "d" * 40, "baseRefName": "main",
        "changedFiles": 3, "state": "OPEN"})
    scripted_gh(monkeypatch, {"pr view": view})
    info = GhScm("example/lab", token_env=None).pr_info(12)
    assert (info.number, info.author, info.state) == (12, "someone", "open")
    assert info.head_sha == "d" * 40 and info.changed_files == 3


def test_ghscm_review_threads_allow_lists_roots_and_keeps_replies(monkeypatch):
    # D-5.12: a stranger's root comment never reaches an agent; replies
    # in a kept thread ride along — they carry resolution.
    listed = json.dumps([{"number": 12}])
    comments = json.dumps([
        {"id": 1, "in_reply_to_id": None, "path": "a.py", "line": 3,
         "user": {"login": "coderabbitai[bot]"}, "body": "root finding"},
        {"id": 2, "in_reply_to_id": 1, "path": "a.py", "line": 3,
         "user": {"login": "Misery7100"}, "body": "fixed in abc"},
        {"id": 3, "in_reply_to_id": None, "path": "b.py", "line": 9,
         "user": {"login": "drive-by"}, "body": "ignore me"},
    ])
    scripted_gh(monkeypatch, {"pr list": listed, "pulls/12/comments": comments})
    threads = GhScm("example/lab", token_env=None).review_threads(
        "torve/T-0100", ("coderabbitai[bot]", "Misery7100"))
    assert len(threads) == 1
    assert threads[0]["path"] == "a.py"
    # The reply address rides the capture (D-5.14, A-41).
    assert threads[0]["id"] == 1 and threads[0]["pr"] == 12
    assert [c["author"] for c in threads[0]["comments"]] == [
        "coderabbitai[bot]", "Misery7100"]
    # An empty allow-list is off — no forge calls at all.
    calls = scripted_gh(monkeypatch, {"pr list": listed})
    assert GhScm("example/lab", token_env=None).review_threads(
        "torve/T-0100", ()) == []
    assert calls == []


def test_answer_captured_threads_posts_once_and_absorbs_replays(monkeypatch):
    # D-5.14 (A-41): one reply per captured root, marker-deduped at the
    # destination — a thread already marked is skipped, not re-answered.
    existing = json.dumps([
        {"id": 5, "body": "old reply\n\n<!-- torve-key:answer:5 -->"}])
    calls = scripted_gh(monkeypatch, {"replies": "{}",
                                      "pulls/12/comments": existing})
    scm = GhScm("example/lab", token_env=None)
    posted, skipped = scm.answer_captured_threads(
        [{"pr": 12, "id": 5}, {"pr": 12, "id": 7}], "landed as abc123")
    assert (posted, skipped) == (1, 1)
    reply_calls = [c for c in calls if "replies" in c]
    assert len(reply_calls) == 1 and "comments/7/replies" in reply_calls[0]
    assert "torve-key:answer:7" in reply_calls[0]


def test_ghscm_close_pr_closes_and_deletes_or_noops(monkeypatch):
    # T-0072: an ff landing closes its own reading surface — comment,
    # close, branch deleted. No open PR for the branch is a no-op.
    listed = json.dumps([{"number": 31}])
    calls = scripted_gh(monkeypatch, {"pr list": listed})
    assert GhScm("example/lab", token_env=None).close_pr(
        "torve/T-0100", "landed as abc") is True
    assert any("pr close 31" in c and "--delete-branch" in c for c in calls)

    bare = scripted_gh(monkeypatch, {"pr list": "[]"})
    assert GhScm("example/lab", token_env=None).close_pr(
        "torve/T-0100", "landed as abc") is False
    assert not any("pr close" in c for c in bare)


def test_ghscm_comment_dedupes_on_the_marker(monkeypatch):
    existing = json.dumps({"comments": [
        {"body": "torve review\n\n<!-- torve-key:review:12:abc -->"}]})
    calls = scripted_gh(monkeypatch, {"pr view": existing})
    url = GhScm("example/lab", token_env=None).comment(12, "again", "review:12:abc")
    assert url == ""
    assert not any("pr comment" in c for c in calls)


# ....................... #
# The git surface over real repositories: a bare origin carrying a
# refs/pull/N/head ref, exactly as the forge serves it.


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_gitvcs_pr_surface_over_a_local_origin(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    git(seed, "config", "user.name", "Seed")
    git(seed, "config", "user.email", "seed@example.invalid")
    (seed / "base.py").write_text("base = 1\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "--no-gpg-sign", "-m", "init")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(seed, "checkout", "-q", "-b", "feature")
    (seed / "feature.py").write_text("feature = 2\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "--no-gpg-sign", "-m",
        "work\n\nTorve-Task: T-0042")
    git(seed, "push", "-q", "origin", "HEAD:refs/pull/7/head")

    root = tmp_path / "engine"
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True)
    vcs = GitVcs()
    base_sha, head_sha = vcs.fetch_pr(root, 7, "main")
    assert base_sha != head_sha

    assert vcs.task_trailers(root, base_sha, head_sha) == ["T-0042"]
    diff = vcs.diff(root, base_sha, head_sha)
    assert "feature = 2" in diff and "base = 1" not in diff

    workdir = tmp_path / "wt"
    vcs.worktree_at(root, head_sha, workdir)
    assert (workdir / "feature.py").is_file()
    vcs.remove_worktree(root, workdir)
    assert not workdir.exists()


def test_ghscm_retries_a_transient_failure_once(monkeypatch):
    # T-0058, the same transport contract as the tracker adapter's.
    view = json.dumps({"number": 12, "title": "t", "author": {"login": "x"},
                       "isDraft": False, "headRefOid": "e" * 40,
                       "baseRefName": "main", "changedFiles": 1,
                       "state": "OPEN"})
    outcomes = iter([
        subprocess.CompletedProcess([], 1, stdout="",
                                    stderr="net/http: TLS handshake timeout"),
        subprocess.CompletedProcess([], 0, stdout=view, stderr=""),
    ])
    monkeypatch.setattr(git_module.subprocess, "run",
                        lambda *a, **k: next(outcomes))
    naps: list[float] = []
    scm = GhScm("example/lab", token_env=None, sleeper=naps.append)
    assert scm.pr_info(12).number == 12
    assert naps == [2.0]


def test_delete_remote_branch_over_a_local_origin(tmp_path: Path) -> None:
    # T-0059: the retry command's re-queue cleanup — a ref deletion, never
    # a rewrite; absent refs and missing origins answer False, quietly.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    git(repo, "config", "user.name", "T")
    git(repo, "config", "user.email", "t@example.invalid")
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "init")
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/torve/T-0042")

    vcs = GitVcs()
    assert vcs.delete_remote_branch(repo, "torve/T-0042") is True
    assert "torve/T-0042" not in git(origin, "for-each-ref", "--format=%(refname)")
    # Already gone: the postcondition holds, not an error.
    assert vcs.delete_remote_branch(repo, "torve/T-0042") is True

    lonely = tmp_path / "lonely"
    subprocess.run(["git", "init", "-q", str(lonely)], check=True)
    assert vcs.delete_remote_branch(lonely, "torve/T-0042") is False


def test_republish_branch_moves_the_candidate_to_its_landed_tip(tmp_path: Path) -> None:
    # D-19.12 (A-34): a rebased landing republishes its branch — a leased
    # ref update in the engine-owned namespace, at landing time only, so
    # the forge recognizes the base push as the merge.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    git(repo, "config", "user.name", "T")
    git(repo, "config", "user.email", "t@example.invalid")
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "init")
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(repo, "checkout", "-q", "-b", "torve/T-0042")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "candidate")
    git(repo, "push", "-q", "-u", "origin", "torve/T-0042")
    superseded = git(repo, "rev-parse", "HEAD").strip()
    # The lane's rebase rewrites the tip; an amend stands in for it here.
    git(repo, "commit", "-q", "--no-gpg-sign", "--amend", "-m", "candidate rebased")
    landed = git(repo, "rev-parse", "HEAD").strip()

    assert GitVcs().republish_branch(repo, "torve/T-0042") is True
    at_origin = git(origin, "rev-parse", "refs/heads/torve/T-0042").strip()
    assert at_origin == landed
    assert at_origin != superseded

    lonely = tmp_path / "lonely"
    subprocess.run(["git", "init", "-q", str(lonely)], check=True)
    assert GitVcs().republish_branch(lonely, "torve/T-0042") is False


def test_push_supersedes_only_when_asked(tmp_path: Path) -> None:
    # D-10.10 (A-37): a new attempt supersedes the task's persistent
    # branch under lease; without supersede the push stays additive —
    # which is how the base is pushed (D-19.9), pinned by the refusal.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    git(repo, "config", "user.name", "T")
    git(repo, "config", "user.email", "t@example.invalid")
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "attempt 1")
    vcs = GitVcs()
    assert vcs.push(repo, "torve/T-0043") is True

    git(repo, "commit", "-q", "--no-gpg-sign", "--amend", "-m", "attempt 2")
    superseding = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(RuntimeError):
        vcs.push(repo, "torve/T-0043")  # additive push refuses the rewrite
    assert vcs.push(repo, "torve/T-0043", supersede=True) is True
    at_origin = git(origin, "rev-parse", "refs/heads/torve/T-0043").strip()
    assert at_origin == superseding


def test_retire_pr_defers_to_the_forge_then_falls_back(monkeypatch):
    # D-19.13 (A-34): a landing the forge marked merged needs no close;
    # one still open after the grace closes with the landing note — the
    # T-0072 close-out as fallback; no pull request retires as "absent".
    merged = json.dumps([{"number": 31, "state": "MERGED"}])
    calls = scripted_gh(monkeypatch, {"pr list": merged})
    naps: list[float] = []
    scm = GhScm("example/lab", token_env=None, sleeper=naps.append)
    assert scm.retire_pr("torve/T-0100", "landed") == "merged"
    assert not any("pr close" in c for c in calls)
    assert naps == []

    stubborn = json.dumps([{"number": 31, "state": "OPEN"}])
    calls = scripted_gh(monkeypatch, {"pr list": stubborn})
    naps = []
    scm = GhScm("example/lab", token_env=None, sleeper=naps.append)
    assert scm.retire_pr("torve/T-0100", "landed") == "closed"
    assert any("pr close 31" in c and "--delete-branch" in c for c in calls)
    assert naps == [2.0, 2.0]

    bare = scripted_gh(monkeypatch, {"pr list": "[]"})
    assert GhScm("example/lab", token_env=None).retire_pr(
        "torve/T-0100", "landed") == "absent"
    assert not any("pr close" in c for c in bare)


def test_retire_pr_sees_a_flip_inside_the_grace(monkeypatch):
    # The forge's merge detection is asynchronous: open on the first look,
    # merged on the second — the grace exists exactly for this.
    bodies = iter([
        json.dumps([{"number": 31, "state": "OPEN"}]),
        json.dumps([{"number": 31, "state": "MERGED"}]),
    ])
    calls: list[str] = []

    def fake_run(command, **kwargs):
        cmd = " ".join(str(part) for part in command)
        calls.append(cmd)
        return subprocess.CompletedProcess(command, 0, stdout=next(bodies), stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    naps: list[float] = []
    scm = GhScm("example/lab", token_env=None, sleeper=naps.append)
    assert scm.retire_pr("torve/T-0100", "landed") == "merged"
    assert naps == [2.0]
    assert not any("pr close" in c for c in calls)
