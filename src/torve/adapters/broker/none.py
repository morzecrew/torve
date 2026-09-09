"""The `none` broker adapter (S-0021/the-port): today's behaviour, named
explicitly. Provider keys pass through the tier's env names exactly as they
did before the port existed; there is no metering and no wire routing.
`torve doctor` names it and says plainly that this leaves the
credential-custody requirement (S-0001/D-13) unmet (S-0021/D-9); it stays the phase-1
default because a repository run on the operator's own machine against
their own key may reasonably decline the extra process.
"""

from __future__ import annotations

from torve.application.ports import (
    BrokerBudget,
    BrokerHandle,
    BrokerRouting,
    BrokerUsage,
    BurnSink,
    RunChannel,
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

    def open(
        self,
        run: str,
        routing: BrokerRouting,
        budget: BrokerBudget,
        sink: BurnSink | None = None,
        channel: RunChannel | None = None,
    ) -> BrokerHandle:
        # No wire, no metering, so nothing to emit: a run opting out of the
        # broker opts out of the burn stream with it (S-0021/D-9) — and of the
        # channel, since the channel is a route on the wire that is not
        # there. The intake writes the worktree log instead (S-0045/D-6).
        return BrokerHandle(token="", base_urls={})

    # ....................... #

    def usage(self, handle: BrokerHandle) -> BrokerUsage:
        return BrokerUsage()

    # ....................... #

    def close(self, handle: BrokerHandle) -> BrokerUsage:
        return BrokerUsage()
