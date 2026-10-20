"""`held-out` — the verifier's properties run against the candidate, and how
thin its failure report is kept (S-0116/D-10).

The point of the builtin is that a next attempt learns which row failed and
why, and never the test's source: the implementer must not be handed the
assertion to special-case. The redaction test is the one that matters most.
"""

from __future__ import annotations

import shlex
import sys

from torve.config.manifest import Gate, Manifest
from torve.config.manifest import TestsConfig as Tests
from torve.domain.events import HoldoutFile
from torve.gates.context import GateContext
from torve.gates.held_out import check_held_out

# ....................... #

GATE = Gate(
    name="held-out",
    run="@held-out",
    state="blocking",
    origin="S-0116",
    input="worktree",
    timeout=120,
)

LAUNCHER = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"


def _context(root, holdout):
    return GateContext(
        root=root,
        manifest=Manifest(gates=[GATE], tests=Tests(run=LAUNCHER)),
        head_sha="0" * 40,
        base=None,
        merge_base=None,
        holdout=[HoldoutFile(**one) for one in holdout],
    )


# ....................... #


def test_a_failing_property_reports_its_identifier_message_and_rows_only(tmp_path):
    """S-0116/D-10: the output keeps each failing test's identifier, message
    and cited rows — no traceback, no source — and the files are removed when
    the gate ends."""

    holdout = [
        {
            "name": "test_p4.py",
            "text": (
                "# the row this proves (S-0116/D-10)\ndef test_row():\n"
                "    assert 1 == 2, 'contradicts row'\n"
            ),
        }
    ]

    result = check_held_out(GATE, _context(tmp_path, holdout))

    assert result.outcome == "fail"
    assert "test_p4.py::test_row" in result.output
    # pytest's short summary truncates the message to fit the terminal width,
    # so what is asserted is that the message reached the report at all.
    assert "AssertionError: contradict" in result.output
    assert "S-0116/D-10" in result.output

    # Neither the assertion's source nor the traceback reaches the report.
    assert "assert 1 == 2" not in result.output
    assert "Traceback" not in result.output
    assert "def test_row" not in result.output

    # The files existed for this run alone.
    assert not (tmp_path / "tests" / "verify" / "holdout").exists()


# ....................... #


def test_a_property_that_errors_names_the_test_and_never_its_source(tmp_path):
    """S-0116/D-10: a property that errors at setup leaves pytest no `FAILED`
    line, and the report names the test and its message from the `ERROR` line
    rather than falling back to the run's raw output — which is exactly where
    the property's source would show."""

    holdout = [
        {
            "name": "test_p4.py",
            "text": (
                "# the row this proves (S-0116/D-10)\nimport pytest\n\n\n"
                "@pytest.fixture\ndef boom():\n    raise RuntimeError('hidden')\n\n\n"
                "def test_row(boom):\n    assert True\n"
            ),
        }
    ]

    result = check_held_out(GATE, _context(tmp_path, holdout))

    assert result.outcome == "fail"
    assert "test_p4.py::test_row" in result.output
    assert "RuntimeError: hidden" in result.output
    assert "S-0116/D-10" in result.output

    assert "raise RuntimeError" not in result.output
    assert "Traceback" not in result.output


# ....................... #


def test_a_red_with_no_summary_line_does_not_dump_the_run(tmp_path):
    """S-0116/D-10: a run pytest cannot summarise — here a module holding no
    test at all — reports the red without the run's output, so nothing of the
    property's source reaches the report."""

    holdout = [{"name": "test_p4.py", "text": "# S-0116/D-10\nSECRET_MARKER = 1\n"}]

    result = check_held_out(GATE, _context(tmp_path, holdout))

    assert result.outcome == "fail"
    assert "the run exited" in result.output
    assert "no tests ran" not in result.output


# ....................... #


def test_a_property_the_candidate_satisfies_passes(tmp_path):
    holdout = [{"name": "test_p4.py", "text": "# S-0116/D-10\ndef test_row():\n    assert True\n"}]

    result = check_held_out(GATE, _context(tmp_path, holdout))

    assert result.outcome == "pass", result.output


# ....................... #


def test_a_file_already_under_the_holdout_directory_survives_the_run(tmp_path):
    """S-0116/D-10, D-14: the gate removes the files it wrote and only those.
    A landed document's properties are committed at the same path, so a run
    that cleared the directory would delete the repository's own tests."""

    landed = tmp_path / "tests" / "verify" / "holdout" / "test_s0116_p3.py"
    landed.parent.mkdir(parents=True)
    landed.write_text("# S-0116/D-9\n", encoding="utf-8")

    holdout = [{"name": "test_p4.py", "text": "# S-0116/D-10\ndef test_row():\n    assert True\n"}]

    result = check_held_out(GATE, _context(tmp_path, holdout))

    assert result.outcome == "pass", result.output
    assert landed.read_text(encoding="utf-8") == "# S-0116/D-9\n"
    assert not (tmp_path / "tests" / "verify" / "holdout" / "test_p4.py").exists()


# ....................... #


def test_no_recorded_files_is_skipped_not_green(tmp_path):
    """S-0116/D-10: with nothing in the event the gate judged nothing, so it
    is skipped — never a pass that would say the properties held."""

    result = check_held_out(GATE, _context(tmp_path, []))

    assert result.outcome == "skipped"


def test_a_verify_tasks_own_properties_are_left_to_verify_tests(tmp_path):
    """Review of #155: a verifier's held-out properties fail by design until
    its entry's implement candidate lands, so run on the verify task's own
    battery they would refuse every verifier. `verify-tests` judges them."""

    from torve.domain.task import Scope, Task

    context = _context(
        tmp_path, [{"name": "test_p.py", "text": "def test_p():\n    assert False\n"}]
    )
    context.task = Task(
        id="T-0001", role="verify", scope=Scope(), decisions=[], acceptance=["pytest -q"]
    )

    result = check_held_out(GATE, context)

    assert result.outcome == "skipped"
