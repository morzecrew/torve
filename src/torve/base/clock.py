"""The one instant the engine writes (S-0058/D-7): `YYYY-MM-DDTHH:MM:SSZ`,
UTC, seconds — what amendments, landings, log entries, telemetry, run
state and their display all carry, so a timeline sorts on one string.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

# ----------------------- #

INSTANT = "%Y-%m-%dT%H:%M:%SZ"
INSTANT_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INSTANT_PATTERN = INSTANT_SHAPE.pattern


def stamp(now: datetime | None = None) -> str:
    """Now, or the moment given, as the instant."""

    moment = now.astimezone(UTC) if now is not None else datetime.now(UTC)

    return moment.strftime(INSTANT)


def is_instant(value: str) -> bool:
    return bool(INSTANT_SHAPE.match(value))


def from_day(day: date | str) -> str:
    """A date as the instant of its midnight, UTC — how a day-only field
    converts once, the second unknown and said so."""

    text = day.isoformat() if isinstance(day, date) else str(day)

    return text if is_instant(text) else f"{text[:10]}T00:00:00Z"


def parse(text: str) -> datetime:
    """An instant read back, UTC — the seconds form, or the microsecond
    form the run state wrote before the one instant (S-0058/D-7)."""

    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def for_name(instant: str) -> str:
    """The instant as a file name carries it: digits and the letters only."""

    return instant.replace("-", "").replace(":", "")
