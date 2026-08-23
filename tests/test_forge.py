"""RFC 0010 phase 2: the credentialed forge leg — PR bodies from data only,
the token resolved by name at the runner boundary and never on argv."""

from __future__ import annotations

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
                             results, tmp_path)
    assert title == "T-8301: Rotate the keys."
    long_task = task_with_contract().model_copy(update={"intent": "x" * 200})
    long_title, _ = compose_pr(long_task, 1, "d", meta, [], tmp_path)
    assert len(long_title) <= len("T-8301: ") + 72
    assert "attempt 2" in body and "`cafecafe1234`" in body
    assert "- `uv run pytest`" in body
    assert "- scope: pass (0.2s)" in body
    assert "- D-9 (LOCKED): keys rotate" in body
    # Divergences surface; routine resolved entries do not.
    assert "D-9 departed: took the other road" in body
    assert "routine" not in body
    assert "cost: $0.0123" in body and "trace://run/1" in body


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
