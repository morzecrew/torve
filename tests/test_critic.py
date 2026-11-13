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
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from test_review_run import SequencedReviewer, review_rig  # noqa: F401 — the fixture, by name
from test_run_loop import OK, MockRuntime, ScriptedAgent, task_for

import torve.application.critic as critic_module
import torve.application.runner as run_module
from torve.application import threads
from torve.application.critic import (
    PROMPT_FILE,
    SCHEMA_FILE,
    CriticDocument,
    CriticFinding,
    build_critic_prompt,
    corpus_repository,
    critic_prompt,
    critic_schema,
    decide,
    parse_critic,
    prompt_sha,
    rejected_spots,
    run_critic,
)
from torve.application.feedback import feedback_file
from torve.application.ports import AgentResult, ExecResult, SandboxHandle
from torve.application.review import Revision, SchemaRefusal, mint_review_task, tree_snapshot
from torve.application.runner import run_task
from torve.config.manifest import load_manifest
from torve.config.runconfig import ReviewConfig, RunnerConfig
from torve.domain.states import TaskState
from torve.domain.task import InheritedDecision, Scope, Task
from torve.gates.context import build_context
from torve.gates.evidence_package import CommandRun, build_evidence
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


# The command a finding's repro names (S-0127/D-1). The rigs re-run it on the
# candidate, where the defect the finding names makes it fail.
REPRO = "uv run pytest -q tests/test_app.py"


def finding(severity="major", evidence="src/app.py:2 — the bound", **fields) -> dict:
    return {
        "id": "F1",
        "severity": severity,
        "kind": "bug",
        "claim": "the bound is off by one",
        "evidence": evidence,
        "verified": "executed",
        "confidence": "high",
        "repro": {"command": REPRO},
        **fields,
    }


class Rerun:
    """A scripted `reproduce` (S-0127/D-1): every command exits *default*,
    and a probe the critic's copy did not keep refuses the repro, as
    `runner.reproduce` does. Every call is recorded."""

    def __init__(self, default: int | None = 1):
        self.default = default
        self.calls: list[tuple[list[str], dict[str, str | None]]] = []

    def __call__(self, commands, probes):
        self.calls.append((list(commands), dict(probes)))

        if any(text is None for text in probes.values()):
            return [
                CommandRun(command, None, "refused: a probe was not kept") for command in commands
            ]

        return [CommandRun(command, self.default, f"{command}: 1 failed") for command in commands]


# ....................... #
# The engine decides (S-0122/D-1, S-0127/D-1, LOCKED). Each row of this table
# is a pinned consequence: the document the critic gave, the bar in force, and
# whether the candidate is blocked when every repro fails on re-run.

LOCATED = "src/app.py:2 — the bound"
COMMAND = "`uv run pytest -q tests/test_app.py` — 1 failed: assert 3 == 4"

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
    # rows: a LOCKED row's located breach blocks, an owned row's never, its
    # evidence command unrun (S-0127/A-5); unknown never does
    ("owned row violated, read only", [{"row": OWNED, "verdict": "violated", "evidence": LOCATED}], [], "major", False),
    ("owned row violated by command", [{"row": OWNED, "verdict": "violated", "evidence": COMMAND}], [], "blocker", False),
    ("owned row violated, unlocated", [{"row": OWNED, "verdict": "violated", "evidence": "trust me"}], [], "major", False),
    ("owned row violated, no evidence", [{"row": OWNED, "verdict": "violated"}], [], "major", False),
    ("owned row unknown", [{"row": OWNED, "verdict": "unknown", "evidence": LOCATED}], [], "major", False),
    ("owned row holds", [{"row": OWNED, "verdict": "holds", "evidence": LOCATED}], [], "major", False),
    ("locked constraint violated", [{"row": LOCKED, "verdict": "violated", "evidence": LOCATED}], [], "major", True),
    ("a row nobody gave violated", [{"row": "S-0700/D-9", "verdict": "violated", "evidence": LOCATED}], [], "major", False),
    ("a later holds keeps a located violation", [{"row": LOCKED, "verdict": "violated", "evidence": LOCATED}, {"row": LOCKED, "verdict": "holds", "evidence": LOCATED}], [], "major", True),
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

    decision = decide(document, {OWNED: True, LOCKED: False}, tmp_path, bar, reproduce=Rerun())

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
# A verdict comes from what ran (S-0127/D-1, LOCKED; S-0127/D-2): a finding or
# an owned row blocks on its re-run's non-zero exit, a `gaming` finding and a
# LOCKED row's breach on what locates, and a `weak_test` finding never.


def _app(root: Path) -> None:
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("a\nb\nc\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("exit_code", "blocks"),
    [(1, True), (2, True), (0, False), (126, False), (127, False), (None, False)],
    ids=["fails", "fails-otherwise", "passes", "not-executable", "not-found", "timed-out"],
)
def test_reproduced_a_finding_blocks_only_when_its_rerun_fails(tmp_path, exit_code, blocks):
    """S-0127/D-1: the engine re-runs a kept finding's repro on the
    candidate; a non-zero exit blocks, and an exit of 0, 126 or 127 or a
    timeout records the finding advisory with the command, its exit code and
    its output tail."""

    _app(tmp_path)
    document = CriticDocument.model_validate({"rows": [], "findings": [finding()]})
    rerun = Rerun(default=exit_code)

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun)

    assert rerun.calls == [([REPRO], {})]
    assert decision.blocked is blocks
    assert [one.id for one in decision.advisory] == ([] if blocks else ["F1"])
    (recorded,) = critic_module._recorded_findings(decision)
    assert recorded["rerun"] == {
        "command": REPRO,
        "exit_code": exit_code,
        "tail": f"{REPRO}: 1 failed",
    }
    assert recorded.get("advisory", False) is not blocks


def test_reproduced_a_finding_with_no_command_is_advisory_and_runs_nothing(tmp_path):
    """S-0127/D-1: a finding with no repro, whose evidence carries no
    backticked command with output, has nothing to re-run: it is advisory and
    nothing ran."""

    _app(tmp_path)
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", repro=None)]}
    )
    rerun = Rerun()

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun)

    assert rerun.calls == []
    assert decision.blocked is False
    assert decision.advisory == decision.kept
    assert critic_module._recorded_findings(decision)[0]["rerun"]["command"] == ""


@pytest.mark.parametrize(
    "evidence",
    [
        "src/app.py:2 — `cast(column, Numeric)` compiles to an unscaled DECIMAL",
        "src/app.py:2 — the bound: `uv run pytest -q tests/test_app.py` printed 1 failed",
    ],
    ids=["code", "command"],
)
def test_reproduced_a_command_shown_only_in_evidence_is_never_run(tmp_path, evidence):
    """S-0127/D-1 (S-0127/A-5): a finding with no `repro.command` is never
    re-run, whatever its evidence shows in backticks: code a shell refuses
    would exit 2 and block on nothing the candidate did. It is advisory."""

    _app(tmp_path)
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding(evidence=evidence, repro=None)]}
    )
    rerun = Rerun()

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun)

    assert rerun.calls == []
    assert decision.blocked is False
    assert decision.advisory == decision.kept


def test_reproduced_a_kept_probe_is_handed_and_a_dropped_one_refuses(tmp_path):
    """S-0127/D-1, S-0127/D-10: the probe a finding's `repro.file` names
    reaches the re-run with the text the critic's copy kept; a probe the copy
    did not keep, named by the repro or by its command, is handed as None, so
    the re-run refuses and the finding is advisory. One command over the same
    probes is re-run once."""

    _app(tmp_path)
    kept = {"probes/p.py": "assert bound(4) == 4\n"}
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding(id="F1", repro={"command": "python probes/p.py", "file": "probes/p.py"}),
                finding(id="F2", repro={"command": "python probes/p.py", "file": "probes/p.py"}),
                finding(id="F3", repro={"command": "python probes/q.py", "file": "probes/q.py"}),
                finding(id="F4", repro={"command": "python ./probes/r.py"}),
                finding(id="F5", repro={"command": "python ./probes/p.py", "file": "probes/p.py"}),
            ],
        }
    )
    rerun = Rerun()

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun, probes=kept)

    assert rerun.calls == [
        (["python probes/p.py"], kept),
        (["python probes/q.py"], {"probes/q.py": None}),
        (["python ./probes/r.py"], {"probes/r.py": None}),
        (["python ./probes/p.py"], kept),
    ]
    assert [one.id for one in decision.blockers] == ["F1", "F2", "F5"]
    assert [one.id for one in decision.advisory] == ["F3", "F4"]


def test_reproduced_a_probe_its_command_names_is_kept_beside_its_file(tmp_path):
    """S-0127/D-10: the probes kept from the critic's copy are every one a
    re-run asks for, the one `repro.file` names and each its command names,
    so a command running a wrapper around its probe is not refused for want
    of the wrapper."""

    (tmp_path / "probes").mkdir()
    (tmp_path / "probes" / "run.sh").write_text('python probes/edge.py "$1"\n')
    (tmp_path / "probes" / "edge.py").write_text("raise SystemExit(1)\n")
    repro = {"command": "sh probes/run.sh range", "file": "probes/edge.py"}
    document = CriticDocument.model_validate({"rows": [], "findings": [finding(repro=repro)]})

    kept = critic_module._keep_probes(tmp_path, document)

    assert sorted(kept) == ["probes/edge.py", "probes/run.sh"]


def test_reproduced_a_pytest_selector_names_its_probe_file(tmp_path):
    """S-0127/D-10: a command selecting a test inside a probe names the probe
    file, not the file with its `::` selector, so the kept probe is handed and
    nothing refuses the re-run."""

    _app(tmp_path)
    command = "uv run pytest probes/test_bug.py::test_bug"
    kept = {"probes/test_bug.py": "def test_bug():\n    assert False\n"}
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding(repro={"command": command})]}
    )
    rerun = Rerun()

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun, probes=kept)

    assert rerun.calls == [([command], kept)]
    assert decision.blocked is True


def test_reproduced_a_carried_finding_is_never_rerun(tmp_path):
    """S-0127/D-1: the S-0126/D-1 and S-0125/D-1 carries are read first, and
    a finding either one carries is never re-run; only the finding the
    revision's own change reaches is."""

    _files(tmp_path, **{"src/app.py": 60})
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding("blocker", "src/app.py:30 — a rejected claim", id="F1"),
                finding("blocker", "src/app.py:55 — code the revision never touched", id="F2"),
                finding("blocker", "src/app.py:11 — the changed line", id="F3"),
            ],
        }
    )
    rerun = Rerun()

    decision = decide(
        document,
        {},
        tmp_path,
        "blocker",
        revision=Revision(diff=REVISION_DIFF),
        rejected=[("src/app.py", 28, 32)],
        reproduce=rerun,
    )

    assert [one.id for one in decision.carried] == ["F1", "F2"]
    assert [one.id for one in decision.blockers] == ["F3"]
    assert rerun.calls == [([REPRO], {})]
    assert [one.id for one, _run in decision.reruns] == ["F3"]


@pytest.mark.parametrize("reproduce", ["raises", "none"])
def test_reproduced_a_rerun_that_could_not_happen_is_advisory(tmp_path, reproduce):
    """S-0127/D-1: an engine that could not re-run — `reproduce` raised
    because the judge checkout or the sandbox would not open, or there is no
    engine at all — measured nothing of the candidate: the finding is
    advisory with the reason, and the decision is still made."""

    def broken(commands, probes):
        raise RuntimeError("the judge checkout would not open")

    _app(tmp_path)
    document = CriticDocument.model_validate({"rows": [], "findings": [finding("blocker")]})

    decision = decide(
        document, {}, tmp_path, "major", reproduce=broken if reproduce == "raises" else None
    )

    assert decision.blocked is False
    (recorded,) = critic_module._recorded_findings(decision)
    assert recorded["advisory"] is True
    assert recorded["rerun"]["exit_code"] is None
    assert recorded["rerun"]["tail"].startswith("not run: ")

    if reproduce == "raises":
        assert "the judge checkout would not open" in recorded["rerun"]["tail"]


@pytest.mark.parametrize("evidence", [LOCATED, COMMAND], ids=["citation", "command"])
def test_reproduced_an_owned_rows_breach_blocks_only_through_a_finding(tmp_path, evidence):
    """S-0127/D-1 (S-0127/A-5): a row carries no repro, so an owned row's
    located `violated` verdict is advisory and nothing is re-run for it,
    whatever its evidence shows; the breach blocks through a finding that
    names the row and whose repro fails."""

    _app(tmp_path)
    breach = {"row": OWNED, "verdict": "violated", "evidence": evidence}
    alone = CriticDocument.model_validate({"rows": [breach], "findings": []})
    named = CriticDocument.model_validate({"rows": [breach], "findings": [finding(row=OWNED)]})
    rerun = Rerun()

    advisory = decide(alone, {OWNED: True}, tmp_path, "major", reproduce=rerun)
    unrun = list(rerun.calls)
    blocked = decide(named, {OWNED: True}, tmp_path, "major", reproduce=rerun)

    assert unrun == []
    assert advisory.blocked is False
    assert [row.row for row in advisory.advisory_rows] == [OWNED]
    assert advisory.rows == [
        {
            "row": OWNED,
            "owned": True,
            "verdict": "violated",
            "located": True,
            "blocks": False,
            "advisory": True,
        }
    ]
    assert rerun.calls == [([REPRO], {})]
    assert blocked.blocked is True
    assert [one.row for one in blocked.blockers] == [OWNED]


def test_the_per_call_prompt_lists_the_documents_other_rows_unjudged():
    """S-0127/D-15: the rows other units of the candidate's document deliver
    reach the critic beside the rows it judges, marked as the document's end
    state and asked for no verdict, while a LOCKED row it judges still binds;
    a row of another document does not, and a unit given every row of its
    document gets no such section."""

    def row(id_: str, text: str) -> InheritedDecision:
        return InheritedDecision(id=id_, grade="ASSUMED", text=text, paths=["src/app.py"])

    keeps, refuses, elsewhere = (
        row("S-0900/D-1", "the option keeps its behaviour"),
        row("S-0900/D-2", "a price-only file passed alone is refused"),
        row("S-0800/D-1", "another document's rule"),
    )
    target = Task(id=TASK_ID, intent="x", spec="S-0900", decisions=[keeps, refuses, elsewhere])

    prompt = build_critic_prompt(target, TASK_ID, [(keeps, True)], True)
    rows, others = prompt.split("## The document's other rows")
    whole = build_critic_prompt(target, TASK_ID, [(keeps, True), (refuses, True)], True)

    assert "S-0900/D-1 [ASSUMED] owned" in rows
    assert "S-0900/D-2" not in rows
    assert "- S-0900/D-2 [ASSUMED]: a price-only file passed alone is refused" in others
    assert "Give them no verdict" in others
    assert "A LOCKED row above is the exception: it\nstill binds" in others
    assert "S-0800/D-1" not in prompt
    assert "## The document's other rows" not in whole


def test_reproduced_the_per_call_prompt_says_only_a_failing_repro_blocks():
    """S-0127/D-1: the per-call prompt tells the critic that only a repro
    exiting non-zero on the candidate can block; the frozen prompt is
    unchanged, as its pin above holds."""

    prompt = build_critic_prompt(Task(id=TASK_ID, intent="x", decisions=[]), TASK_ID, [], True)

    assert "Only a repro that exits non-zero on the candidate can block." in prompt
    assert "Only a repro" not in critic_prompt()


def test_blocks_on_reading_a_gaming_finding_and_a_locked_rows_breach(tmp_path):
    """S-0127/D-1: a kept `gaming` finding at or above the bar, a violated
    LOCKED constraint and an owned row graded LOCKED block on located
    evidence alone: nothing is re-run, so a re-run that would pass changes
    nothing. A `gaming` finding below the bar does not block."""

    _app(tmp_path)
    owned_locked = "S-0900/D-2"
    document = CriticDocument.model_validate(
        {
            "rows": [
                {"row": LOCKED, "verdict": "violated", "evidence": LOCATED},
                {"row": owned_locked, "verdict": "violated", "evidence": LOCATED},
            ],
            "findings": [
                finding("major", kind="gaming", id="F1"),
                finding("minor", kind="gaming", id="F2"),
            ],
        }
    )
    rerun = Rerun(default=0)

    decision = decide(
        document,
        {LOCKED: False, owned_locked: True},
        tmp_path,
        "major",
        locked=[owned_locked],
        reproduce=rerun,
    )

    assert rerun.calls == []
    assert decision.blocked is True
    assert [one.id for one in decision.blockers] == ["F1"]
    assert [row.row for row in decision.violated] == [LOCKED, owned_locked]
    assert decision.advisory == [] and decision.advisory_rows == []


def test_weak_test_finding_never_blocks_and_is_never_rerun(tmp_path):
    """S-0127/D-2: a `weak_test` finding never blocks and is never re-run,
    whatever its severity and its repro: one at blocker whose repro would fail
    is recorded `advisory: true`, as is one below the bar."""

    _app(tmp_path)
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding("blocker", kind="weak_test", id="F1"),
                finding("minor", kind="weak_test", id="F2"),
            ],
        }
    )
    rerun = Rerun()

    decision = decide(document, {}, tmp_path, "major", reproduce=rerun)

    assert rerun.calls == []
    assert decision.blocked is False
    assert [one.id for one in decision.advisory] == ["F1", "F2"]
    recorded = critic_module._recorded_findings(decision)
    assert [one["advisory"] for one in recorded] == [True, True]
    assert all("rerun" not in one for one in recorded)


def test_repeats_a_blocker_t0804_a_new_defect_on_the_next_line_spends_the_budget(tmp_path):
    """S-0127/D-4, S-0127/D-11: T-0804's shape — a revision's blocker of the
    earlier one's kind, cited on the line after it, with a claim about another
    defect — is a new blocker and is not re-raised. The same defect described
    in other words over the earlier line is, and so is a blocker naming the
    same row."""

    _files(tmp_path, **{"src/app.py": 60})
    earlier = [
        {
            "round": 1,
            "claim": "the cap is never enforced when a file has more findings than the cap",
            "kind": "bug",
            "path": "src/app.py",
            "line": 12,
        },
        {"round": 1, "claim": f"{OWNED} is violated", "kind": "contract", "row": OWNED},
    ]
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -40,1 +40,1 @@\n-old\n+new\n"
    )
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding(
                    id="F1",
                    evidence="src/app.py:13 — the message",
                    claim="the escalation message names the wrong configuration key",
                ),
                finding(
                    id="F2",
                    evidence="src/app.py:11-12 — the guard",
                    claim="a file holding more findings than the cap is never split, "
                    "so the cap is not enforced",
                ),
                finding(id="F3", evidence="src/app.py:40 — here", claim="another", row=OWNED),
            ],
        }
    )

    decision = decide(
        document,
        {},
        tmp_path,
        "major",
        revision=Revision(diff=diff, blockers=earlier),
        reproduce=Rerun(),
    )

    assert [one.id for one in decision.blockers] == ["F1", "F2", "F3"]
    assert [one.id for one in decision.reraised if isinstance(one, CriticFinding)] == [
        "F2",
        "F3",
    ]


# ....................... #
# A revision is judged by its own change and the blockers it was asked to fix
# (S-0125/D-1): only a finding near a changed line blocks, one near an earlier
# blocker re-raises, and everything else is carried.

REVISION_DIFF = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -10,4 +10,4 @@\n"
    "-old\n"
    "+new\n"
)


def test_carried_finding_blocks_only_near_the_revisions_own_change(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 61)) + "\n", encoding="utf-8"
    )
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding("major", "src/app.py:12 — the changed line", id="F1"),
                finding("major", "src/app.py:32 — the earlier blocker", id="F2"),
                finding("major", "src/app.py:55 — code the revision never touched", id="F3"),
                finding("major", COMMAND, id="F4"),
            ],
        }
    )

    decision = decide(
        document,
        {},
        tmp_path,
        "major",
        revision=Revision(
            diff=REVISION_DIFF,
            blockers=[{"round": 1, "claim": "the earlier bound", "path": "src/app.py", "line": 30}],
        ),
        reproduce=Rerun(),
    )

    # Near the revision's change or an earlier blocker it counts, and blocks
    # on its failing re-run; near is no repeat (S-0127/D-4), so nothing is
    # re-raised. The untouched code and the anchorless finding are carried.
    assert decision.blocked is True
    assert [one.id for one in decision.blockers] == ["F1", "F2"]
    assert decision.reraised == []
    assert [one.id for one in decision.carried] == ["F3", "F4"]


def test_carried_finding_alone_leaves_the_revision_unblocked(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 61)) + "\n", encoding="utf-8"
    )
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding("blocker", "src/app.py:55 — code the revision never touched", id="F1"),
                finding("blocker", COMMAND, id="F2"),
            ],
        }
    )

    decision = decide(
        document,
        {},
        tmp_path,
        "major",
        revision=Revision(diff=REVISION_DIFF, blockers=[]),
    )

    assert decision.blocked is False
    assert decision.blockers == []
    assert [one.id for one in decision.carried] == ["F1", "F2"]
    assert decision.kept == decision.carried


def test_a_dotted_citation_blocks_where_its_normalised_path_was_changed(tmp_path):
    """S-0125/D-1: an anchor is compared to the revision's diff the way
    `locate` resolves it, so `./src/app.py:12` and `src/app.py:12` name the
    same changed line and both block."""

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 61)) + "\n", encoding="utf-8"
    )
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [finding("major", "./src/app.py:12 — the changed line")],
        }
    )

    decision = decide(
        document,
        {},
        tmp_path,
        "major",
        revision=Revision(diff=REVISION_DIFF),
        reproduce=Rerun(),
    )

    assert [one.id for one in decision.blockers] == ["F1"]
    assert decision.carried == []


# ....................... #
# A round rejected a claim, so the engine carries what the critic says about it
# (S-0126/D-1, S-0126/D-2). The critic never sees the rejection.


def _lines(count: int) -> str:
    return "\n".join(f"line {number}" for number in range(1, count + 1)) + "\n"


def _files(root: Path, **files: int) -> None:
    for name, count in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_lines(count), encoding="utf-8")


def _round_task(*findings: tuple[str, int | None, int | None], identifier="T-9002") -> Task:
    return Task(
        id=identifier,
        intent="Answer the round.",
        decisions=[],
        round={
            "branch": f"round/{identifier}",
            "findings": [
                {"path": path, "line": line, "end_line": end_line, "threads": []}
                for path, line, end_line in findings
            ],
        },
    )


def _rejection(claim: str, evidence: str, attempt: int = 1) -> dict:
    return {
        "decision": "unlisted",
        "kind": "contradicted",
        "attempt": attempt,
        "claim": claim,
        "evidence": evidence,
    }


def _write_log(worktree: Path, task: Task, *entries: dict) -> None:
    path = worktree / ".torve" / "tasks" / task.id / "log.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"entries": list(entries)}), encoding="utf-8")


def test_rejected_spots_reads_the_rounds_log_for_the_attempt(tmp_path):
    """S-0126/D-2: each rejection returns the span of every round finding its
    claim names, or the round's one finding when it names none, and every
    citation in its evidence that locates."""

    _files(tmp_path, **{"src/app.py": 60, "src/other.py": 60})
    task = _round_task(("src/app.py", 50, None), ("src/other.py", 3, None))
    _write_log(
        tmp_path,
        task,
        _rejection("the claim", "src/app.py:20-30 — decided here"),
        _rejection("the claim about app.py:50 is not a defect", "src/ghost.py:1 — gone"),
    )

    assert rejected_spots(task, tmp_path, 1) == [
        ("src/app.py", 20, 30),
        ("src/app.py", 50, 50),
    ]


def test_rejected_spots_names_a_finding_with_no_end_line(tmp_path):
    """S-0126/D-2: a finding with no `end_line` contributes its one line."""

    _files(tmp_path, **{"src/app.py": 40})
    task = _round_task(("src/app.py", 12, None), ("src/app.py", 30, None))
    _write_log(tmp_path, task, _rejection("app.py:12 is not a defect", "src/ghost.py:1 — gone"))

    assert rejected_spots(task, tmp_path, 1) == [("src/app.py", 12, 12)]


def test_rejected_spots_unnamed_rejection_answers_a_one_finding_round(tmp_path):
    """S-0126/D-2: when the round carries one finding and the claim names no
    spot, that finding's whole span is rejected, as the reply already answers
    its thread."""

    _files(tmp_path, **{"src/app.py": 40})
    task = _round_task(("src/app.py", 7, 9))
    _write_log(tmp_path, task, _rejection("the bound is wrong", "src/ghost.py:1 — gone"))

    assert rejected_spots(task, tmp_path, 1) == [("src/app.py", 7, 9)]


def test_rejected_spots_a_shared_basename_rejects_both_findings(tmp_path):
    """S-0126/A-1: a claim naming the basename two findings of the round share
    rejects both spans, as the reply answers both threads, and leaves the
    round's other finding blocking."""

    _files(tmp_path, **{"a/runconfig.py": 60, "b/runconfig.py": 60, "src/app.py": 60})
    task = _round_task(
        ("a/runconfig.py", 50, None), ("b/runconfig.py", 50, 52), ("src/app.py", 50, None)
    )
    _write_log(
        tmp_path, task, _rejection("runconfig.py:50 — the cap is the rule", "src/ghost.py:1 — gone")
    )

    assert rejected_spots(task, tmp_path, 1) == [
        ("a/runconfig.py", 50, 50),
        ("b/runconfig.py", 50, 52),
    ]


def test_rejected_spots_an_earlier_attempts_rejection_carries_nothing(tmp_path):
    """S-0126/D-2: a rejection stands only for the attempt that recorded it."""

    _files(tmp_path, **{"src/app.py": 40})
    task = _round_task(("src/app.py", 7, 9))
    _write_log(
        tmp_path, task, _rejection("app.py:7 is wrong", "src/app.py:7-9 — decided", attempt=1)
    )

    assert rejected_spots(task, tmp_path, 2) == []


def test_rejected_spots_a_task_without_a_round_has_none(tmp_path):
    """S-0126/D-2: only a review round carries rejected claims."""

    _files(tmp_path, **{"src/app.py": 40})
    task = Task(id="T-9003", intent="No round here.", decisions=[])
    _write_log(
        tmp_path, task, _rejection("app.py:7 is wrong", "src/app.py:7-9 — decided", attempt=1)
    )

    assert rejected_spots(task, tmp_path, 1) == []


def test_rejected_claim_the_t0913_row_verdict_is_carried(tmp_path):
    """S-0126/D-1: T-0913's attempt-3 critic findings cited a command and the
    disputed lines later in their evidence, and the round's rejection cited a
    range; the findings are carried, and the violated row with them."""

    _files(
        tmp_path,
        **{
            "src/torve/config/runconfig.py": 1400,
            "src/torve/application/reviewleg.py": 900,
        },
    )
    task = _round_task(("src/torve/config/runconfig.py", 1350, 1350))
    _write_log(
        tmp_path,
        task,
        _rejection(
            "The claim at runconfig.py:1350 that a file with more findings than "
            "the cap makes an oversized round is not a defect.",
            "src/torve/application/reviewleg.py:831-861 — batch_claims never splits a file",
        ),
    )
    document = CriticDocument.model_validate(
        {
            "rows": [
                {
                    "row": OWNED,
                    "verdict": "violated",
                    "evidence": "`python probes/p.py` — S-0125/D-3 is violated; "
                    "src/torve/application/reviewleg.py:852",
                }
            ],
            "findings": [
                finding(
                    "blocker",
                    "`python probes/p.py` — the cap is not enforced; "
                    "src/torve/application/reviewleg.py:852",
                    id="F1",
                ),
                finding("blocker", "src/torve/config/runconfig.py:1351 — the cap", id="F2"),
            ],
        }
    )
    spots = rejected_spots(task, tmp_path, 1)

    assert ("src/torve/application/reviewleg.py", 831, 861) in spots
    assert ("src/torve/config/runconfig.py", 1350, 1350) in spots

    decision = decide(document, {OWNED: True}, tmp_path, "blocker", rejected=spots)

    assert decision.blocked is False
    assert [one.id for one in decision.rejected] == ["F1", "F2"]
    assert decision.violated == []
    assert decision.rows[0]["blocks"] is False
    assert decision.rows[0]["rejected"] is True


def test_rejected_claim_a_path_with_spaces_is_read_whole(tmp_path):
    """S-0126/D-1: `locate` accepts a path containing spaces, so
    `src/legacy module.py:12` is one citation and not the truncated
    `module.py:12`; a finding disputing a rejected claim is carried."""

    _files(tmp_path, **{"src/legacy module.py": 60})
    task = _round_task(("src/legacy module.py", 8, None))
    _write_log(
        tmp_path,
        task,
        _rejection(
            "legacy module.py:8 is not a defect",
            "src/legacy module.py:8 — the caller checks it",
        ),
    )
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", "src/legacy module.py:9 — disputed")]}
    )

    spots = rejected_spots(task, tmp_path, 1)
    decision = decide(document, {}, tmp_path, "blocker", rejected=spots)

    assert spots == [("src/legacy module.py", 8, 8)]
    assert decision.blocked is False
    assert [one.id for one in decision.rejected] == ["F1"]


def test_rejected_claim_a_path_with_the_citation_alphabet_is_read_whole(tmp_path):
    """S-0126/D-1: `locate` accepts `src/a+b.py:12`, so the carry reads the
    whole name and not the truncated `b.py:12`; a finding disputing the
    rejected claim is carried, not left blocking."""

    _files(tmp_path, **{"src/a+b.py": 60})
    task = _round_task(("src/a+b.py", 12, None))
    _write_log(
        tmp_path,
        task,
        _rejection("src/a+b.py:12 is not a defect", "src/a+b.py:12 — the caller checks it"),
    )
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", "src/a+b.py:12 — disputed")]}
    )

    spots = rejected_spots(task, tmp_path, 1)
    decision = decide(document, {}, tmp_path, "blocker", rejected=spots)

    assert spots == [("src/a+b.py", 12, 12)]
    assert decision.blocked is False
    assert [one.id for one in decision.rejected] == ["F1"]


def test_rejected_claim_a_citation_outside_every_span_blocks(tmp_path):
    """S-0126/D-1: the finding's citation must lie within `threads.WINDOW`
    lines of a rejected span in the same file."""

    _files(tmp_path, **{"src/app.py": 60})
    task = _round_task(("src/app.py", 50, None), ("src/other.py", 3, None))
    _write_log(tmp_path, task, _rejection("the claim", "src/app.py:10-20 — decided here"))
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", "src/app.py:40 — elsewhere")]}
    )
    spots = rejected_spots(task, tmp_path, 1)

    decision = decide(document, {}, tmp_path, "blocker", rejected=spots, reproduce=Rerun())

    assert decision.blocked is True
    assert decision.blockers == decision.kept
    assert decision.rejected == []


def test_rejected_claim_a_citation_in_another_file_is_not_carried(tmp_path):
    """S-0126/D-1: the line alone does not carry a finding — the citation
    must sit near a rejected span in the same file."""

    _files(tmp_path, **{"src/app.py": 60, "src/other.py": 60})
    task = _round_task(("src/app.py", 50, None), ("src/other.py", 3, None))
    _write_log(tmp_path, task, _rejection("the claim", "src/app.py:10-20 — decided here"))
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", "src/other.py:12 — the same line elsewhere")]}
    )
    spots = rejected_spots(task, tmp_path, 1)

    decision = decide(document, {}, tmp_path, "blocker", rejected=spots, reproduce=Rerun())

    assert decision.blocked is True
    assert decision.rejected == []
    assert decision.carried == []


def test_rejected_claim_a_citation_that_does_not_locate_is_not_carried(tmp_path):
    """S-0126/D-1: every cited `path:line` must locate, so a second citation
    that does not leaves the finding under the ordinary rules."""

    _files(tmp_path, **{"src/app.py": 60})
    task = _round_task(("src/app.py", 50, None), ("src/other.py", 3, None))
    _write_log(tmp_path, task, _rejection("the claim", "src/app.py:10-20 — decided here"))
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [finding("blocker", "src/app.py:12 — see also src/ghost.py:3")],
        }
    )
    spots = rejected_spots(task, tmp_path, 1)

    decision = decide(document, {}, tmp_path, "blocker", rejected=spots, reproduce=Rerun())

    assert decision.blocked is True
    assert decision.rejected == []
    assert decision.carried == []


def test_rejected_claim_is_carried_and_marked_on_a_revision_attempt(tmp_path):
    """S-0126/D-1: on a revision attempt, first or revision alike, a finding
    near a rejected spot is carried before the S-0125/D-1 rules, even where
    the revision changed the line, and the record marks it `rejected: true`."""

    _files(tmp_path, **{"src/app.py": 60})
    task = _round_task(("src/app.py", 12, None))
    _write_log(tmp_path, task, _rejection("app.py:12 is not a defect", "src/ghost.py:1 — gone"))
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -12,1 +12,1 @@\n-old\n+new\n@@ -50,1 +50,1 @@\n-old\n+new\n"
    )
    document = CriticDocument.model_validate(
        {
            "rows": [],
            "findings": [
                finding("blocker", "src/app.py:12 — disputed", id="F1"),
                finding("blocker", "src/app.py:50 — elsewhere", id="F2"),
            ],
        }
    )

    decision = decide(
        document,
        {},
        tmp_path,
        "blocker",
        revision=Revision(diff=diff),
        rejected=rejected_spots(task, tmp_path, 1),
        reproduce=Rerun(),
    )

    assert [one.id for one in decision.blockers] == ["F2"]
    assert [one.id for one in decision.carried] == ["F1"]
    assert [one.id for one in decision.rejected] == ["F1"]
    recorded = {one["id"]: one for one in critic_module._recorded_findings(decision)}
    assert recorded["F1"]["rejected"] is True and recorded["F1"]["carried"] is True
    assert "rejected" not in recorded["F2"] and "carried" not in recorded["F2"]


def test_rejected_claim_judge_reads_the_attempt_under_review(monkeypatch):
    """S-0126/D-2: `judge` hands `run_critic` the attempt under review, so a
    rejection a later attempt recorded is the one read."""

    from types import SimpleNamespace

    seen: dict = {}
    monkeypatch.setattr(critic_module, "run_critic", lambda *a, **kw: seen.update(kw))
    monkeypatch.setattr(critic_module, "reviewer_for", lambda run: None)
    monkeypatch.setattr(critic_module, "revision_for", lambda run, state: None)
    monkeypatch.setattr(critic_module, "mapped_blocker_anchors", lambda worktree, state: [])
    run = SimpleNamespace(
        root=Path("."),
        worktree=Path("."),
        task=None,
        config=None,
        deps=SimpleNamespace(runtime=None, broker=None),
        last_pass=SimpleNamespace(patch="", evidence=None, digest=""),
        broker_handle=None,
    )

    critic_module.judge(run, None, SimpleNamespace(attempts=3))  # type: ignore[arg-type]

    assert seen["attempt"] == 3


def test_reproduced_judge_reruns_through_the_runs_reproduce(monkeypatch):
    """S-0127/D-1: `judge` hands `run_critic` `runner.reproduce` over the run
    under review and its state, so a re-run opens its own judge checkout and
    gates sandbox for that candidate."""

    from types import SimpleNamespace

    seen: dict = {}
    calls: list[tuple] = []
    monkeypatch.setattr(critic_module, "run_critic", lambda *a, **kw: seen.update(kw))
    monkeypatch.setattr(critic_module, "reviewer_for", lambda run: None)
    monkeypatch.setattr(critic_module, "revision_for", lambda run, state: None)
    monkeypatch.setattr(critic_module, "mapped_blocker_anchors", lambda worktree, state: [])
    monkeypatch.setattr(run_module, "reproduce", lambda *args: calls.append(args) or [])
    run = SimpleNamespace(
        root=Path("."),
        worktree=Path("."),
        task=None,
        config=None,
        deps=SimpleNamespace(runtime=None, broker=None),
        last_pass=SimpleNamespace(patch="", evidence=None, digest=""),
        broker_handle=None,
    )
    state = SimpleNamespace(attempts=1)

    critic_module.judge(run, None, state)  # type: ignore[arg-type]
    seen["reproduce"](["uv run pytest -q"], {"probes/p.py": None})

    assert calls == [(run, state, ["uv run pytest -q"], {"probes/p.py": None})]


@given(
    spot=st.integers(min_value=1, max_value=30),
    width=st.integers(min_value=0, max_value=15),
    cited=st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=3),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_rejected_claim_carries_exactly_the_findings_within_the_window(
    tmp_path, spot, width, cited
):
    """S-0126/D-1: a finding is carried exactly when it cites at least one
    `path:line`, all of them locate, and all lie within `threads.WINDOW` lines
    of a rejected span."""

    _files(tmp_path, **{"src/app.py": 60})
    rejected = [("src/app.py", spot, spot + width)]
    evidence = f"src/app.py:{cited[0]} — the bound" + "".join(
        f"; src/app.py:{line}" for line in cited[1:]
    )
    document = CriticDocument.model_validate(
        {"rows": [], "findings": [finding("blocker", evidence)]}
    )

    decision = decide(document, {}, tmp_path, "blocker", rejected=rejected, reproduce=Rerun())

    expected = all(spot - threads.WINDOW <= line <= spot + width + threads.WINDOW for line in cited)
    assert (decision.rejected != []) is expected
    assert decision.blocked is not expected


def test_a_rejected_row_verdict_never_erases_an_independent_violation(tmp_path):
    """S-0122/D-1, S-0126/D-1: a LOCKED row the critic answers twice keeps a
    located `violated` that stands on its own beside one disputing a rejected
    claim, so the independent violation still blocks on what locates
    (S-0127/D-1)."""

    _files(tmp_path, **{"src/app.py": 60})
    rows = [
        {"row": LOCKED, "verdict": "violated", "evidence": "src/app.py:60 — an independent defect"},
        {"row": LOCKED, "verdict": "violated", "evidence": "src/app.py:14 — the rejected claim"},
    ]
    document = CriticDocument.model_validate({"rows": rows, "findings": []})

    decision = decide(
        document,
        {LOCKED: False},
        tmp_path,
        "blocker",
        rejected=[("src/app.py", 12, 14)],
    )

    assert decision.blocked is True
    assert [one.evidence for one in decision.violated] == ["src/app.py:60 — an independent defect"]
    assert "rejected" not in decision.rows[0]


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

        for name in ("revision.diff", "blockers.yaml"):
            path = review / name

            if path.is_file():
                self.seen[name] = path.read_text(encoding="utf-8")

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
    rerun = Rerun()

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
        reproduce=rerun,
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

    # The engine decided (S-0127/D-1): the located major's repro was re-run
    # with the probe its copy kept and failed, so it blocks; the row's breach
    # names no command, so it is advisory.
    assert verdict.decision is not None and verdict.decision.blocked
    assert rerun.calls == [(["python probes/p.py"], {"probes/p.py": "assert bound(4) == 4\n"})]
    assert [f.claim for f in verdict.blockers] == ["the bound is off by one"]
    assert [f.claim for f in verdict.kept] == ["the bound is off by one", f"{OWNED} is violated"]
    assert verdict.discarded and "it reads wrong" in verdict.discarded[0]

    # Only the probe a surviving finding names is kept.
    assert verdict.probes == {"probes/p.py": "assert bound(4) == 4\n"}
    kept = repo.root / ".torve" / "tasks" / review.id / "probes"
    assert sorted(p.name for p in kept.iterdir()) == ["p.py"]
    assert "assert bound(4) == 4" in verdict.threads[0]["comments"][0]["body"]
    assert (
        "the engine re-ran `python probes/p.py` on this candidate: exit 1"
        in (verdict.threads[0]["comments"][0]["body"])
    )
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
        {
            "row": OWNED,
            "owned": True,
            "verdict": "violated",
            "located": True,
            "blocks": False,
            "advisory": True,
        }
    ]
    assert [(f["kind"], f["severity"], f["verified"]) for f in record["findings"]] == [
        ("bug", "major", "executed")
    ]
    assert record["findings"][0]["rerun"] == {
        "command": "python probes/p.py",
        "exit_code": 1,
        "tail": "python probes/p.py: 1 failed",
    }
    assert "advisory" not in record["findings"][0]
    assert record["evidence"]["rows"] == {OWNED: "proved"}

    # Nothing the critic wrote survived.
    assert not (worktree / "probes").exists()
    assert not (repo.root / ".wt" / review.id).exists()


def test_revision_inputs_stage_the_diff_and_blockers_and_a_carried_finding_is_recorded(repo):
    """S-0125/D-2: a revision's call stages the diff since the last judged
    head and every earlier round's blockers, and names both; S-0125/D-1: a
    finding on a line the revision did not touch is carried, not blocked."""

    worktree, target = _candidate(repo)
    ctx = build_context(worktree, load_manifest(worktree / ".torve" / "gates.yaml"), base="main")
    ctx.task = target
    evidence = build_evidence(ctx, [], candidate=TASK_ID)
    review = mint_review_task(repo.root, target)
    revision = Revision(
        diff=(
            "diff --git a/src/other.py b/src/other.py\n"
            "--- a/src/other.py\n"
            "+++ b/src/other.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        blockers=[
            {
                "round": 1,
                "claim": "an earlier round asked for this",
                "path": "src/elsewhere.py",
                "line": 1,
            }
        ],
    )
    critic = ProbingCritic(answer(findings=[finding()]))

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
        revision=revision,
    )

    # The head recorded is the tree the critic judged, uncommitted files and
    # all, never the worktree's HEAD commit.
    assert verdict.head == tree_snapshot(worktree)

    # Both files are staged in the copy and named in the per-call prompt.
    assert ".torve/review/revision.diff" in critic.seen["staged"]
    assert ".torve/review/blockers.yaml" in critic.seen["staged"]
    prompt = str(critic.seen["prompt"])
    assert ".torve/review/revision.diff" in prompt
    assert ".torve/review/blockers.yaml" in prompt
    assert "+new" in str(critic.seen["revision.diff"])
    assert yaml.safe_load(str(critic.seen["blockers.yaml"])) == revision.blockers

    # The finding is on a file the revision did not change: recorded carried,
    # in the record as well as in the decision.
    assert verdict.decision is not None and verdict.decision.blocked is False
    assert [one.id for one in verdict.decision.carried] == ["F1"]
    records = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]
    record = next(row for row in records if row.get("kind") == "review")
    assert record["blocked"] is False
    assert record["findings"][0]["carried"] is True
    assert record["findings"][0]["claim"] == "the bound is off by one"


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


def test_reproduced_only_once_the_critics_session_has_ended(repo):
    """S-0122/D-2, S-0127/D-1: `run_critic` re-runs a repro only after the
    critic's sandbox is gone, so nothing the re-run measures reaches the
    critic, and its verdict blocks on what the re-run measured."""

    worktree, target = _candidate(repo)
    runtime = MockRuntime()
    destroyed_at_rerun: list[list[str]] = []

    def rerun(commands, probes):
        destroyed_at_rerun.append(list(runtime.destroyed))
        return [CommandRun(command, 1, "1 failed") for command in commands]

    verdict = run_critic(
        repo.root,
        worktree,
        target,
        mint_review_task(repo.root, target),
        critic_config(),
        runtime,
        ScriptedAgent([AgentResult(exit_code=0, output=answer(findings=[finding()]))]),
        "",
        None,
        "digest",
        reproduce=rerun,
    )

    assert len(destroyed_at_rerun) == 1 and destroyed_at_rerun[0]
    assert verdict.decision is not None and verdict.decision.blocked is True
    assert verdict.fact.startswith("critic blocked: 1 finding(s)")


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
    repros, the engine's re-run of each (S-0127/D-1) and the package's
    failing items — never the critic's own prose, which is a trace no
    revision reads."""

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
        [answer(findings=[finding(fix_hint="the subtraction in bound")]), answer()]
    )

    state = run_task(repo.root, task_for(repo), critic_config(), deps_for(critic))

    assert state.state is TaskState.READY
    assert any(h["fact"].startswith("review blocker (revision 1 of 1):") for h in state.history)
    text = feedback_file(repo.root, "T-9001").read_text(encoding="utf-8")
    assert "the bound is off by one" in text
    assert f"repro: `{REPRO}`" in text
    # S-0127/D-1: the thread carries the engine's re-run, its exit and output.
    assert f"the engine re-ran `{REPRO}` on this candidate: exit 1" in text
    assert "1 failed: assert 3 == 4" in text
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
