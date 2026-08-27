"""RFC 0005 §4a (A-32): the revision loop — capture at retry, verbatim
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


def test_capture_retains_reply_addresses_when_threads_carry_them(root):
    # D-5.14 (A-41): the landing that consumes this record answers its
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
