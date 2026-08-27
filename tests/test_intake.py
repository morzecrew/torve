"""Intake and the drafting run (RFC 0020 phase 1): the parse discipline,
the contract lint's refusals, the draft-lint loop, and adoption — ids
minted under the lock, refs rewritten, contracts committed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from torve.application.intake import (
    DraftsDocument,
    adopt,
    drafts_file,
    lint_contract,
    lint_drafts,
    mint_intake_task,
    parse_drafts,
    run_intake,
)
from torve.application.ports import AgentResult, SandboxHandle
from torve.application.runstate import RunState
from torve.base import naming
from torve.config.runconfig import RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import Task

# ----------------------- #


class ScriptedAgent:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def run(self, ctx):
        self.prompts.append(ctx.prompt)
        return AgentResult(exit_code=0, output=self.outputs.pop(0))


class StubRuntime:
    def __init__(self) -> None:
        self.created = 0
        self.destroyed = 0

    def create(self, spec, workspace):
        self.created += 1
        assert spec.workspace_read_only  # D-20.2: the drafter reads, never writes
        return SandboxHandle(id=f"sbx-{self.created}", name=spec.name)

    def destroy(self, handle):
        self.destroyed += 1


def draft_dict(ref: str = "DRAFT-1", *, allow: list[str] | None = None,
               deny: list[str] | None = None, intent: str = "add a module",
               acceptance: list[str] | None = None,
               depends_on: list[str] | None = None) -> dict:
    return {"ref": ref, "intent": intent,
            "scope": {"allow": allow if allow is not None
                      else ["src/newmod.py", "tests/test_newmod.py"],
                      "deny": deny or []},
            "acceptance": acceptance if acceptance is not None
            else ["python3 -m unittest discover -s tests -v"],
            "depends_on": depends_on or []}


def document(*drafts: dict) -> DraftsDocument:
    return DraftsDocument.model_validate({"drafts": list(drafts), "rationale": "r"})


def output_for(*drafts: dict) -> str:
    return "chatter before\n" + json.dumps(
        {"drafts": list(drafts), "rationale": "the decomposition"})


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test(): pass\n",
                                                    encoding="utf-8")
    return tmp_path


# ----------------------- #
# The parse discipline (D-20.3): last document wins, unparseable is None.


def test_parse_takes_the_last_drafts_document():
    first = json.dumps({"drafts": [draft_dict("DRAFT-1")]})
    second = json.dumps({"drafts": [draft_dict("DRAFT-2")]})
    parsed = parse_drafts(f"noise {first} more noise {second} trailing")
    assert parsed is not None
    assert [d.ref for d in parsed.drafts] == ["DRAFT-2"]


def test_parse_strips_ansi_and_tolerates_chatter():
    body = json.dumps({"drafts": [draft_dict()], "rationale": "why"})
    parsed = parse_drafts(f"\x1b[32mgreen\x1b[0m {body}\nsession goodbye")
    assert parsed is not None
    assert parsed.rationale == "why"


def test_parse_without_a_drafts_document_is_none():
    assert parse_drafts("no json here") is None
    assert parse_drafts(json.dumps({"findings": []})) is None
    # A drafts key with an invalid shape is unparseable, not repaired.
    assert parse_drafts(json.dumps({"drafts": [{"no": "ref"}]})) is None


# ----------------------- #
# The lint (D-20.3): every refusal names the draft and the field.


def test_lint_green_on_a_creatable_disjoint_batch(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict("DRAFT-1"),
        draft_dict("DRAFT-2", allow=["src/other.py", "tests/test_other.py"])), 4)
    assert errors == []


def test_lint_refuses_an_empty_batch(tree: Path):
    errors = lint_drafts(tree, DraftsDocument(drafts=[]), 4)
    assert any("empty batch" in e for e in errors)


def test_lint_enforces_the_ceiling(tree: Path):
    drafts = [draft_dict(f"DRAFT-{n}",
                         allow=[f"src/m{n}.py", f"tests/test_m{n}.py"])
              for n in range(1, 6)]
    errors = lint_drafts(tree, document(*drafts), 4)
    assert any("ceiling" in e for e in errors)


def test_lint_refuses_task_id_shaped_refs(tree: Path):
    errors = lint_drafts(tree, document(draft_dict("T-0090")), 4)
    assert any("ids exist only from adoption" in e for e in errors)


def test_lint_names_empty_fields(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict(intent="  ", acceptance=[], allow=[])), 4)
    assert any("intent is empty" in e for e in errors)
    assert any("acceptance is empty" in e for e in errors)
    assert any("scope.allow is empty" in e for e in errors)


def test_lint_refuses_unparseable_acceptance(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict(acceptance=["echo 'unclosed"])), 4)
    assert any("does not shell-parse" in e for e in errors)


def test_lint_refuses_escaping_and_dead_globs(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict(allow=["/etc/passwd", "../up.py", "src/nothing_*.py"])), 4)
    assert sum("escapes the tree" in e for e in errors) == 2
    assert any("matches nothing" in e for e in errors)


def test_lint_refuses_allow_deny_overlap_and_bad_deps(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict(deny=["src/newmod.py"], depends_on=["DRAFT-1", "DRAFT-9"])), 4)
    assert any("both allowed and denied" in e for e in errors)
    assert any("depends on itself" in e for e in errors)
    assert any("unknown draft 'DRAFT-9'" in e for e in errors)


def test_lint_t0113_rule_wants_the_existing_test_file(tree: Path):
    red = lint_drafts(tree, document(draft_dict(allow=["src/app.py"])), 4)
    assert any("T-0113" in e for e in red)
    green = lint_drafts(tree, document(
        draft_dict(allow=["src/app.py", "tests/test_app.py"])), 4)
    assert green == []


def test_lint_refuses_intersecting_scopes(tree: Path):
    errors = lint_drafts(tree, document(
        draft_dict("DRAFT-1"), draft_dict("DRAFT-2")), 4)
    assert any("scopes intersect" in e for e in errors)


def test_lint_contract_standalone_and_role_guard(tree: Path):
    contract = tree / "contract.yaml"
    contract.write_text(yaml.safe_dump({
        "schema_version": 1, "id": "T-0001", "role": "implement",
        "intent": "do", "scope": {"allow": ["src/app.py"], "deny": []},
        "acceptance": ["true"], "decisions": []}), encoding="utf-8")
    errors = lint_contract(tree, contract)
    assert any("T-0113" in e and "T-0001" in e for e in errors)
    review = tree / "review.yaml"
    review.write_text(yaml.safe_dump({
        "schema_version": 1, "id": "T-0002", "role": "review",
        "targets": ["T-0001"], "decisions": []}), encoding="utf-8")
    assert lint_contract(tree, review) == []


# ----------------------- #
# The role's contract shape.


def test_draft_role_carries_no_acceptance_and_no_targets():
    with pytest.raises(ValueError, match="contract lint"):
        Task(id="T-1", role="draft", acceptance=["true"], decisions=[])
    with pytest.raises(ValueError, match="targets"):
        Task(id="T-1", role="draft", targets=["T-0"], decisions=[])
    task = Task(id="T-1", role="draft", intent="req", decisions=[])
    assert task.tier == "executor"  # the mint sets planner; the model does not care


# ----------------------- #
# The draft-lint loop.


@pytest.fixture
def seeded(repo):
    repo.seed()
    repo.git("checkout", "-q", "main")
    return repo


def test_run_intake_green_first_try(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget module", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    outcome = run_intake(seeded.root, seeded.root, task, config,
                         StubRuntime(), agent, "digest")

    assert outcome.attempts == 1
    assert [d.ref for d in outcome.drafts] == ["DRAFT-1"]
    state = RunState.load(naming.state_file(seeded.root, task.id))
    assert state.state is TaskState.READY
    assert any("awaiting adoption" in h["fact"] for h in state.history)
    stored = json.loads(drafts_file(seeded.root, task.id).read_text(encoding="utf-8"))
    assert stored["rationale"] == "the decomposition"
    assert stored["request"] == "add a widget module"


def test_run_intake_feeds_lint_refusals_into_the_retry(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add things", config)
    agent = ScriptedAgent([
        output_for(draft_dict(allow=["src/app.py"])),  # T-0113 red
        output_for(draft_dict()),
    ])
    outcome = run_intake(seeded.root, seeded.root, task, config,
                         StubRuntime(), agent, "digest")

    assert outcome.attempts == 2
    assert "refused by the lint" in agent.prompts[1]
    assert "T-0113" in agent.prompts[1]
    assert RunState.load(naming.state_file(seeded.root, task.id)).state \
        is TaskState.READY


def test_run_intake_spent_budget_escalates(seeded):
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add things", config)
    agent = ScriptedAgent(["not json at all"] * config.intake.iterations)
    outcome = run_intake(seeded.root, seeded.root, task, config,
                         StubRuntime(), agent, "digest")

    assert outcome.unparseable
    assert not outcome.drafts
    state = RunState.load(naming.state_file(seeded.root, task.id))
    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "budget_exhausted"
    assert not drafts_file(seeded.root, task.id).exists()


# ----------------------- #
# Adoption (D-20.1, D-20.4).


def adopted_ready_run(seeded, *, rfc: str | None = None) -> str:
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "two modules", config, rfc=rfc)
    agent = ScriptedAgent([output_for(
        draft_dict("DRAFT-1"),
        draft_dict("DRAFT-2", allow=["src/other.py", "tests/test_other.py"],
                   depends_on=["DRAFT-1"]))])
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(),
               agent, "digest")
    seeded.commit("intake bookkeeping")
    return task.id


def test_adopt_mints_ids_rewrites_refs_and_commits(seeded):
    source = adopted_ready_run(seeded)
    adopted = adopt(seeded.root, source, RunnerConfig())

    assert len(adopted) == 2
    first, second = adopted
    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / second / "contract.yaml")
        .read_text(encoding="utf-8"))
    assert contract["depends_on"] == [first]  # DRAFT-1 rewritten (D-20.4)
    assert contract["role"] == "implement"
    assert contract["decisions"] == []
    Task.model_validate(contract)  # the adopted contract is a legal task
    log = subprocess.run(
        ["git", "-C", str(seeded.root), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True).stdout
    assert "adopt" in log and source in log
    assert not drafts_file(seeded.root, source).exists()
    assert not (seeded.root / ".torve" / "tick.lock").exists()  # lock released


def test_adopt_copies_decisions_from_an_accepted_document(seeded):
    seeded.write("rfcs/0099-fixture.md", "\n".join([
        "---", 'id: "0099"', "title: Fixture", "status: accepted",
        "owner: t", "schema_version: 1", "---", "",
        "## Decisions", "",
        "| # | Grade | Decision | Paths | Consequence |",
        "| --- | --- | --- | --- | --- |",
        "| D-99.1 | `LOCKED` | The rule | `src/**` | — |", ""]))
    seeded.commit("fixture rfc")
    source = adopted_ready_run(seeded, rfc="rfcs/0099-fixture.md")
    adopted = adopt(seeded.root, source, RunnerConfig())

    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / adopted[0] / "contract.yaml")
        .read_text(encoding="utf-8"))
    assert contract["decisions"] == [
        {"id": "D-99.1", "grade": "LOCKED", "text": "The rule",
         "paths": ["src/**"]}]


def test_adopt_refuses_a_draft_status_document(seeded):
    seeded.write("rfcs/0098-fixture.md", "\n".join([
        "---", 'id: "0098"', "title: Fixture", "status: draft",
        "owner: t", "schema_version: 1", "---", "",
        "## Decisions", "",
        "| # | Grade | Decision | Paths | Consequence |",
        "| --- | --- | --- | --- | --- |",
        "| D-98.1 | `LOCKED` | The rule | `src/**` | — |", ""]))
    seeded.commit("draft rfc")
    source = adopted_ready_run(seeded, rfc="rfcs/0098-fixture.md")
    with pytest.raises(ValueError, match="not accepted"):
        adopt(seeded.root, source, RunnerConfig())


def test_adopt_refuses_without_a_ready_run(seeded):
    with pytest.raises(ValueError, match="nothing to adopt"):
        adopt(seeded.root, "T-9999", RunnerConfig())
    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "req", config)
    agent = ScriptedAgent(["garbage"] * config.intake.iterations)
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(),
               agent, "digest")
    drafts_file(seeded.root, task.id).write_text(
        json.dumps({"schema_version": 1, "request": "req", "rfc": None,
                    "rationale": "", "drafts": [draft_dict()]}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="ready drafting run"):
        adopt(seeded.root, task.id, config)


# The follow-up (T-0090): a READY draft run is intake's output, not the
# lane's input, and adoption is the disposal.


def test_the_lane_skips_a_ready_draft_run(seeded):
    from torve.application.lane import ready_candidates

    config = RunnerConfig()
    task = mint_intake_task(seeded.root, "add a widget", config)
    agent = ScriptedAgent([output_for(draft_dict())])
    run_intake(seeded.root, seeded.root, task, config, StubRuntime(),
               agent, "digest")

    assert ready_candidates(seeded.root) == []


def test_adopt_tolerates_a_swept_state_and_disposes_of_a_kept_one(seeded):
    source = adopted_ready_run(seeded)
    state_path = naming.state_file(seeded.root, source)
    assert state_path.exists()
    adopt(seeded.root, source, RunnerConfig())
    assert not state_path.exists()  # adoption is the disposal (D-20.10)

    swept = adopted_ready_run(seeded)
    naming.state_file(seeded.root, swept).unlink()  # a pre-fix reaper's sweep
    adopted = adopt(seeded.root, swept, RunnerConfig())
    assert len(adopted) == 2


# Phase 2 (T-0091): the board as the intake surface.


class FakeIntakeTracker:
    def __init__(self, requests=None):
        self.requests = list(requests or [])
        self.retitled: list[tuple[int, str]] = []

    def intake_requests(self):
        return list(self.requests)

    def retitle(self, number: int, title: str):
        # The real adapter filters claimed requests by their new T-nnnn
        # title prefix; consuming the row here mimics that filter.
        self.retitled.append((number, title))
        self.requests = [r for r in self.requests if r.number != number]
        from torve.application.ports import ReflectResult

        return ReflectResult("applied", "retitled")


def leg_deps(tracker, agent):
    from torve.application.intake import IntakeDeps

    return IntakeDeps(
        tracker=tracker, runtime=StubRuntime(),
        agent_factory=lambda: agent,
        worktree_at=lambda root, sha, workdir: workdir.mkdir(
            parents=True, exist_ok=True),
        remove_worktree=lambda root, workdir: None,
        base_tip=lambda: "a" * 40,
        config_digest="digest")


def request(number=7, title="add a rolling widget", body="", author="cmdr"):
    from torve.application.ports import IntakeRequest

    return IntakeRequest(number=number, title=title, body=body, author=author)


def test_intake_leg_claims_only_commander_requests(seeded):
    from torve.application.intake import intake_leg, ledger_file

    tracker = FakeIntakeTracker([request(), request(number=8, author="rando")])
    # The claimed request drafts against the SEEDED tree, not a worktree
    # copy — leg_deps points worktrees at empty dirs, so use a draft the
    # lint judges creatable there.
    agent = ScriptedAgent([output_for(draft_dict())])
    detail, moved = intake_leg(seeded.root, RunnerConfig(),
                               leg_deps(tracker, agent), ("cmdr",))

    assert moved
    assert "skipped 1 non-commander" in detail
    assert len(tracker.retitled) == 1
    number, title = tracker.retitled[0]
    assert number == 7
    assert title.split(":", 1)[1].strip() == "add a rolling widget"
    rows = [json.loads(line) for line in
            ledger_file(seeded.root).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["issue"] == 7


def test_intake_leg_runs_the_claim_and_projects_the_drafts(seeded):
    from torve.application.intake import intake_leg
    from torve.application.outbox import staged_keys

    tracker = FakeIntakeTracker([request()])
    agent = ScriptedAgent([output_for(draft_dict())])
    detail, _ = intake_leg(seeded.root, RunnerConfig(),
                           leg_deps(tracker, agent), ("cmdr",))

    assert "ran 1" in detail and "projected 1" in detail
    keys = staged_keys(seeded.root)
    assert any(k.endswith(":drafts:a1") for k in keys)
    # The drafter saw the request text, over a worktree the leg made.
    assert "add a rolling widget" in agent.prompts[0]


def test_intake_leg_rfc_line_reaches_the_contract(seeded):
    from torve.application.intake import intake_leg, ledger_file

    tracker = FakeIntakeTracker([request(body="rfc: rfcs/0099-fixture.md")])
    agent = ScriptedAgent([output_for(draft_dict())])
    intake_leg(seeded.root, RunnerConfig(), leg_deps(tracker, agent), ("cmdr",))

    task_id = json.loads(ledger_file(seeded.root).read_text(
        encoding="utf-8").splitlines()[0])["task"]
    contract = yaml.safe_load(
        (seeded.root / ".torve" / "tasks" / task_id / "contract.yaml")
        .read_text(encoding="utf-8"))
    assert contract["rfc"] == "rfcs/0099-fixture.md"


def test_revise_requeues_a_draft_with_the_comment_as_feedback(seeded):
    from torve.application.feedback import feedback_file
    from torve.application.intake import intake_leg
    from torve.application.ports import TrackerCommand
    from torve.application.tracker import _apply

    tracker = FakeIntakeTracker([request()])
    agent = ScriptedAgent([output_for(draft_dict()),
                           output_for(draft_dict("DRAFT-1",
                                                 allow=["src/other.py",
                                                        "tests/test_other.py"]))])
    intake_leg(seeded.root, RunnerConfig(), leg_deps(tracker, agent), ("cmdr",))
    task_id = RunState.load_all(seeded.root / ".wt")[0].task_id

    def draft_feedback(tid: str, text: str) -> str:
        body = text.split("\n", 1)[1] if "\n" in text else ""
        feedback_file(seeded.root, tid).write_text(body, encoding="utf-8")
        return "thread feedback captured"

    outcome = _apply(seeded.root, TrackerCommand(
        verb="revise", task_id=task_id, actor="cmdr", source="c1",
        text="/torve revise\nprefer a different module"),
        draft_feedback=draft_feedback)
    assert outcome.applied and "re-drafting" in outcome.detail
    assert RunState.load(naming.state_file(seeded.root, task_id)).state \
        is TaskState.QUEUED

    detail, _ = intake_leg(seeded.root, RunnerConfig(),
                           leg_deps(tracker, agent), ("cmdr",))
    assert "ran 1" in detail
    assert "prefer a different module" in agent.prompts[1]
    assert RunState.load(naming.state_file(seeded.root, task_id)).attempts == 2


def test_adopt_verb_role_refusal_and_wiring(seeded):
    from torve.application.ports import TrackerCommand
    from torve.application.tracker import _apply

    # An implement task is never adopted.
    (seeded.root / ".torve" / "tasks" / "T-0500").mkdir(parents=True)
    (seeded.root / ".torve" / "tasks" / "T-0500" / "contract.yaml").write_text(
        "schema_version: 1\nid: T-0500\nrole: implement\nintent: x\n"
        "decisions: []\n", encoding="utf-8")
    outcome = _apply(seeded.root, TrackerCommand(
        verb="adopt", task_id="T-0500", actor="cmdr", source="c2"),
        adopt_drafts=lambda t: ["T-9999"])
    assert not outcome.applied and "role" in outcome.detail

    source = adopted_ready_run(seeded)
    outcome = _apply(seeded.root, TrackerCommand(
        verb="adopt", task_id=source, actor="cmdr", source="c3"),
        adopt_drafts=lambda t: adopt(seeded.root, t, RunnerConfig()))
    assert outcome.applied and "adopted: " in outcome.detail


def test_adopt_twice_is_refused_by_the_marker(seeded):
    from torve.application.intake import adopted_file

    source = adopted_ready_run(seeded)
    adopt(seeded.root, source, RunnerConfig())
    assert adopted_file(seeded.root, source).is_file()
    with pytest.raises(ValueError, match="already adopted"):
        adopt(seeded.root, source, RunnerConfig())


def test_abandon_discards_a_ready_drafts_batch(seeded):
    from torve.application.ports import TrackerCommand
    from torve.application.tracker import _apply

    source = adopted_ready_run(seeded)
    outcome = _apply(seeded.root, TrackerCommand(
        verb="abandon", task_id=source, actor="cmdr", source="c4"))
    assert outcome.applied and "drafts discarded" in outcome.detail
    assert RunState.load(naming.state_file(seeded.root, source)).state \
        is TaskState.ABANDONED
    assert not drafts_file(seeded.root, source).exists()
    with pytest.raises(ValueError, match="nothing to adopt"):
        adopt(seeded.root, source, RunnerConfig())
