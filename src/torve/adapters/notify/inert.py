"""The explicit non-destination (S-0051 S-0051/D-4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torve.application.ports import Notification

# ----------------------- #


class NoNotifier:
    """Delivers nothing, and is chosen rather than defaulted into.

    It returns an empty receipt rather than raising, so a pass with it
    wired records a delivery that went nowhere — which is the honest
    account of what happened, and keeps the queue draining. The
    alternative, refusing, would leave every escalation in the queue
    forever on a repository that has deliberately configured silence.
    """

    name = "none"

    def deliver(self, notification: Notification) -> str:
        return ""
