"""The `suite` builtin (S-0118/D-1 to D-4): the whole suite once, a red
attributed by the head, rerun and base readings, and the reports that ride
beside it without convicting."""

from __future__ import annotations

from typing import Any

from conftest import context_for
from hypothesis import given
from hypothesis import strategies as st

from torve.gates.runner import run_gates
from torve.gates.sabotage import VERIFY_MANIFEST_RUN, Repo
from torve.gates.suite import attribution, failing_ids

TASK_ID = "T-9001"
SUITE_ID = "tests/test_app.py::test_app"

READING_TEST = (
    "from pathlib import Path\n\n"
    'VALUE = (Path(__file__).resolve().parents[1] / "src" / "app.py").read_text()\n\n\n'
    "def test_app():\n"
    '    assert "VALUE = 1" in VALUE\n'
)

RED_SOURCE = "VALUE = 2\n"

FLAKY_TEST = (
    "from pathlib import Path\n\n"
    'MARKER = Path(__file__).resolve().parents[1] / ".torve" / "suite-flaky"\n\n\n'
    "def test_app():\n"
    "    if not MARKER.exists():\n"
    "        MARKER.parent.mkdir(parents=True, exist_ok=True)\n"
    '        MARKER.write_text("x")\n'
    "        assert False\n"
)

ORDER_BASE = (
    'STATE = {"seen": False}\n\n\n'
    "def test_first():\n"
    '    STATE["seen"] = False\n\n\n'
    "def test_second():\n"
    '    assert not STATE["seen"]\n'
)

ORDER_HEAD = ORDER_BASE.replace('STATE["seen"] = False', 'STATE["seen"] = True')


# ....................... #


def suite_manifest(commands: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scope": {"allow": [], "deny": []},
        "tests": {"run": VERIFY_MANIFEST_RUN},
        "gates": [
            {
                "name": "suite",
                "run": "@suite",
                "state": "blocking",
                "origin": "S-0118",
                "timeout": 120,
                "sabotage": "suite",
                "commands": commands or [VERIFY_MANIFEST_RUN],
            }
        ],
    }


def judged(repo: Repo):
    report = run_gates(context_for(repo), only={"suite"})
    return report.results[0]


def red_base(repo: Repo, test: str = READING_TEST) -> None:
    """A main branch already red: the attempt merged it and changed something
    the failing test does not read."""

    repo.git("checkout", "-q", "main")
    repo.write("src/app.py", RED_SOURCE)
    repo.write("tests/test_app.py", test)
    repo.commit("a red the base carries")
    repo.git("checkout", "-q", f"torve/{TASK_ID}")
    repo.git("merge", "-q", "main")


# ....................... #
# The pure attribution (D-1, D-2).


def test_failing_ids_are_read_from_pytest_short_summary():
    """S-0118/D-2: a line naming a file but no test id is a collection error,
    not something to attribute."""

    output = (
        "FAILED tests/test_a.py::test_one - AssertionError: nope\n"
        "FAILED tests/test_b.py::TestThing::test_two[p] - ValueError\n"
        "ERROR tests/test_c.py - ImportError: no module\n"
    )

    assert failing_ids(output) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::TestThing::test_two[p]",
    ]


@given(
    head=st.sets(st.text(min_size=1)),
    rerun=st.one_of(st.none(), st.sets(st.text(min_size=1))),
    base=st.one_of(st.none(), st.sets(st.text(min_size=1))),
)
def test_attribution_partitions_the_head_failures(head, rerun, base):
    """S-0118/D-1: the three outcomes partition the head's failures, and a test
    convicts exactly when it is red on the rerun and not red on the base.
    S-0118/D-2: no base reading clears nothing, so every id still red convicts."""

    result = attribution(head, rerun, base)

    parts = set(result.flaky) | set(result.inherited) | set(result.convicted)
    assert parts == head
    assert not (set(result.flaky) & set(result.inherited))
    assert not (set(result.flaky) & set(result.convicted))
    assert not (set(result.inherited) & set(result.convicted))

    rerun_ids = None if rerun is None else set(rerun)
    base_ids = None if base is None else set(base)

    for node in head:
        red_on_rerun = rerun_ids is None or node in rerun_ids
        red_on_base = base_ids is not None and node in base_ids

        assert (node in result.convicted) == (red_on_rerun and not red_on_base)
        assert (node in result.inherited) == (red_on_rerun and red_on_base)
        assert (node in result.flaky) == (rerun_ids is not None and node not in rerun_ids)


# ....................... #
# The builtin over a scratch repository.


def test_a_regression_convicts_naming_the_test(repo):
    """S-0118/D-1: a test red at the head and green on the base convicts, and
    S-0118/D-11: the base reading comes from the tree the host extracts."""

    repo.seed(suite_manifest())
    repo.write("src/app.py", RED_SOURCE)
    repo.write("tests/test_app.py", READING_TEST)
    repo.commit("a change the test does not meet")

    result = judged(repo)

    assert result.outcome == "fail"
    assert SUITE_ID in result.output
    assert result.inherited_failures == []
    assert result.exit_code == 1


def test_an_inherited_red_passes_and_is_recorded(repo):
    """S-0118/D-1, S-0118/D-4: a red the base carries too never convicts, and
    is recorded by name on the passing result."""

    repo.seed(suite_manifest())
    red_base(repo)
    repo.write("src/other.py", "VALUE = 3\n")
    repo.commit("an unrelated change")

    result = judged(repo)

    assert result.outcome == "pass"
    assert result.exit_code == 0
    assert result.inherited_failures == [SUITE_ID]


def test_an_order_dependence_inside_the_file_convicts(repo):
    """S-0118/D-1: an order dependence inside a file convicts as it did before,
    because the rerun keeps the file's own order."""

    repo.seed(suite_manifest())
    red_base(repo, ORDER_BASE)
    repo.write("tests/test_app.py", ORDER_HEAD)
    repo.commit("the first test poisons the second")

    result = judged(repo)

    assert result.outcome == "fail"
    assert "tests/test_app.py::test_second" in result.output


def test_a_failing_file_the_base_lacks_convicts(repo):
    """S-0118/D-2: a failing test the base cannot run has no base reading, so
    it convicts rather than inheriting."""

    repo.seed(suite_manifest())
    repo.write("tests/test_new.py", "def test_new():\n    assert False\n")
    repo.commit("a failing test the base lacks")

    result = judged(repo)

    assert result.outcome == "fail"
    assert "tests/test_new.py::test_new" in result.output


def test_a_new_failing_file_does_not_convict_an_inherited_red(repo):
    """S-0118/D-1, S-0118/D-2: the base holds one of the failing files and not
    the other, so the inherited id is read there and only the file the base
    cannot run convicts."""

    repo.seed(suite_manifest())
    red_base(repo)
    repo.write("tests/test_new.py", "def test_new():\n    assert False\n")
    repo.commit("a new failing test beside an inherited red")

    result = judged(repo)

    assert result.outcome == "fail"
    assert "tests/test_new.py::test_new" in result.output
    assert result.inherited_failures == [SUITE_ID]


def test_a_red_naming_no_test_convicts(repo):
    """S-0118/D-2: a red whose output names no failing test convicts."""

    repo.seed(suite_manifest())
    repo.write("tests/test_app.py", "def test_app(:\n    assert True\n")
    repo.commit("a file that does not collect")

    result = judged(repo)

    assert result.outcome == "fail"
    assert "names no failing test" in result.output


def test_a_red_cleared_by_the_rerun_is_flaky(repo):
    """S-0118/D-1: green on the rerun is flaky, never a conviction."""

    repo.seed(suite_manifest())
    repo.write("tests/test_app.py", FLAKY_TEST)
    repo.commit("a test that passes on the rerun")

    result = judged(repo)

    assert result.outcome == "flaky"
    assert result.exit_code == 0
    assert result.flaky_commands == [VERIFY_MANIFEST_RUN]


def test_reports_run_before_any_rerun_over_what_the_suite_wrote(repo):
    """S-0118/D-3: a later command runs right after the suite's own run and
    before any rerun, over what that run wrote."""

    commands = [
        "pytest -q -p no:cacheprovider; code=$?; echo HEAD > suite-artifact; exit $code",
        "cat suite-artifact",
    ]
    repo.seed(suite_manifest(commands))
    repo.write("src/app.py", RED_SOURCE)
    repo.write("tests/test_app.py", READING_TEST)
    repo.commit("a red with a report")

    result = judged(repo)

    assert result.outcome == "fail"
    assert "HEAD" in result.output
    assert result.output.index("HEAD") < result.output.index("head rerun")


def test_a_failing_report_does_not_convict(repo):
    """S-0118/D-3: a report's exit code is a reading, never a conviction."""

    repo.seed(suite_manifest([VERIFY_MANIFEST_RUN, "exit 1"]))
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("a green suite with a red report")

    result = judged(repo)

    assert result.outcome == "pass"
    assert "report, exit 1" in result.output


def test_a_report_substitutes_the_battery_base(repo):
    """S-0118/D-3: a report is a command like any other, with the battery's
    base substituted into it."""

    repo.seed(suite_manifest([VERIFY_MANIFEST_RUN, "echo BASE={base}"]))
    repo.write("src/app.py", "print('changed')\n")
    repo.commit("a report naming the base")

    result = judged(repo)

    assert result.outcome == "pass"
    assert "{base}" not in result.output
    assert "BASE=" in result.output
