"""RFC 0010 phase 2: the credentialed forge leg — PR bodies from data only,
the token resolved by name at the runner boundary and never on argv."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import torve.adapters.vcs.git as git_module
from torve.adapters.vcs.git import GhScm, GitVcs
from torve.application.forge import compose_pr
from torve.domain.attempt import GateResult
from torve.domain.task import InheritedDecision, Scope, Task


def task_with_contract() -> Task:
    return Task(
        id="T-8301", intent="Rotate the keys.\nBecause they leaked.",
        scope=Scope(), acceptance=["uv run pytest"],
        decisions=[InheritedDecision(id="D-9", grade="LOCKED", text="keys rotate")])


def test_the_pr_is_composed_from_records_never_prose(tmp_path: Path):
    log_dir = tmp_path / ".torve" / "tasks" / "T-8301"
    log_dir.mkdir(parents=True)
    (log_dir / "log.yaml").write_text(
        "schema_version: 1\ntask: T-8301\ndrift_count: 1\nentries:\n"
        "  - decision: D-9\n    kind: departed\n    claim: took the other road\n"
        "  - decision: D-9\n    kind: resolved\n    claim: routine\n",
        encoding="utf-8")
    results = [GateResult(name="scope", outcome="pass", state="blocking",
                          duration_s=0.2)]
    meta = {"adapter": "harness", "model": "deepseek-chat",
            "cost_usd": 0.0123, "trace_ref": "trace://run/1"}

    title, body = compose_pr(task_with_contract(), 2, "cafecafe1234", meta,
                             results, tmp_path,
                             changed=["src/keys.py", "tests/test_keys.py"])
    assert title == "T-8301: Rotate the keys."
    long_task = task_with_contract().model_copy(update={"intent": "x" * 200})
    long_title, _ = compose_pr(long_task, 1, "d", meta, [], tmp_path)
    assert len(long_title) <= len("T-8301: ") + 72
    assert "attempt 2" in body and "`cafecafe1234`" in body
    # The body leads with what changed and where the control surface is;
    # the contract folds behind a details block (owner feedback: the wall
    # of intent buried the decision-relevant facts).
    assert "- `src/keys.py`" in body
    assert "merge button is never used" in body
    assert "supersedes the previous candidate" in body  # attempt 2 note
    assert "<details><summary>Contract</summary>" in body
    assert "- `uv run pytest`" in body
    assert "all 1 pass (slowest: scope 0.2s)" in body
    assert "- D-9 (LOCKED): keys rotate" in body
    # Divergences surface; routine resolved entries do not.
    assert "D-9 departed: took the other road" in body
    assert "routine" not in body
    assert "cost: $0.0123" in body and "trace://run/1" in body

    # A red gate itemizes instead of summarizing; a host path shrinks to
    # its basename while a URI trace stays whole.
    red = [GateResult(name="scope", outcome="fail", state="blocking",
                      duration_s=0.1)]
    host_meta = dict(meta, trace_ref="/home/op/lab/.wt/T-8301.a1.trace.log")
    _, red_body = compose_pr(task_with_contract(), 1, "d", host_meta,
                             red, tmp_path)
    assert "- scope: fail (0.1s)" in red_body
    assert "trace: T-8301.a1.trace.log" in red_body
    assert "/home/op" not in red_body


def test_the_push_token_reaches_git_by_environment_never_argv(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        if command[-1] == "remote":
            return subprocess.CompletedProcess(command, 0, stdout="origin\n", stderr="")
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    assert GitVcs().push(repo, "torve/T-8301", token="sekrit-value") is True
    command = [str(part) for part in captured["command"]]  # type: ignore[index]
    assert all("sekrit-value" not in part for part in command)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["TORVE_PUSH_TOKEN"] == "sekrit-value"
    assert any("credential.helper" in part for part in command)


def test_gh_receives_the_named_token_and_the_configured_repo(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0,
                                           stdout="https://github.com/example/lab/pull/1\n",
                                           stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    monkeypatch.setenv("LAB_TOKEN", "sekrit-value")
    scm = GhScm(repo="example/lab", token_env="LAB_TOKEN")
    url = scm.open_pr(tmp_path, "torve/T-8301", "title", "body")
    assert url == "https://github.com/example/lab/pull/1"
    command = [str(part) for part in captured["command"]]  # type: ignore[index]
    assert command[-2:] == ["--repo", "example/lab"]
    assert all("sekrit-value" not in part for part in command)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "sekrit-value"


def test_a_named_but_absent_token_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    scm = GhScm(repo="example/lab", token_env="MISSING_TOKEN")
    with pytest.raises(RuntimeError, match="MISSING_TOKEN"):
        scm.open_pr(tmp_path, "torve/T-8301", "title", "body")


def test_open_pr_reuses_the_branchs_open_pull_request(tmp_path, monkeypatch):
    # One pull request per task (D-10.10, A-37): a create refused because
    # the branch already has one finds it, refreshes title and body, and
    # returns its url — attempts iterate one thread of review.
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        cmd = [str(part) for part in command]
        calls.append(cmd)
        joined = " ".join(cmd)
        if "pr create" in joined:
            return subprocess.CompletedProcess(
                command, 1, stdout="",
                stderr="a pull request for branch \"torve/T-8302\" "
                       "already exists: https://github.com/example/lab/pull/31")
        if "pr list" in joined:
            return subprocess.CompletedProcess(
                command, 0, stderr="", stdout=json.dumps(
                    [{"number": 31,
                      "url": "https://github.com/example/lab/pull/31"}]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    scm = GhScm(repo="example/lab", token_env=None)
    url = scm.open_pr(tmp_path, "torve/T-8302", "attempt 2", "fresh body")
    assert url == "https://github.com/example/lab/pull/31"
    edits = [c for c in calls if "edit" in c]
    assert edits and "--body" in edits[0] and "fresh body" in edits[0]


# ....................... #
# GhCi (RFC 0006 §3): the lightweight runs endpoint, polled with backoff,
# settling to one word — the rate budget is shared with the agents.


def ci_with_script(monkeypatch, bodies: list[str], **kwargs):
    from torve.adapters.vcs.git import GhCi

    calls: dict[str, object] = {"commands": [], "sleeps": []}

    def fake_run(command, **run_kwargs):
        calls["commands"].append(command)  # type: ignore[union-attr]
        calls["env"] = run_kwargs.get("env")
        body = bodies[min(len(calls["commands"]) - 1, len(bodies) - 1)]  # type: ignore[arg-type]
        return subprocess.CompletedProcess(command, 0, stdout=body, stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    ci = GhCi("example/lab", token_env="LAB_TOKEN",
              sleeper=lambda s: calls["sleeps"].append(s), **kwargs)  # type: ignore[union-attr]
    return ci, calls


def test_ghci_polls_with_backoff_until_the_run_settles(monkeypatch):
    monkeypatch.setenv("LAB_TOKEN", "sekrit-value")
    pending = '[{"status": "in_progress", "conclusion": null, "workflow_id": 7}]'
    green = '[{"status": "completed", "conclusion": "success", "workflow_id": 7}]'
    ci, calls = ci_with_script(monkeypatch, [pending, pending, green], delay_s=7.0)

    assert ci.conclusion("cafe" * 10) == "success"
    assert calls["sleeps"] == [7.0, 7.0]  # two waits, then the settled verdict
    first = [str(part) for part in calls["commands"][0]]  # type: ignore[index]
    assert any("repos/example/lab/actions/runs?head_sha=" + "cafe" * 10 in part
               for part in first)
    env = calls["env"]
    assert isinstance(env, dict) and env["GH_TOKEN"] == "sekrit-value"


def test_ghci_settles_to_absent_and_failure(monkeypatch):
    monkeypatch.setenv("LAB_TOKEN", "sekrit-value")
    ci, calls = ci_with_script(monkeypatch, ["[]"], attempts=3)
    assert ci.conclusion("abc1234") == "absent"
    assert len(calls["sleeps"]) == 2  # absence is retried — CI may be starting

    red = '[{"status": "completed", "conclusion": "failure", "workflow_id": 7}]'
    ci, _ = ci_with_script(monkeypatch, [red])
    assert ci.conclusion("abc1234") == "failure"


def test_ghci_lets_a_rerun_supersede_the_run_it_replaced(monkeypatch):
    # Newest first from the API: a green re-run outranks the stale failure
    # of the same workflow; a red run of a DIFFERENT workflow still vetoes.
    monkeypatch.setenv("LAB_TOKEN", "sekrit-value")
    rerun = ('[{"status": "completed", "conclusion": "success", "workflow_id": 7},'
             ' {"status": "completed", "conclusion": "failure", "workflow_id": 7}]')
    ci, _ = ci_with_script(monkeypatch, [rerun])
    assert ci.conclusion("abc1234") == "success"

    mixed = ('[{"status": "completed", "conclusion": "success", "workflow_id": 7},'
             ' {"status": "completed", "conclusion": "failure", "workflow_id": 8}]')
    ci, _ = ci_with_script(monkeypatch, [mixed])
    assert ci.conclusion("abc1234") == "failure"
