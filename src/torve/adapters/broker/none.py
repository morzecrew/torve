"""The `none` broker adapter (RFC 0021 §5.1): today's behaviour, named
explicitly. Provider keys pass through the tier's env names exactly as they
did before the port existed; there is no metering and no wire routing.
`torve doctor` names it and says plainly that this leaves the
credential-custody requirement (D-4b) unmet (D-21.9); it stays the phase-1
default because a repository run on the operator's own machine against
their own key may reasonably decline the extra process.
"""

from __future__ import annotations

from torve.application.ports import (
    BrokerBudget,
    BrokerHandle,
    BrokerRouting,
    BrokerUsage,
)

# ....................... #


class NoneBroker:
    """The no-op adapter: `open` hands back an empty handle, `close` reports
    nothing. The runner treats the empty handle as "no broker" — a tier
    command that names broker placeholders under this adapter is refused at
    dispatch rather than silently sending a literal placeholder into the
    sandbox."""

    name = "none"

    # ....................... #

    def open(self, run: str, routing: BrokerRouting, budget: BrokerBudget) -> BrokerHandle:
        return BrokerHandle(token="", base_urls={})

    # ....................... #

    def usage(self, handle: BrokerHandle) -> BrokerUsage:
        return BrokerUsage()

    # ....................... #

    def close(self, handle: BrokerHandle) -> BrokerUsage:
        return BrokerUsage()
