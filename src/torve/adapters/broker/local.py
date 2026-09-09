"""The `local` broker adapter (S-0021/two-modes-because-custody-and-containment-are-different-problems, S-0021/D-10): a reverse proxy the
runner starts on loopback for the life of the run — one route per routed
provider — holding the real provider keys in its own environment and
injecting them at the wire. Metering comes from the provider's own
responses; counts and metadata only, never request or response bodies
(S-0021/D-7).

The adapter is an in-process thread of the runner (S-0021/D-10, decided in
T-0105): the runner already holds the keys in its environment, a thread
shares that environment with no serialization or lifecycle machinery, and
the broker dies with the run it serves — there is nothing to reap.

Sealed mode (S-0021/D-3, decided in T-0106) keeps the thread and changes where
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

Remote endpoint mode (S-0041/D-6, built in S-0041 phase 2) serves sandboxes
that live on another machine: `broker.bind` replaces the bridge-gateway
derivation with a configured host:port the thread listens on, and the
routes are published at `broker.advertise` — the address the sandboxes
dial, for the NAT/hostname split — so a remote run reaches its providers
over the same token-authenticated routes with the keys no closer to the
sandbox than before. The pass-through leg has no remote form: it
authenticates by a topology that does not exist across the open internet,
so in remote endpoint mode a CONNECT or absolute-URI request is refused
loudly, by its own cause, with the destination named — until someone
designs the token-authenticated equivalent. What rides the wire between a
remote sandbox and this broker is plaintext http: prompts and diffs cross
it exposed unless the operator puts TLS or a private network under the
deployment — the engine ships plaintext-capable and names that plainly.
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
    BurnEvent,
    BurnSink,
    RunChannel,
)
from torve.config.runconfig import (
    BrokerConfig,
    pass_through_allows,
    remote_broker_proxy,
    sealed_broker_port,
    split_host_port,
)

# ----------------------- #

CAUSE_AUTH = "auth"
CAUSE_ROUTING = "routing"
CAUSE_BUDGET = "budget"
CAUSE_CONTAINMENT = "containment"
CAUSE_PASS_THROUGH = "pass_through"
CAUSE_AUTHORITY = "authority"

# The intake route's path prefix (S-0045/the-intake-route). One segment, reserved: a
# provider named this would collide, which is why it carries a leading
# underscore no provider name does.
CHANNEL_PREFIX = "/_torve"
CHANNEL_RECORDS = f"{CHANNEL_PREFIX}/records"
CHANNEL_NOTES = f"{CHANNEL_PREFIX}/notes"

# What the remote-mode refusal says on the wire: the rule, in the sandbox's
# own terms — no corpus coordinates in strings read outside this repository.
REMOTE_PASS_THROUGH_MESSAGE = (
    "this broker runs in remote endpoint mode and serves only the routed "
    "providers, each at its own route with the run token; non-provider "
    "egress through a remote broker is refused until a token-authenticated "
    "pass-through design exists — the destination is named above and the "
    "refusal is counted"
)

# The network label naming the run a sealed network belongs to; cleanup at
# close removes only torve-owned networks, never the operator's.
NETWORK_LABEL_TASK = "torve.task"

# Hop-by-hop headers never survive the proxy (RFC 7230): the connection
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
    provider's own usage fields are the wire's truth (S-0021/D-5). The body is
    read, counted and discarded — the broker keeps no bodies (S-0021/D-7)."""

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
    here — counts and metadata, never bodies (S-0021/D-7). In sealed mode the
    state also carries the pass-through declaration and the routed
    providers' hosts, so the wire can refuse what containment and routing
    forbid (S-0021/D-3, S-0021/D-4)."""

    def __init__(
        self,
        routing: BrokerRouting,
        budget: BrokerBudget,
        *,
        sealed: bool,
        pass_through: tuple[str, ...],
        remote: bool = False,
        sink: BurnSink | None = None,
        channel: RunChannel | None = None,
    ) -> None:
        self.sink = sink
        # S-0045/the-intake-route: the run's route into the record. None means the run
        # has no channel and the intake path 404s like any unrouted path —
        # the sandbox then writes its worktree file, which is v1's behaviour
        # and stays correct (S-0045/D-6).
        self.channel = channel
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
        # Sealed mode (S-0021/D-3): the broker also serves the run's declared
        # pass-through egress. Provider hosts are never pass-through — a
        # routed provider's traffic must travel the route, key injected and
        # metered (S-0021/D-4).
        self.sealed = sealed
        self.pass_through = pass_through
        # Remote endpoint mode (S-0041/D-6): a broker whose address was
        # configured for sandboxes on another machine. The pass-through
        # leg — sealed mode's topology-authenticated relay — has no
        # remote form, so these refusals get their own cause and their
        # own explanation on the wire.
        self.remote = remote
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

        # S-0045 S-0045/D-3: emitted outside the lock, after the aggregate is
        # updated, so the two views of one run's spending are the same
        # numbers seen twice. A sink that raises is swallowed on purpose —
        # an observer that can break the run's egress is not an observer,
        # and this is the request path (S-0045/D-3).
        if self.sink is not None:
            with contextlib.suppress(Exception):
                self.sink(BurnEvent(provider=provider, tokens=tokens, cost_usd=cost))

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
        meter the answer. Every refusal is counted by cause (S-0021/D-6). In
        sealed mode the handler is also the run's only egress: a CONNECT or
        plain-http request to a declared pass-through host is relayed
        without inspection, and anything else is refused with the
        destination named (S-0021/D-3)."""

        # ....................... #

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ....................... #

        def _refuse(
            self,
            cause: str,
            status: int,
            provider: str,
            destination: str | None = None,
            message: str | None = None,
        ) -> None:
            state.refuse(cause, provider)
            error = {"error": {"cause": cause, "provider": provider}}

            if destination is not None:
                error["error"]["destination"] = destination

            if message is not None:
                error["error"]["message"] = message

            self._reply(status, json.dumps(error).encode("utf-8"))

        # ....................... #

        def _forward(self, route: BrokerRoute, body: bytes) -> None:
            upstream = urlsplit(route.upstream)
            host = upstream.hostname or "localhost"
            proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")

            if route.via_proxy and proxy and upstream.scheme == "https":
                # S-0021/A-1: this upstream is unreachable from the host directly
                # (region gating) — tunnel the broker's own leg through the
                # host's proxy. The sandbox still sees only the loopback.
                proxied = urlsplit(proxy)
                conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                    proxied.hostname or "localhost",
                    proxied.port,
                    timeout=UPSTREAM_TIMEOUT_S,
                )
                conn.set_tunnel(host, upstream.port or 443)
            elif upstream.scheme == "https":
                conn = http.client.HTTPSConnection(host, upstream.port, timeout=UPSTREAM_TIMEOUT_S)
            else:
                conn = http.client.HTTPConnection(host, upstream.port, timeout=UPSTREAM_TIMEOUT_S)

            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP and key.lower() not in FORWARD_DROP
            }

            # The sandbox's token never travels past the broker: the wire
            # credential is the provider key, injected here (S-0001/D-13).
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
            """The pass-through relay (S-0021/D-3): CONNECT the declared host and
            splice the sockets — bytes are relayed, never inspected, kept or
            metered beyond the request count (S-0021/D-7). The tunnel dies with
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
            just the request forwarded and the answer relayed (S-0021/D-3)."""

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
            declared, and it is not a routed provider's host (S-0021/D-3,
            S-0021/D-4). Otherwise the refusal is counted and the destination
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

            state.record(authority, 0, None)  # counts only (S-0021/D-7)
            return True

        # ....................... #

        def _refuse_pass_through(self, destination: str) -> None:
            """The remote endpoint mode refusal (S-0041/D-6): the pass-through
            leg authenticates by network topology — the sealed network is
            the run's private envelope — and that envelope does not exist
            across the open internet. A non-provider request is refused
            loudly: its own cause in the counters, the destination named
            on the wire, and the rule spelled out, because a silent
            connection reset would let a run limp past the one thing it
            should stop on."""

            self._refuse(
                CAUSE_PASS_THROUGH,
                403,
                destination,
                destination=destination,
                message=REMOTE_PASS_THROUGH_MESSAGE,
            )

        # ....................... #

        def _serve_connect(self) -> None:
            if not state.sealed:
                if state.remote:
                    self._refuse_pass_through(self.path)
                    return

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
                if state.remote:
                    self._refuse_pass_through(self.path)
                    return

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

        def _serve_channel(self, path: str) -> None:
            """The run's route into the record (S-0045/the-intake-route).

            The request carries content and nothing else. Who is writing,
            which partition, and which task are the channel's own — it was
            built for this run — so a caller cannot state them and therefore
            cannot forge them (S-0045/D-2). The authority table is checked here,
            at the boundary the untrusted side reaches, before anything is
            appended (S-0045/D-5).
            """

            if state.channel is None:
                self._refuse(CAUSE_ROUTING, 404, "", message="this run has no channel")
                return

            if path == CHANNEL_NOTES and self.command == "GET":
                self._reply(200, json.dumps({"notes": state.channel.notes()}).encode("utf-8"))
                return

            if path == CHANNEL_RECORDS and self.command == "GET":
                # A sandbox posting through the channel never sees its own
                # entries in the worktree: the engine writes that file at
                # the next gate pass. Reading them back is how an attempt
                # checks its own bookkeeping before it is judged on it.
                self._reply(200, json.dumps({"records": state.channel.records()}).encode("utf-8"))
                return

            if path != CHANNEL_RECORDS or self.command != "POST":
                self._refuse(CAUSE_ROUTING, 404, "", destination=path)
                return

            length = int(self.headers.get("Content-Length") or 0)

            try:
                sent = json.loads(self.rfile.read(length) or b"{}")
                kind = str(sent["kind"])
                payload = dict(sent["payload"])

            except (ValueError, KeyError, TypeError):
                self._refuse(CAUSE_ROUTING, 400, "", message="expected {kind, payload} as JSON")
                return

            try:
                state.channel.record(kind, payload)

            except Exception as exc:
                # A kind an agent may not write, or a payload its kind
                # rejects: refused before anything is appended, and the
                # refusal is counted like any other.
                self._refuse(CAUSE_AUTHORITY, 403, "", message=str(exc)[:300])
                return

            self._reply(201, b'{"recorded": true}')

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

            if parsed.path.startswith(CHANNEL_PREFIX):
                self._serve_channel(parsed.path)
                return

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
    loopback-facing port — or, when `broker.bind` names one, on the
    configured address with the routes published at `broker.advertise`
    (remote endpoint mode) — in sealed mode on the internal network's
    gateway at a port derived from the network's name. The keys live in
    the runner's own environment; `open` starts the server and issues the
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
        (S-0021/D-3)."""

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

    def open(
        self,
        run: str,
        routing: BrokerRouting,
        budget: BrokerBudget,
        sink: BurnSink | None = None,
        channel: RunChannel | None = None,
    ) -> BrokerHandle:
        missing = [
            route.key_env for route in routing.routes if os.environ.get(route.key_env) is None
        ]

        if missing:
            raise RuntimeError(
                "broker 'local' cannot hold the run's keys: environment variable(s) "
                f"{', '.join(sorted(missing))} are unset — the broker reads keys from "
                "its own environment"
            )

        advertised = ""

        if self._config.mode == "sealed":
            host, port = self._sealed_bind(run)
        elif self._config.bind:
            # Remote endpoint mode (S-0041/D-6): the configured listen address
            # replaces the bridge-gateway derivation, and the routes are
            # published at the advertised address — the bind host is often
            # a wildcard the sandbox must not dial, and the bound port is
            # configured, so the advertised URL is both sound and complete
            # without asking the socket. The `host` override is a test hook
            # for the old derivation; a configured bind is configuration,
            # so it wins.
            advertised = remote_broker_proxy(self._config)
            host, bind_port = split_host_port(self._config.bind)

            if bind_port is None or not advertised:
                raise RuntimeError(
                    f"broker.bind {self._config.bind!r} names no port — the listen "
                    "port must be configured, never ephemeral: the sandbox-side "
                    "composition learns this address with no channel to the broker"
                )

            port = bind_port
        else:
            host = self._host if self._host is not None else default_sandbox_host()
            port = 0

        state = _BrokerState(
            routing,
            budget,
            sealed=self._config.mode == "sealed",
            pass_through=tuple(self._config.pass_through),
            remote=bool(advertised),
            sink=sink,
            channel=channel,
        )
        server = ThreadingHTTPServer((host, port), _handler_for(state))
        thread = threading.Thread(
            target=server.serve_forever, name=f"torve-broker-{run}", daemon=True
        )
        thread.start()

        bound_port = server.server_address[1]
        route_base = advertised or f"http://{host}:{bound_port}"
        base_urls = {provider: f"{route_base}/{provider}" for provider in state.routes}
        handle = BrokerHandle(
            token=state.token,
            base_urls=base_urls,
            channel_url=f"{route_base}{CHANNEL_PREFIX}" if channel is not None else "",
        )
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
