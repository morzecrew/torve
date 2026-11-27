"""The output clip (base/shell.py): bounded for human logs, whole-final-line
for harness envelopes — a clip landing inside the one JSON line that carries
the verdict destroyed a review (T-0220)."""

import json
import time
from pathlib import Path

from torve.application.contextpack import OUTPUT_TAIL
from torve.application.telemetry import RECORDED_OUTPUT
from torve.base.shell import (
    FINAL_LINE_LIMIT,
    OUTPUT_LIMIT,
    clip,
    final_envelope,
    host_executor,
    truncate,
)

# ----------------------- #

TRACEBACK = "E   KeyError: 'detail_id'"


def _pytest_run() -> str:
    """An e2e run as pytest prints it: the failure report opens mid-output, and
    the coverage table and the summary follow it."""

    return "\n".join(
        [
            "uv sync noise\n" * 300,
            "." * 135 + "FF",
            "=" * 35 + " FAILURES " + "=" * 35,
            "_____ test_rows_are_read_off_the_filter _____",
            "tests/e2e/test_requirements.py:402: in _article",
            '    return row["detail_id"]',
            TRACEBACK,
            "src/erp/domain/models/stock.py    46    0    10    5  91.1%   31->34\n" * 400,
            "=" * 28 + " short test summary info " + "=" * 28,
            "FAILED tests/e2e/test_requirements.py::test_rows_are_read_off_the_filter",
            "=" * 20 + " 1 failed, 135 passed in 54.92s " + "=" * 20,
        ]
    )


def test_short_output_untouched() -> None:
    assert truncate("hello") == "hello"


def test_long_log_clipped_bounded() -> None:
    clipped = truncate("line\n" * 10_000)

    assert "… truncated …" in clipped
    assert len(clipped) <= OUTPUT_LIMIT + 100


def test_huge_final_json_line_survives_whole() -> None:
    envelope = ("chatter\n" * 2_000) + '{"result": "' + "x" * 50_000 + '", "findings": []}'
    clipped = truncate(envelope)

    assert clipped.endswith('"findings": []}')
    assert "x" * 50_000 in clipped


def test_pathological_final_line_stays_bounded() -> None:
    monster = "y" * (FINAL_LINE_LIMIT + 1)
    clipped = truncate("head\n" + monster)

    assert len(clipped) <= OUTPUT_LIMIT + 100


def test_oversize_json_verdict_survives_whole() -> None:
    """T-0283: a review envelope that clears FINAL_LINE_LIMIT is still the
    verdict. The bound was standing in for shape, and the reviewer's own
    tool inputs echoed back cleared it — the clip cut the JSON and a
    correct, paid review was recorded as unparseable."""

    verdict = '{"chatter": "%s", "findings": []}' % ("z" * (FINAL_LINE_LIMIT * 2))
    clipped = truncate("noise\n" * 2_000 + verdict)

    assert (
        json.loads(clipped[clipped.index("{", clipped.index("… truncated …")) :])["findings"] == []
    )


def test_oversize_non_json_final_line_stays_bounded() -> None:
    """The bound still holds for a line that is merely long — a minified
    bundle catted by a gate is not a verdict."""

    monster = "{" + "y" * (FINAL_LINE_LIMIT + 1)
    clipped = truncate("head\n" + monster)

    assert len(clipped) <= OUTPUT_LIMIT + 100


def test_the_final_line_rescue_survives_the_streams_trailing_newline() -> None:
    """T-0103: a harness stream ends with a newline. Read after it, the final
    line was empty, and a verdict longer than the tail was clipped through —
    two paid, readable reviews recorded as unparseable in one night."""

    verdict = '{"chatter": "%s", "findings": []}' % ("z" * OUTPUT_LIMIT)
    clipped = truncate("noise\n" * 2_000 + verdict + "\n")

    assert (
        json.loads(clipped[clipped.index("{", clipped.index("… truncated …")) :])["findings"] == []
    )


def _notices() -> str:
    """What claude prints after its result envelope when a background task
    the session started ends late: short `system` lines, none of them the answer."""

    return "".join(
        json.dumps({"type": "system", "subtype": subtype, "session_id": "s"}) + "\n"
        for subtype in ("background_tasks_changed", "task_updated", "task_notification")
    )


def test_an_envelope_followed_by_notices_survives_whole() -> None:
    """A compile reviewer's 14 kB envelope was followed by three notices; the
    clip kept the tail, cut through the envelope, and the paid verdict read as
    unparseable — a second ask, and the first ask's cost never recorded."""

    envelope = json.dumps({"type": "result", "result": "z" * OUTPUT_LIMIT, "findings": []})
    clipped = truncate("noise\n" * 2_000 + envelope + "\n" + _notices())

    assert envelope in clipped
    assert clipped.endswith(_notices().rstrip("\n"))


def test_a_long_document_before_a_long_tail_keeps_the_ordinary_clip() -> None:
    """Only the line the clip lands in is rescued: a JSON line early in a long
    log is the log's, and the clip stays bounded."""

    clipped = truncate(json.dumps({"x": "y" * 50_000}) + "\n" + "log line\n" * 2_000)

    assert len(clipped) <= OUTPUT_LIMIT + 100

    # A tail that starts exactly where a line starts, or on the newline that
    # ends one, cuts nothing: the JSON line before it is not restored.
    room = OUTPUT_LIMIT - 2000
    document = json.dumps({"x": "y" * 50_000})

    for text in (
        document + "\n" + "log\n" * (room // 4 - 1) + "end!",
        document + "\n" + "log\n" * (room // 4 - 1) + "end",
    ):
        assert len(text) - len(document) in (room, room + 1)
        assert len(truncate(text)) <= OUTPUT_LIMIT + 100


def test_the_final_envelope_is_the_result_not_the_notices_after_it() -> None:
    envelope = {"type": "result", "total_cost_usd": 1.28}

    assert final_envelope("chatter\n" + json.dumps(envelope) + "\n" + _notices()) == envelope
    # A harness that prints no result envelope is read off its last object.
    assert final_envelope('{"a": 1}\n{"b": 2}\nbye') == {"b": 2}
    assert final_envelope("no json here") is None


def test_a_failure_report_survives_every_clip_down_to_the_retry() -> None:
    """erp T-0007: the gate's clip, the record's and the retry pack's each kept
    the tail, which is the coverage table and the summary; the retry saw which
    tests failed and not why, and spent its attempt guessing."""

    gate = truncate(_pytest_run())
    record = clip(gate, RECORDED_OUTPUT)
    retry = clip(record, OUTPUT_TAIL)

    for kept, limit in (
        (gate, OUTPUT_LIMIT + 100),
        (record, RECORDED_OUTPUT),
        (retry, OUTPUT_TAIL),
    ):
        assert TRACEBACK in kept
        assert kept.endswith("in 54.92s " + "=" * 20)
        assert len(kept) <= limit


def test_output_without_a_failure_report_keeps_its_tail() -> None:
    text = "build step\n" * 2_000 + "error: recipe `e2e` failed"

    assert clip(text, RECORDED_OUTPUT) == text[-RECORDED_OUTPUT:]
    assert clip("short", RECORDED_OUTPUT) == "short"


def test_a_failure_report_opening_in_the_head_keeps_its_traceback() -> None:
    """A quiet run's report opens within the head, and a long traceback runs
    past it into what the plain clip drops."""

    text = "\n".join(
        [
            "=" * 10 + " FAILURES " + "=" * 10,
            "frame\n" * 500,
            TRACEBACK,
            "src/erp/x.py    46    0    10    5  91.1%\n" * 400,
            "1 failed",
        ]
    )

    assert TRACEBACK in truncate(text)


def test_a_bound_too_small_for_the_elision_keeps_a_bounded_tail() -> None:
    text = _pytest_run()

    for limit in (1, 10, 40):
        assert clip(text, limit) == text[-limit:]


def test_a_failure_report_already_in_the_tail_keeps_the_plain_tail() -> None:
    text = "noise\n" * 2_000 + "=" * 10 + " FAILURES " + "=" * 10 + "\nE   boom\n1 failed"

    assert clip(text, 200) == text[-200:]


def _still_running(pid: int) -> bool:
    """Whether *pid* is a live process — a zombie has ended and only waits to be
    reaped, so it counts as gone."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()

    # A process reaped between the open and the read leaves the read ESRCH.
    except (FileNotFoundError, ProcessLookupError):
        return False

    # `pid (comm) state ...`: comm may hold spaces and parentheses, so the
    # state is the first field after the last ')'.
    return stat[stat.rindex(")") + 2] != "Z"


def test_a_timed_out_command_leaves_no_live_process_group(tmp_path: Path) -> None:
    """S-0123/D-6: a line that times out takes every process it started with
    it. The shell leads a session of its own, and the timeout kills the whole
    group — a background sleep does not outlive the command that spawned it."""

    pid_file = tmp_path / "sleep.pid"
    code, output = host_executor(tmp_path)(
        f"sleep 30 & echo $! > {pid_file}; wait",
        0.5,
    )

    assert code is None
    assert output == "timed out after 0s"

    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10

    while _still_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not _still_running(pid)


def test_a_descendant_that_left_the_session_does_not_hold_the_timeout(tmp_path: Path) -> None:
    """S-0123/D-6: the timeout kills the group and reaps the shell; a process
    that escaped the session with the pipes still open does not keep the
    executor waiting for it."""

    started = time.monotonic()
    code, _ = host_executor(tmp_path)("setsid sleep 6 & wait", 0.5)

    assert code is None
    assert time.monotonic() - started < 3
