"""The critic (S-0122/D-1, D-2, D-6, D-19): one candidate per call, judged
from the evidence the engine measured, under a frozen prompt, with block or
pass decided by the engine from the findings and never from the critic's
words."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_review_run import SequencedReviewer, review_rig  # noqa: F401 — the fixture, by name
from test_run_loop import OK, MockRuntime, ScriptedAgent, task_for

import torve.application.critic as critic_module
import torve.application.runner as run_module
from torve.application.critic import (
    PROMPT_FILE,
    SCHEMA_FILE,
    CriticDocument,
    corpus_repository,
    critic_prompt,
    critic_schema,
    decide,
    parse_critic,
    prompt_sha,
    run_critic,
)
from torve.application.feedback import feedback_file
from torve.application.ports import AgentResult, ExecResult, SandboxHandle
from torve.application.review import SchemaRefusal, mint_review_task
from torve.application.runner import run_task
from torve.config.manifest import load_manifest
from torve.config.runconfig import ReviewConfig, RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import InheritedDecision, Scope, Task
from torve.gates.context import build_context
from torve.gates.evidence_package import build_evidence
from torve.gates.sabotage import SUITE_TEST_RUN, TASK_ID

# The frozen prompt's own bytes (S-0122/D-19). A change to `critic-v1.md` is a
# new version, tested on 30 labelled past reviews before it ships — not an
# edit this pin should quietly follow.
FROZEN_PROMPT = "6498a9749a069024040b85c2c1703f6fca7a0fe43099da8f39ee1b0b2e2e26a6"

OWNED, LOCKED = "S-0900/D-1", "S-0800/D-4"


def critic_config(**review: object) -> RunnerConfig:
    return RunnerConfig(review=ReviewConfig(on=["task_gated"], mode="critic", **review))


def answer(rows=(), findings=(), **extra) -> str:
    return "the critic ran its probes\n" + json.dumps(
        {"schema": "torve.critic/v1", "rows": list(rows), "findings": list(findings), **extra}
    )


def finding(severity="major", evidence="src/app.py:2 — the bound", **fields) -> dict:
    return {
        "id": "F1",
        "severity": severity,
        "kind": "bug",
        "claim": "the bound is off by one",
        "evidence": evidence,
        "verified": "executed",
        "confidence": "high",
        **fields,
    }


# ....................... #
# The engine decides (S-0122/D-1, LOCKED). Each row of this table is a pinned
# consequence: the document the critic gave, the bar in force, and whether
# the candidate is blocked.

LOCATED = "src/app.py:2 — the bound"
COMMAND = "`python probes/p.py` — AssertionError: 3 != 4"

TABLE = [
    # findings: located, at or above the bar, block
    ("major finding, bar major", [], [finding("major")], "major", True),
    ("major finding, bar blocker", [], [finding("major")], "blocker", False),
    ("blocker finding, bar blocker", [], [finding("blocker")], "blocker", True),
    ("minor finding", [], [finding("minor")], "major", False),
    ("nit finding", [], [finding("nit")], "major", False),
    ("command evidence locates", [], [finding("major", COMMAND)], "major", True),
    # findings whose evidence does not locate are discarded unread
    ("blocker citing no file", [], [finding("blocker", "src/ghost.py:1 — x")], "major", False),
    ("blocker past the file's end", [], [finding("blocker", "src/app.py:99 — x")], "major", False),
    ("blocker with prose evidence", [], [finding("blocker", "it is wrong")], "major", False),
    ("command with no output", [], [finding("blocker", "`pytest`")], "major", False),
    # rows: violated with located evidence blocks; unknown never does
    ("owned row violated", [{"row": OWNED, "verdict": "violated", "evidence": LOCATED}], [], "major", True),
    ("owned row violated by command", [{"row": OWNED, "verdict": "violated", "evidence": COMMAND}], [], "blocker", True),
    ("owned row violated, unlocated", [{"row": OWNED, "verdict": "violated", "evidence": "trust me"}], [], "major", False),
    ("owned row violated, no evidence", [{"row": OWNED, "verdict": "violated"}], [], "major", False),
    ("owned row unknown", [{"row": OWNED, "verdict": "unknown", "evidence": LOCATED}], [], "major", False),
    ("owned row holds", [{"row": OWNED, "verdict": "holds", "evidence": LOCATED}], [], "major", False),
    ("locked constraint violated", [{"row": LOCKED, "verdict": "violated", "evidence": LOCATED}], [], "major", True),
    ("a row nobody gave violated", [{"row": "S-0700/D-9", "verdict": "violated", "evidence": LOCATED}], [], "major", False),
    ("a later holds keeps a located violation", [{"row": OWNED, "verdict": "violated", "evidence": LOCATED}, {"row": OWNED, "verdict": "holds", "evidence": LOCATED}], [], "major", True),
    ("nothing at all", [], [], "major", False),
]  # fmt: skip


@pytest.mark.parametrize(
    ("rows", "findings", "bar", "blocked"),
    [case[1:] for case in TABLE],
    ids=[case[0] for case in TABLE],
)
def test_the_engine_turns_findings_into_block_or_pass(tmp_path, rows, findings, bar, blocked):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    document = CriticDocument.model_validate({"rows": rows, "findings": findings})

    decision = decide(document, {OWNED: True, LOCKED: False}, tmp_path, bar)

    assert decision.blocked is blocked
    assert decision.blocked == bool(decision.blockers or decision.violated)


def test_an_unanswered_row_is_recorded_unknown_and_never_blocks(tmp_path):
    document = CriticDocument.model_validate({"rows": [], "findings": []})

    decision = decide(document, {OWNED: True}, tmp_path, "major")

    assert decision.blocked is False
    assert decision.rows == [{"row": OWNED, "owned": True, "verdict": "unknown", "answered": False}]


def test_a_violated_row_without_located_evidence_is_counted_as_discarded(tmp_path):
    document = CriticDocument.model_validate(
        {"rows": [{"row": OWNED, "verdict": "violated", "evidence": "trust me"}], "findings": []}
    )

    decision = decide(document, {OWNED: True}, tmp_path, "major")

    assert decision.discarded == [f"row {OWNED}: violated without evidence that locates"]
    assert decision.rows[0]["blocks"] is False


# ....................... #
# The frozen prompt and its schema (S-0122/D-19).


def test_the_prompt_is_frozen_and_its_sha_covers_the_schema():
    assert hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest() == FROZEN_PROMPT, (
        "critic/v1 is frozen: a changed prompt is a new version (S-0122/D-19)"
    )
    assert critic_prompt().startswith("You are the independent critic for a coding engine.")
    assert ".torve/review/critic.schema.json" in critic_prompt()
    assert (
        prompt_sha()
        == hashlib.sha256(PROMPT_FILE.read_bytes() + SCHEMA_FILE.read_bytes()).hexdigest()
    )


def test_the_staged_schema_is_the_models_own():
    """The schema the critic is told to match is the one its answer is
    parsed with: regenerate `critic-v1.schema.json` from `CriticDocument`
    when the model changes, and that is a new prompt version."""

    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == critic_schema()


def test_the_last_document_is_read_through_a_harness_envelope():
    inner = answer(findings=[finding()])
    envelope = json.dumps({"type": "result", "is_error": False, "result": inner})

    document = parse_critic("chatter {not json}\n" + envelope)

    assert document is not None
    assert [f.kind for f in document.findings] == ["bug"]
    assert parse_critic("no document here") is None


def test_a_document_the_schema_refuses_is_named_by_field():
    with pytest.raises(SchemaRefusal, match=r"findings\.0\.kind"):
        parse_critic(answer(findings=[finding(kind="vibes")]))


# ....................... #
# One call over a real candidate: the inputs staged, the author's channels
# scrubbed, the probes kept only when named.


def _candidate(repo) -> tuple[Path, Task]:
    """A repository whose main holds the base and a task worktree that
    changed it, with the files an executor's session leaves in a worktree."""

    from torve.adapters.workspace.git import GitWorkspace

    repo.seed(
        {
            "schema_version": 1,
            "tests": {"run": SUITE_TEST_RUN},
            "gates": [],
        }
    )
    repo.git("checkout", "-q", "main")
    repo.write("conftest.py", "")
    repo.write("src/__init__.py", "")
    repo.write("src/app.py", "def bound(n):\n    return n\n")
    repo.commit("the base")
    worktree = GitWorkspace(repo.root).create(TASK_ID, "main")

    (worktree / "src" / "app.py").write_text("def bound(n):\n    return n - 1\n", encoding="utf-8")
    (worktree / "tests" / "test_bound.py").write_text(
        f'from src.app import bound\n\n\ndef test_bound():\n    """{OWNED}"""\n'
        "    assert bound(1) == 0\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-q", "--no-gpg-sign", "-m", "the change"],
        check=True,
    )

    # The author's channels: the log, the pack, the staged prompt, a trace,
    # an earlier revision's feedback. None of it may reach the critic.
    for name, text in {
        f".torve/tasks/{TASK_ID}/log.yaml": "entries: [{claim: 'trust me, reviewer'}]\n",
        ".torve/context/attempts.json": '{"attempts": ["what I tried"]}\n',
        ".torve/tmp/prompt.md": "the executor's prompt\n",
        ".torve/tmp/harness-output.a1.raw": "the executor's session\n",
        ".torve/feedback.md": "an earlier review said: it is fine\n",
    }.items():
        path = worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    target = Task(
        id=TASK_ID,
        intent="Make the bound exclusive.",
        scope=Scope(allow=["src/**", "tests/**"]),
        decisions=[
            InheritedDecision(
                id=OWNED, grade="ASSUMED", text="bound is exclusive", paths=["src/app.py"]
            )
        ],
    )

    return worktree, target


class ProbingCritic:
    """Reads its copy while it exists, writes three probes and names two."""

    kind = "harness"

    def __init__(self, document: str) -> None:
        self.document = document
        self.seen: dict[str, object] = {}

    def run(self, ctx):
        review = ctx.workspace / ".torve" / "review"
        self.seen = {
            "system": ctx.system,
            "prompt": ctx.prompt,
            "staged": sorted(
                str(path.relative_to(ctx.workspace))
                for path in review.rglob("*")
                if path.is_file() and "base" not in path.relative_to(review).parts[:1]
            ),
            "base": sorted(
                str(path.relative_to(review / "base"))
                for path in (review / "base").rglob("*")
                if path.is_file()
            ),
            "torve": sorted(
                str(path.relative_to(ctx.workspace))
                for path in (ctx.workspace / ".torve").rglob("*")
                if path.is_file() and "review" not in path.relative_to(ctx.workspace).parts
            ),
            "evidence": yaml.safe_load((review / "evidence.yaml").read_text(encoding="utf-8")),
        }
        probes = ctx.workspace / "probes"
        probes.mkdir()
        (probes / "p.py").write_text("assert bound(4) == 4\n", encoding="utf-8")
        (probes / "q.py").write_text("unlocated\n", encoding="utf-8")
        (probes / "stray.py").write_text("named by nobody\n", encoding="utf-8")

        return AgentResult(exit_code=0, output=self.document)


def test_one_call_stages_the_evidence_scrubs_the_author_and_keeps_named_probes(repo):
    worktree, target = _candidate(repo)
    ctx = build_context(worktree, load_manifest(worktree / ".torve" / "gates.yaml"), base="main")
    ctx.task = target
    evidence = build_evidence(ctx, [], candidate=TASK_ID)
    review = mint_review_task(repo.root, target)
    critic = ProbingCritic(
        answer(
            rows=[{"row": OWNED, "verdict": "violated", "evidence": "src/app.py:2 — n - 1"}],
            findings=[
                finding(repro={"command": "python probes/p.py", "file": "probes/p.py"}),
                finding(
                    "minor",
                    "it reads wrong",
                    id="F2",
                    kind="style",
                    verified="read",
                    repro={"command": "python probes/q.py", "file": "probes/q.py"},
                ),
            ],
        )
    )

    verdict = run_critic(
        repo.root,
        worktree,
        target,
        review,
        critic_config(),
        MockRuntime(),
        critic,
        ctx.patch,
        evidence,
        "digest",
    )

    # The inputs, and nothing of the author's (S-0122/D-2).
    assert critic.seen["system"] == critic_prompt()
    assert "Make the bound exclusive." in str(critic.seen["prompt"])
    assert critic.seen["staged"] == [
        ".torve/review/critic.schema.json",
        ".torve/review/diff.patch",
        ".torve/review/evidence.yaml",
        ".torve/review/logs/tests-base.log",
        ".torve/review/logs/tests-head.log",
        ".torve/review/rows.yaml",
    ]
    assert "src/app.py" in critic.seen["base"]
    assert critic.seen["torve"] == [".torve/gates.yaml"]
    staged_evidence = critic.seen["evidence"]
    assert isinstance(staged_evidence, dict)
    assert staged_evidence["rows"] == [
        {
            "row": OWNED,
            "grade": "ASSUMED",
            "owned": True,
            "status": "proved",
            "checks": ["tests/test_bound.py::test_bound"],
        }
    ]

    # The engine decided: a located major blocks, the row's breach blocks.
    assert verdict.decision is not None and verdict.decision.blocked
    assert [f.claim for f in verdict.blockers] == [
        "the bound is off by one",
        f"{OWNED} is violated",
    ]
    assert verdict.discarded and "it reads wrong" in verdict.discarded[0]

    # Only the probe a surviving finding names is kept.
    assert verdict.probes == {"probes/p.py": "assert bound(4) == 4\n"}
    kept = repo.root / ".torve" / "tasks" / review.id / "probes"
    assert sorted(p.name for p in kept.iterdir()) == ["p.py"]
    assert "assert bound(4) == 4" in verdict.threads[0]["comments"][0]["body"]
    assert verdict.threads[0]["path"] == "src/app.py"

    # The record (S-0122/D-19): mode, the prompt's sha, row verdicts and the
    # findings' kinds, severities and provenance.
    records = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    record = next(r for r in records if r.get("kind") == "review")
    assert record["mode"] == "critic"
    assert record["prompt_sha"] == prompt_sha()
    assert record["blocked"] is True
    assert record["rows"] == [
        {"row": OWNED, "owned": True, "verdict": "violated", "located": True, "blocks": True}
    ]
    assert [(f["kind"], f["severity"], f["verified"]) for f in record["findings"]] == [
        ("bug", "major", "executed")
    ]
    assert record["evidence"]["rows"] == {OWNED: "proved"}

    # Nothing the critic wrote survived.
    assert not (worktree / "probes").exists()
    assert not (repo.root / ".wt" / review.id).exists()


def test_the_probes_are_read_only_once_the_critics_sandbox_is_gone(repo, monkeypatch):
    """A process the critic left running could swap a probe for a link to a
    host file between the containment check and the read."""

    worktree, target = _candidate(repo)
    runtime = MockRuntime()
    destroyed_at_harvest: list[list[str]] = []

    def harvest(copy, document):
        destroyed_at_harvest.append(list(runtime.destroyed))
        return {}

    monkeypatch.setattr(critic_module, "_keep_probes", harvest)

    run_critic(
        repo.root,
        worktree,
        target,
        mint_review_task(repo.root, target),
        critic_config(),
        runtime,
        ScriptedAgent([AgentResult(exit_code=0, output=answer())]),
        "",
        None,
        "digest",
    )

    assert len(destroyed_at_harvest) == 1 and destroyed_at_harvest[0]


def test_a_corpus_case_that_deletes_a_file_replays_without_it(tmp_path):
    case = tmp_path / "case"
    (case / "tree" / "src").mkdir(parents=True)
    (case / "tree" / "src" / "kept.py").write_text("kept\n", encoding="utf-8")
    (case / "diff.patch").write_text(
        "diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n"
        "--- a/src/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"

    assert corpus_repository(case, repo) is None

    shown = subprocess.run(
        ["git", "-C", str(repo), "show", "main:src/old.py"], capture_output=True, text=True
    )
    assert shown.stdout == "gone\n"
    assert not (repo / "src" / "old.py").exists()
    assert (repo / "src" / "kept.py").read_text(encoding="utf-8") == "kept\n"


# ....................... #
# The run loop with `review.mode: critic`.


def test_a_clean_critic_lets_the_target_land(review_rig):  # noqa: F811
    repo, _runtime, deps_for = review_rig
    critic = ScriptedAgent([AgentResult(exit_code=0, output=answer())])

    state = run_task(repo.root, task_for(repo), critic_config(), deps_for(critic))

    assert state.state is TaskState.READY
    assert any("critic clean" in h["fact"] for h in state.history)
    assert state.reviewed_by is not None


def test_a_critic_block_revises_with_its_findings_and_what_the_engine_measured(
    review_rig,  # noqa: F811
    monkeypatch,
):
    """S-0122/D-8: the revision is handed the blocking findings with their
    repros and the package's failing items — never the critic's own prose,
    which is a trace no revision reads."""

    from torve.gates.evidence_package import EditedTest, EvidencePackage

    repo, _runtime, deps_for = review_rig
    worktree = repo.root / ".wt" / "T-9001"
    (worktree / "src").mkdir(parents=True, exist_ok=True)
    (worktree / "src" / "app.py").write_text("a\nb\n", encoding="utf-8")
    package = EvidencePackage(candidate="T-9001", base=None, head="h")
    package.test_changes = [EditedTest("tests/t.py::test_x", "assert_removed", "2 -> 1")]

    real = run_module.judge

    async def judged(run, state):
        outcome = await real(run, state)
        run.last_pass.evidence = package
        return outcome

    monkeypatch.setattr(run_module, "judge", judged)
    critic = SequencedReviewer(
        [
            answer(
                findings=[
                    finding(
                        repro={"command": "python probes/p.py"},
                        fix_hint="the subtraction in bound",
                    )
                ]
            ),
            answer(),
        ]
    )

    state = run_task(repo.root, task_for(repo), critic_config(), deps_for(critic))

    assert state.state is TaskState.READY
    assert any(h["fact"].startswith("review blocker (revision 1 of 1):") for h in state.history)
    text = feedback_file(repo.root, "T-9001").read_text(encoding="utf-8")
    assert "the bound is off by one" in text
    assert "repro: `python probes/p.py`" in text
    assert "fix: the subtraction in bound" in text
    assert "## What the engine measured" in text
    assert "assert_removed" in text
    assert text.index("## What the engine measured") < text.index("## The superseded")
    assert "the critic ran its probes" not in text


# ....................... #
# The gate pass measures the candidate the critic will read.


class HostRuntime:
    """Runs a sandbox's commands on the host in its workspace."""

    def __init__(self) -> None:
        self.workspaces: dict[str, Path] = {}

    def create(self, spec, workspace):
        self.workspaces[spec.name] = workspace
        return SandboxHandle(id=spec.name, name=spec.name)

    def exec(self, handle, command, timeout_s):
        proc = subprocess.run(
            command,
            shell=True,
            cwd=self.workspaces[handle.id],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return ExecResult(
            exit_code=proc.returncode, output=proc.stdout + proc.stderr, duration_s=0.0
        )

    def sync_out(self, handle, workspace):
        pass

    def destroy(self, handle):
        self.workspaces.pop(handle.id, None)


@pytest.mark.parametrize(("mode", "built"), [("critic", True), ("classic", False)])
def test_a_green_pass_measures_the_package_only_for_the_critic(repo, mode, built):
    from test_runner import _gate_dispatch

    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.runner import run_gate_pass
    from torve.application.runstate import RunState

    worktree, _target = _candidate(repo)
    run = _gate_dispatch(
        root=repo.root, worktree=worktree, runtime=HostRuntime(), workspace=GitWorkspace(repo.root)
    )
    run.config = RunnerConfig(review=ReviewConfig(on=["task_gated"], mode=mode))
    state = RunState(task_id=TASK_ID, path=repo.root / "state.json", run_id="run-1")

    exit_code, *_ = run_gate_pass(run, state)

    assert exit_code == 0
    assert (run.evidence is not None) is built

    if built:
        assert run.evidence is not None
        assert [t.id for t in run.evidence.tests] == ["tests/test_bound.py::test_bound"]
        assert run.evidence.tests[0].status == "proved"


def test_a_critic_seat_with_no_verdict_escalates_like_a_reviewer(review_rig):  # noqa: F811
    repo, _runtime, deps_for = review_rig
    critic = ScriptedAgent([OK, OK])

    state = run_task(repo.root, task_for(repo), critic_config(), deps_for(critic))

    assert state.state is TaskState.ESCALATED
    assert state.escalation is not None
    assert state.escalation.reason == "gate_infrastructure_failure"


# ....................... #
# S-0122/D-15: the critic's one call over a head's bot threads.


def test_a_triage_dismissal_stands_only_on_evidence_the_head_holds(repo):
    from torve.application.critic import triage_threads

    worktree, _target = _candidate(repo)
    answer_text = json.dumps(
        {
            "schema": "torve.triage/v1",
            "threads": [
                {"thread": "t1", "verdict": "invalid", "evidence": "src/app.py:1 — it holds"},
                {"thread": "t2", "verdict": "out_of_scope", "evidence": "nowhere.py:9 — gone"},
                {"thread": "t3", "verdict": "apply", "evidence": "src/app.py:2 — fix it"},
            ],
        }
    )
    seat = ScriptedAgent([AgentResult(exit_code=0, output=answer_text)])

    document = triage_threads(
        repo.root, worktree, "torve/S-0090", "triage these", critic_config(), MockRuntime(), seat
    )

    assert document is not None
    assert [v.verdict for v in document.threads] == ["invalid", "apply", "apply"]
    rows = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    (record,) = [row for row in rows if row.get("kind") == "review"]
    assert (record["target"], record["mode"], record["trigger"]) == (
        "S-0090",
        "triage",
        "pr_threads",
    )


def test_a_triage_dismissal_on_a_command_it_never_ran_is_applied(repo):
    """Only a leading `path:line` citation into the head's tree stands a
    dismissal; a backticked command with an output nobody ran does not."""

    from torve.application.critic import triage_threads

    worktree, _target = _candidate(repo)
    answer_text = json.dumps(
        {
            "schema": "torve.triage/v1",
            "threads": [
                {"thread": "t1", "verdict": "invalid", "evidence": "`pytest -q` — 3 passed"},
            ],
        }
    )
    seat = ScriptedAgent([AgentResult(exit_code=0, output=answer_text)])

    document = triage_threads(
        repo.root, worktree, "torve/S-0090", "triage these", critic_config(), MockRuntime(), seat
    )

    assert document is not None and document.threads[0].verdict == "apply"
