"""S-0005 phase 2: the review run through the pipeline — runner-minted at
gates green, findings as data, blocker escalation as a configured consequence,
and the reviewer at work in a disposable copy of the target worktree: it runs
what it judges, and nothing it writes survives the review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_run_loop import (
    OK,
    MockRuntime,
    MockScm,
    MockVcs,
    MockWorkspace,
    ScriptedAgent,
    task_for,
)

import torve.application.review as review_module
import torve.application.runner as run_module
from torve.adapters.store.durable import open_store
from torve.application.dispatch import RunDeps
from torve.application.feedback import feedback_file
from torve.application.ports import AgentResult, BrokerHandle, BrokerUsage
from torve.application.review import (
    SchemaRefusal,
    build_review_prompt,
    elide_diff_bulk,
    elided_prefixes,
    mint_review_task,
    parse_findings,
    run_review,
    seat_api_error,
    touched_file,
    transient_api_failure,
)
from torve.application.runner import run_task
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.manifest import Manifest
from torve.config.runconfig import (
    ReviewConfig,
    RunnerConfig,
    RuntimeConfig,
    SkillsConfig,
    TierConfig,
)
from torve.domain.attempt import Finding
from torve.domain.states import TaskState
from torve.domain.task import InheritedDecision, Scope, Task
from torve.gates.sabotage import BASE_MANIFEST

# The shipped role skills, as a repository's profiles would declare them.
ROLE_SKILLS: dict[str, list[str]] = {
    "implement": ["flag-dont-flip", "working-rules"],
    "review": ["working-rules"],
    "revert": ["flag-dont-flip", "working-rules"],
}


def reviewer_output(findings: list[dict[str, str]]) -> str:
    return "review considered the diff\n" + json.dumps({"findings": findings})


def review_config() -> RunnerConfig:
    return RunnerConfig(review=ReviewConfig(on=["task_gated"]))


def snapshot(tree):
    """Every file the target worktree holds, by relative path and bytes."""

    return {
        str(path.relative_to(tree)): path.read_bytes()
        for path in sorted(tree.rglob("*"))
        if path.is_file()
    }


class SpecRecordingRuntime(MockRuntime):
    def __init__(self):
        super().__init__()
        self.specs = []
        self.workspaces = []

    def create(self, spec, workspace):
        self.specs.append(spec)
        self.workspaces.append(workspace)
        return super().create(spec, workspace)


@pytest.fixture
def review_rig(repo, monkeypatch):
    repo.seed()
    runtime = SpecRecordingRuntime()

    def scripted_gates(*args, **kwargs):
        return 0, "scripted", "cafecafe1234", [], "diff --git a/x b/x"

    monkeypatch.setattr(run_module, "run_gate_pass", scripted_gates)

    def deps_with_reviewer(review_agent):
        return RunDeps(
            workspace=MockWorkspace(repo.root),
            runtime=runtime,
            agent=ScriptedAgent([OK]),
            vcs=MockVcs(),
            scm=MockScm(),
            store=open_store,
            review_agent=review_agent,
        )

    return repo, runtime, deps_with_reviewer


def review_inputs(repo, name="T-0227"):
    """A target contract, its worktree, and the review task minted over them —
    the shapes `run_review` drives, without the run loop around them."""

    worktree = repo.root / ".wt" / "T-9001"
    (worktree / "src").mkdir(parents=True, exist_ok=True)
    (worktree / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (worktree / "src" / "gone.py").write_text("y = 2\n", encoding="utf-8")

    target = Task(id="T-9001", intent="Hold the line.", decisions=[])
    review = Task(id=name, role="review", targets=[target.id], decisions=[])
    return target, review, worktree


def test_clean_review_lets_the_target_land(review_rig):
    repo, runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output([]))])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    facts = [h["fact"] for h in state.history]
    assert any("review clean" in fact for fact in facts)
    # The verdict the lane's require_review predicate reads (S-0006/D-14, A-43).
    assert state.reviewed_by is not None
    # The review was minted as a contract and ran to its own terminal state.
    minted = sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    assert len(minted) == 1
    review_id = minted[0].parent.name
    assert state.reviewed_by == review_id
    review_state = RunState.load(naming.state_file(repo.root, review_id))
    assert review_state.state is TaskState.READY
    # The reviewer's sandbox ran on a copy of the worktree, writable and gone
    # with the sandbox — not on the worktree itself (S-0005/D-2).
    review_mounts = [
        (spec, path)
        for spec, path in zip(runtime.specs, runtime.workspaces, strict=False)
        if review_id.lower() in spec.name
    ]
    assert review_mounts
    for spec, mount in review_mounts:
        assert not spec.workspace_read_only
        assert mount == repo.root / ".wt" / review_id
        assert mount != repo.root / ".wt" / "T-9001"
        assert not mount.exists()


def test_a_surviving_blocker_escalates_the_target(review_rig):
    repo, _runtime, deps_for = review_rig
    # Evidence must locate: cite a file that exists in the worktree.
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [
                        {
                            "severity": "blocker",
                            "claim": "the change is wrong",
                            "evidence": "app.py:1 — the flag",
                        }
                    ]
                ),
            )
        ]
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation.reason == "blocker_finding"
    # A surviving blocker never records a verdict (S-0006/D-14).
    assert state.reviewed_by is None
    # S-0043 S-0043/D-1: the default budget (1) bought one revision attempt —
    # the same blocker survived it, so the target spent two attempts, not
    # one, before escalating.
    assert state.attempts == 2
    facts = [h["fact"] for h in state.history]
    assert any(f.startswith("review blocker (revision 1 of 1):") for f in facts)


class SequencedReviewer:
    """A different verdict per call. `run_review` always stamps its own
    AgentContext attempt=1 (one iteration budget, S-0005/the-review-contract), so
    ScriptedAgent's index-by-ctx.attempt cannot vary a revision's second
    verdict from its first — this fake counts calls instead."""

    kind = "harness"

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    def run(self, ctx):
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return AgentResult(exit_code=0, output=output)


def test_a_blocker_with_budget_left_continues_in_the_same_worktree(review_rig):
    # S-0043 S-0043/D-1/D-43.2: a surviving blocker with revision budget left
    # writes the S-0005/the-revision-loop-added-by-a-32-2026-08-24 feedback record and continues in place instead
    # of escalating; a clean revision lands like any other.
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    (worktree / ".torve").mkdir(parents=True, exist_ok=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    reviewer = SequencedReviewer(
        [
            reviewer_output(
                [
                    {
                        "severity": "blocker",
                        "claim": "the change is wrong",
                        "evidence": "app.py:1 — the flag",
                    }
                ]
            ),
            reviewer_output([]),
        ]
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    assert state.escalation is None
    assert state.attempts == 2
    facts = [h["fact"] for h in state.history]
    assert any(f.startswith("review blocker (revision 1 of 1):") for f in facts)
    # The revision's own verdict landed the target — the second review's id,
    # not the first blocker's (S-0006/D-14, A-43).
    assert state.reviewed_by is not None

    # S-0043/D-2: the blockers and the convicted diff rode the record — root
    # side (S-0043/D-5), so it outlives this run.
    record = feedback_file(repo.root, "T-9001").read_text(encoding="utf-8")
    assert "the change is wrong" in record
    assert "app.py:1" in record
    assert "diff --git a/x b/x" in record  # last_pass["patch"], the scripted gate diff

    # Two reviews ran on this one run, each its own contract and verdict row
    # (the runner already re-mints a fresh review task per invocation).
    minted = sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    assert len(minted) == 2
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 2
    assert review_records[0]["task_id"] != review_records[1]["task_id"]
    assert [f["severity"] for f in review_records[0]["findings"]] == ["blocker"]
    assert review_records[1]["findings"] == []


def test_revision_rounds_are_two_under_a_critic_unless_named(review_rig):
    """S-0124/D-2: a critic's surviving blocker spends the two rounds S-0122/D-8
    promises — not the classic one — before the third escalates the target."""

    from test_critic import answer, critic_config, finding

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / "src").mkdir(parents=True, exist_ok=True)
    (worktree / "src" / "app.py").write_text("x\n" * 50, encoding="utf-8")
    critic = SequencedReviewer(
        [
            answer(findings=[finding(evidence="src/app.py:1 — the first bound")]),
            answer(findings=[finding(evidence="src/app.py:20 — the second bound")]),
            answer(findings=[finding(evidence="src/app.py:40 — the third bound")]),
        ]
    )

    state = run_task(repo.root, task_for(repo), critic_config(), deps_for(critic))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "blocker_finding"
    assert state.attempts == 3
    revisions = [
        h["fact"] for h in state.history if h["fact"].startswith("review blocker (revision")
    ]
    assert len(revisions) == 2
    # Two rounds were spent, so the third block escalates. Each fact names the
    # resolved budget, so the first round reads as one of two.
    assert revisions[0].startswith("review blocker (revision 1 of 2):")
    assert revisions[1].startswith("review blocker (revision 2 of 2):")


def test_a_reraised_blocker_escalates_without_spending_the_next_round(review_rig):
    """S-0124/D-3: the same blocker at the same anchor after a revision is a
    re-raise — the run escalates naming the file and line instead of buying
    a round the revision the budget still allows would have spent."""

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    blocker = {
        "severity": "blocker",
        "claim": "the change is wrong",
        "evidence": "app.py:1 — the flag",
    }
    reviewer = SequencedReviewer([reviewer_output([blocker]), reviewer_output([blocker])])
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], blocker_revisions=2))

    state = run_task(repo.root, task_for(repo), config, deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "blocker_finding"
    assert "app.py:1" in state.escalation.detail
    assert "re-raised" in state.escalation.detail
    # One revision was spent; the second round was refused because the finding
    # had not moved.
    assert state.attempts == 2
    revisions = [
        h["fact"] for h in state.history if h["fact"].startswith("review blocker (revision")
    ]
    assert len(revisions) == 1
    assert revisions[0].startswith("review blocker (revision 1 of 2):")
    # The round's anchors ride its fact, where the next round reads them.
    assert 'anchors=[{"path":"app.py","line":1}]' in revisions[0]


def test_an_anchor_with_no_line_matches_the_same_file():
    """S-0124/D-3: a recorded or raised anchor with no line matches its file,
    because nothing narrower can say the finding moved."""

    from torve.application.review import _anchor_reraised

    finding = Finding(severity="blocker", claim="file-level", evidence="app.py:7 — here")
    assert _anchor_reraised([finding], [{"path": "app.py", "line": None}]) == ("app.py", 7)


def test_an_anchor_within_the_window_is_the_same_finding():
    """S-0124/D-3: a finding within `threads.WINDOW` lines of the recorded
    anchor is the same one; past it, the code moved and the round is spent."""

    from torve.application.review import _anchor_reraised
    from torve.application.threads import WINDOW

    finding = Finding(severity="blocker", claim="the bound", evidence="app.py:12 — here")

    assert _anchor_reraised([finding], [{"path": "app.py", "line": 12 - WINDOW}]) == ("app.py", 12)
    assert _anchor_reraised([finding], [{"path": "app.py", "line": 11 - WINDOW}]) is None
    assert _anchor_reraised([finding], [{"path": "other.py", "line": 12}]) is None


def test_revision_inputs_record_the_judged_head_and_read_it_back(repo):
    """S-0125/D-2: the revision fact carries the tree the review judged beside
    its blockers' anchors and claims, and the next attempt reads that tree to
    stage the diff the revision itself produced. The engine commits nothing
    before land, so the judged tree is uncommitted and the revision's new
    module is untracked."""

    from types import SimpleNamespace

    from test_critic import _candidate

    from torve.application.critic import CriticDocument, decide
    from torve.application.review import (
        _blocker_rounds,
        blocker_fact,
        revision_for,
        tree_snapshot,
    )

    worktree, target = _candidate(repo)
    # The first round's change, left uncommitted as the engine leaves it.
    (worktree / "src" / "app.py").write_text(
        "def bound(n):\n    return n - 1  # round one\n", encoding="utf-8"
    )
    head = tree_snapshot(worktree)
    state = RunState(task_id=target.id, path=repo.root / "state.json", run_id="run-1")
    blocker = Finding(
        severity="major", claim="the bound is off by one", evidence="src/app.py:2 — n - 1"
    )
    state.history.append(
        {
            "at": state.heartbeat,
            "from": str(state.state),
            "to": str(state.state),
            "fact": blocker_fact("T-0900", 1, 2, [blocker], head),
        }
    )

    fact = state.history[-1]["fact"]
    assert head and f" head={head}" in fact
    assert 'anchors=[{"path":"src/app.py","line":2}]' in fact
    assert _blocker_rounds(state) == [
        {
            "round": 1,
            "head": head,
            "blockers": [{"claim": "the bound is off by one", "path": "src/app.py", "line": 2}],
        }
    ]

    # The revision adds an untracked module and touches nothing else: the diff
    # holds that module and none of the first round's change.
    (worktree / "src" / "new.py").write_text(
        "".join(f"n{number}\n" for number in range(1, 21)), encoding="utf-8"
    )
    revision = revision_for(SimpleNamespace(worktree=worktree), state)

    assert revision is not None
    assert "src/new.py" in revision.diff
    assert "round one" not in revision.diff
    assert revision.changed == {"src/new.py": set(range(1, 21))}
    assert revision.blockers == [
        {
            "claim": "the bound is off by one",
            "path": "src/app.py",
            "line": 2,
            "round": 1,
        }
    ]

    # Read against it, a finding in the new module blocks and one on the
    # first round's untouched code is carried.
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                {
                    "id": f"F{number}",
                    "severity": "blocker",
                    "kind": "bug",
                    "claim": claim,
                    "evidence": evidence,
                    "verified": "read",
                    "confidence": "high",
                }
                for number, (claim, evidence) in enumerate(
                    [
                        ("a bug the revision added", "src/new.py:3 — x"),
                        ("code no round asked about", "src/__init__.py:1 — x"),
                    ],
                    start=1,
                )
            ],
        }
    )
    (worktree / "src" / "__init__.py").write_text("x\n", encoding="utf-8")
    decision = decide(document, {}, worktree, "major", revision=revision)

    assert [one.id for one in decision.blockers] == ["F1"]
    assert [one.id for one in decision.carried] == ["F2"]


def test_a_blocker_that_survives_the_revision_escalates_only_after_the_rung(review_rig):
    # S-0117/D-1: the first blocker sends the revision to the seat's
    # revision_variant — same worktree, same critique, stronger agent — and
    # only the blocker that survives that revision reaches a person.
    from dataclasses import replace

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")

    blocker = {
        "severity": "blocker",
        "claim": "the change is wrong",
        "evidence": "app.py:1 — the flag",
    }
    reviewer = SequencedReviewer([reviewer_output([blocker]), reviewer_output([blocker])])
    built: list[TierConfig] = []

    def retry_agent(tier: TierConfig):
        built.append(tier)
        return ScriptedAgent([OK])

    config = RunnerConfig(
        tiers={
            "planner": TierConfig(),
            "reviewer": TierConfig(),
            "executor": TierConfig(adapter="fake", revision_variant="executor.strong"),
            "executor.strong": TierConfig(adapter="fake"),
        },
        review=ReviewConfig(on=["task_gated"]),
    )

    state = run_task(
        repo.root, task_for(repo), config, replace(deps_for(reviewer), retry_agent=retry_agent)
    )

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "blocker_finding"
    assert state.attempts == 2
    assert built and built[0] is config.tiers["executor.strong"]
    facts = [h["fact"] for h in state.history]
    assert any(f.startswith("review blocker (revision 1 of 1):") for f in facts)


def test_blocker_revisions_zero_reproduces_todays_transitions_byte_for_byte(review_rig):
    # S-0043/D-3: the conservative setting is always reachable — zero spends no
    # revision, so the transition sequence is exactly the pre-0043 one.
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [
                        {
                            "severity": "blocker",
                            "claim": "the change is wrong",
                            "evidence": "app.py:1 — the flag",
                        }
                    ]
                ),
            )
        ]
    )
    config = RunnerConfig(review=ReviewConfig(on=["task_gated"], blocker_revisions=0))

    state = run_task(repo.root, task_for(repo), config, deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "blocker_finding"
    assert state.attempts == 1
    facts = [h["fact"] for h in state.history]
    assert not any(f.startswith("review blocker (revision") for f in facts)
    assert not feedback_file(repo.root, "T-9001").exists()


def test_a_revision_that_would_pass_the_poison_ceiling_escalates_the_ceiling(review_rig):
    # S-0043/the-knob: revisions above 1 are legal but interact with the
    # poison ceiling — the ceiling still wins, so the escalation names it,
    # not blocker_finding.
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "app.py").write_text("broken = True\n", encoding="utf-8")
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [
                        {
                            "severity": "blocker",
                            "claim": "the change is wrong",
                            "evidence": "app.py:1 — the flag",
                        }
                    ]
                ),
            )
        ]
    )
    config = RunnerConfig(
        review=ReviewConfig(on=["task_gated"], blocker_revisions=1), poison_ceiling=1
    )

    state = run_task(repo.root, task_for(repo), config, deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "poison_ceiling"
    assert state.attempts == 1
    # The revision fact was still recorded — the budget was spent, only the
    # next dispatch never happened.
    facts = [h["fact"] for h in state.history]
    assert any(f.startswith("review blocker (revision 1 of 1):") for f in facts)


class RefusingBroker:
    """The budget refusal mid-attempt (S-0021/D-6): usage always reports one
    refusal, so any request the review makes reads as over budget."""

    name = "local"

    def open(self, run, routing, budget, sink=None, channel=None):
        return BrokerHandle(token="t-1")

    def usage(self, handle):
        return BrokerUsage(requests=4, refusals={"budget": 2})

    def close(self, handle):
        return self.usage(None)


def test_broker_budget_refusal_during_review_escalates_immediately(review_rig):
    # S-0043/D-4: a broker budget refusal is a cost conviction, not a revisable
    # defect — it escalates immediately even with revision budget unspent.
    repo, runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output([]))])
    deps = deps_for(reviewer)
    deps = RunDeps(
        workspace=deps.workspace,
        runtime=runtime,
        agent=deps.agent,
        vcs=deps.vcs,
        scm=deps.scm,
        store=deps.store,
        review_agent=reviewer,
        broker=RefusingBroker(),
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps)

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "cost_anomaly"
    assert state.attempts == 1
    facts = [h["fact"] for h in state.history]
    assert not any(f.startswith("review blocker (revision") for f in facts)


def test_the_unconfigured_bridge_never_records_a_verdict(review_rig):
    # S-0006/D-14: with require_review set, an unreviewed candidate must be
    # unlandable — so the "review not configured" bridge sets nothing.
    repo, _runtime, deps_for = review_rig
    state = run_task(repo.root, task_for(repo), RunnerConfig(), deps_for(None))
    assert state.state is TaskState.READY
    assert state.reviewed_by is None


def test_a_blocker_with_unlocatable_evidence_is_discarded(review_rig):
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [
                        {
                            "severity": "blocker",
                            "claim": "invented",
                            "evidence": "ghost.py:9 — nothing here",
                        }
                    ]
                ),
            )
        ]
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    # The fabricated coordinate never reached anyone: the target landed.
    assert state.state is TaskState.READY


def test_unparseable_review_output_escalates_never_promotes(review_rig):
    # Fail closed (S-0005/D-4): an opus review once carried two blockers inside a
    # harness envelope, parsed as nothing, and waved a no-op to ready.
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output="prose, no document")])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"
    assert "unparseable" in state.escalation.detail


def _seat_error(result: str) -> str:
    return json.dumps({"type": "result", "is_error": True, "result": result}) + "\n"


def test_a_rate_limit_is_waited_out_once_and_a_refusal_is_not():
    rate_limited = _seat_error("API Error: Request rejected (429) · rate limit")

    assert seat_api_error(rate_limited) == "API Error: Request rejected (429) · rate limit"
    assert transient_api_failure(rate_limited)
    assert transient_api_failure(_seat_error("API Error: 529 overloaded"))
    assert not transient_api_failure(_seat_error("API Error: 400 model not supported"))
    assert seat_api_error('{"findings": []}') is None


def test_a_seat_with_no_credential_is_a_seat_error():
    # The claude harness without a credential ends on this envelope; it read
    # as "review output unparseable" while the seat had never been reached.
    logged_out = (
        json.dumps(
            {
                "type": "result",
                "is_error": True,
                "result": "Not logged in · Please run /login",
                "terminal_reason": "api_error",
            }
        )
        + "\n"
    )

    assert seat_api_error(logged_out) == "Not logged in · Please run /login"
    assert not transient_api_failure(logged_out)


def test_a_seat_that_gives_no_verdict_escalates_naming_its_error(review_rig, monkeypatch):
    # A reviewer at its usage limit answered every ask with a 429 and the
    # escalation said "review output unparseable": the operator read a broken
    # verdict where the seat had simply not answered yet.
    import time

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    repo, _runtime, deps_for = review_rig
    limited = _seat_error("API Error: Request rejected (429) · usage limit")
    reviewer = ScriptedAgent([AgentResult(exit_code=1, output=limited)])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"
    assert "gave no verdict (API Error: Request rejected (429)" in state.escalation.detail
    assert "requeue once the seat answers" in state.escalation.detail


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


def test_unquoted_on_key_is_refused_not_silently_dropped():
    # The classic YAML 1.1 landmine: an unquoted `on:` under `review:`
    # parses as the boolean key True, not the string 'on' — without this
    # guard the trigger list would never load.
    raw = yaml.safe_load(
        """
        review:
          on:
            - task_gated
        """
    )
    with pytest.raises(ValueError, match="quote"):
        RunnerConfig.model_validate(raw)


# ....................... #
# the disposable copy the reviewer works in (S-0005/D-2 as reworded, S-0005/D-16)


class BatteryRunningReviewer:
    """A reviewer that does what the amendment licenses: it reads, runs and
    writes inside the tree it was given, then reports. Its findings include
    one whose only coordinate is a line its own edit created — evidence about
    its scratch, not about the change."""

    kind = "harness"

    def __init__(self, output: str) -> None:
        self.output = output
        self.workspace: Path | None = None
        self.read_back = ""

    def run(self, ctx):
        self.workspace = ctx.workspace
        self.read_back = (ctx.workspace / "src" / "app.py").read_text(encoding="utf-8")

        # What a reviewer that runs the battery leaves behind: a rewritten
        # file, a deleted one, a planted one, and its own staged prompt.
        (ctx.workspace / "src" / "app.py").write_text(
            "x = 1\nplanted_by_the_reviewer = True\n", encoding="utf-8"
        )
        (ctx.workspace / "src" / "gone.py").unlink()
        (ctx.workspace / "reviewer_scratch.py").write_text("here only\n", encoding="utf-8")
        stage = ctx.workspace / ".torve" / "tmp"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "prompt.md").write_text(ctx.prompt or "", encoding="utf-8")

        return AgentResult(exit_code=0, output=self.output)


def test_what_stops_a_promotion_is_configuration_not_the_reviewer(repo):
    """Three consecutive reviews of this engine found a real defect apiece
    — a leg that shipped the next phase's deliverable, a leg whose
    construction took the manager down, a decode error that abandoned every
    candidate in a lane pass — and graded all three `major`, so all three
    promoted. The reviewer uses `major` for "this must be fixed" and
    reserves `blocker` for what has not once occurred here. The grade stays
    the reviewer's reading; the bar is configuration's (D-2)."""

    repo.seed()
    target, review, worktree = review_inputs(repo)
    verdict = reviewer_output(
        [
            {
                "severity": "major",
                "claim": "the decode aborts the pass",
                "evidence": "src/app.py:1 — the line as it arrived",
            }
        ]
    )

    def reviewed(config: RunnerConfig):
        return run_review(
            repo.root,
            worktree,
            target,
            review,
            config,
            MockRuntime(),
            SequencedReviewer([verdict]),
            "diff --git a/src/app.py b/src/app.py\n+x = 1\n",
            [],
            "digest",
        )

    # The default: a `major` finding stops the promotion.
    stopped = reviewed(RunnerConfig())
    assert [f.severity for f in stopped.blockers] == ["major"]
    assert "at or above major" in stopped.fact

    # `blocker` restores the behaviour every reading before this had — the
    # same finding, recorded identically, promoting.
    promoted = reviewed(RunnerConfig(review=ReviewConfig(blocks_at="blocker")))
    assert promoted.blockers == []
    assert [f.severity for f in promoted.kept] == ["major"]


def test_an_edit_in_the_copy_leaves_the_target_worktree_byte_identical(repo):
    repo.seed()
    target, review, worktree = review_inputs(repo)
    before = snapshot(worktree)
    reviewer = BatteryRunningReviewer(
        reviewer_output(
            [
                {
                    "severity": "major",
                    "claim": "the bound is off by one",
                    "evidence": "src/app.py:1 — the line as it arrived",
                },
                {
                    "severity": "blocker",
                    "claim": "a defect my own edit created",
                    "evidence": "src/app.py:2 — the line I wrote",
                },
                {
                    "severity": "minor",
                    "claim": "a file I made",
                    "evidence": "reviewer_scratch.py:1 — nothing under review",
                },
            ]
        )
    )

    outcome = run_review(
        repo.root,
        worktree,
        target,
        review,
        RunnerConfig(),
        MockRuntime(),
        reviewer,
        "diff --git a/src/app.py b/src/app.py\n+x = 1\n",
        [],
        "digest",
    )

    # The reviewer worked in a copy, and the copy is what it changed.
    assert reviewer.workspace is not None
    assert reviewer.workspace != worktree
    assert reviewer.read_back == "x = 1\n"
    assert snapshot(worktree) == before
    assert (worktree / "src" / "gone.py").is_file()
    assert not (worktree / "reviewer_scratch.py").exists()
    assert not (worktree / ".torve" / "tmp" / "prompt.md").exists()

    # The copy is destroyed with its sandbox: what it held is nowhere.
    assert not reviewer.workspace.exists()

    # And an edit inside it cannot manufacture evidence: the coordinate that
    # only the reviewer's own write created is discarded, the one the change
    # really holds is kept (S-0005/D-4, S-0005/D-16).
    assert [f.evidence for f in outcome.kept] == ["src/app.py:1 — the line as it arrived"]
    assert len(outcome.discarded) == 2
    # The surviving finding is `major`, which stops a promotion under the
    # default `review.blocks_at`. The discarded two never reach the question.
    assert [f.severity for f in outcome.blockers] == ["major"]


def test_the_copy_is_destroyed_even_when_the_reviewer_dies(repo):
    repo.seed()
    target, review, worktree = review_inputs(repo)

    class DyingReviewer:
        kind = "harness"

        def __init__(self):
            self.workspace = None

        def run(self, ctx):
            self.workspace = ctx.workspace
            (ctx.workspace / "src" / "app.py").write_text("half a thought\n", encoding="utf-8")
            raise RuntimeError("the reviewer died mid-write")

    reviewer = DyingReviewer()

    with pytest.raises(RuntimeError, match="mid-write"):
        run_review(
            repo.root,
            worktree,
            target,
            review,
            RunnerConfig(),
            MockRuntime(),
            reviewer,
            "diff",
            [],
            "digest",
        )

    assert reviewer.workspace is not None
    assert not reviewer.workspace.exists()
    assert snapshot(worktree) == {
        "src/app.py": b"x = 1\n",
        "src/gone.py": b"y = 2\n",
    }


def test_the_review_input_is_composed_before_the_copy_exists(repo, monkeypatch):
    # S-0005/D-2 as reworded: the judgment is composed, and the executor's evidence
    # taken in hand, while there is no copy to be tainted by — so nothing the
    # reviewer writes can be part of what it was handed to judge.
    repo.seed()
    target, review, worktree = review_inputs(repo)
    copy = naming.worktree(repo.root, review.id)
    events: list[str] = []

    compose = review_module.build_review_prompt

    def watch_composition(*args, **kwargs):
        prompt = compose(*args, **kwargs)
        assert "diff --git a/src/app.py b/src/app.py" in prompt
        assert not copy.exists()
        events.append("input composed")
        return prompt

    stage = review_module.stage_review_copy

    def watch_staging(source, destination):
        events.append("copy staged")
        return stage(source, destination)

    monkeypatch.setattr(review_module, "build_review_prompt", watch_composition)
    monkeypatch.setattr(review_module, "stage_review_copy", watch_staging)

    run_review(
        repo.root,
        worktree,
        target,
        review,
        RunnerConfig(),
        MockRuntime(),
        ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output([]))]),
        "diff --git a/src/app.py b/src/app.py\n+x = 1\n",
        [],
        "digest",
    )

    assert events == ["input composed", "copy staged"]


def test_a_command_the_reviewer_ran_is_evidence_for_a_finding(review_rig):
    # S-0005/D-16 through the whole run: output the reviewer produced in its copy
    # locates like any path:line, so a blocker found by executing still stops
    # the target.
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [
                        {
                            "severity": "blocker",
                            "claim": "the battery is red under the review's own clock",
                            "evidence": "`uv run pytest tests/test_app.py` — 1 failed in 0.04s",
                        }
                    ]
                ),
            )
        ]
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None and state.escalation.reason == "blocker_finding"
    assert "the battery is red" in state.escalation.detail


# ....................... #
# the reviewer's input


def test_the_prompt_carries_the_contract_and_permission_to_be_clean():
    target = Task(id="T-0001", intent="Build the widget.", decisions=[])
    prompt = build_review_prompt(target, "diff --git", [])
    assert "Build the widget." in prompt
    assert "normal, frequent outcome" in prompt


def test_the_reviewer_told_itself_may_run_the_battery():
    # The prompt is the whole of the reviewer's permission: it names the copy
    # for what it is, names the execution, and names the bound that execution
    # stays inside. No "read-only" claim survives the amendment. Prose is
    # wrapped for reading, so the pins are prose-normalised, not line-exact.
    target = Task(
        id="T-0001",
        intent="Build the widget.",
        decisions=[],
        acceptance=["uv run pytest tests/test_widget.py"],
    )
    prompt = " ".join(build_review_prompt(target, "diff --git", []).split())
    assert "This workspace is a copy of the tree the change lives in" in prompt
    assert "destroyed when this review ends" in prompt
    assert "Run what you are judging" in prompt
    assert "acceptance commands, yours to run in this copy" in prompt
    assert "uv run pytest tests/test_widget.py" in prompt
    assert "A battery too slow to finish inside the window is a finding about the battery" in prompt
    assert "never waits, never restarts, and never extends itself" in prompt
    assert "read-only" not in prompt


def test_the_degraded_prompt_forbids_invented_specifications():
    target = Task(id="T-0001", decisions=[])
    prompt = build_review_prompt(target, "diff --git", [], degraded=True)
    assert "degraded" in prompt
    assert "invent" in prompt


def test_the_review_prompt_lists_a_promotion():
    """S-0104/D-7: the reviewer is shown the contingent paths the diff
    actually widened, read from the diff the way the scope gate reads it —
    so a widening is reviewed, not discovered after the fact."""

    target = Task(
        id="T-0001",
        intent="Build the widget.",
        decisions=[],
        scope=Scope(allow=["src/app/**"], contingent=["docs/notes.md", "src/app/kept.py"]),
    )
    diff = (
        "diff --git a/docs/notes.md b/docs/notes.md\n"
        "--- a/docs/notes.md\n+++ b/docs/notes.md\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/src/app/kept.py b/src/app/kept.py\n"
        "--- a/src/app/kept.py\n+++ b/src/app/kept.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    prompt = build_review_prompt(target, diff, [])

    assert "contingent paths this change widened beyond its task's scope:" in prompt
    assert "- docs/notes.md" in prompt
    # A path the declared globs already admit is the scope, not a widening.
    assert "- src/app/kept.py" not in prompt

    # A diff that widened nothing says nothing about promotions.
    assert "widened beyond" not in build_review_prompt(target, "diff --git a/x b/x", [])


def test_the_reviewer_elides_the_rider_files_a_manifest_names():
    """S-0110/D-10: the reviewer's elision set is the manifest's own
    `scope.derived` rider files, so a nested lock is bulk no review reads line
    by line — beside the vendored prefix and root lock always elided. With no
    manifest the nested lock is reviewed in full."""

    manifest = Manifest.model_validate(
        {"scope": {"derived": {"web/package.json": ["web/package-lock.json"]}}}
    )
    diff = (
        "diff --git a/web/package-lock.json b/web/package-lock.json\n"
        "--- a/web/package-lock.json\n+++ b/web/package-lock.json\n@@ -1 +1 @@\n"
        '-{"a":1}\n+{"a":2}\n'
        "diff --git a/src/x.py b/src/x.py\n"
        "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    )

    elided = elided_prefixes(manifest)

    assert "web/package-lock.json" in elided
    assert "[vendored/generated: web/package-lock.json" in elide_diff_bulk(diff, elided)
    assert "+x = 2" in elide_diff_bulk(diff, elided)
    # With no manifest the nested lock's body is reviewed in full.
    assert "[vendored/generated:" not in elide_diff_bulk(diff)
    assert '-{"a":1}' in elide_diff_bulk(diff)


def test_a_rider_file_elides_only_itself():
    """A rider is a file and matches exactly: `web/package-lock.json` does not
    hide a file whose name merely starts with it. A directory entry, ending in
    `/`, still elides everything under it."""

    manifest = Manifest.model_validate(
        {"scope": {"derived": {"web/package.json": ["web/package-lock.json"]}}}
    )
    diff = (
        "diff --git a/web/package-lock.json.audit.py b/web/package-lock.json.audit.py\n"
        "--- a/web/package-lock.json.audit.py\n+++ b/web/package-lock.json.audit.py\n"
        "@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "diff --git a/uv.lock.py b/uv.lock.py\n"
        "--- a/uv.lock.py\n+++ b/uv.lock.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n"
        "diff --git a/src/torve/_web/app.js b/src/torve/_web/app.js\n"
        "--- a/src/torve/_web/app.js\n+++ b/src/torve/_web/app.js\n@@ -1 +1 @@\n-a\n+b\n"
    )

    elided = elide_diff_bulk(diff, elided_prefixes(manifest))

    assert "+x = 2" in elided
    assert "+y = 2" in elided
    assert "[vendored/generated: src/torve/_web/app.js" in elided


def test_the_audit_lists_a_checked_rows_check_and_twin_with_the_two_questions():
    """S-0106/D-5: the reviewer is told each checked row's check and the twin
    that proves it can fail, and asked the faithfulness and can-it-fail
    questions, a failure of either being a `major` finding."""

    target = Task(
        id="T-0001",
        intent="Build the widget.",
        decisions=[
            InheritedDecision(
                id="S-0001/D-1",
                grade="LOCKED",
                text="the parser round-trips",
                check="uv run pytest tests/test_parser.py -q -k roundtrip",
                check_twin="tests/test_parser_sabotage.py",
            ),
            InheritedDecision(id="S-0001/D-2", grade="ASSUMED", text="a rule with no check"),
        ],
    )
    prompt = build_review_prompt(target, "diff --git", [])

    assert "uv run pytest tests/test_parser.py -q -k roundtrip" in prompt
    assert "tests/test_parser_sabotage.py" in prompt
    assert "does the check test what the row says" in prompt
    assert "would a plausible wrong implementation make it fail" in prompt
    assert "finding at `major`" in prompt


def test_the_audit_lists_an_unchecked_row_without_a_check_or_the_questions():
    """S-0106/D-5: a row with no check is listed without one and draws no
    finding for lacking one, so the audit questions are not asked of it."""

    target = Task(
        id="T-0001",
        intent="Build the widget.",
        decisions=[
            InheritedDecision(id="S-0001/D-1", grade="ASSUMED", text="a rule with no check")
        ],
    )
    prompt = build_review_prompt(target, "diff --git", [])

    assert "- S-0001/D-1 [ASSUMED] a rule with no check" in prompt
    assert "check:" not in prompt
    assert "check_twin:" not in prompt
    assert "does the check test what the row says" not in prompt


def test_parse_findings_takes_the_last_document():
    output = 'thinking...\n{"findings": []}'
    assert parse_findings(output) == []
    found = parse_findings(
        json.dumps({"findings": [{"severity": "nit", "claim": "c", "evidence": "e"}]})
    )
    assert found == [Finding(severity="nit", claim="c", evidence="e")]
    assert parse_findings("no document here") is None

    # S-0054/D-15: a document that exists but fails the model is refused by the
    # field it fails on — never folded into "unparseable", which is the
    # word for no document at all.
    with pytest.raises(SchemaRefusal, match=r"findings document\.findings: Input should be"):
        parse_findings('{"findings": "not a list"}')

    with pytest.raises(SchemaRefusal, match=r"findings\.0\.severity"):
        parse_findings('{"findings": [{"severity": "huge", "claim": "c", "evidence": "e"}]}')


def test_parse_findings_unwraps_a_harness_result_envelope():
    # `claude -p --output-format json` wraps the reviewer's answer as the
    # envelope's `result` string — the findings document rides inside it,
    # escaped (parse_drafts' envelope discipline, relearned on T-0171).
    inner = 'prose first\n{"findings": [{"severity": "blocker", "claim": "no change", "evidence": "src/x.py:1 — absent"}]}'
    envelope = json.dumps({"type": "result", "total_cost_usd": 0.8, "result": inner})
    found = parse_findings(envelope)
    assert found is not None and found[0].severity == "blocker"
    # A direct document still wins over an envelope when both are present.
    both = envelope + '\n{"findings": []}'
    assert parse_findings(both) == []


def test_parse_findings_survives_ansi_and_multiline_documents():
    # Real harness output (opencode, first live corpus run): escape codes
    # around a pretty-printed document with session chatter after it.
    output = (
        '\x1b[0m{"findings": [\n'
        '  {"severity": "blocker", "claim": "swallowed",\n'
        '   "evidence": "src/app.py:3 — the bare except"}\n'
        "]}\x1b[0m\n> build · model\n→ Read src/app.py\n"
    )
    found = parse_findings(output)
    assert found is not None and found[0].severity == "blocker"


class HarnessLikeReviewer:
    """Mimics the harness's trace behaviour: writes the session trace into
    the durable store named after the workspace it is given — which for a
    review is the review's own worktree address (the collision T-0172
    fixes) — and returns that trace_ref."""

    def __init__(self, output):
        self.output = output

    def run(self, ctx):
        trace = naming.trace_file(ctx.workspace, ctx.attempt)
        trace.write_text(self.output, encoding="utf-8")
        return AgentResult(exit_code=0, output=self.output, trace_ref=str(trace))


def test_the_review_trace_lands_under_the_review_id_and_spares_the_executors(review_rig):
    # T-0172: the reviewer's session used to overwrite the executor's trace —
    # the review's own trace must land under the review id in the durable
    # store (S-0039/D-1) and the executor's evidence must survive byte-for-byte
    # (its record still cites it).
    repo, _runtime, deps_for = review_rig

    # The executor's trace, as its own record cites it (S-0004/why-the-agent-port-earns-its-existence) —
    # written through the store's one path helper, which creates the home.
    executor_trace = naming.trace_file(repo.root / ".wt" / "T-9001", 1)
    executor_trace.write_bytes(b"the executor's session, verbatim\n")

    # The review's record rides the worktree's manifest telemetry path.
    (repo.root / ".wt" / "T-9001" / ".torve").mkdir(parents=True, exist_ok=True)
    (repo.root / ".wt" / "T-9001" / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    output = reviewer_output([])
    state = run_task(
        repo.root, task_for(repo), review_config(), deps_for(HarnessLikeReviewer(output))
    )

    assert state.state is TaskState.READY
    # The executor's trace survived the review byte-for-byte.
    assert executor_trace.read_bytes() == b"the executor's session, verbatim\n"

    review_id = state.reviewed_by
    assert review_id is not None

    # The review's own session is recorded under the review task id, named
    # exactly as the harness would have named it for the review's workspace.
    review_trace = naming.trace_file(repo.root / ".wt" / review_id, 1)
    assert review_trace.is_file()
    assert review_trace.read_text(encoding="utf-8") == output

    # And the review's record cites it — root-relative (S-0039/D-1), so the
    # pointer resolves from the stream alone on the host that owns the
    # store — and it is never the executor's path.
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    assert review_records[0]["task_id"] == review_id
    assert review_records[0]["agent"]["trace_ref"] == (
        naming.trace_ref(repo.root / ".wt" / review_id, 1)
    )
    assert review_records[0]["agent"]["trace_ref"] == f".torve/traces/{review_id}.a1.trace.log"


def test_a_reviewer_that_wrote_no_trace_records_none(review_rig):
    # T-0176: the review record's trace_ref used to be rewritten to a
    # harness-shaped path even when the adapter wrote no trace — a
    # fabricated coordinate, exactly as misleading as a missing one. Only
    # an actually-written trace earns a trace_ref; an adapter that wrote
    # none records None and leaves no file behind.
    repo, _runtime, deps_for = review_rig

    # The review's record rides the worktree's manifest telemetry path.
    (repo.root / ".wt" / "T-9001" / ".torve").mkdir(parents=True, exist_ok=True)
    (repo.root / ".wt" / "T-9001" / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    reviewer = ScriptedAgent([AgentResult(exit_code=0, output=reviewer_output([]))])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    review_id = state.reviewed_by
    assert review_id is not None

    # No fabricated file: the harness-shaped path was never written.
    review_trace = naming.trace_file(repo.root / ".wt" / review_id, 1)
    assert not review_trace.exists()

    # And the review record carries no invented coordinate.
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    assert review_records[0]["task_id"] == review_id
    assert review_records[0]["agent"]["trace_ref"] is None


def test_a_review_session_never_leaks_onto_the_executors_trace_path(review_rig):
    # T-0172: without a pre-existing executor trace, the reviewer's session
    # must not remain under the executor's name in the trace store — an
    # evidence file in the wrong place is as misleading as a missing one.
    repo, _runtime, deps_for = review_rig
    output = reviewer_output([])

    state = run_task(
        repo.root, task_for(repo), review_config(), deps_for(HarnessLikeReviewer(output))
    )

    assert state.state is TaskState.READY
    executor_trace = naming.trace_file(repo.root / ".wt" / "T-9001", 1)
    assert not executor_trace.exists()


# ....................... #
# The tier clock (S-0035/the-tier-clock, S-0035/D-6): the review lane reads the
# resolved reviewer tier's values.


class ClockRecordingReviewer:
    """Mimics ScriptedAgent but keeps the contexts it was run under — the
    agent-side view of the clock the review lane resolved."""

    def __init__(self):
        self.contexts = []

    def run(self, ctx):
        self.contexts.append(ctx)
        return AgentResult(exit_code=0, output=reviewer_output([]))


def _review_sandbox_specs(runtime, review_id: str | None):
    return [s for s in runtime.specs if review_id and review_id.lower() in s.name]


def test_the_reviewer_seat_carries_its_own_clock(review_rig):
    repo, runtime, deps_for = review_rig
    reviewer = ClockRecordingReviewer()
    config = RunnerConfig(
        review=ReviewConfig(on=["task_gated"]),
        runtime=RuntimeConfig(agent_timeout=1200, sandbox_timeout=1800),
        tiers={
            "planner": TierConfig(),
            "executor": TierConfig(),
            "reviewer": TierConfig(agent_timeout=3300, sandbox_timeout=3900),
        },
    )

    state = run_task(repo.root, task_for(repo), config, deps_for(reviewer))

    assert state.state is TaskState.READY
    # The reviewer's own clock reached its agent context…
    assert reviewer.contexts and reviewer.contexts[0].timeout_s == 3300
    # …and its sandbox bound is the tier's, not the global.
    review_specs = _review_sandbox_specs(runtime, state.reviewed_by)
    assert review_specs and all(s.timeout_s == 3900 for s in review_specs)
    # The executor's attempt sandbox keeps the runtime global: the reviewer
    # seat's clock never leaks sideways into the seat beside it.
    executor_specs = [s for s in runtime.specs if "t-9001" in s.name]
    assert executor_specs and all(s.timeout_s == 1800 for s in executor_specs)


def test_a_reviewer_tier_without_clocks_keeps_the_globals(review_rig):
    # The fall-through, end to end: absent tier clocks read exactly as
    # today — the RuntimeConfig globals rule the review lane.
    repo, runtime, deps_for = review_rig
    reviewer = ClockRecordingReviewer()
    config = RunnerConfig(
        review=ReviewConfig(on=["task_gated"]),
        runtime=RuntimeConfig(agent_timeout=900, sandbox_timeout=1500),
    )

    state = run_task(repo.root, task_for(repo), config, deps_for(reviewer))

    assert state.state is TaskState.READY
    assert reviewer.contexts and reviewer.contexts[0].timeout_s == 900
    review_specs = _review_sandbox_specs(runtime, state.reviewed_by)
    assert review_specs and all(s.timeout_s == 1500 for s in review_specs)


# ....................... #
# S-0054/the-reviewer: what the reviewer reads besides the diff


def test_the_prompt_points_at_the_pack_and_the_schema():
    target = Task(id="T-0001", intent="Build the widget.", decisions=[])
    prompt = " ".join(build_review_prompt(target, "diff --git", []).split())

    assert ".torve/context/index.md" in prompt
    assert "touched.json" in prompt
    assert ".torve/context/schema/finding.json" in prompt


class PackReadingReviewer:
    """Reads the copy while it exists: the pack, touched.json and the
    review role's skills are gone with the copy after the run."""

    kind = "harness"

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def run(self, ctx):
        context = ctx.workspace / ".torve" / "context"
        self.seen = {
            path.name: path.read_text(encoding="utf-8")
            for path in context.iterdir()
            if path.is_file()
        }
        self.seen["skills"] = ",".join(
            sorted(p.name for p in (ctx.workspace / ".torve" / "skills").iterdir())
        )
        return AgentResult(exit_code=0, output=reviewer_output([]))


def test_the_reviewer_gets_the_pack_touched_and_its_own_skills(repo):
    repo.seed()
    target, review, worktree = review_inputs(repo)
    target.decisions = [
        InheritedDecision(id="S-0001/D-1", grade="LOCKED", text="app stays", paths=["src/app.py"]),
        InheritedDecision(id="S-0001/D-2", grade="LOCKED", text="elsewhere", paths=["docs/**"]),
    ]
    # the executor's skills travel with the copy; the review set replaces them
    (worktree / ".torve" / "skills" / "flag-dont-flip").mkdir(parents=True)
    reviewer = PackReadingReviewer()

    run_review(
        repo.root,
        worktree,
        target,
        review,
        RunnerConfig(skills=SkillsConfig(sets=dict(ROLE_SKILLS))),
        MockRuntime(),
        reviewer,
        "diff --git a/src/app.py b/src/app.py\n@@ -1,1 +1,2 @@\n x = 1\n+y = 2\n",
        [],
        "digest",
    )

    touched = json.loads(reviewer.seen["touched.json"])
    assert touched["changed_paths"] == ["src/app.py"]
    assert [row["id"] for row in touched["decisions"]] == ["S-0001/D-1"]
    assert "prior_reviews" not in touched
    assert "touched.json" in reviewer.seen["index.md"]
    # a replay: nothing model-authored, not even this task's own attempts
    assert "attempts.json" not in reviewer.seen
    assert reviewer.seen["skills"] == "working-rules"
    # the pack died with the copy
    assert not (worktree / ".torve" / "context").exists()


def test_touched_file_reads_coverage_and_never_earlier_findings(repo):
    """S-0122/D-6: an earlier review's findings on the same target are in
    the record and never in the next review's input — they anchored it."""

    repo.seed()
    target = Task(id="T-9001", intent="x", decisions=[])
    (repo.root / "coverage.xml").write_text(
        '<coverage><packages><package><classes><class filename="app.py">'
        '<lines><line number="1" hits="1"/><line number="2" hits="0"/><line number="3" hits="1"/>'
        "</lines></class></classes></package></packages></coverage>",
        encoding="utf-8",
    )
    (repo.root / ".torve" / "telemetry.jsonl").write_text(
        json.dumps(
            {
                "kind": "review",
                "task_id": "T-0227",
                "target": "T-9001",
                "at": "2026-09-09T00:00:00Z",
                "findings": [{"severity": "major", "claim": "c", "evidence": "src/app.py:1 — e"}],
            }
        )
        + "\n"
        + json.dumps({"kind": "review", "task_id": "T-0228", "target": "T-0002", "findings": []})
        + "\n",
        encoding="utf-8",
    )

    payload = touched_file(
        repo.root, target, "diff --git a/src/app.py b/src/app.py\n@@ -1,2 +2,2 @@\n+a\n+b\n"
    )

    assert payload["coverage"] == [{"path": "src/app.py", "changed": 2, "covered": 1}]
    assert "prior_reviews" not in payload
    assert "T-0227" not in json.dumps(payload)


def test_touched_file_reads_coverage_under_the_project_directory(repo):
    """S-0110/D-8: the fallback prefix is the layout's source root, so a
    project under `app/` matches coverage to its own `app/src/` paths."""

    repo.seed(dict(BASE_MANIFEST, layout={"root": "app"}))
    target = Task(id="T-9001", intent="x", decisions=[])
    (repo.root / "coverage.xml").write_text(
        '<coverage><packages><package><classes><class filename="app.py">'
        '<lines><line number="1" hits="1"/><line number="2" hits="0"/><line number="3" hits="1"/>'
        "</lines></class></classes></package></packages></coverage>",
        encoding="utf-8",
    )

    payload = touched_file(
        repo.root,
        target,
        "diff --git a/app/src/app.py b/app/src/app.py\n@@ -1,2 +2,2 @@\n+a\n+b\n",
    )

    assert payload["coverage"] == [{"path": "app/src/app.py", "changed": 2, "covered": 1}]


def test_a_shape_failure_is_refused_by_field_never_unparseable(review_rig):
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=reviewer_output(
                    [{"severity": "huge", "claim": "c", "evidence": "src/app.py:1 — e"}]
                ),
            )
        ]
    )

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert "refused" in state.escalation.detail
    assert "findings.0.severity" in state.escalation.detail
    assert "unparseable" not in state.escalation.detail

    # the outcome and its record carry the refusal, not an unparseable row
    target, review, worktree = review_inputs(repo)
    outcome = run_review(
        repo.root,
        worktree,
        target,
        review,
        RunnerConfig(),
        MockRuntime(),
        SequencedReviewer(
            [reviewer_output([{"severity": "huge", "claim": "c", "evidence": "src/app.py:1 — e"}])]
        ),
        "diff --git a/src/app.py b/src/app.py\n+x = 1\n",
        [],
        "digest",
    )

    assert outcome.unparseable is False
    assert outcome.refusal is not None and "findings.0.severity" in outcome.refusal
    assert outcome.fact.startswith("review output refused: ")


# ....................... #
# The second ask (S-0086/D-1): an unreadable verdict costs one more review in
# the same staged copy, never a phase.


class TracingSequencedReviewer(SequencedReviewer):
    """SequencedReviewer that writes its session where the harness would —
    the store, named after the workspace it was given — so each ask leaves
    its own evidence to cite."""

    def run(self, ctx):
        result = super().run(ctx)
        trace = naming.trace_file(ctx.workspace, self.calls)
        trace.write_text(result.output, encoding="utf-8")
        return AgentResult(exit_code=0, output=result.output, trace_ref=str(trace))


def test_an_unreadable_verdict_is_asked_once_more_and_the_second_answer_stands(review_rig):
    # S-0086/D-1: the first answer carried no findings document at all. The
    # reviewer is asked again in the same copy, and the readable second
    # verdict is the review — a stray subprocess costs one more review.
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / ".torve").mkdir(parents=True, exist_ok=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    reviewer = SequencedReviewer(["prose, no document", reviewer_output([])])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    # One review, two asks — not a second review task, and not an escalation.
    assert state.state is TaskState.READY
    assert state.escalation is None
    assert state.attempts == 1
    assert reviewer.calls == 2
    assert len(sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))) == 1

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    # The record names a review that needed the second ask, and records the
    # answer that was readable.
    assert review_records[0]["second_ask"] is True
    assert review_records[0]["unparseable"] is False
    assert review_records[0]["refusal"] is None


def test_a_readable_verdict_is_never_re_asked(review_rig):
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / ".torve").mkdir(parents=True, exist_ok=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    reviewer = SequencedReviewer([reviewer_output([])])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.READY
    assert reviewer.calls == 1

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert review_records[0]["second_ask"] is False
    assert "trace_refs" not in review_records[0]["agent"]


def test_a_second_unreadable_answer_escalates_with_both_trace_refs(review_rig):
    # S-0086/D-1: a reviewer that cannot answer twice is broken, so the target
    # escalates as S-0043/D-4 says — with both asks' sessions on the record.
    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / ".torve").mkdir(parents=True, exist_ok=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )

    reviewer = TracingSequencedReviewer(["prose, no document", "still no document"])
    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"
    assert "unparseable" in state.escalation.detail
    assert reviewer.calls == 2

    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    review_id = review_records[0]["task_id"]
    assert review_records[0]["second_ask"] is True
    assert review_records[0]["unparseable"] is True

    # Both refs, root-relative and one per ask, and both files are there to
    # read: the unreadable answers are the evidence for a broken harness.
    assert review_records[0]["agent"]["trace_refs"] == [
        f".torve/traces/{review_id}.a1.trace.log",
        f".torve/traces/{review_id}.a2.trace.log",
    ]
    for ask, expected in enumerate(["prose, no document", "still no document"], start=1):
        trace = naming.trace_file(repo.root / ".wt" / review_id, ask)
        assert trace.read_text(encoding="utf-8") == expected
    # The verdict that escalated is the one `trace_ref` names.
    assert review_records[0]["agent"]["trace_ref"] == f".torve/traces/{review_id}.a2.trace.log"


def test_a_refused_document_is_asked_once_more_too(review_rig):
    # S-0086/D-1 covers both shapes of unreadable: a document the schema
    # refused by field is re-asked exactly as no document at all is.
    repo, _runtime, _deps_for = review_rig
    target, review, worktree = review_inputs(repo)
    reviewer = SequencedReviewer(
        [
            reviewer_output([{"severity": "huge", "claim": "c", "evidence": "src/app.py:1 — e"}]),
            reviewer_output([]),
        ]
    )

    outcome = run_review(
        repo.root,
        worktree,
        target,
        review,
        RunnerConfig(),
        MockRuntime(),
        reviewer,
        "diff --git a/src/app.py b/src/app.py\n+x = 1\n",
        [],
        "digest",
    )

    assert reviewer.calls == 2
    assert outcome.second_ask is True
    assert outcome.refusal is None
    assert outcome.unparseable is False
    assert outcome.fact == "review clean"


def test_a_review_numbers_past_the_record_when_a_store_holds_the_tasks(repo):
    """S-0056/D-9: a store-backed plan writes no file, so counted from the files
    alone a review took a queued task's id, and staging its copy removed that
    task's worktree (T-0603, 2026-10-16). The record's ids count as well."""

    (repo.root / ".torve" / "tasks" / "T-0602").mkdir(parents=True)
    target = Task(id="T-0603", intent="Pin forze.", decisions=[])

    assert mint_review_task(repo.root, target, taken=("T-0603", "T-0609")).id == "T-0610"
    # Without a store the files are the record, and the review just minted counts.
    assert mint_review_task(repo.root, target).id == "T-0611"


def test_mints_no_review(review_rig):
    """S-0123/D-4: a verdict selection already holds is the review that ran,
    so `review_step` mints no second review task and records that one's id."""

    repo, _runtime, deps_for = review_rig
    critic = ScriptedAgent(
        [
            AgentResult(
                exit_code=0,
                output=json.dumps({"schema": "torve.critic/v1", "rows": [], "findings": []}),
            )
        ]
    )

    state = run_task(
        repo.root,
        task_for(repo),
        RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic")),
        deps_for(critic),
    )

    assert state.state is TaskState.READY
    minted = sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    assert len(minted) == 1
    assert state.reviewed_by == minted[0].parent.name


class StagedReading:
    """A critic that keeps, per call, the revision inputs staged in its copy
    and the prompt that named them; it blocks once, then passes."""

    kind = "harness"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def run(self, ctx):
        review = ctx.workspace / ".torve" / "review"
        seen = {"prompt": ctx.prompt}

        for name in ("revision.diff", "blockers.yaml"):
            if (review / name).is_file():
                seen[name] = (review / name).read_text(encoding="utf-8")

        self.calls.append(seen)
        findings = (
            [
                {
                    "id": "F1",
                    "severity": "major",
                    "kind": "bug",
                    "claim": "the bound is off by one",
                    "evidence": "src/app.py:1 — the bound",
                    "verified": "read",
                    "confidence": "high",
                }
            ]
            if len(self.calls) == 1
            else []
        )

        return AgentResult(
            exit_code=0,
            output=json.dumps({"schema": "torve.critic/v1", "rows": [], "findings": findings}),
        )


class RevisingExecutor:
    """The executor: its second session, the revision, adds a module."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, ctx):
        self.calls += 1

        if self.calls == 2:
            (ctx.workspace / "src" / "new.py").write_text("fixed = True\n", encoding="utf-8")

        return OK


def test_revision_inputs_reach_the_revisions_critic_through_the_run_loop(review_rig):
    """S-0125/D-2, end to end: `review_step` writes the tree the first critic
    judged into the revision fact, and the revision's critic is staged the
    diff from that tree to what the revision wrote, beside the earlier
    round's blockers, both named in its prompt."""

    import subprocess

    from torve.application.review import tree_snapshot

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / "src").mkdir(parents=True, exist_ok=True)
    (worktree / "src" / "app.py").write_text("bound = 1\n", encoding="utf-8")
    # A worktree of its own, as the engine's are: the judged tree is read from it.
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(["git", "-C", str(worktree), *args], check=True)
    subprocess.run(
        [
            *("git", "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@t"),
            *("commit", "-q", "--no-gpg-sign", "-m", "base"),
        ],
        check=True,
    )
    judged = tree_snapshot(worktree)
    critic = StagedReading()
    deps = deps_for(critic)
    deps.agent = RevisingExecutor()

    state = run_task(
        repo.root,
        task_for(repo),
        RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic", max_width=1)),
        deps,
    )

    assert state.state is TaskState.READY
    (fact,) = [h["fact"] for h in state.history if h["fact"].startswith("review blocker")]
    head = fact.split(" head=", 1)[1].split()[0]

    def src(tree: str) -> str:
        return subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", f"{tree}:src"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    # The head is the tree the critic judged: the engine's own `.torve/` files
    # differ from it, the code does not.
    assert src(head) == src(judged)

    first, second = critic.calls
    assert "revision.diff" not in first and "blockers.yaml" not in first
    assert "src/new.py" in second["revision.diff"]
    assert "src/app.py" not in second["revision.diff"]
    assert yaml.safe_load(second["blockers.yaml"]) == [
        {"claim": "the bound is off by one", "path": "src/app.py", "line": 1, "round": 1}
    ]
    assert ".torve/review/revision.diff" in second["prompt"]
    assert ".torve/review/blockers.yaml" in second["prompt"]


def test_a_deletion_marks_where_it_stood_and_maps_a_removed_anchor_there():
    """S-0125/D-1: a revision that only deletes code is judged on the deletion,
    and an earlier blocker whose line the revision removed or replaced is still
    found at the position the deletion left."""

    from torve.application.review import _changed_lines, _mapped_line

    deleted = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -19,3 +19,2 @@\n a\n-bad = 1\n c\n"
    )
    replaced = deleted.replace("+19,2", "+19,3").replace("-bad = 1\n", "-bad = 1\n+bad = 2\n")

    assert _changed_lines(deleted) == {"src/app.py": {20}}
    assert _mapped_line(deleted, "src/app.py", 20) == 20
    assert _mapped_line(replaced, "src/app.py", 20) == 20
    assert _mapped_line(deleted, "src/app.py", 30) == 29


def test_each_rounds_blockers_are_mapped_from_the_head_that_round_judged(repo):
    """S-0125/D-1: a revision that inserts more than `threads.WINDOW` lines
    above an unresolved blocker still meets it, in the round right after and
    two rounds later when a later revision touched another file only; the
    critic's decide and the escalation check read the same mapped anchors."""

    from types import SimpleNamespace

    from test_critic import _candidate

    from torve.application.critic import CriticDocument, decide
    from torve.application.review import (
        _anchor_reraised,
        blocker_fact,
        mapped_blocker_anchors,
        revision_for,
        tree_snapshot,
    )

    worktree, target = _candidate(repo)
    app = worktree / "src" / "app.py"
    app.write_text("".join(f"line{number}\n" for number in range(1, 61)), encoding="utf-8")
    state = RunState(task_id=target.id, path=repo.root / "state.json", run_id="run-1")

    def record(round_no: int, evidence: str) -> None:
        blocker = Finding(severity="major", claim=f"round {round_no}", evidence=evidence)
        state.history.append(
            {
                "at": state.heartbeat,
                "from": str(state.state),
                "to": str(state.state),
                "fact": blocker_fact("T-0900", round_no, 3, [blocker], tree_snapshot(worktree)),
            }
        )

    def reraises(evidence: str) -> bool:
        revision = revision_for(SimpleNamespace(worktree=worktree), state)
        assert revision is not None
        finding = {
            "id": "F1",
            "severity": "blocker",
            "kind": "bug",
            "claim": "still bad",
            "evidence": evidence,
            "verified": "read",
            "confidence": "high",
        }
        document = CriticDocument.model_validate({"rows": [], "findings": [finding]})
        decision = decide(document, {}, worktree, "major", revision=revision)
        blocker = Finding(severity="blocker", claim="still bad", evidence=evidence)
        escalates = _anchor_reraised([blocker], mapped_blocker_anchors(worktree, state))
        assert (escalates is not None) == bool(decision.reraised)
        return bool(decision.reraised)

    record(1, "src/app.py:50 — bad")
    # Revision 1 inserts twenty lines at the top: the blocker now sits at 70.
    app.write_text(
        "".join(f"new{number}\n" for number in range(20)) + app.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert reraises("src/app.py:70 — bad")
    assert not reraises("src/app.py:50 — bad")

    record(2, "src/__init__.py:1 — other")
    # Revision 2 touches another file only; round 1's anchor is still at 70.
    (worktree / "src" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    assert reraises("src/app.py:70 — bad")


class ReadingStagedInput:
    """A critic that keeps the files staged in its copy and gives one finding."""

    kind = "harness"

    def __init__(self, evidence: str) -> None:
        self.evidence = evidence
        # Names alone would pass an implementation that writes the rejected
        # spans into an already-staged file; the bytes are what prove the
        # critic's input unchanged (S-0126/D-2).
        self.staged: list[tuple[str, str]] = []

    def run(self, ctx):
        review = ctx.workspace / ".torve" / "review"
        self.staged = [
            (
                str(path.relative_to(ctx.workspace)),
                path.read_text(encoding="utf-8", errors="replace"),
            )
            for path in sorted(review.rglob("*"))
            if path.is_file()
        ]

        return AgentResult(
            exit_code=0,
            output=json.dumps(
                {
                    "schema": "torve.critic/v1",
                    "rows": [],
                    "findings": [
                        {
                            "id": "F1",
                            "severity": "blocker",
                            "kind": "bug",
                            "claim": "the bound is off by one",
                            "evidence": self.evidence,
                            "verified": "read",
                            "confidence": "high",
                        }
                    ],
                }
            ),
        )


def test_rejected_spots_run_critic_reads_the_log_and_stages_nothing(tmp_path):
    """S-0126/D-2: `run_critic` reads the attempt's rejected spots from the
    worktree's task log and passes them to `decide`, and nothing of them is
    staged — the critic's input is unchanged by the rejection."""

    from torve.application.critic import run_critic

    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "app.py").write_text(
        "".join(f"line {number}\n" for number in range(1, 41)), encoding="utf-8"
    )
    target = Task(
        id="T-9004",
        intent="Answer the round.",
        decisions=[],
        round={
            "branch": "round/T-9004",
            "findings": [{"path": "src/app.py", "line": 10, "end_line": 20, "threads": []}],
        },
    )
    log = worktree / ".torve" / "tasks" / target.id / "log.yaml"
    log.parent.mkdir(parents=True)
    log.write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "decision": "unlisted",
                        "kind": "contradicted",
                        "attempt": 1,
                        "claim": "the claim at app.py:10 is not a defect",
                        "evidence": "src/app.py:10-20 — batch_claims never splits a file",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    critic = ReadingStagedInput("src/app.py:12 — the bound")
    review = Task(id="T-9005", role="review", targets=[target.id], decisions=[])

    verdict = run_critic(
        tmp_path,
        worktree,
        target,
        review,
        review_config(),
        MockRuntime(),
        critic,
        "",
        None,
        "digest",
        attempt=1,
    )

    assert verdict.decision is not None and verdict.decision.blocked is False
    assert [one.id for one in verdict.decision.rejected] == ["F1"]
    staged_with_rejection = critic.staged

    # The same call with no rejection is decided by the ordinary rule, and the
    # critic sees exactly the same files.
    log.unlink()
    second = ReadingStagedInput("src/app.py:12 — the bound")
    other = Task(id="T-9006", role="review", targets=[target.id], decisions=[])

    blocked = run_critic(
        tmp_path,
        worktree,
        target,
        other,
        review_config(),
        MockRuntime(),
        second,
        "",
        None,
        "digest",
        attempt=1,
    )

    assert blocked.decision is not None and blocked.decision.blocked is True
    assert second.staged == staged_with_rejection


def test_an_earlier_attempts_rejection_does_not_excuse_a_later_empty_diff(tmp_path):
    """S-0126/D-3: only the current attempt's rejection answers for an empty
    diff; attempt 1's contradiction does not let attempt 2 land an unchanged
    tree."""

    from torve.gates.context import DiffEntry, GateContext

    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(),
        head_sha="head",
        base="main",
        merge_base="main-tip",
        diff=[DiffEntry(status="A", path=".torve/tasks/T-9001/log.yaml")],
        task=Task(id="T-9001", role="implement", decisions=[]),
        log_text=yaml.safe_dump(
            {
                "entries": [
                    {
                        "decision": "unlisted",
                        "kind": "contradicted",
                        "attempt": 1,
                        "claim": "the claim is not a defect",
                        "evidence": "src/app.py:10 — the guard checks it",
                    }
                ]
            }
        ),
    )

    assert not run_module._is_empty_implement_diff(ctx, tmp_path, 1)
    assert run_module._is_empty_implement_diff(ctx, tmp_path, 2)
