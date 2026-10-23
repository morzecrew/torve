"""The output clip (base/shell.py): bounded for human logs, whole-final-line
for harness envelopes — a clip landing inside the one JSON line that carries
the verdict destroyed a review (T-0220)."""

import json

from torve.application.contextpack import OUTPUT_TAIL
from torve.application.telemetry import RECORDED_OUTPUT
from torve.base.shell import FINAL_LINE_LIMIT, OUTPUT_LIMIT, clip, truncate

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


def test_a_failure_report_already_in_the_tail_keeps_the_plain_tail() -> None:
    text = "noise\n" * 2_000 + "=" * 10 + " FAILURES " + "=" * 10 + "\nE   boom\n1 failed"

    assert clip(text, 200) == text[-200:]
