"""The `local` broker adapter (RFC 0021 §5.2, D-21.10): a reverse proxy the
runner starts on loopback for the life of the run — one route per routed
provider — holding the real provider keys in its own environment and
injecting them at the wire. Metering comes from the provider's own
responses; counts and metadata only, never request or response bodies
(D-21.7).

The adapter is an in-process thread of the runner (D-21.10, decided in
T-0105): the runner already holds the keys in its environment, a thread
shares that environment with no serialization or lifecycle machinery, and
the broker dies with the run it serves — there is nothing to reap.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlsplit

from torve.application.ports import (
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    BrokerUsage,
)
from torve.config.runconfig import BrokerConfig

# ----------------------- #

CAUSE_AUTH = "auth"
CAUSE_ROUTING = "routing"
CAUSE_BUDGET = "budget"

# Hop-by-hop headers never survive the proxy (RFC 7230 §6.1): the connection
# to the provider is the broker's own, and the body length is re-derived from
# what was actually read.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

# The upstream answer is metered, so the request must not ask for a body the
# broker cannot read: compression is negotiated per request, and the sandbox
# survives plain JSON just fine.
FORWARD_DROP = frozenset({"accept-encoding"})

UPSTREAM_TIMEOUT_S = 300.0


# ....................... #


def _meter(body: bytes) -> tuple[int, float | None]:
    """(tokens, cost_usd) from a provider response body, best effort: the
    provider's own usage fields are the wire's truth (D-21.5). The body is
    read, counted and discarded — the broker keeps no bodies (D-21.7)."""

    try:
        data: Any = json.loads(body)

    except ValueError:
        return 0, None

    if not isinstance(data, dict):
        return 0, None

    record = cast("dict[str, Any]", data)
    usage = record.get("usage")
    tokens = 0

    if isinstance(usage, dict):
        usage_map = cast("dict[str, Any]", usage)
        total = usage_map.get("total_tokens")

        if isinstance(total, (int, float)):
            tokens = int(total)
        else:
            for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens"):
                value = usage_map.get(key)

                if isinstance(value, (int, float)):
                    tokens += int(value)

    cost: Any = record.get("total_cost_usd", record.get("cost_usd", record.get("cost")))

    return tokens, float(cost) if isinstance(cost, (int, float)) else None


# ....................... #


def default_sandbox_host() -> str:
    """The address a sandbox on the Docker default bridge reaches the host
    at: the bridge's gateway IP. Where no Docker daemon is present the
    sandbox shares the host's network view (host-shell runtimes, host-mode
    networking), so loopback is the reachable address."""

    try:
        proc = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                "bridge",
                "--format",
                "{{(index .IPAM.Config 0).Gateway}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    except (OSError, subprocess.SubprocessError):
        return "127.0.0.1"

    host = proc.stdout.strip()

    return host if proc.returncode == 0 and host else "127.0.0.1"


# ....................... #


class _BrokerState:
    """The run's counters: request count, token counts per provider, cost,
    refusals by cause, and the wall clock. Everything the broker keeps is
    here — counts and metadata, never bodies (D-21.7)."""

    def __init__(self, routing: BrokerRouting, budget: BrokerBudget) -> None:
        self.routes = {route.provider: route for route in routing.routes}
        self.budget = budget
        self.token = secrets.token_urlsafe(32)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.requests = 0
        self.tokens: dict[str, int] = {}
        self.cost = 0.0
        self.cost_seen = False
        self.refusals: dict[str, int] = {}
        self.refused_providers: dict[str, int] = {}

    # ....................... #

    def refuse(self, cause: str, provider: str | None = None) -> None:
        with self.lock:
            self.refusals[cause] = self.refusals.get(cause, 0) + 1

            if provider is not None:
                self.refused_providers[provider] = self.refused_providers.get(provider, 0) + 1

    # ....................... #

    def budget_exhausted(self) -> bool:
        if self.budget.tokens is None:
            return False

        with self.lock:
            return sum(self.tokens.values()) >= self.budget.tokens

    # ....................... #

    def record(self, provider: str, tokens: int, cost: float | None) -> None:
        with self.lock:
            self.requests += 1

            if tokens:
                self.tokens[provider] = self.tokens.get(provider, 0) + tokens

            if cost is not None:
                self.cost += cost
                self.cost_seen = True

    # ....................... #

    def usage(self) -> BrokerUsage:
        with self.lock:
            return BrokerUsage(
                requests=self.requests,
                tokens_per_provider=dict(self.tokens),
                wall_time_s=time.monotonic() - self.started,
                refusals=dict(self.refusals),
                cost_usd=round(self.cost, 6) if self.cost_seen else None,
                refused_providers=dict(self.refused_providers),
            )


# ....................... #


def _handler_for(state: _BrokerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        """One wire request: authenticate the run token, route the provider,
        check the budget, forward to the provider with the key injected, and
        meter the answer. Every refusal is counted by cause (D-21.6)."""

        # ....................... #

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ....................... #

        def _refuse(self, cause: str, status: int, provider: str) -> None:
            state.refuse(cause, provider)
            self._reply(
                status,
                json.dumps({"error": {"cause": cause, "provider": provider}}).encode("utf-8"),
            )

        # ....................... #

        def _forward(self, route: BrokerRoute, body: bytes) -> None:
            upstream = urlsplit(route.upstream)
            host = upstream.hostname or "localhost"

            if upstream.scheme == "https":
                conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                    host, upstream.port, timeout=UPSTREAM_TIMEOUT_S
                )
            else:
                conn = http.client.HTTPConnection(host, upstream.port, timeout=UPSTREAM_TIMEOUT_S)

            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP and key.lower() not in FORWARD_DROP
            }

            # The sandbox's token never travels past the broker: the wire
            # credential is the provider key, injected here (D-4b).
            headers["Authorization"] = f"Bearer {os.environ.get(route.key_env, '')}"

            parsed = urlsplit(self.path)
            segments = parsed.path.strip("/").split("/", 1)
            rest = segments[1] if len(segments) > 1 else ""
            base = upstream.path.rstrip("/")
            target = f"{base}/{rest}" if rest else (base or "/")

            if parsed.query:
                target += f"?{parsed.query}"

            # ponytail: whole-response buffering — streamed (SSE) completions
            # arrive at once; switch to chunked relay when a harness needs
            # incremental delivery.
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            tokens, cost = _meter(data)
            state.record(route.provider, tokens, cost)

            self.send_response(resp.status)

            for key, value in resp.getheaders():
                if key.lower() in HOP_BY_HOP:
                    continue

                self.send_header(key, value)

            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            conn.close()

        # ....................... #

        def _serve(self) -> None:
            if self.headers.get("Authorization") != f"Bearer {state.token}":
                state.refuse(CAUSE_AUTH)
                self._reply(401, b'{"error": {"cause": "auth"}}')
                return

            parsed = urlsplit(self.path)
            segments = parsed.path.strip("/").split("/", 1)
            provider = segments[0] if segments and segments[0] else ""
            route = state.routes.get(provider)

            if route is None:
                self._refuse(CAUSE_ROUTING, 403, provider)
                return

            if state.budget_exhausted():
                self._refuse(CAUSE_BUDGET, 429, provider)
                return

            length = int(self.headers.get("Content-Length") or 0)
            self._forward(route, self.rfile.read(length))

        # ....................... #

        def _handle(self) -> None:
            with contextlib.suppress(BrokenPipeError, ConnectionError):
                # A dead sandbox is not a broker failure: the refusal counts
                # already live in the state.
                self._serve()

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        def log_message(self, format: str, *args: Any) -> None:
            pass  # the broker's observability is its counters, not stderr

    return Handler


# ....................... #


class LocalBroker:
    """One reverse-proxy server per run on an ephemeral port, holding the
    keys in the runner's own environment. `open` starts the server and
    issues the run-scoped token; `close` stops the server and returns the
    run's usage. A single instance serves sequential runs (a tick dispatch
    reuses the injected broker). The configuration is consumed at load and
    at open time (the run's routing and budget arrive per open), never
    stored."""

    name = "local"

    def __init__(self, config: BrokerConfig, *, host: str | None = None) -> None:
        self._host = host if host is not None else default_sandbox_host()
        self._live: dict[str, tuple[ThreadingHTTPServer, _BrokerState]] = {}

    # ....................... #

    def open(self, run: str, routing: BrokerRouting, budget: BrokerBudget) -> BrokerHandle:
        missing = [
            route.key_env for route in routing.routes if os.environ.get(route.key_env) is None
        ]

        if missing:
            raise RuntimeError(
                "broker 'local' cannot hold the run's keys: environment variable(s) "
                f"{', '.join(sorted(missing))} are unset — the broker reads keys from "
                "its own environment"
            )

        state = _BrokerState(routing, budget)
        server = ThreadingHTTPServer((self._host, 0), _handler_for(state))
        thread = threading.Thread(
            target=server.serve_forever, name=f"torve-broker-{run}", daemon=True
        )
        thread.start()

        port = server.server_address[1]
        base_urls = {
            provider: f"http://{self._host}:{port}/{provider}" for provider in state.routes
        }
        handle = BrokerHandle(token=state.token, base_urls=base_urls)
        self._live[state.token] = (server, state)

        return handle

    # ....................... #

    def usage(self, handle: BrokerHandle) -> BrokerUsage:
        entry = self._live.get(handle.token)

        return entry[1].usage() if entry is not None else BrokerUsage()

    # ....................... #

    def close(self, handle: BrokerHandle) -> BrokerUsage:
        entry = self._live.pop(handle.token, None)

        if entry is None:
            return BrokerUsage()  # idempotent: an already-closed run reports nothing

        server, state = entry
        server.shutdown()
        server.server_close()

        return state.usage()
