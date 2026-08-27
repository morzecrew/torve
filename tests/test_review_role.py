"""RFC 0005 phase 1: the Finding type, the review role's contract shape,
the acceptance-gate skip, and the shared evidence locator that discards
findings nothing can resolve."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.config.manifest import Gate
from torve.domain.attempt import Finding
from torve.domain.task import Task
from torve.gates.acceptance import check_acceptance
from torve.gates.context import GateContext
from torve.gates.evidence import filter_findings, locate


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
