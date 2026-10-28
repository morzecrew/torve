from __future__ import annotations

import shlex
import sys

import pytest
from conftest import context_for

from torve.config.manifest import Gate
from torve.gates import row_evidence
from torve.gates.sabotage import TASK_ID

GATE = Gate(name="evidence", run="@evidence", state="shadow", origin="S-0115", timeout=120)

APP = "def f(x):\n    return 1 if x >= 0 else -1\n"

HEAD = (
    "import sys\n"
    "from pathlib import Path\n\n"
    'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))\n'
)

# A changed test that cites the row and is red on the base tree — the source
# function it imports does not exist there — and green on the candidate.
RED_TEST = HEAD + (
    "from app import f  # noqa: E402\n\n\n"
    "def test_row():\n"
    '    """S-0001/D-1"""\n'
    "    assert f(-1) == -1\n"
)

# The same test citing nothing: it proves no row however red it is on base.
UNCITED_TEST = HEAD + (
    "from app import f  # noqa: E402\n\n\ndef test_row():\n    assert f(-1) == -1\n"
)

# A test that passes on the base tree: it would have passed before the change.
PASSING_TEST = HEAD + ('\n\ndef test_row():\n    """S-0001/D-1"""\n    assert True\n')


def _row(identifier: str, paths: list[str], check: str | None = None) -> dict:
    row = {
        "id": identifier,
        "grade": "ASSUMED",
        "text": "a row this battery carries",
        "paths": paths,
    }

    if check is not None:
        row["check"] = check

    return row


def _task(decisions: list[dict], deny: list[str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "id": TASK_ID,
        "role": "implement",
        "scope": {"allow": [], "deny": deny or []},
        "acceptance": [],
        "decisions": decisions,
    }


@pytest.fixture
def evidence(monkeypatch):
    """The gate with its launcher pointed at this interpreter, so the scratch
    repository runs without the project's own environment."""

    launcher = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
    monkeypatch.setattr(row_evidence, "TEST_COMMAND", launcher)

    return row_evidence.check_evidence


def _changed(repo, test_source: str, decisions: list[dict], deny: list[str] | None = None):
    """A task branch whose app module and test file are both new against main."""

    repo.seed()
    repo.task(_task(decisions, deny), None)
    repo.write("src/app.py", APP)
    repo.write("tests/test_app.py", test_source)
    repo.commit("the attempt")


def _landed_test(repo, test_source: str, decisions: list[dict], deny: list[str] | None = None):
    """The verifier's test landed on main, red against a source that lacks `f`;
    the attempt then adds `f` and leaves the test untouched."""

    repo.seed()
    repo.git("checkout", "-q", "main")
    repo.write("src/app.py", "VALUE = 1\n")
    repo.write("tests/test_app.py", test_source)
    repo.commit("the verifier's test landed red")
    repo.git("checkout", "-q", f"torve/{TASK_ID}")
    repo.git("merge", "-q", "main")
    repo.task(_task(decisions, deny), None)
    repo.write("src/app.py", APP)
    repo.commit("the implementation")


# ....................... #
# D-1: the citation is the function's own, and a whole token.


def test_a_function_docstring_citing_the_row_proves_it(repo, evidence):
    _changed(repo, RED_TEST, [_row("S-0001/D-1", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "pass", outcome.output
    assert "row S-0001/D-1: pass" in outcome.output
    assert "tests/test_app.py::test_row" in outcome.output


def test_a_module_docstring_proves_nothing_for_its_functions(repo, evidence):
    module_citation = (
        '"""S-0001/D-1"""\n'
        "import sys\n"
        "from pathlib import Path\n\n"
        'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))\n'
        "from app import f  # noqa: E402\n\n\n"
        "def test_row():\n"
        "    assert f(-1) == -1\n"
    )
    _changed(repo, module_citation, [_row("S-0001/D-1", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "row S-0001/D-1: fail — no test cites the row" in outcome.output


def test_a_class_docstring_proves_nothing_for_its_methods(repo, evidence):
    class_citation = HEAD + (
        "from app import f  # noqa: E402\n\n\n"
        "class TestRows:\n"
        '    """S-0001/D-1"""\n\n'
        "    def test_row(self):\n"
        "        assert f(-1) == -1\n"
    )
    _changed(repo, class_citation, [_row("S-0001/D-1", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "no test cites the row" in outcome.output


def test_row_two_does_not_match_row_twenty_ones_citation(repo, evidence):
    other = HEAD + (
        "from app import f  # noqa: E402\n\n\n"
        "def test_row():\n"
        '    """S-0001/D-21"""\n'
        "    assert f(-1) == -1\n"
    )
    _changed(repo, other, [_row("S-0001/D-2", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "row S-0001/D-2: fail" in outcome.output


def test_an_unchanged_test_function_does_not_qualify(repo, evidence):
    """D-1: a test that cited the row before this change was evidence for an
    earlier one, so it proves nothing here even when it is red on base."""

    _landed_test(repo, RED_TEST, [_row("S-0001/D-1", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "no test cites the row" in outcome.output


# ....................... #
# D-2: red on the base tree and green on the candidate.


def test_a_citing_test_that_passes_on_the_base_tree_proves_nothing(repo, evidence):
    _changed(repo, PASSING_TEST, [_row("S-0001/D-1", ["src/**"])])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "fail"
    assert "green on the candidate" in outcome.output


def test_a_row_with_a_check_is_not_run_by_evidence(repo, evidence):
    check = _row("S-0001/D-1", ["src/**"], check="true")
    _changed(repo, UNCITED_TEST, [check])

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "pass", outcome.output
    assert "row S-0001/D-1: check — judged by its decision gate" in outcome.output


# ....................... #
# D-9: the verifier's file, in an implement attempt, counts as changed; a verify
# attempt's battery skips.


def test_the_verifier_file_the_contract_denies_counts_as_changed(repo, evidence):
    _landed_test(
        repo,
        RED_TEST,
        [_row("S-0001/D-1", ["src/**"])],
        deny=["tests/test_app.py"],
    )

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "pass", outcome.output
    assert "row S-0001/D-1: pass" in outcome.output


def test_a_verify_attempts_battery_skips_evidence(repo, evidence):
    repo.seed()
    repo.task(
        {
            "schema_version": 1,
            "id": TASK_ID,
            "role": "verify",
            "scope": {"allow": ["tests/verify/**"], "deny": []},
            "acceptance": ["pytest tests/verify/test_s0001_p1.py"],
            "decisions": [_row("S-0001/D-1", ["src/**"])],
        },
        None,
    )
    repo.write("tests/verify/test_s0001_p1.py", RED_TEST)
    repo.commit("a verifier's tests, red by design")

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "skipped"
    assert "verify-tests judges them" in outcome.output


# ....................... #
# D-3: the documentation exemption, and the skip.


def test_a_battery_of_documentation_rows_skips(repo, evidence):
    _changed(
        repo,
        RED_TEST,
        [
            _row("S-0001/D-1", ["pages/docs/operating.md"]),
            _row("S-0001/D-2", ["skills/spec-writer/SKILL.md"]),
        ],
    )

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "skipped"
    assert "no behavioural row" in outcome.output


def test_a_documentation_row_does_not_fail_beside_a_proved_one(repo, evidence):
    _changed(
        repo,
        RED_TEST,
        [
            _row("S-0001/D-1", ["src/**"]),
            _row("S-0001/D-2", ["pages/**"]),
        ],
    )

    outcome = evidence(GATE, context_for(repo))

    assert outcome.outcome == "pass", outcome.output
    assert "row S-0001/D-1: pass" in outcome.output
    assert "row S-0001/D-2: documentation" in outcome.output


# ....................... #
# The reading: one result back to rows for the lane and the review.


def test_evidence_reading_parses_each_rows_verdict():
    text = (
        "row S-0001/D-1: pass — tests/test_a.py::test_one, tests/test_b.py::test_two\n"
        "row S-0001/D-2: fail — no test cites the row\n"
        "row S-0001/D-3: check — judged by its decision gate\n"
        "row S-0001/D-4: documentation — no test required\n"
    )

    rows = row_evidence.evidence_reading(text)

    assert [row.row for row in rows] == [
        "S-0001/D-1",
        "S-0001/D-2",
        "S-0001/D-3",
        "S-0001/D-4",
    ]
    assert rows[0].proved
    assert rows[0].tests == ("tests/test_a.py::test_one", "tests/test_b.py::test_two")
    assert not rows[1].proved and rows[1].tests == ()
    assert rows[2].state == "check"
    assert rows[3].state == "documentation"


def test_a_skipped_battery_reads_as_no_rows():
    assert row_evidence.evidence_reading("no behavioural row") == []
