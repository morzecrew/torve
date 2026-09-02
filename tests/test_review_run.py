"""RFC 0005 phase 2: the review run through the pipeline — runner-minted at
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
from torve.application.ports import AgentResult
from torve.application.review import build_review_prompt, parse_findings, run_review
from torve.application.runner import RunDeps, run_task
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import ReviewConfig, RunnerConfig, RuntimeConfig, TierConfig
from torve.domain.attempt import Finding
from torve.domain.states import TaskState
from torve.domain.task import Task


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

    monkeypatch.setattr(run_module, "_run_gates_in_worktree", scripted_gates)

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
    # The verdict the lane's require_review predicate reads (D-6.14, A-43).
    assert state.reviewed_by is not None
    # The review was minted as a contract and ran to its own terminal state.
    minted = sorted((repo.root / ".torve" / "tasks").glob("T-*/contract.yaml"))
    assert len(minted) == 1
    review_id = minted[0].parent.name
    assert state.reviewed_by == review_id
    review_state = RunState.load(naming.state_file(repo.root, review_id))
    assert review_state.state is TaskState.READY
    # The reviewer's sandbox ran on a copy of the worktree, writable and gone
    # with the sandbox — not on the worktree itself (D-5.2).
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
    # Fail closed (D-5.4): an opus review once carried two blockers inside a
    # harness envelope, parsed as nothing, and waved a no-op to ready.
    repo, _runtime, deps_for = review_rig
    reviewer = ScriptedAgent([AgentResult(exit_code=0, output="prose, no document")])

    state = run_task(repo.root, task_for(repo), review_config(), deps_for(reviewer))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"
    assert "unparseable" in state.escalation.detail


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
# the disposable copy the reviewer works in (D-5.2 as reworded, D-5.16)


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
    # really holds is kept (D-5.4, D-5.16).
    assert [f.evidence for f in outcome.kept] == ["src/app.py:1 — the line as it arrived"]
    assert len(outcome.discarded) == 2
    assert outcome.blockers == []


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
    # D-5.2 as reworded: the judgment is composed, and the executor's evidence
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
    # D-5.16 through the whole run: output the reviewer produced in its copy
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


def test_parse_findings_takes_the_last_document():
    output = 'thinking...\n{"findings": []}'
    assert parse_findings(output) == []
    found = parse_findings(
        json.dumps({"findings": [{"severity": "nit", "claim": "c", "evidence": "e"}]})
    )
    assert found == [Finding(severity="nit", claim="c", evidence="e")]
    assert parse_findings("no document here") is None
    assert parse_findings('{"findings": "not a list"}') is None


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
    """Mimics the harness's trace behaviour: writes the session trace
    beside the workspace it is given — which for a review is the target's
    worktree (the collision T-0172 fixes) — and returns that trace_ref."""

    def __init__(self, output):
        self.output = output

    def run(self, ctx):
        trace = naming.trace_file(ctx.workspace, ctx.attempt)
        trace.write_text(self.output, encoding="utf-8")
        return AgentResult(exit_code=0, output=self.output, trace_ref=str(trace))


def test_the_review_trace_lands_under_the_review_id_and_spares_the_executors(review_rig):
    # T-0172: the reviewer's session used to overwrite the executor's trace
    # at .wt/<target>.a1.trace.log — the review's own trace must land at
    # .wt/<review-id>.a1.trace.log and the executor's evidence must survive
    # byte-for-byte (its record still cites it).
    repo, _runtime, deps_for = review_rig

    # The executor's trace, as its own record cites it (RFC 0004 §4).
    executor_trace = naming.trace_file(repo.root / ".wt" / "T-9001", 1)
    executor_trace.parent.mkdir(parents=True, exist_ok=True)
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

    # And the review's record cites it — the evidence telemetry points at
    # actually exists, and it is never the executor's path.
    telemetry = repo.root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    review_records = [r for r in records if r.get("kind") == "review"]
    assert len(review_records) == 1
    assert review_records[0]["task_id"] == review_id
    assert review_records[0]["agent"]["trace_ref"] == str(review_trace)


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
    # must not remain at .wt/<target>.a1.trace.log under the executor's name
    # — an evidence file in the wrong place is as misleading as a missing one.
    repo, _runtime, deps_for = review_rig
    output = reviewer_output([])

    state = run_task(
        repo.root, task_for(repo), review_config(), deps_for(HarnessLikeReviewer(output))
    )

    assert state.state is TaskState.READY
    executor_trace = naming.trace_file(repo.root / ".wt" / "T-9001", 1)
    assert not executor_trace.exists()


# ....................... #
# The tier clock (RFC 0035 §5.3, D-35.6): the review lane reads the
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
