from __future__ import annotations

from torve.gates.sabotage import CASES, run_all


def test_every_gate_has_a_red_case_and_a_green_case():
    """D-2.2: no case, no gate — both directions, for every shipped case."""
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
