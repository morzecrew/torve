"""Notification destinations (S-0051 S-0051/D-3): the adapters behind the
`Notifier` port.

`none` is the default and is explicit, following the broker's precedent
(S-0021/D-9): a repository that has not chosen a destination sends nothing
because somebody decided that, not because a field was left blank.
"""

from torve.adapters.notify.inert import NoNotifier
from torve.adapters.notify.webhook import WebhookNotifier

__all__ = ["NoNotifier", "WebhookNotifier"]
