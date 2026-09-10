"""The one instant (S-0058/D-7): `torve.base.clock` owns the format, and the
sweep that put every writer through it (T-0320) stays swept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from torve.base.clock import INSTANT, for_name, from_day, is_instant, parse, stamp

# ----------------------- #

SRC = Path(__file__).resolve().parents[1] / "src" / "torve"
OWNER = SRC / "base" / "clock.py"


def test_no_module_but_the_clock_spells_the_instant():
    """The format string lives in one file. A second spelling is how the
    engine ended up with 27 time formats before S-0058/D-7, and every one of
    them was correct on the day it was written."""

    spellings = [
        f"{path.relative_to(SRC)}:{number}"
        for path in sorted(SRC.rglob("*.py"))
        if path != OWNER and "_web" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if INSTANT in line
    ]

    assert not spellings, (
        "these write or read the instant by hand instead of through "
        f"torve.base.clock: {', '.join(spellings)}"
    )


def test_stamp_reads_back_as_the_moment_it_was_given():
    moment = datetime(2026, 9, 10, 14, 30, 45, tzinfo=UTC)

    assert stamp(moment) == "2026-09-10T14:30:45Z"
    assert parse(stamp(moment)) == moment
    assert is_instant(stamp(moment))


def test_parse_reads_the_microsecond_form_the_run_state_wrote():
    """The lenient half of the reader: run state predating S-0058/D-7 carries
    microseconds, and a projection that refused it would lose the row."""

    assert parse("2026-09-10T14:30:45.123456Z") == datetime(
        2026, 9, 10, 14, 30, 45, 123456, tzinfo=UTC
    )

    with pytest.raises(ValueError):
        parse("not an instant")


def test_a_day_becomes_its_midnight_and_a_name_drops_the_punctuation():
    assert from_day("2026-09-10") == "2026-09-10T00:00:00Z"
    assert from_day("2026-09-10T14:30:45Z") == "2026-09-10T14:30:45Z"
    assert for_name("2026-09-10T14:30:45Z") == "20260910T143045Z"
