from __future__ import annotations

import pytest
import yaml

from torve.gates.sabotage import CASES, run_all


def test_every_gate_has_a_red_case_and_a_green_case():
    """S-0002/D-2: no case, no gate — both directions, for every shipped case."""
    for gate in (
        "scope",
        "acceptance",
        "no-test-tampering",
        "decisions-reported",
        "self-audit",
        "secrets",
        "source-layout",
        "coverage-delta",
    ):
        expectations = {c.expected for c in CASES if c.gate == gate}
        assert "fail" in expectations, f"{gate} has no sabotage case"
        assert expectations & {"pass", "flaky", "bypassed"}, f"{gate} has no green twin"


def test_the_coverage_twins_declare_their_tools():
    # A case that shells out to a dev tool must declare it, so a consuming
    # repository without the tool skips the case instead of reddening it.
    from torve.gates.sabotage import Case

    for case in CASES:
        assert isinstance(case, Case)
        assert all(case.requires)
        if case.gate == "coverage-delta":
            assert case.requires == ("pytest", "diff-cover")


def test_the_scope_family_promotes_a_contingent_neighbour():
    """S-0104/D-2, D-7: the sabotage suite's gate half — the scope gate does
    not convict a contingent path, it promotes it and names it, so the
    widening is read rather than made silently."""
    from torve.gates.sabotage import run_case

    case = next(c for c in CASES if c.name == "scope: contingent neighbour is promoted")
    assert case.expected == "pass"
    outcome = run_case(case)
    assert outcome.ok, outcome.detail
    assert outcome.got == "pass"


def _holder(root, task_id: str, allow: list[str]) -> None:
    """An in-flight run holding *allow*: the contract `_in_flight` reads and
    the state file that places it in flight — the second task of D-3."""

    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    contract = root / ".torve" / "tasks" / task_id
    contract.mkdir(parents=True, exist_ok=True)
    (contract / "contract.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": task_id,
                "role": "implement",
                "scope": {"allow": allow, "deny": []},
                "acceptance": [],
                "decisions": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    state = RunState(task_id=task_id, path=naming.state_file(root, task_id))
    state.transition(TaskState.CLAIMED, "seeded in-flight run")
    state.transition(TaskState.RUNNING, "seeded in-flight run")
    state.save()


def _promotion(root, task):
    """A minimal run and context for the runner's write-time promotion check:
    the refusal reads the diff, the contract and the in-flight fence, and
    touches nothing else on the run."""

    from types import SimpleNamespace

    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.config.manifest import Manifest
    from torve.gates.context import DiffEntry, GateContext

    run = SimpleNamespace(
        shadow=False,
        root=root,
        task=task,
        deps=SimpleNamespace(facts=None, channel=None),
    )
    state = RunState(task_id=task.id, path=naming.state_file(root, task.id))
    ctx = GateContext(
        root=root,
        manifest=Manifest(),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
        diff=[DiffEntry(status="A", path="docs/notes.md")],
        task=task,
    )

    return run, state, ctx


def test_a_contingent_write_another_task_holds_is_convicted(tmp_path):
    """S-0104/D-3: the sabotage suite convicts a write to a contingent path
    another in-flight task holds — the runner refuses it naming the holder,
    and promotes nothing. The holder is load-bearing: with its contract gone
    the same write is an ordinary promotion, so the case cannot pass with no
    task holding the path.

    The refusal lives in the runner, not the scope gate: the gate judges one
    contract and reads no other task, and `torve.gates` may not import
    `torve.application`, so this is the suite's reachable proof."""
    from torve.application.runner import _record_promotions
    from torve.domain.states import BlockedDispatch
    from torve.domain.task import Scope, Task

    _holder(tmp_path, "T-9002", ["docs/**"])
    task = Task(
        id="T-9001",
        decisions=[],
        scope=Scope(allow=["src/app/**"], contingent=["docs/notes.md"]),
    )

    run, state, ctx = _promotion(tmp_path, task)

    with pytest.raises(BlockedDispatch, match=r"blocked_by_overlap: T-9002 on docs/notes\.md"):
        _record_promotions(run, state, ctx)


def test_a_contingent_write_no_task_holds_is_promoted(tmp_path):
    """S-0104/D-2: the clean twin — the same write, no holder in flight, is
    promoted and recorded. The refusal above is the holder's doing, not the
    path's, which is what makes the pair a proof."""

    from torve.application.runner import _record_promotions, _recorded_promotions
    from torve.domain.task import Scope, Task

    task = Task(
        id="T-9001",
        decisions=[],
        scope=Scope(allow=["src/app/**"], contingent=["docs/notes.md"]),
    )

    run, state, ctx = _promotion(tmp_path, task)
    _record_promotions(run, state, ctx)

    assert _recorded_promotions(state) == ["docs/notes.md"]


def test_a_case_requiring_an_absent_tool_skips_instead_of_convicting():
    from torve.gates.sabotage import Case, run_case

    phantom = Case(
        name="phantom tool",
        gate="coverage-delta",
        expected="fail",
        build=lambda repo: None,
        requires=("torve-absent-tool-xyz",),
    )
    outcome = run_case(phantom)
    assert outcome.got == "skipped"
    assert outcome.ok  # an environment without the tool convicts nobody


def test_sabotage_suite_behaves():
    outcomes = run_all()
    misbehaved = [f"{o.name}: expected {o.expected}, got {o.got}" for o in outcomes if not o.ok]
    assert not misbehaved, "\n".join(misbehaved)
