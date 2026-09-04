"""The sandbox's end of the live channel (RFC 0045 §5.2).

An agent's process cannot reach the store and must not be able to: with a
store credential inside a sandbox the authority table becomes advice, since
anything holding the connection can write anything (D-45.1). What it gets
instead is one authenticated route on the broker the run already has, and a
file naming it — the same trick the log's pin uses, for the same reason
(nothing inside the sandbox can derive either).

What travels is content. Who is writing, which partition and which task are
the broker's to stamp from the run-scoped token, so this module has no way
to say them and no way to get them wrong (D-45.2).

Without a channel every verb here reports its absence rather than failing:
a run with no broker writes its worktree file exactly as it did before
(D-45.6), and the channel is an addition, never a dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from torve.config import layout

if TYPE_CHECKING:
    from pathlib import Path

# ----------------------- #

# Engine scratch, generated and never committed (RFC 0013 §5) — beside the
# log's pin, which the sandbox reads for the same reason.
CHANNEL_FILE = "channel.json"
TIMEOUT_S = 30.0


# ....................... #


class ChannelRefused(Exception):
    """The broker refused the record. Carries what it said: an authority
    refusal and a malformed payload are the caller's to read, not this
    module's to interpret."""


# ....................... #


@dataclass(frozen=True)
class Channel:
    url: str
    token: str

    # ....................... #

    def _call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )

        if not self.url.startswith(("http://", "https://")):
            # The URL comes from a file inside the worktree, so the scheme is
            # checked rather than assumed: `file:` here would turn a channel
            # post into a local read.
            raise ChannelRefused(f"the channel names an unusable URL: {self.url!r}")

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:  # nosec B310
                return dict(json.loads(answer.read() or b"{}"))

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")

            try:
                error = dict(json.loads(detail)).get("error", {})
                message = str(error.get("message") or error.get("cause") or detail)

            except ValueError:
                message = detail or str(exc)

            raise ChannelRefused(message) from exc

        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ChannelRefused(f"the channel is unreachable: {exc}") from exc

    # ....................... #

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self._call("/records", {"kind": kind, "payload": payload})

    # ....................... #

    def notes(self) -> list[dict[str, Any]]:
        answer = self._call("/notes")

        return [dict(one) for one in answer.get("notes", [])]

    # ....................... #

    def records(self) -> list[dict[str, Any]]:
        answer = self._call("/records")

        return [dict(one) for one in answer.get("records", [])]


# ....................... #


def seed(workspace: Path, url: str, token: str) -> Path | None:
    """Write the channel where the sandbox can find it, before the agent
    runs. No URL means no channel and no file: absence is how the verbs
    learn there is nothing to post to."""

    if not url:
        return None

    path = workspace / layout.TORVE_DIR / "tmp" / CHANNEL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "token": token}, indent=2) + "\n")

    return path


# ....................... #


def open_channel(root: Path) -> Channel | None:
    """The run's channel, or None when this run has none."""

    path = root / layout.TORVE_DIR / "tmp" / CHANNEL_FILE

    if not path.is_file():
        return None

    try:
        loaded = dict(json.loads(path.read_text()))

    except (ValueError, OSError):
        return None

    url, token = str(loaded.get("url") or ""), str(loaded.get("token") or "")

    return Channel(url=url, token=token) if url and token else None
