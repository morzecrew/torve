#!/usr/bin/env python3
"""End the attempt at its next tool call when an operator's note is urgent.

A PreToolUse hook on every tool. A note reaches the agent when it polls with
`torve log notes`, and an attempt told to halt ran on for minutes before it
did. This reads the run's notes before each call instead, through the channel
the engine names in the worktree, and an urgent note sent during this attempt
stops the session: claude makes no further call. The halt itself is the
engine's, read from the same note in the record once the session ends; this
only makes it come sooner.

Deliberately forgiving, like its neighbours: no channel, a broker that does
not answer, a shape it cannot read — each lets the call through. A note it
cannot read is one the agent still reads when it polls.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

# Where the engine names the run's channel: its URL, its token, and `since`,
# when this attempt began.
CHANNEL = Path(".torve") / "tmp" / "channel.json"

# Read before every tool call: a broker that hangs costs each call this long.
TIMEOUT_S = 5.0


def urgent_note(notes: list[dict[str, object]], since: str) -> dict[str, object] | None:
    """The first urgent note sent at or after *since*. Instants are the
    engine's one sortable shape, so they compare as strings."""

    return next(
        (note for note in notes if note.get("urgent") is True and str(note.get("at", "")) >= since),
        None,
    )


def main() -> int:
    try:
        channel = json.loads(CHANNEL.read_text(encoding="utf-8"))
        url = f"{channel['url']}/notes"

        if not url.startswith(("http://", "https://")):
            return 0  # the channel names an unusable URL; the verb says so

        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {channel['token']}"}
        )

        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:  # nosec B310
            note = urgent_note(json.loads(answer.read())["notes"], str(channel["since"]))

    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0

    if note is not None:
        reason = f"an urgent operator note ended this attempt: {note.get('body', '')}"
        sys.stdout.write(json.dumps({"continue": False, "stopReason": reason}) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
