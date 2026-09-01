"""The output clip (base/shell.py): bounded for human logs, whole-final-line
for harness envelopes — a clip landing inside the one JSON line that carries
the verdict destroyed a review (T-0220)."""

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
