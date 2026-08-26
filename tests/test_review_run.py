"""RFC 0005 phase 2: the review run through the pipeline — runner-minted at
gates green, read-only workspace, findings as data, blocker escalation as a
configured consequence."""

from __future__ import annotations

import json

import pytest
from test_run_loop import (
    OK,
    MockRuntime,
    MockScm,
    MockVcs,
    MockWorkspace,
    ScriptedAgent,
    task_for,
)

import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.application.ports import AgentResult
from torve.application.review import build_review_prompt, parse_findings
from torve.application.runner import RunDeps, run_task
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import ReviewConfig, RunnerConfig
from torve.domain.attempt import Finding
from torve.domain.states import TaskState
from torve.domain.task import Task


def reviewer_output(findings: list[dict[str, str]]) -> str:
    return "review considered the diff\n" + json.dumps({"findings": findings})


def review_config() -> RunnerConfig:
    return RunnerConfig(review=ReviewConfig(on=["task_gated"]))


class SpecRecordingRuntime(MockRuntime):
    def __init__(self):
        super().__init__()
        self.specs = []

    def create(self, spec, workspace):
        self.specs.append(spec)
        return super().create(spec, workspace)


@pytest.fixture
def review_rig(repo, monkeypatch):
    repo.seed()
    runtime = SpecRecordingRuntime()

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

    def deps_with_reviewer(review_agent):
        return RunDeps(workspace=MockWorkspace(repo.root), runtime=runtime,
                       agent=ScriptedAgent([OK]), vcs=MockVcs(), scm=MockScm(),
                       store=open_store, review_agent=review_agent)

    return repo, runtime, deps_with_reviewer


def test_clean_review_lets_the_target_land(review_rig):
    repo, runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output([]))])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    facts = [h["fact"] for h in state.history]
    assert any("review clean" in fact for fact in facts)
    # The verdict the lane's require_review predicate reads (D-6.14, A-43).
    assert state.reviewed_by is not None
    # The review was minted as a contract and ran to its own terminal state.
    minted = sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    assert len(minted) == 1
    review_id = minted[0].parent.name
    assert state.reviewed_by == review_id
    review_state = RunState.load(naming.state_file(repo.root, review_id))
    assert review_state.state is TaskState.READY
    # The reviewer's sandbox mounted the workspace read-only.
    review_specs = [s for s in runtime.specs if review_id.lower() in s.name]
    assert review_specs and all(s.workspace_read_only for s in review_specs)


def test_a_surviving_blocker_escalates_the_target(review_rig):
    repo, _runtime, deps_for = review_rig
    # Evidence must locate: cite a file that exists in the worktree.
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output(
        [{"severity": "blocker", "claim": "the change is wrong",
          "evidence": "app.py:1 — the flag"}]))])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "blocker_finding"
    # A surviving blocker never records a verdict (D-6.14).
    assert state.reviewed_by is None


def test_the_unconfigured_bridge_never_records_a_verdict(review_rig):
    # D-6.14: with require_review set, an unreviewed candidate must be
    # unlandable — so the "review not configured" bridge sets nothing.
    repo, _runtime, deps_for = review_rig
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps_for(None))
    assert state.state is TaskState.READY
    assert state.reviewed_by is None


def test_a_blocker_with_unlocatable_evidence_is_discarded(review_rig):
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output(
        [{"severity": "blocker", "claim": "invented",
          "evidence": "ghost.py:9 — nothing here"}]))])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    # The fabricated coordinate never reached anyone: the target landed.
    assert state.state is TaskState.READY


def test_unparseable_review_output_is_recorded_not_invented(review_rig):
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output="prose, no document")])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    facts = [h["fact"] for h in state.history]
    assert any("unparseable" in fact for fact in facts)


def test_review_configured_without_an_agent_is_loud(review_rig):
    repo, _runtime, deps_for = review_rig
    with pytest.raises(ValueError, match="reviewer agent"):
        run_task(repo.root, task_for(repo), review_config(), deps_for(None))


def test_unknown_review_triggers_are_refused():
    # The forge triggers joined the vocabulary with T-0053; anything else
    # still refuses loudly.
    assert ReviewConfig(on=["pr_opened", "pr_synchronized"]).on
    with pytest.raises(ValueError, match="unsupported review trigger"):
        ReviewConfig(on=["pr_closed"])


# ....................... #
# the reviewer's input


def test_the_prompt_carries_the_contract_and_permission_to_be_clean():
    target = Task(id="T-0001", intent="Build the widget.", decisions=[])
    prompt = build_review_prompt(target, "diff --git", [])
    assert "Build the widget." in prompt
    assert "normal, frequent outcome" in prompt
    assert "read-only" in prompt


def test_the_degraded_prompt_forbids_invented_specifications():
    target = Task(id="T-0001", decisions=[])
    prompt = build_review_prompt(target, "diff --git", [], degraded=True)
    assert "degraded" in prompt
    assert "invent" in prompt


def test_parse_findings_takes_the_last_document():
    output = "thinking...\n{\"findings\": []}"
    assert parse_findings(output) == []
    found = parse_findings(json.dumps(
        {"findings": [{"severity": "nit", "claim": "c", "evidence": "e"}]}))
    assert found == [Finding(severity="nit", claim="c", evidence="e")]
    assert parse_findings("no document here") is None
    assert parse_findings("{\"findings\": \"not a list\"}") is None


def test_parse_findings_survives_ansi_and_multiline_documents():
    # Real harness output (opencode, first live corpus run): escape codes
    # around a pretty-printed document with session chatter after it.
    output = ("\x1b[0m{\"findings\": [\n"
              "  {\"severity\": \"blocker\", \"claim\": \"swallowed\",\n"
              "   \"evidence\": \"src/app.py:3 — the bare except\"}\n"
              "]}\x1b[0m\n> build · model\n→ Read src/app.py\n")
    found = parse_findings(output)
    assert found is not None and found[0].severity == "blocker"
