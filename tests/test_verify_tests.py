"""`verify-tests` — the three ways a verifier's tests prove nothing
(S-0116/D-11).

A verify candidate adds tests and no implementation, so a test that already
passes on that candidate has proved nothing about the work still to come; an
entry row no test cites is a row nothing proves; a file that does not collect
proves nothing at all. Each conviction has to name what it convicted, or the
next attempt cannot act on it.
"""

from __future__ import annotations

from conftest import context_for

from torve.domain.events import HoldoutFile
from torve.gates.sabotage import (
    VERIFY_BROKEN_TEST,
    VERIFY_FAILING_TEST,
    VERIFY_PASSING_TEST,
    VERIFY_TESTS_MANIFEST,
    VERIFY_UNCITED_TEST,
    base_task,
    verify_task,
)
from torve.gates.verify_tests import check_verify_tests

FILE = "tests/verify/test_s0001_p1.py"


def _gate(context):
    return next(one for one in context.manifest.gates if one.name == "verify-tests")


# ....................... #


def _run(repo, text, *, holdout=()):
    repo.seed(VERIFY_TESTS_MANIFEST)
    repo.task(verify_task(FILE), None)
    repo.write(FILE, text)
    repo.commit("the verifier's candidate")

    context = context_for(repo, base="main")
    context.holdout = [HoldoutFile(**one) for one in holdout]

    return context, check_verify_tests(_gate(context), context)


# ....................... #


def test_a_test_that_already_passes_on_the_candidate_convicts(repo):
    """S-0116/D-11: the verifier's job is to write a test the candidate fails;
    one it passes proves nothing, and the gate names it."""

    _context, result = _run(repo, VERIFY_PASSING_TEST)

    assert result.outcome == "fail"
    assert "tests/verify/test_s0001_p1.py::test_row" in result.output
    assert "pass" in result.output


# ....................... #


def test_a_row_no_test_cites_convicts(repo):
    """S-0116/D-11: an entry row nothing cites is a claim left unproved, so
    the gate names the row."""

    _context, result = _run(repo, VERIFY_UNCITED_TEST)

    assert result.outcome == "fail"
    assert "S-0001/D-1" in result.output


# ....................... #


def test_a_file_that_does_not_collect_convicts(repo):
    _context, result = _run(repo, VERIFY_BROKEN_TEST)

    assert result.outcome == "fail"
    assert "does not collect" in result.output


# ....................... #


def test_a_failing_test_citing_its_row_passes(repo):
    """The green twin: the work a verifier is asked for."""

    _context, result = _run(repo, VERIFY_FAILING_TEST)

    assert result.outcome == "pass", result.output


# ....................... #


def test_a_held_out_test_that_passes_on_the_candidate_convicts(repo):
    """S-0116/D-11: the held-out properties run here too, for this run alone —
    one that the candidate satisfies convicts like any other passing test, and
    the files are gone once the gate ends."""

    _context, result = _run(
        repo,
        VERIFY_FAILING_TEST,
        holdout=[
            {"name": "test_hold.py", "text": "# S-0001/D-1\ndef test_row():\n    assert True\n"}
        ],
    )

    assert result.outcome == "fail"
    assert "holdout/test_hold.py::test_row" in result.output
    assert not (repo.root / "tests" / "verify" / "holdout").exists()


# ....................... #


def test_a_task_that_is_not_a_verifier_is_left_alone(repo):
    """The gate is the verifier's; another role's failing tests are not
    evidence of a verifier proving nothing (S-0116/D-11)."""

    repo.seed(VERIFY_TESTS_MANIFEST)
    repo.task(base_task(allow=["tests/**"]), None)
    repo.write(FILE, VERIFY_PASSING_TEST)
    repo.commit("a candidate of another role")

    context = context_for(repo, base="main")

    assert check_verify_tests(_gate(context), context).outcome == "skipped"


def test_a_test_that_errors_in_setup_is_not_read_as_passing(repo):
    """Review of #155: a setup error is a test that did not pass. Read off the
    `FAILED` lines alone, it was counted as passing on the candidate and
    convicted a verifier whose test could not pass."""

    erroring = (
        "import pytest\n\n\n@pytest.fixture\ndef surface():\n"
        "    raise RuntimeError('not yet implemented')\n\n\n"
        "# S-0001/D-1\ndef test_row(surface):\n    assert surface\n"
    )

    _context, result = _run(repo, erroring)

    assert result.outcome == "pass", result.output
