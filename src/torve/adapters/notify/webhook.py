"""The webhook destination (RFC 0051 D-51.4).

First because it needs no account, no vendor SDK and no credential beyond a
URL the operator already holds — which makes Slack, Discord and PagerDuty a
configuration line rather than a code change, and makes the adapter
testable against a local server.

`urllib.request` rather than a client library, following `application.
channel`'s precedent: one POST with a timeout is not worth a dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from torve.application.ports import TransientDelivery

if TYPE_CHECKING:
    from torve.application.ports import Notification

# ----------------------- #

# The status classes that mean "later" rather than "no". 408 and 429 are
# the destination asking for exactly that; 5xx is its own failure, not the
# notification's.
RETRYABLE = frozenset({408, 429})


# ....................... #


class WebhookNotifier:
    """One POST per notification, carrying the escalation's own id as the
    idempotency key (D-51.6).

    The key rides both the body and an `Idempotency-Key` header: the header
    is what a destination that implements the convention reads, and the
    body is what one that does not can still be deduplicated on by hand.
    """

    name = "webhook"

    def __init__(self, url: str, *, timeout_s: float = 10.0) -> None:
        if not url:
            raise ValueError("the webhook destination needs a URL — set the configured variable")

        self._url = url
        self._timeout_s = timeout_s

    # ....................... #

    def deliver(self, notification: Notification) -> str:
        body = json.dumps(
            {
                "event_id": notification.event_id,
                "task": notification.task_id,
                "partition": notification.partition,
                "reason": notification.reason,
                "detail": notification.detail,
                "at": notification.at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "age_s": round(notification.age_s, 1),
                # Composed from records, saying what happened and never what
                # the finding deserves (RFC 0051 §5.3).
                "text": (
                    f"{notification.task_id} escalated: {notification.reason}"
                    f" — {notification.detail}"
                    if notification.detail
                    else f"{notification.task_id} escalated: {notification.reason}"
                ),
            }
        ).encode()

        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": notification.event_id,
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as answer:  # nosec B310
                return str(answer.headers.get("X-Request-Id") or answer.status)

        except urllib.error.HTTPError as exc:
            # A 4xx is the destination refusing this notification, and it
            # will refuse the same one again; a 5xx is the destination being
            # unwell, which is what a retry is for.
            if exc.code >= 500 or exc.code in RETRYABLE:
                raise TransientDelivery(f"{exc.code} from the destination") from exc

            raise RuntimeError(f"{exc.code} from the destination — not retried") from exc

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientDelivery(str(exc)) from exc
