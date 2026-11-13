"""Threads folded into findings (S-0084/D-3, S-0084/D-4): the group is what a
thread anchors to and never who wrote it, and the rule errs toward merging
with which way it erred readable from the finding it produced."""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from torve.application.ports import ReviewThread, ThreadComment
from torve.application.threads import (
    WINDOW,
    Finding,
    group_findings,
    names_spot,
    rejections,
)

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


# ....................... #
# S-0126/D-3: the reply and the carry read rejections by one rule.


def test_rejections_keep_one_attempts_contradictions_once() -> None:
    """S-0126/D-3: a rejection stands for the attempt that recorded it, an
    entry with no attempt counts for every attempt, and recording the same
    rejection again posts it once (T-0913's attempt 3 recorded one three
    times)."""

    entries = [
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": 3,
            "claim": "the cap is not an oversized round",
            "evidence": "src/a.py:10 — the cap bounds findings, not files",
            "at": "2026-11-08T09:00:00Z",
        },
        # The same rejection, recorded again on the same attempt.
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": 3,
            "claim": "the cap is not an oversized round",
            "evidence": "src/a.py:10 — the cap bounds findings, not files",
            "at": "2026-11-08T09:02:00Z",
        },
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": 1,
            "claim": "an earlier attempt's rejection",
            "evidence": "src/a.py:11 — this was fixed on the retry",
        },
        {
            "decision": "S-0096/D-1",
            "grade": "LOCKED",
            "kind": "contradicted",
            "action": "halted",
            "attempt": 3,
            "claim": "a halt on a LOCKED row is not a rejection",
            "evidence": "src/a.py:12 — the owner's to reopen",
        },
        {
            "decision": "S-0126/D-3",
            "kind": "contradicted",
            "attempt": 3,
            "claim": "a departure is not an unlisted rejection",
            "evidence": "src/a.py:13 — a cited row",
        },
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "claim": "an entry with no attempt",
            "evidence": "src/a.py:14 — written before the pin",
        },
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": 3,
            "claim": "a different rejection",
            "evidence": "src/a.py:15 — another claim",
        },
    ]

    kept = rejections(entries, 3)

    assert [entry["claim"] for entry in kept] == [
        "the cap is not an oversized round",
        "an entry with no attempt",
        "a different rejection",
    ]
    assert [entry["claim"] for entry in rejections(entries, 1)] == [
        "an earlier attempt's rejection",
        "an entry with no attempt",
    ]


def test_rejections_keep_a_declined_claim_beside_a_contradicted_one() -> None:
    """S-0127/D-12: an attempt's `halted` entries that cite no LOCKED row are
    claims it declined and are kept beside its contradicted ones, once each
    and for their own attempt; a halt on a LOCKED row is the owner's
    (`locked_conflict`, S-0096/D-1) and is no rejection."""

    entries = [
        {
            "decision": "unlisted",
            "grade": "UNLISTED",
            "kind": "contradicted",
            "action": "departed",
            "attempt": 2,
            "claim": "app.py:12 is already guarded",
            "evidence": "src/app.py:10 — the guard",
        },
        {
            "decision": "unlisted",
            "grade": "UNLISTED",
            "kind": "blocked",
            "action": "halted",
            "attempt": 2,
            "claim": "schema.py:3 asks for a migration outside this round",
            "evidence": "src/schema.py:1 — another phase's",
        },
        # `torve abort` citing two rows writes one entry per row.
        {
            "decision": "S-0001/D-2",
            "grade": "ASSUMED",
            "kind": "blocked",
            "action": "halted",
            "attempt": 2,
            "claim": "schema.py:3 asks for a migration outside this round",
            "evidence": "src/schema.py:1 — another phase's",
        },
        {
            "decision": "S-0001/D-1",
            "grade": "LOCKED",
            "kind": "contradicted",
            "action": "halted",
            "attempt": 2,
            "claim": "api.py:7 would break the frozen interface",
            "evidence": "src/api.py:7 — the LOCKED row",
        },
        {
            "decision": "unlisted",
            "grade": "UNLISTED",
            "kind": "blocked",
            "action": "halted",
            "attempt": 1,
            "claim": "an earlier attempt's halt",
            "evidence": "src/app.py:1 — the first attempt",
        },
    ]

    assert [entry["claim"] for entry in rejections(entries, 2)] == [
        "app.py:12 is already guarded",
        "schema.py:3 asks for a migration outside this round",
    ]
    assert [entry["claim"] for entry in rejections(entries, 1)] == ["an earlier attempt's halt"]


def test_rejections_treat_a_persisted_non_attempt_as_unpinned() -> None:
    """S-0126/D-3: an invalid `attempt` is read like an entry written before
    the pin — it answers every attempt rather than being dropped, so a legacy
    log is never reported as fixed."""

    entries = [
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": "unknown",
            "claim": "the legacy rejection",
            "evidence": "src/a.py:10 — still not applied",
        },
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": 1,
            "claim": "an earlier attempt's rejection",
            "evidence": "src/a.py:11 — fixed on the retry",
        },
    ]

    assert [entry["claim"] for entry in rejections(entries, 2)] == ["the legacy rejection"]
    assert [entry["claim"] for entry in rejections(entries, 1)] == [
        "the legacy rejection",
        "an earlier attempt's rejection",
    ]


def test_rejections_treat_an_infinite_attempt_as_unpinned() -> None:
    """S-0126/D-3: YAML reads `.inf` as a float infinity and `int` refuses it
    with OverflowError; the hand-edited value answers every attempt like one
    written before the pin instead of breaking the reply."""

    entries = [
        {
            "decision": "unlisted",
            "kind": "contradicted",
            "attempt": float("inf"),
            "claim": "the infinite rejection",
            "evidence": "src/a.py:10 — still not applied",
        }
    ]

    assert [entry["claim"] for entry in rejections(entries, 1)] == ["the infinite rejection"]
    assert [entry["claim"] for entry in rejections(entries, 2)] == ["the infinite rejection"]


def test_names_spot_matches_a_full_path_or_every_spot_sharing_a_basename() -> None:
    """S-0126/D-3, as S-0126/A-1 amends it: a claim names a spot by its full
    `path:line`, or by the file's name and the line, so a `runconfig.py:1350`
    claim names both findings that share it while a full path names its own."""

    shared = ("b/runconfig.py", 1350)
    unique = ("c/only.py", 12)

    assert names_spot("a/runconfig.py:1350 — the cap", ("a/runconfig.py", 1350))
    assert not names_spot("a/runconfig.py:1350 — the cap", shared)
    assert names_spot("runconfig.py:1350 — the cap", ("a/runconfig.py", 1350))
    assert names_spot("runconfig.py:1350 — the cap", shared)
    assert not names_spot("runconfig.py:1350 — the cap", unique)
    assert not names_spot("runconfig.py:13500 — the cap", shared)
    assert names_spot("only.py:12 — the check", unique)
    assert names_spot("c/only.py:12 — the check", unique)


_segment = st.text(alphabet="abcdefgh_", min_size=1, max_size=6)


@given(
    first=_segment,
    second=_segment,
    name=_segment,
    line=st.integers(min_value=1, max_value=99999),
)
def test_names_spot_a_basename_names_every_file_sharing_it(
    first: str, second: str, name: str, line: int
) -> None:
    """S-0126/D-3, as S-0126/A-1 amends it: for any two files with one name,
    the bare `name:line` names both and each full path names only itself."""

    assume(first != second)
    a, b = (f"{first}/{name}.py", line), (f"{second}/{name}.py", line)

    assert names_spot(f"{name}.py:{line} — the claim", a)
    assert names_spot(f"{name}.py:{line} — the claim", b)
    assert names_spot(f"{a[0]}:{line} — the claim", a)
    assert not names_spot(f"{a[0]}:{line} — the claim", b)
    assert not names_spot(f"{name}.py:{line + 1} — the claim", a)
