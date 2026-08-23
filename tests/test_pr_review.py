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
