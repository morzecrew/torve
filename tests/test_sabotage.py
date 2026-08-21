from __future__ import annotations

from torve.sabotage import CASES, run_all


def test_every_gate_has_a_red_case_and_a_green_case():
    """D-2.2: no case, no gate — both directions, for every builtin."""
    for gate in ("scope", "acceptance", "no-test-tampering",
                 "decisions-reported", "self-audit", "secrets"):
        expectations = {c.expected for c in CASES if c.gate == gate}
        assert "fail" in expectations, f"{gate} has no sabotage case"
        assert expectations & {"pass", "flaky", "bypassed"}, f"{gate} has no green twin"


def test_sabotage_suite_behaves():
    outcomes = run_all()
    misbehaved = [f"{o.name}: expected {o.expected}, got {o.got}" for o in outcomes if not o.ok]
    assert not misbehaved, "\n".join(misbehaved)
