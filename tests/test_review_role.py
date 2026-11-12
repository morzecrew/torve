"""S-0005 phase 1: the Finding type, the review role's contract shape,
the acceptance-gate skip, and the shared evidence locator that discards
findings nothing can resolve."""

from __future__ import annotations

import string
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from torve.config.manifest import Gate
from torve.domain.attempt import Finding
from torve.domain.task import Task
from torve.gates.acceptance import check_acceptance
from torve.gates.context import GateContext
from torve.gates.evidence import citations, filter_findings, locate


def review_task(**overrides):
    fields = {
        "id": "T-0143",
        "role": "review",
        "targets": ["T-0142"],
        "decisions": [],
    }
    fields.update(overrides)
    return Task(**fields)


# ....................... #
# the contract shape


def test_a_review_task_carries_targets_and_no_acceptance():
    task = review_task()
    assert task.targets == ["T-0142"]
    assert task.acceptance == []


def test_a_review_task_refuses_acceptance_commands():
    with pytest.raises(ValidationError, match="findings"):
        review_task(acceptance=["make test"])


def test_a_review_task_requires_targets():
    with pytest.raises(ValidationError, match="targets"):
        review_task(targets=[])


def test_targets_are_refused_outside_the_review_role():
    with pytest.raises(ValidationError, match="targets"):
        Task(id="T-0001", role="implement", targets=["T-0000"], decisions=[])


def test_finding_severities_are_the_documented_vocabulary():
    finding = Finding(severity="blocker", claim="wrong", evidence="src/app.py:1")
    assert finding.severity == "blocker"
    with pytest.raises(ValidationError):
        Finding(severity="catastrophic", claim="x", evidence="y")


# ....................... #
# the acceptance skip


def test_acceptance_is_skipped_for_the_review_role(tmp_path):
    from torve.config.manifest import Manifest

    gate = Gate(name="acceptance", run="@task.acceptance", state="blocking", origin="structural")
    ctx = GateContext(
        root=tmp_path,
        manifest=Manifest(gates=[gate]),
        head_sha="",
        base=None,
        merge_base=None,
        task=review_task(),
    )
    outcome = check_acceptance(gate, ctx)
    assert outcome.outcome == "skipped"
    assert "findings" in outcome.output


# ....................... #
# the shared locator, second consumer


def test_findings_with_unlocatable_evidence_are_discarded_and_counted(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    real = Finding(severity="major", claim="off by one", evidence="src/app.py:2 — the loop bound")
    fabricated = Finding(
        severity="blocker", claim="invented", evidence="src/ghost.py:9 — no such file"
    )
    prose = Finding(severity="minor", claim="just an opinion", evidence="it feels wrong")

    kept, discarded = filter_findings([real, fabricated, prose], tmp_path)

    assert kept == [real]
    assert len(discarded) == 2
    assert any("ghost" in reason for reason in discarded)


def test_the_locator_still_serves_the_log_check(tmp_path):
    (tmp_path / "app.py").write_text("line\n", encoding="utf-8")
    assert locate("app.py:1 — the line", tmp_path) is None
    assert locate("app.py:9", tmp_path) is not None
    assert locate("`uv run pytest` 3 passed", tmp_path) is None
    assert locate("`uv run pytest`", tmp_path) is not None


# ....................... #
# S-0127/D-3: a citation or a command anywhere in the evidence

LINES = 20
#: Files a citation may name: a plain one, one whose path holds a space, and
#: one using the citation alphabet beyond word characters.
FILES = ("src/app.py", "src/legacy module.py", "src/a+b.py")
#: Prose that cites nothing: no citing colon, no backtick.
PROSE = st.lists(
    st.text(alphabet=string.ascii_lowercase + ",;()—", min_size=1, max_size=8), max_size=12
)
SPANS = st.integers(min_value=1, max_value=LINES).flatmap(
    lambda start: st.tuples(st.just(start), st.integers(min_value=start, max_value=LINES))
)
COMMANDS = st.tuples(
    st.text(alphabet=string.ascii_lowercase + " -./", min_size=1, max_size=20).filter(str.strip),
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=12),
).map(lambda pair: f"`{pair[0]}` {pair[1]}")


def _sources(root: Path) -> None:
    for name in FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("line\n" * LINES, encoding="utf-8")


def _span(path: str, start: int, end: int) -> str:
    return f"{path}:{start}" if end == start else f"{path}:{start}-{end}"


def _insert(prose: list[str], at: int, text: str) -> str:
    return " ".join([*prose[:at], text, *prose[at:]])


@given(
    prose=PROSE, at=st.integers(min_value=0, max_value=12), path=st.sampled_from(FILES), span=SPANS
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_locates_anywhere_a_resolving_citation(tmp_path, prose, at, path, span):
    """S-0127/D-3: prose holding a `path:line` or `path:start-end` that
    resolves under the root locates wherever the citation sits, and
    `citations` reads it whole, a path containing spaces included."""

    _sources(tmp_path)
    evidence = _insert(prose, at, _span(path, *span))

    assert locate(evidence, tmp_path) is None
    assert (path, *span) in citations(evidence, tmp_path)


@given(prose=PROSE, at=st.integers(min_value=0, max_value=12), command=COMMANDS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_locates_anywhere_a_command_with_output(tmp_path, prose, at, command):
    """S-0127/D-3: a backticked command followed by its output locates
    wherever it sits in the prose."""

    assert locate(_insert(prose, at, command), tmp_path) is None


@given(prose=PROSE)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_locates_anywhere_prose_citing_nothing_does_not_locate(tmp_path, prose):
    """S-0127/D-3: prose holding neither a citation nor a command is
    refused with today's message."""

    problem = locate(" ".join(prose), tmp_path)

    assert problem is not None
    assert "a sentence is a claim, not evidence" in problem


@given(
    prose=PROSE,
    at=st.integers(min_value=0, max_value=12),
    cited=st.one_of(
        SPANS.map(lambda span: _span("src/ghost.py", *span)),
        SPANS.map(lambda span: _span("src/app.py", span[0] + LINES, span[1] + LINES)),
    ),
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_locates_anywhere_a_citation_that_does_not_resolve_locates_nothing(
    tmp_path, prose, at, cited
):
    """S-0127/D-3: a citation of a missing file, or past the end of a real
    one, locates nothing wherever it sits, and the refusal names it."""

    _sources(tmp_path)
    problem = locate(_insert(prose, at, cited), tmp_path)

    assert problem is not None
    assert "does not exist" in problem or "points past end of file" in problem
