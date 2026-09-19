"""Threads folded into findings (S-0084/D-3, S-0084/D-4): the group is what a
thread anchors to and never who wrote it, and the rule errs toward merging
with which way it erred readable from the finding it produced."""

from __future__ import annotations

from torve.application.ports import ReviewThread, ThreadComment
from torve.application.threads import WINDOW, Finding, group_findings

# ----------------------- #


def thread(id: str, path: str, line: int | None, author: str = "coderabbitai[bot]") -> ReviewThread:
    return ReviewThread(
        id=id, path=path, line=line, comments=(ThreadComment(author=author, body=f"{id} says so"),)
    )


def anchors(findings: list[Finding]) -> list[tuple[str, int | None, int | None, tuple[str, ...]]]:
    return [(f.path, f.line, f.end_line, f.ids) for f in findings]


# ....................... #


def test_three_reviewers_on_one_line_are_one_finding() -> None:
    # S-0084/D-3: three bots on one null check cost one task, and the answering
    # half names one commit to all three threads.
    findings = group_findings(
        [
            thread("t1", "a.py", 12, "coderabbitai[bot]"),
            thread("t2", "a.py", 12, "sonarcloud[bot]"),
            thread("t3", "a.py", 12, "a-person"),
        ]
    )

    assert anchors(findings) == [("a.py", 12, 12, ("t1", "t2", "t3"))]


def test_the_same_file_far_apart_is_two_findings() -> None:
    findings = group_findings([thread("t1", "a.py", 10), thread("t2", "a.py", 10 + WINDOW + 1)])

    assert anchors(findings) == [
        ("a.py", 10, 10, ("t1",)),
        ("a.py", 10 + WINDOW + 1, 10 + WINDOW + 1, ("t2",)),
    ]


def test_lines_inside_the_window_merge_and_the_span_says_so() -> None:
    # S-0084/D-4: the cheap error is the merging one, and the span beside the
    # threads is how the record says which way it went.
    findings = group_findings([thread("t1", "a.py", 10), thread("t2", "a.py", 10 + WINDOW)])

    assert anchors(findings) == [("a.py", 10, 10 + WINDOW, ("t1", "t2"))]

    # A chain of near threads is one finding even when its ends are further
    # apart than the window: each link merges.
    chain = group_findings([thread(f"t{n}", "a.py", 10 + n * WINDOW) for n in range(4)])

    assert anchors(chain) == [("a.py", 10, 10 + 3 * WINDOW, ("t0", "t1", "t2", "t3"))]


def test_two_files_never_merge() -> None:
    findings = group_findings([thread("t2", "b.py", 3), thread("t1", "a.py", 3)])

    # Ordered by path, so the record is stable across passes.
    assert anchors(findings) == [("a.py", 3, 3, ("t1",)), ("b.py", 3, 3, ("t2",))]


def test_a_file_level_thread_takes_its_whole_file() -> None:
    # A claim about the file is a claim about every line of it; merging is the
    # error this rule is allowed to make (S-0084/D-4).
    findings = group_findings(
        [thread("t1", "a.py", 5), thread("whole", "a.py", None), thread("t2", "a.py", 90)]
    )

    assert anchors(findings) == [("a.py", 5, 90, ("whole", "t1", "t2"))]


def test_a_file_level_thread_alone_anchors_to_no_line() -> None:
    findings = group_findings([thread("whole", "a.py", None)])

    assert anchors(findings) == [("a.py", None, None, ("whole",))]


def test_no_threads_are_no_findings() -> None:
    assert group_findings([]) == []
