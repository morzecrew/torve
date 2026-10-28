"""S-0115/D-5: after the whole-document battery, one joined table answers, row
by row, what proves it and what argues against it — the evidence verdict and
its tests, the row's own check, the mutation survivors under its paths and the
review findings recorded on the document's tasks. The table is keyed by the tip
it was measured on (D-6), so a body never carries another tip's reading."""

from __future__ import annotations

from pathlib import Path

from torve.application.evidence_matrix import RowRecord, join, read_table, record_table
from torve.domain.attempt import Finding, GateResult
from torve.domain.task import InheritedDecision
from torve.domain.vocabulary import GateOutcome


def row(identifier: str, paths: list[str]) -> InheritedDecision:
    return InheritedDecision(id=identifier, grade="ASSUMED", text="a rule", paths=paths)


def result(name: str, outcome: GateOutcome, output: str = "") -> GateResult:
    return GateResult(name=name, outcome=outcome, state="shadow", output=output)


def evidence_output(*lines: str) -> str:
    return "\n".join(lines)


def test_the_join_reads_evidence_checks_survivors_and_findings_per_row():
    rows = [
        row("S-0115/D-5", ["src/torve/application/lane.py"]),
        row("S-0115/D-6", ["src/torve/cli/merge.py"]),
        row("S-0115/D-7", ["src/torve/application/forge.py"]),
    ]
    results = [
        result(
            "evidence",
            "fail",
            evidence_output(
                "row S-0115/D-5: pass — tests/test_lane.py::test_the_join",
                "row S-0115/D-6: fail — no test cites the row",
                "row S-0115/D-7: check — judged by its decision gate",
            ),
        ),
        result("decision:S-0115/D-7", "pass"),
        result(
            "mutation",
            "fail",
            "mutation score 0.5: 1 survived, 1 killed: 50%\n"
            "src/torve/application/lane.py:120 — replaced the tip\n"
            "src/torve/cli/merge.py:220 — replaced the body",
        ),
    ]
    findings = [
        Finding(
            severity="major",
            claim="the join drops a row",
            evidence="src/torve/application/lane.py:90 — the join",
        ),
        Finding(severity="minor", claim="unrelated", evidence="src/other.py:3 — elsewhere"),
    ]

    table = join(rows, results, findings)

    assert table is not None
    by_row = {record.row: record for record in table}
    assert by_row["S-0115/D-5"] == RowRecord(
        row="S-0115/D-5",
        state="pass",
        tests=("tests/test_lane.py::test_the_join",),
        survivors=("src/torve/application/lane.py:120 — replaced the tip",),
        findings=("the join drops a row",),
    )
    assert by_row["S-0115/D-6"].state == "fail"
    assert by_row["S-0115/D-6"].tests == ()
    assert by_row["S-0115/D-6"].survivors == ("src/torve/cli/merge.py:220 — replaced the body",)
    assert by_row["S-0115/D-7"].check == "pass"
    # A finding whose evidence path falls under no row's paths is not joined.
    assert by_row["S-0115/D-6"].findings == ()


def test_a_battery_with_no_evidence_result_has_no_table():
    assert join([row("S-0115/D-5", ["src/torve/application/lane.py"])], []) is None
    skipped = [result("evidence", "skipped", "a verify attempt's tests are red by design")]
    assert join([row("S-0115/D-5", ["src/torve/application/lane.py"])], skipped) is None


def test_the_table_is_keyed_by_the_tip_it_was_measured_on(tmp_path: Path):
    measured = [RowRecord(row="S-0115/D-5", state="pass", tests=("tests/test_lane.py::test_one",))]

    record_table(tmp_path, "a" * 40, "S-0115", measured)

    assert read_table(tmp_path, "a" * 40) == measured
    assert read_table(tmp_path, "b" * 40) is None
    assert read_table(tmp_path, "") is None


def test_the_publisher_joins_the_boards_findings_per_row():
    """S-0115/D-5, D-6: the findings column comes off the board's review
    records at compose time, matched to each row's paths, and only the
    document's own tasks count; the lane's file carries none (S-0099/D-6)."""

    import uuid
    from datetime import UTC, datetime

    from torve.application.evidence_matrix import board_findings, with_findings
    from torve.application.review import review_items
    from torve.domain.events import ActorKind, EventKind, EventRecord, SubjectType

    now = datetime.now(UTC)

    def review(target: str, claim: str, evidence: str) -> EventRecord:
        return EventRecord(
            id=uuid.uuid4().hex,
            rev=1,
            created_at=now,
            last_update_at=now,
            kind=EventKind.REVIEW_RECORDED,
            partition="torve/torve",
            subject_type=SubjectType.TASK,
            subject_id=target,
            actor_kind=ActorKind.WORKER,
            actor_id="worker-1",
            payload={
                "review_id": f"{target}-review",
                "trigger": "task_gated",
                "findings": 1,
                "blockers": 0,
                "items": review_items([Finding(severity="minor", claim=claim, evidence=evidence)]),
            },
        )

    records = [
        review("T-0001", "the join drops a row", "src/torve/application/lane.py:9 — dropped"),
        review("T-0009", "another document's finding", "src/torve/application/lane.py:4 — x"),
    ]
    findings = board_findings(records, ["T-0001"])

    assert [finding.claim for finding in findings] == ["the join drops a row"]

    measured = [
        RowRecord(row="S-0115/D-5", state="pass"),
        RowRecord(row="S-0115/D-7", state="pass"),
    ]
    rows = [
        row("S-0115/D-5", ["src/torve/application/lane.py"]),
        row("S-0115/D-7", ["src/torve/application/forge.py"]),
    ]
    joined = with_findings(measured, rows, findings)

    assert joined[0].findings == ("the join drops a row",)
    assert joined[1].findings == ()
