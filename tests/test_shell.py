"""The output clip (base/shell.py): bounded for human logs, whole-final-line
for harness envelopes — a clip landing inside the one JSON line that carries
the verdict destroyed a review (T-0220)."""

import json

from torve.base.shell import FINAL_LINE_LIMIT, OUTPUT_LIMIT, truncate

# ----------------------- #


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
