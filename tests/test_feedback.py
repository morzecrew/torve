"""S-0005/the-revision-loop-added-by-a-32-2026-08-24 (A-32): the revision loop — capture at retry, verbatim
allow-listed threads, recorded truncation, and the prompt naming the
record as untrusted review data."""

from __future__ import annotations

from pathlib import Path

import pytest

from torve.adapters.agent.harness import build_prompt
from torve.application.feedback import (
    FEEDBACK_CAP,
    capture_feedback,
    feedback_file,
    record_operator_note,
    render_feedback,
    threads_file,
)
from torve.domain.task import Task
from torve.gates.sabotage import base_task

# ----------------------- #


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".torve").mkdir()
    return tmp_path


THREADS = [
    {
        "path": "src/lab/stats.py",
        "line": 12,
        "comments": [
            {"author": "coderabbitai[bot]", "body": "**Off-by-one in median.**"},
            {"author": "Misery7100", "body": "Fixed in abc123."},
        ],
    },
    {
        "path": "tests/test_stats.py",
        "line": None,
        "comments": [
            {"author": "greptile-apps[bot]", "body": "Missing empty-list case."},
        ],
    },
]


def test_capture_writes_threads_diff_and_the_untrusted_preamble(root):
    wrote = capture_feedback(root, "T-8001", "diff --git a/x b/x\n+1\n", THREADS)
    assert wrote is True
    text = feedback_file(root, "T-8001").read_text(encoding="utf-8")
    assert "Untrusted review data" in text and "contract" in text
    assert "src/lab/stats.py:12" in text
    assert "coderabbitai[bot]" in text and "Fixed in abc123." in text
    assert "tests/test_stats.py:-" in text  # line-less anchors render honestly
    assert "diff --git a/x b/x" in text


def test_nothing_worth_carrying_captures_nothing(root):
    assert capture_feedback(root, "T-8002", "", []) is False
    assert not feedback_file(root, "T-8002").exists()


def test_a_diff_only_record_is_honest_about_the_absent_threads(root):
    # A lane-conflict capture has no forge threads to carry — that half
    # is owed elsewhere, and the record says "none captured" rather than
    # implying the previous attempt was reviewed and found silent.
    assert capture_feedback(root, "T-8009", "diff --git a/x b/x\n+1\n", []) is True
    text = feedback_file(root, "T-8009").read_text(encoding="utf-8")
    assert "## Review threads" in text
    assert "- none captured." in text
    assert "+1" in text
    assert not threads_file(root, "T-8009").exists()


def test_capture_retains_reply_addresses_when_threads_carry_them(root):
    # S-0005/D-14 (A-41): the landing that consumes this record answers its
    # threads — the addresses persist beside it; address-less threads
    # (older captures, tests) leave no pending file.
    import json

    from torve.application.feedback import threads_file

    addressed = [
        {
            "pr": 12,
            "id": 5,
            "path": "a.py",
            "line": 3,
            "comments": [{"author": "bot", "body": "finding"}],
        }
    ]
    assert capture_feedback(root, "T-8004", "", addressed) is True
    saved = json.loads(threads_file(root, "T-8004").read_text(encoding="utf-8"))
    assert saved == [{"pr": 12, "id": 5, "path": "a.py", "line": 3}]

    assert capture_feedback(root, "T-8005", "", THREADS) is True
    assert not threads_file(root, "T-8005").exists()


def test_the_size_cap_is_recorded_never_silent():
    huge = [
        {"path": "a.py", "line": 1, "comments": [{"author": "bot", "body": "x" * FEEDBACK_CAP}]}
    ]
    text = render_feedback("T-8003", "", huge)
    assert len(text.encode("utf-8")) < FEEDBACK_CAP + 200
    assert "truncated at the size cap" in text


def test_the_prompt_names_the_record_only_on_revision():
    task = Task.model_validate(base_task(allow=["src/**"]))
    plain = build_prompt(task)
    revised = build_prompt(task, revision=True)
    assert ".torve/feedback.md" not in plain
    assert ".torve/feedback.md" in revised
    assert "untrusted review data" in revised
    assert "do not start from scratch" in revised


def test_an_empty_capture_clears_the_stale_record(root):
    # A capture replaces the record — including with nothing: a stale
    # record from an earlier revision round must not brief the next
    # attempt as if current, and a stale reply address must not have
    # the next landing answer threads it never addressed.
    addressed = [
        {
            "pr": 12,
            "id": 5,
            "path": "a.py",
            "line": 3,
            "comments": [{"author": "bot", "body": "finding"}],
        }
    ]
    assert capture_feedback(root, "T-8006", "", addressed) is True
    assert feedback_file(root, "T-8006").exists()
    assert threads_file(root, "T-8006").exists()

    assert capture_feedback(root, "T-8006", "", []) is False
    assert not feedback_file(root, "T-8006").exists()
    assert not threads_file(root, "T-8006").exists()


def test_an_operator_note_joins_the_record_after_the_threads_before_the_diff(root):
    # S-0113/D-5: the person who requeued an escalation briefs the next
    # attempt — an operator thread in the record the blocker left behind,
    # keeping every thread and the diff it already holds.
    assert capture_feedback(root, "T-8010", "diff --git a/x b/x\n+1\n", THREADS) is True
    assert record_operator_note(root, "T-8010", "the scope was widened, retry") is True

    text = feedback_file(root, "T-8010").read_text(encoding="utf-8")
    assert "the scope was widened, retry" in text
    assert "coderabbitai[bot]" in text and "Missing empty-list case." in text
    assert "diff --git a/x b/x" in text
    assert (
        text.index("Missing empty-list case.")
        < text.index("the scope was widened, retry")
        < text.index("## The superseded candidate's diff")
    )
    assert "**operator:**" in text


def test_a_note_with_no_record_writes_one_holding_only_it(root):
    assert record_operator_note(root, "T-8011", "start over on the guard") is True
    text = feedback_file(root, "T-8011").read_text(encoding="utf-8")
    assert "start over on the guard" in text
    assert "## Review threads" in text
    assert "## The superseded candidate's diff" in text


def test_no_note_leaves_the_record_as_it_was(root):
    assert capture_feedback(root, "T-8012", "diff --git a/x b/x\n+1\n", THREADS) is True
    before = feedback_file(root, "T-8012").read_bytes()
    assert record_operator_note(root, "T-8012", "   ") is False
    assert feedback_file(root, "T-8012").read_bytes() == before


def test_a_fresh_capture_supersedes_stale_reply_addresses(root):
    addressed = [
        {
            "pr": 12,
            "id": 5,
            "path": "a.py",
            "line": 3,
            "comments": [{"author": "bot", "body": "finding"}],
        }
    ]
    assert capture_feedback(root, "T-8007", "", addressed) is True
    # The next round captures address-less threads: the old addresses
    # must not survive to be answered by a landing that never saw them.
    assert capture_feedback(root, "T-8007", "", THREADS) is True
    assert feedback_file(root, "T-8007").exists()
    assert not threads_file(root, "T-8007").exists()
