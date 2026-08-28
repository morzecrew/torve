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

Sealed mode (D-21.3, decided in T-0106) keeps the thread and changes where
it listens: the sandbox joins the configured internal Docker network
(`broker.network`, created `--internal`), whose only host-side address is
its gateway — the host's interface on that network — and the broker binds
that gateway at a port derived from the network's name, so the runtime can
compose the sandbox's proxy env from the same two facts without a channel
between the adapters. The broker then also serves the run's non-provider
egress: a CONNECT or plain-http request to a declared pass-through host is
relayed without inspection, and anything else is refused loudly with the
destination named. The pass-through leg authenticates by topology — the
network is the run's private envelope, and the run token has no in-scope
channel into the sandbox's proxy env — while the provider routes keep the
token (see the T-0106 execution log).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import secrets
import socket
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
from torve.config.runconfig import (
    BrokerConfig,
    pass_through_allows,
    sealed_broker_port,
    split_host_port,
)

# ----------------------- #

CAUSE_AUTH = "auth"
CAUSE_ROUTING = "routing"
CAUSE_BUDGET = "budget"
CAUSE_CONTAINMENT = "containment"

# The network label naming the run a sealed network belongs to; cleanup at
# close removes only torve-owned networks, never the operator's.
NETWORK_LABEL_TASK = "torve.task"

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
# The sandbox's own Authorization (the run token, any case node sends it
# in) must never reach the provider beside the injected key — two auth
# headers and the upstream reads whichever it likes.
FORWARD_DROP = frozenset({"accept-encoding", "authorization"})

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
    here — counts and metadata, never bodies (D-21.7). In sealed mode the
    state also carries the pass-through declaration and the routed
    providers' hosts, so the wire can refuse what containment and routing
    forbid (D-21.3, D-21.4)."""

    def __init__(
        self,
        routing: BrokerRouting,
        budget: BrokerBudget,
        *,
        sealed: bool,
        pass_through: tuple[str, ...],
    ) -> None:
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
        # Sealed mode (D-21.3): the broker also serves the run's declared
        # pass-through egress. Provider hosts are never pass-through — a
        # routed provider's traffic must travel the route, key injected and
        # metered (D-21.4).
        self.sealed = sealed
        self.pass_through = pass_through
        self.provider_hosts = frozenset(
            urlsplit(route.upstream).hostname for route in routing.routes
        )

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
        meter the answer. Every refusal is counted by cause (D-21.6). In
        sealed mode the handler is also the run's only egress: a CONNECT or
        plain-http request to a declared pass-through host is relayed
        without inspection, and anything else is refused with the
        destination named (D-21.3)."""

        # ....................... #

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ....................... #

        def _refuse(
            self, cause: str, status: int, provider: str, destination: str | None = None
        ) -> None:
            state.refuse(cause, provider)
            error = {"error": {"cause": cause, "provider": provider}}

            if destination is not None:
                error["error"]["destination"] = destination

            self._reply(status, json.dumps(error).encode("utf-8"))

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

        def _tunnel(self, host: str, port: int) -> None:
            """The pass-through relay (D-21.3): CONNECT the declared host and
            splice the sockets — bytes are relayed, never inspected, kept or
            metered beyond the request count (D-21.7). The tunnel dies with
            either side: the first direction to close shuts both sockets, so
            a half-closed tunnel cannot pin the broker's port."""

            upstream = socket.create_connection((host, port), timeout=UPSTREAM_TIMEOUT_S)
            upstream.settimeout(UPSTREAM_TIMEOUT_S)
            self.connection.settimeout(UPSTREAM_TIMEOUT_S)
            self.send_response(200, "Connection established")
            self.end_headers()
            closing = threading.Event()

            def pump(source: socket.socket, sink: socket.socket) -> None:
                try:
                    while not closing.is_set():
                        data = source.recv(65536)

                        if not data:
                            break

                        sink.sendall(data)

                except OSError:
                    pass  # a closed tunnel is not a broker failure

                finally:
                    closing.set()

                    with contextlib.suppress(OSError):
                        source.shutdown(socket.SHUT_RDWR)

                    with contextlib.suppress(OSError):
                        sink.shutdown(socket.SHUT_RDWR)

            one = threading.Thread(target=pump, args=(self.connection, upstream), daemon=True)
            two = threading.Thread(target=pump, args=(upstream, self.connection), daemon=True)
            one.start()
            two.start()
            one.join()
            two.join()

            with contextlib.suppress(OSError):
                self.connection.close()

            with contextlib.suppress(OSError):
                upstream.close()

        # ....................... #

        def _forward_plain(self, host: str, port: int) -> None:
            """A pass-through http:// request, relayed without inspection:
            no key injection (this is not a provider route), no metering —
            just the request forwarded and the answer relayed (D-21.3)."""

            conn = http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT_S)
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP and key.lower() not in FORWARD_DROP
            }
            parsed = urlsplit(self.path)
            target = parsed.path or "/"

            if parsed.query:
                target += f"?{parsed.query}"

            length = int(self.headers.get("Content-Length") or 0)
            conn.request(self.command, target, body=self.rfile.read(length), headers=headers)
            resp = conn.getresponse()
            data = resp.read()

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

        def _pass_through(self, host: str, port: int, authority: str) -> bool:
            """True when the sealed broker may relay this destination: it is
            declared, and it is not a routed provider's host (D-21.3,
            D-21.4). Otherwise the refusal is counted and the destination
            named — an undeclared destination fails loudly, and the run
            escalates rather than succeed through a path nobody meant to
            leave open."""

            bound = cast("tuple[str, int]", self.server.server_address)

            if (host, port) == bound:
                # The broker itself is not a declared destination: a tunnel
                # to the broker's own address would recurse into itself.
                self._refuse(CAUSE_CONTAINMENT, 403, authority, destination=authority)

                return False

            if host in state.provider_hosts:
                self._refuse(CAUSE_ROUTING, 403, host, destination=authority)

                return False

            if not pass_through_allows(state.pass_through, host, port):
                self._refuse(CAUSE_CONTAINMENT, 403, authority, destination=authority)

                return False

            state.record(authority, 0, None)  # counts only (D-21.7)
            return True

        # ....................... #

        def _serve_connect(self) -> None:
            if not state.sealed:
                # Endpoint mode has no pass-through function: the sandbox
                # keeps the default bridge, and a CONNECT to the broker is
                # a request for a destination this run is not routed to.
                self._refuse(CAUSE_ROUTING, 403, self.path, destination=self.path)
                return

            host, port = split_host_port(self.path)
            port = port if port is not None else 443

            if not self._pass_through(host, port, self.path):
                return

            self._tunnel(host, port)

        # ....................... #

        def _serve_absolute(self) -> None:
            parsed = urlsplit(self.path)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)

            if not state.sealed:
                self._refuse(CAUSE_ROUTING, 403, self.path, destination=self.path)
                return

            if parsed.scheme != "http":
                # https travels as CONNECT; a plain-http relay is the only
                # absolute-URI form the broker serves.
                self._refuse(CAUSE_CONTAINMENT, 403, self.path, destination=self.path)
                return

            if not self._pass_through(host, port, self.path):
                return

            self._forward_plain(host, port)

        # ....................... #

        def _serve(self) -> None:
            # Sealed mode's forward-proxy forms: a CONNECT authority or an
            # absolute-URI request line. The pass-through leg authenticates
            # by topology (the internal network is the run's private
            # envelope); the provider routes below keep the run token.
            if self.command == "CONNECT":
                self._serve_connect()
                return

            if "://" in self.path:
                self._serve_absolute()
                return

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

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_CONNECT = _handle

        def log_message(self, format: str, *args: Any) -> None:
            pass  # the broker's observability is its counters, not stderr

    return Handler


# ....................... #


class LocalBroker:
    """One reverse-proxy server per run: in endpoint mode on an ephemeral
    loopback-facing port, in sealed mode on the internal network's gateway
    at a port derived from the network's name. The keys live in the
    runner's own environment; `open` starts the server and issues the
    run-scoped token; `close` stops the server, returns the run's usage,
    and removes the internal network it created once it is empty. A single
    instance serves sequential runs (a tick dispatch reuses the injected
    broker). The configuration is consumed at load and at open time (the
    run's routing and budget arrive per open), never stored."""

    name = "local"

    def __init__(
        self,
        config: BrokerConfig,
        *,
        host: str | None = None,
        docker_bin: str = "docker",
    ) -> None:
        self._config = config
        # Endpoint-mode bind override (tests); sealed mode always binds the
        # internal network's gateway — the host's only address on it.
        self._host = host
        self._docker = docker_bin
        self._live: dict[str, tuple[ThreadingHTTPServer, _BrokerState]] = {}

    # ....................... #

    def _docker_run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._docker, *args], capture_output=True, text=True, timeout=30, check=False
            )

        except FileNotFoundError:
            raise RuntimeError(
                "broker 'local' sealed mode needs the docker CLI "
                f"({self._docker!r} not found) — sealed containment is the "
                "docker internal-network mechanism"
            ) from None

    # ....................... #

    def _sealed_bind(self, run: str) -> tuple[str, int]:
        """The sealed broker's address: the configured internal network's
        gateway at the port derived from the network's name. The network is
        created `--internal` when missing — sealed mode needs no operator
        step beyond configuration — and an existing network that is not
        internal is a refused configuration, never a silent endpoint run
        (D-21.3)."""

        network = self._config.network
        proc = self._docker_run("network", "inspect", "--format", "{{.Internal}}", network)

        if proc.returncode != 0:
            created = self._docker_run(
                "network", "create", "--internal", "--label", f"{NETWORK_LABEL_TASK}={run}", network
            )

            if created.returncode != 0:
                raise RuntimeError(
                    "broker 'local' sealed mode: could not create the internal "
                    f"network {network!r}: {created.stderr.strip()} — check the "
                    "docker daemon and the network name"
                )

        elif proc.stdout.strip().lower() != "true":
            raise RuntimeError(
                "broker 'local' sealed mode: network "
                f"{network!r} exists but is not internal — sealed containment "
                "needs a network created with --internal; refuse rather than "
                "sandbox into a network that can reach the outside"
            )

        gateway = self._docker_run(
            "network", "inspect", "--format", "{{(index .IPAM.Config 0).Gateway}}", network
        )

        if gateway.returncode != 0 or not gateway.stdout.strip():
            raise RuntimeError(
                "broker 'local' sealed mode: cannot resolve the gateway of the "
                f"internal network {network!r}: {gateway.stderr.strip()}"
            )

        return gateway.stdout.strip(), sealed_broker_port(network)

    # ....................... #

    def _cleanup_sealed_network(self) -> None:
        """Remove the run's internal network once it is empty — only a
        torve-owned network (labeled with the run), never the operator's.
        An empty check keeps a concurrent run on the same configured
        network alive until its own close."""

        network = self._config.network
        containers = self._docker_run(
            "network", "inspect", "--format", "{{len .Containers}}", network
        )

        if containers.returncode != 0 or containers.stdout.strip() != "0":
            return

        # `if index` renders "torve" when the run label is present and the
        # empty string when it is not — a missing map key would otherwise
        # render Go's `<no value>`.
        label = self._docker_run(
            "network",
            "inspect",
            "--format",
            f'{{{{if index .Labels "{NETWORK_LABEL_TASK}"}}}}torve{{{{end}}}}',
            network,
        )

        if label.returncode != 0 or label.stdout.strip() != "torve":
            return

        self._docker_run("network", "rm", network)

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

        if self._config.mode == "sealed":
            host, port = self._sealed_bind(run)
        else:
            host = self._host if self._host is not None else default_sandbox_host()
            port = 0

        state = _BrokerState(
            routing,
            budget,
            sealed=self._config.mode == "sealed",
            pass_through=tuple(self._config.pass_through),
        )
        server = ThreadingHTTPServer((host, port), _handler_for(state))
        thread = threading.Thread(
            target=server.serve_forever, name=f"torve-broker-{run}", daemon=True
        )
        thread.start()

        bound_port = server.server_address[1]
        base_urls = {
            provider: f"http://{host}:{bound_port}/{provider}" for provider in state.routes
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

        if self._config.mode == "sealed":
            self._cleanup_sealed_network()

        return state.usage()
