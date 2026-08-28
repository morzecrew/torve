"""The egress broker's adapters (RFC 0021 §5.1, D-21.2): `local` is a
reverse proxy the runner starts on loopback for the life of the run and
holds the real provider keys in its own environment; `none` is today's
behaviour named explicitly and stays the phase-1 default; `opensandbox`
arrives with its server — the port exists so that work is an adapter, never
a prerequisite (the config refuses it until then).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torve.application.ports import Broker
    from torve.config.runconfig import BrokerConfig

from torve.adapters.broker.local import LocalBroker
from torve.adapters.broker.none import NoneBroker

# ....................... #


def build_broker(config: BrokerConfig) -> Broker:
    """The configured adapter. `opensandbox` is refused at configuration
    load, so this factory never sees it."""

    if config.adapter == "none":
        return NoneBroker()

    if config.adapter == "local":
        return LocalBroker(config)

    raise ValueError(
        f"broker adapter {config.adapter!r} is not built — "
        "the roster is 'none' (default) and 'local'"
    )
