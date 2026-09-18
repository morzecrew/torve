"""S-0010 phase 2: the credentialed forge leg — PR bodies from data only,
the token resolved by name at the runner boundary and never on argv."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import torve.adapters.vcs.git as git_module
from torve.adapters.vcs.git import GhScm, GitVcs
from torve.application.forge import DocumentLanding, compose_document_pr, compose_pr
from torve.domain.attempt import GateResult
from torve.domain.task import InheritedDecision, Scope, Task


def task_with_contract() -> Task:
    return Task(
        id="T-8301",
        intent="Rotate the keys.\nBecause they leaked.",
        scope=Scope(),
        acceptance=["uv run pytest"],
        decisions=[InheritedDecision(id="D-9", grade="LOCKED", text="keys rotate")],
    )


def test_the_pr_is_composed_from_records_never_prose(tmp_path: Path):
    log_dir = tmp_path / ".torve" / "tasks" / "T-8301"
    log_dir.mkdir(parents=True)
    (log_dir / "log.yaml").write_text(
        "schema_version: 1\ntask: T-8301\ndrift_count: 1\nentries:\n"
        "  - decision: D-9\n    kind: departed\n    claim: took the other road\n"
        "  - decision: D-9\n    kind: resolved\n    claim: routine\n",
        encoding="utf-8",
    )
    results = [GateResult(name="scope", outcome="pass", state="blocking", duration_s=0.2)]
    meta = {
        "adapter": "harness",
        "model": "deepseek-chat",
        "cost_usd": 0.0123,
        "trace_ref": "trace://run/1",
    }

    title, body = compose_pr(
        task_with_contract(),
        2,
        "cafecafe1234",
        meta,
        results,
        tmp_path,
        changed=["src/keys.py", "tests/test_keys.py"],
    )
    assert title == "T-8301: Rotate the keys."
    long_task = task_with_contract().model_copy(update={"intent": "x" * 200})
    long_title, _ = compose_pr(long_task, 1, "d", meta, [], tmp_path)
    assert len(long_title) <= len("T-8301: ") + 72
    assert "attempt 2" in body and "`cafecafe1234`" in body
    # The body leads with the contract — the paragraph a reviewer reads
    # first, in the open and not behind a details block (owner feedback) —
    # then what changed and where the control surface is.
    assert "## Contract" in body and "<details>" not in body
    assert body.index("## Contract") < body.index("## Changed")
    assert "- `src/keys.py`" in body
    assert "merge button is never used" in body
    assert "supersedes the previous candidate" in body  # attempt 2 note
    assert "- `uv run pytest`" in body
    assert "all 1 pass (slowest: scope 0.2s)" in body
    # The rows a contract carried are a table, so the grade is a column.
    assert "| Decision | Grade | Text |" in body
    assert "| D-9 | `LOCKED` | keys rotate |" in body
    # Divergences surface; routine resolved entries do not.
    assert "D-9 departed: took the other road" in body
    assert "routine" not in body
    assert "cost: $0.0123" in body and "trace://run/1" in body

    # A red gate itemizes instead of summarizing; a host path shrinks to
    # its basename while a URI trace stays whole.
    red = [GateResult(name="scope", outcome="fail", state="blocking", duration_s=0.1)]
    host_meta = dict(meta, trace_ref="/home/op/lab/.wt/T-8301.a1.trace.log")
    _, red_body = compose_pr(task_with_contract(), 1, "d", host_meta, red, tmp_path)
    assert "- scope: fail (0.1s)" in red_body
    assert "trace: T-8301.a1.trace.log" in red_body
    assert "/home/op" not in red_body


def test_the_landings_body_names_its_document_and_drops_the_ff_sentence(tmp_path: Path):
    # S-0080/D-4, S-0080/D-5: a body opened by the lane is the landing's
    # record — the document it was minted from is in it — and it never tells
    # a reader not to press the one control the mode depends on.
    task = task_with_contract().model_copy(update={"spec": "S-0080"})
    meta = {"adapter": "harness", "model": "deepseek-chat"}

    _, body = compose_pr(task, 1, "d", meta, [], tmp_path, landing="pull_request")
    assert "S-0080" in body
    assert "merge button" not in body
    assert "| D-9 | `LOCKED` | keys rotate |" in body

    _, local_body = compose_pr(task, 1, "d", meta, [], tmp_path)
    assert "merge button is never used" in local_body


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
        return subprocess.CompletedProcess(
            command, 0, stdout="https://github.com/example/lab/pull/1\n", stderr=""
        )

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
    # One pull request per task (S-0010/D-10, A-37): a create refused because
    # the branch already has one finds it, refreshes title and body, and
    # returns its url — attempts iterate one thread of review.
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        cmd = [str(part) for part in command]
        calls.append(cmd)
        joined = " ".join(cmd)
        if "pr create" in joined:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr='a pull request for branch "torve/T-8302" '
                "already exists: https://github.com/example/lab/pull/31",
            )
        if "pr list" in joined:
            return subprocess.CompletedProcess(
                command,
                0,
                stderr="",
                stdout=json.dumps(
                    [{"number": 31, "url": "https://github.com/example/lab/pull/31"}]
                ),
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    scm = GhScm(repo="example/lab", token_env=None)
    url = scm.open_pr(tmp_path, "torve/T-8302", "attempt 2", "fresh body")
    assert url == "https://github.com/example/lab/pull/31"
    edits = [c for c in calls if "edit" in c]
    assert edits and "--body" in edits[0] and "fresh body" in edits[0]


def test_pr_for_branch_answers_with_the_merge_commit(monkeypatch):
    # S-0080/D-6: the lane's one question — what happened to this branch —
    # asked by head branch, answered with the sha a squash merge landed in.
    commands: list[list[str]] = []
    listed = json.dumps(
        [
            {
                "number": 44,
                "title": "T-8301: Rotate the keys.",
                "author": {"login": "torve"},
                "isDraft": False,
                "headRefOid": "head" * 10,
                "baseRefName": "main",
                "changedFiles": 2,
                "state": "MERGED",
                "mergeCommit": {"oid": "squash" * 6},
            }
        ]
    )

    def fake_run(command, **kwargs):
        commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, stdout=listed, stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    scm = GhScm(repo="example/lab", token_env=None)
    info = scm.pr_for_branch("torve/T-8301")

    assert info is not None
    assert (info.number, info.state, info.merge_commit) == (44, "merged", "squash" * 6)
    assert commands[0][:6] == ["gh", "pr", "list", "--head", "torve/T-8301", "--state"]

    commands.clear()
    monkeypatch.setattr(
        git_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="[]", stderr=""),
    )
    assert GhScm(repo="example/lab", token_env=None).pr_for_branch("torve/T-8302") is None


# ....................... #
# GhCi (S-0006/promotion): the lightweight runs endpoint, polled with backoff,
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
    ci = GhCi(
        "example/lab", token_env="LAB_TOKEN", sleeper=lambda s: calls["sleeps"].append(s), **kwargs
    )  # type: ignore[union-attr]
    return ci, calls


def test_ghci_polls_with_backoff_until_the_run_settles(monkeypatch):
    monkeypatch.setenv("LAB_TOKEN", "sekrit-value")
    pending = '[{"status": "in_progress", "conclusion": null, "workflow_id": 7}]'
    green = '[{"status": "completed", "conclusion": "success", "workflow_id": 7}]'
    ci, calls = ci_with_script(monkeypatch, [pending, pending, green], delay_s=7.0)

    assert ci.conclusion("cafe" * 10) == "success"
    assert calls["sleeps"] == [7.0, 7.0]  # two waits, then the settled verdict
    first = [str(part) for part in calls["commands"][0]]  # type: ignore[index]
    assert any("repos/example/lab/actions/runs?head_sha=" + "cafe" * 10 in part for part in first)
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
    rerun = (
        '[{"status": "completed", "conclusion": "success", "workflow_id": 7},'
        ' {"status": "completed", "conclusion": "failure", "workflow_id": 7}]'
    )
    ci, _ = ci_with_script(monkeypatch, [rerun])
    assert ci.conclusion("abc1234") == "success"

    mixed = (
        '[{"status": "completed", "conclusion": "success", "workflow_id": 7},'
        ' {"status": "completed", "conclusion": "failure", "workflow_id": 8}]'
    )
    ci, _ = ci_with_script(monkeypatch, [mixed])
    assert ci.conclusion("abc1234") == "failure"


# ....................... #
# The document composer (S-0083/D-8): one body for the landings a document
# branch carries so far, with the phases still to come read from the
# document's own phasing list (S-0083/D-17) — never a count somebody derived.

PHASES = [
    {
        "phase": 1,
        "title": "the unit is a term",
        "intent": "Give promotion a unit.",
        "scope": ["src/torve/config/runconfig.py"],
    },
    {
        "phase": 1,
        "title": "a document has a branch name",
        "intent": "Name the branch from the spec.",
        "scope": ["src/torve/base/naming.py"],
    },
    {
        "phase": 2,
        "title": "the lane lands onto it",
        "intent": "Land every candidate onto the branch.",
        "scope": ["src/torve/application/lane.py"],
        "depends_on": [1],
    },
]


def phase_task(task_id: str, phase: int, title: str) -> Task:
    return Task(
        id=task_id,
        spec="S-0090",
        phase=phase,
        title=title,
        intent="Some contract prose the body never repeats.",
        scope=Scope(),
        decisions=[InheritedDecision(id="S-0090/D-1", grade="ASSUMED", text="the unit is a term")],
    )


def corpus_with_phasing(tmp_path: Path) -> Path:
    from test_decisions import document, place

    root = tmp_path / "repo"
    spec_dir = root / ".torve" / "specs"
    spec_dir.mkdir(parents=True)
    place(
        spec_dir,
        "0090",
        document(
            "0090",
            rows=[("S-0090/D-1", "ASSUMED", "the unit is a term", "—", "cheap to revisit")],
            title="Landing by document",
            phasing=PHASES,
        ),
    )
    return root


def test_the_document_body_carries_the_landings_and_the_phases_still_to_come(tmp_path: Path):
    root = corpus_with_phasing(tmp_path)
    log_dir = root / ".torve" / "tasks" / "T-8402"
    log_dir.mkdir(parents=True)
    (log_dir / "log.yaml").write_text(
        "schema_version: 1\ntask: T-8402\ndrift_count: 0\nentries:\n"
        "  - decision: S-0090/D-1\n    kind: departed\n    claim: the helper already existed\n"
        "  - decision: S-0090/D-1\n    kind: resolved\n    claim: routine\n",
        encoding="utf-8",
    )
    landings = [
        DocumentLanding(
            task=phase_task("T-8401", 1, "the unit is a term"),
            sha="a" * 40,
            results=[GateResult(name="scope", outcome="pass", state="blocking", duration_s=0.2)],
        ),
        DocumentLanding(
            task=phase_task("T-8402", 1, "a document has a branch name"),
            sha="b" * 40,
            results=[GateResult(name="scope", outcome="fail", state="blocking", duration_s=0.3)],
        ),
    ]

    title, body = compose_document_pr("S-0090", landings, root)

    # The title says which document and how much of it; phase 1 holds two
    # entries and counts once.
    assert title == "S-0090: Landing by document · 1/2 phases"
    assert "1 of this document's 2 phases are still to come" in body
    # Every task the branch carries, with its contract, its rows as a
    # table, its gates and its divergences — and nothing the agent wrote.
    assert "## T-8401 · phase 1 · `aaaaaaaaaaaa`" in body
    assert "## T-8402 · phase 1 · `bbbbbbbbbbbb`" in body
    assert "Some contract prose the body never repeats." in body
    assert "- gates: all 1 pass" in body
    assert "- gates: scope fail" in body
    assert "| S-0090/D-1 | `ASSUMED` | the unit is a term |" in body
    assert "- divergence: S-0090/D-1 departed: the helper already existed" in body
    assert "routine" not in body
    # The phases to come are named from the phasing list, by number and title.
    assert "- phase 2 — the lane lands onto it" in body
    assert "- phase 1 —" not in body


def test_a_document_fully_landed_says_so_and_an_unreadable_one_names_no_phases(tmp_path: Path):
    root = corpus_with_phasing(tmp_path)
    landings = [
        DocumentLanding(task=phase_task("T-8401", 1, "one")),
        DocumentLanding(task=phase_task("T-8403", 2, "two")),
    ]

    _, body = compose_document_pr("S-0090", landings, root)
    assert "Every phase of this document is on this branch (2)." in body
    assert "Still to come" not in body

    # No corpus to read: the composer names no phases rather than guessing one.
    title, bare = compose_document_pr("S-0091", landings, tmp_path / "nowhere")
    assert title == "S-0091: 2 landed"
    assert "phases" not in bare
    assert "## T-8403 · phase 2" in bare


def test_the_publisher_composes_a_document_branch_from_every_task_it_carries(tmp_path: Path):
    # S-0083/D-8: the pull request the lane opens for a document branch is the
    # document's — every task the records say the branch carries plus the
    # one landing now, which is published before its own record is written.
    # The observed failure: bloomery #136 carried two phases and wore the
    # last task's title.
    import yaml

    from torve.application.telemetry import engine_event
    from torve.cli.merge import _document_pr_text

    root = corpus_with_phasing(tmp_path)
    branch = "torve/S-0090"

    for task in (
        phase_task("T-8401", 1, "the unit is a term"),
        phase_task("T-8403", 2, "the lane lands onto it"),
    ):
        contract = root / ".torve" / "tasks" / task.id / "contract.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(yaml.safe_dump(task.model_dump(mode="json")), encoding="utf-8")

    engine_event(
        root,
        "lane_landed",
        {"task": "T-8401", "branch": branch, "unit": "document", "sha": "a" * 40},
    )

    title, body = _document_pr_text(root, "T-8403", branch)

    assert title == "S-0090: Landing by document · 2/2 phases"
    assert "## T-8401 · phase 1 · `aaaaaaaaaaaa`" in body
    assert "## T-8403 · phase 2" in body
    assert "Every phase of this document is on this branch (2)." in body
