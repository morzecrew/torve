"""RFC 0021 phase 2 — sealed mode (T-0106, D-21.3): containment on top of
custody. The sandbox joins an internal Docker network shared with the
broker, so no destination is reachable except through it, and every
non-provider host the run legitimately needs is declared and CONNECTed
without inspection. An undeclared destination fails loudly and names
itself.

The broker's sealed behavior is exercised for real over loopback (a fake
`docker` binary simulates the internal network's lifecycle, and the
broker binds the simulated gateway); the runtime's sealed wiring is
asserted against the same fake daemon. The RFC §6 integration case — a
real sandbox on a real internal network failing to reach an undeclared
host — runs under the same docker-availability skip as the phase-1
integration tests.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from torve.adapters.broker.local import LocalBroker
from torve.application.ports import (
    BrokerBudget,
    BrokerRoute,
    BrokerRouting,
    SandboxSpec,
)
from torve.base import naming
from torve.config.runconfig import (
    BrokerConfig,
    BrokerProvider,
    RunnerConfig,
    RuntimeConfig,
    sealed_broker_port,
)

# ----------------------- #

KEY_ENV = "TORVE_BROKER_TEST_KEY"
PROVIDER = "test-vendor"
NETWORK = "torve-sealed"
TEST_IMAGE = "python:3.13-slim"


# ....................... #
# The fake docker daemon: records every invocation and simulates just
# enough of the network lifecycle (inspect/create/rm) and `docker run`
# for the sealed paths to run without a daemon.

FAKE_DOCKER_SRC = r"""#!/usr/bin/env python3
import json, os, sys

LOG = os.environ["FAKE_DOCKER_LOG"]
STATE = os.environ["FAKE_DOCKER_STATE"]

with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")

def load_state():
    if not os.path.exists(STATE):
        return {"networks": {}}
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)

args = sys.argv[1:]

if args[:2] == ["network", "inspect"]:
    marker = args.index("--format")
    fmt, name = args[marker + 1], args[marker + 2]
    network = load_state()["networks"].get(name)
    if network is None:
        print("no such network", file=sys.stderr)
        sys.exit(1)
    if fmt == "{{.Internal}}":
        print(str(network.get("internal", True)).lower())
    elif fmt == "{{(index .IPAM.Config 0).Gateway}}":
        print(os.environ.get("FAKE_DOCKER_GATEWAY", "127.0.0.1"))
    elif fmt == "{{len .Containers}}":
        print(network.get("containers", 0))
    elif "Labels" in fmt:
        print("torve" if "torve.task" in network.get("labels", {}) else "")
    sys.exit(0)

if args[:2] == ["network", "create"]:
    state = load_state()
    labels = {}
    index = 2
    while index < len(args) - 1:
        if args[index] == "--label":
            key, value = args[index + 1].split("=", 1)
            labels[key] = value
            index += 2
        else:
            index += 1
    state["networks"][args[-1]] = {"labels": labels, "containers": 0}
    save_state(state)
    sys.exit(0)

if args[:2] == ["network", "rm"]:
    state = load_state()
    state["networks"].pop(args[-1], None)
    save_state(state)
    sys.exit(0)

if args[0] == "run":
    print("fakecontainerid")
    sys.exit(0)

sys.exit(0)
"""


class FakeDocker:
    def __init__(self, tmp_path: Path) -> None:
        self.state_file = tmp_path / "fake-docker-state.json"
        self.log_file = tmp_path / "fake-docker.log"
        self.script = tmp_path / "docker"
        self.script.write_text(FAKE_DOCKER_SRC, encoding="utf-8")
        self.script.chmod(0o755)

    def seed_network(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        internal: bool = True,
    ) -> None:
        state = (
            json.loads(self.state_file.read_text(encoding="utf-8"))
            if self.state_file.exists()
            else {"networks": {}}
        )
        state["networks"][name] = {"labels": labels or {}, "containers": 0, "internal": internal}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def invocations(self) -> list[list[str]]:
        if not self.log_file.exists():
            return []

        return [json.loads(line) for line in self.log_file.read_text().splitlines()]

    def run_args(self) -> list[str]:
        return next(args for args in self.invocations() if args[0] == "run")


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch) -> FakeDocker:
    fake = FakeDocker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(fake.log_file))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fake.state_file))
    return fake


@pytest.fixture
def sealed_network() -> str:
    """A per-test internal network name: the sealed broker's port derives
    from it, so tests never contend for one another's ports."""

    return f"torve-sealed-{uuid.uuid4().hex[:8]}"


# ....................... #
# Loopback servers: a metering fake provider, a plain http origin for the
# pass-through forward, and a raw TCP echo server for the CONNECT tunnel.


@pytest.fixture
def provider_upstream():
    """(state, upstream_url) — a fake provider on loopback, reached as
    ``localhost`` so its host name never collides with the ``127.0.0.1``
    pass-through declarations the loopback tests use."""

    state: dict[str, object] = {
        "auth": [],
        "paths": [],
        "requests": 0,
        "usage": {"total_tokens": 5},
        "cost": 0.01,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            state["auth"].append(self.headers.get("Authorization", ""))
            state["paths"].append(self.path)
            state["requests"] = int(state["requests"]) + 1
            payload = json.dumps(
                {"usage": state["usage"], "total_cost_usd": state["cost"], "model": "m-9"}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    yield state, f"http://localhost:{server.server_address[1]}"

    server.shutdown()
    server.server_close()


@pytest.fixture
def plain_origin():
    """A plain http origin on loopback whose response echoes the path — the
    absolute-URI pass-through forward relays to it without inspection."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"path": self.path}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    yield f"http://127.0.0.1:{server.server_address[1]}"

    server.shutdown()
    server.server_close()


@pytest.fixture
def echo_port():
    """A raw TCP echo server on loopback — the far end of a CONNECT tunnel."""

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]

    def loop() -> None:
        while True:
            conn, _ = server.accept()

            def serve(connection: socket.socket) -> None:
                with connection:
                    while True:
                        data = connection.recv(65536)

                        if not data:
                            return

                        connection.sendall(data)

            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()

    yield port

    server.close()


# ....................... #
# Wire helpers: a raw CONNECT through the broker, and an absolute-URI GET.


def broker_address(handle) -> tuple[str, int]:
    url = handle.url_for(PROVIDER)
    assert url is not None
    parsed = urlsplit(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 0


def connect_through(
    host: str, port: int, authority: str, timeout: float = 10
) -> tuple[socket.socket, str, bytes]:
    """CONNECT through the broker and read the full response — headers and
    the refusal body (Content-Length sized), so callers can assert on the
    JSON error that names the destination."""

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
    response = b""

    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)

        if not chunk:
            break

        response += chunk

    head, _, rest = response.partition(b"\r\n\r\n")
    match = re.search(rb"Content-Length: (\d+)", head, re.IGNORECASE)

    if match is not None:
        length = int(match.group(1))

        while len(rest) < length:
            chunk = sock.recv(4096)

            if not chunk:
                break

            rest += chunk

    status = head.split(b"\r\n", 1)[0].decode("ascii", "replace")

    return sock, status, rest


def proxy_get(host: str, port: int, target: str, timeout: float = 10) -> tuple[str, bytes]:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(
        f"GET {target} HTTP/1.1\r\nHost: {urlsplit(target).netloc}\r\n"
        "Connection: close\r\n\r\n".encode("ascii")
    )
    data = b""

    while True:
        chunk = sock.recv(4096)

        if not chunk:
            break

        data += chunk

    sock.close()
    head, _, body = data.partition(b"\r\n\r\n")

    return head.split(b"\r\n", 1)[0].decode("ascii", "replace"), body


# ....................... #


def sealed_config(upstream_url: str, *, network: str = NETWORK, **overrides) -> BrokerConfig:
    return BrokerConfig(
        adapter="local",
        mode="sealed",
        network=network,
        providers={PROVIDER: BrokerProvider(upstream=upstream_url, key_env=KEY_ENV)},
        **{"pass_through": ["127.0.0.1"], **overrides},
    )


def routing_for(upstream_url: str) -> BrokerRouting:
    return BrokerRouting(
        routes=(BrokerRoute(provider=PROVIDER, upstream=upstream_url, key_env=KEY_ENV),)
    )


# ....................... #
# Configuration (D-21.3, D-21.11)


def test_sealed_requires_a_broker_on_the_wire():
    with pytest.raises(ValidationError, match="no wire presence"):
        BrokerConfig(adapter="none", mode="sealed", network=NETWORK)


def test_sealed_requires_a_valid_network_name():
    with pytest.raises(ValidationError, match="network mode"):
        BrokerConfig(adapter="local", mode="sealed", network="host")

    with pytest.raises(ValidationError, match="valid Docker network name"):
        BrokerConfig(adapter="local", mode="sealed", network="has/slash")


def test_endpoint_refuses_sealed_only_fields():
    with pytest.raises(ValidationError, match="names no network"):
        BrokerConfig(adapter="local", mode="endpoint", network=NETWORK)

    with pytest.raises(ValidationError, match="declares nothing"):
        BrokerConfig(adapter="local", mode="endpoint", pass_through=["pypi.org"])


def test_pass_through_entries_are_hosts_not_urls():
    for bad in ("https://pypi.org", "pypi.org/simple", "*.example.com", "pypi.org:99999", " "):
        with pytest.raises(ValidationError, match="pass_through entry"):
            sealed_config("http://localhost:1", pass_through=[bad])


def test_pass_through_may_not_shadow_a_routed_provider():
    # D-21.4: a destination cannot be both a routed provider (key injected,
    # metered) and an uninspected pass-through — the wire enforcement would
    # be bypassable.
    with pytest.raises(ValidationError, match="routed provider host"):
        sealed_config("http://localhost:1", pass_through=["localhost"])


def test_sealed_runner_needs_the_docker_runtime_and_the_shared_network():
    broker = sealed_config("http://localhost:1")
    with pytest.raises(ValidationError, match="needs the docker runtime"):
        RunnerConfig(broker=broker, runtime=RuntimeConfig(adapter="opensandbox"))

    with pytest.raises(ValidationError, match="must name the same network"):
        RunnerConfig(
            broker=broker,
            runtime=RuntimeConfig(network="some-other-network"),
            tiers={},
        )

    config = RunnerConfig(
        broker=broker,
        runtime=RuntimeConfig(network=NETWORK),
        tiers={},
    )
    assert config.broker.network == config.runtime.network == NETWORK


def test_sealed_runner_refuses_the_host_daemon_socket():
    # D-17.10: a socket is host-equivalent capability — the exact trust
    # sealed containment exists to remove.
    with pytest.raises(ValidationError, match=r"refuses runtime\.docker: socket"):
        RunnerConfig(
            broker=sealed_config("http://localhost:1"),
            runtime=RuntimeConfig(network=NETWORK, docker="socket"),
            tiers={},
        )


# ....................... #
# The sealed broker: network lifecycle, the derived address, and the wire


def test_sealed_open_creates_the_internal_network_and_binds_the_derived_port(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    assert any(
        args[:3] == ["network", "create", "--internal"]
        and args[-1] == sealed_network
        and "torve.task=T-0106" in args
        for args in fake_docker.invocations()
    )
    assert handle.url_for(PROVIDER) == (
        f"http://127.0.0.1:{sealed_broker_port(sealed_network)}/{PROVIDER}"
    )

    broker.close(handle)

    assert ["network", "rm", sealed_network] in fake_docker.invocations()


def test_sealed_reuses_an_operator_network_and_never_removes_it(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    fake_docker.seed_network(sealed_network, labels={})  # operator-owned: no torve label
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    assert not any(args[:3] == ["network", "create"] for args in fake_docker.invocations())

    broker.close(handle)

    assert not any(args[:3] == ["network", "rm"] for args in fake_docker.invocations())


def test_sealed_refuses_a_network_that_is_not_internal(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    # D-21.3 fail-closed: an existing network of that name that is not
    # internal is a refused configuration, never a silent endpoint run on a
    # network that can reach the outside.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    fake_docker.seed_network(sealed_network, labels={"torve.task": "T-0106"}, internal=False)
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )

    with pytest.raises(RuntimeError, match="not internal"):
        broker.open("T-0106", routing_for(upstream_url), BrokerBudget())


def test_sealed_open_fails_loudly_when_the_network_cannot_be_created(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    # The broker is the run's only egress; a sealed run whose network
    # cannot be provisioned must refuse to start (RFC 0021 §9: the failure
    # is loud, never a fallback to a less isolated path).
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    script = fake_docker.script.parent / "docker-failing-create"
    src = FAKE_DOCKER_SRC.replace(
        'if args[:2] == ["network", "create"]:',
        'if args[:2] == ["network", "create"]:\n    print("permission denied", file=sys.stderr)\n    sys.exit(1)',
    )
    script.write_text(src, encoding="utf-8")
    script.chmod(0o755)

    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(script)
    )

    with pytest.raises(RuntimeError, match="could not create the internal network"):
        broker.open("T-0106", routing_for(upstream_url), BrokerBudget())


def test_sealed_connect_tunnels_a_declared_host(
    fake_docker, provider_upstream, echo_port, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    sock, status, _ = connect_through(host, port, f"127.0.0.1:{echo_port}")
    assert status.startswith("HTTP/1.0 200")

    sock.sendall(b"ping")
    assert sock.recv(64) == b"ping"
    sock.close()

    usage = broker.close(handle)
    assert usage.requests == 1  # the tunnel is counted, never its bytes (D-21.7)
    assert usage.refusals == {}


def test_sealed_connect_refuses_an_undeclared_host(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    sock, status, body = connect_through(host, port, "127.0.0.2:443")
    sock.close()

    assert status.startswith("HTTP/1.0 403")
    error = json.loads(body)
    assert error["error"]["cause"] == "containment"
    assert error["error"]["destination"] == "127.0.0.2:443"

    usage = broker.close(handle)
    assert usage.refusals == {"containment": 1}
    assert usage.refused_providers == {"127.0.0.2:443": 1}


def test_sealed_connect_refuses_a_provider_host(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    # D-21.4: a routed provider's host is never a pass-through — its
    # traffic must travel the route, key injected and metered.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    sock, status, _ = connect_through(host, port, "localhost:443")
    sock.close()

    assert status.startswith("HTTP/1.0 403")

    usage = broker.close(handle)
    assert usage.refusals == {"routing": 1}
    assert usage.refused_providers == {"localhost": 1}


def test_sealed_connect_refuses_the_broker_itself(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    # The broker is not a declared destination: a tunnel to its own address
    # would recurse into itself, so it is refused even though the address is
    # on the declared loopback host.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    sock, status, _ = connect_through(host, port, f"{host}:{port}")
    sock.close()

    assert status.startswith("HTTP/1.0 403")

    usage = broker.close(handle)
    assert usage.refusals == {"containment": 1}


def test_sealed_absolute_uri_http_is_forwarded_without_inspection(
    fake_docker, provider_upstream, plain_origin, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    status, body = proxy_get(host, port, f"{plain_origin}/hello")

    assert status.startswith("HTTP/1.0 200")
    assert json.loads(body)["path"] == "/hello"

    usage = broker.close(handle)
    assert usage.requests == 1


def test_sealed_absolute_uri_undeclared_is_refused(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    status, body = proxy_get(host, port, "http://127.0.0.2/x")

    assert status.startswith("HTTP/1.0 403")
    assert b"containment" in body
    assert b"127.0.0.2" in body

    broker.close(handle)


def test_endpoint_mode_has_no_pass_through(provider_upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = provider_upstream
    config = BrokerConfig(
        adapter="local",
        mode="endpoint",
        providers={PROVIDER: BrokerProvider(upstream=upstream_url, key_env=KEY_ENV)},
    )
    broker = LocalBroker(config, host="127.0.0.1")
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    host, port = broker_address(handle)
    sock, status, _ = connect_through(host, port, "127.0.0.1:1")
    sock.close()

    assert status.startswith("HTTP/1.0 403")

    usage = broker.close(handle)
    assert usage.refusals == {"routing": 1}


def test_sealed_provider_route_keeps_the_run_token(
    fake_docker, provider_upstream, sealed_network, monkeypatch
):
    # The pass-through leg authenticates by topology; the provider routes
    # keep the run token exactly as in endpoint mode.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = provider_upstream
    broker = LocalBroker(
        sealed_config(upstream_url, network=sealed_network), docker_bin=str(fake_docker.script)
    )
    handle = broker.open("T-0106", routing_for(upstream_url), BrokerBudget())

    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    request = Request(
        handle.url_for(PROVIDER) + "/v1/chat/completions",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(HTTPError) as excinfo:
        urlopen(request, timeout=10)

    assert excinfo.value.code == 401

    request.add_header("Authorization", f"Bearer {handle.token}")

    with urlopen(request, timeout=10) as response:
        assert response.status == 200

    assert state["requests"] == 1
    assert state["auth"] == [f"Bearer {os.environ[KEY_ENV]}"]

    usage = broker.close(handle)
    assert usage.requests == 1
    assert usage.tokens_per_provider == {PROVIDER: 5}


# ....................... #
# The runtime's sealed wiring: join the internal network, point the proxy
# env at the broker, and never forward the host's own proxy.


def sealed_spec() -> SandboxSpec:
    return SandboxSpec(
        name=f"torve-sealed-{uuid.uuid4().hex[:8]}",
        image=TEST_IMAGE,
        labels=naming.labels("T-0106", uuid.uuid4().hex, Path.cwd()),
        timeout_s=120,
    )


def test_runtime_sealed_joins_the_network_and_proxies_to_the_broker(
    fake_docker, tmp_path, monkeypatch
):
    from torve.adapters.runtime.docker import DockerRuntime

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")  # the host's own proxy
    fake_docker.seed_network(NETWORK, labels={"torve.task": "T-0106"})
    runtime = DockerRuntime(docker_bin=str(fake_docker.script), network=NETWORK)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    handle = runtime.create(sealed_spec(), workspace)
    args = fake_docker.run_args()

    assert "--network" in args and args[args.index("--network") + 1] == NETWORK
    proxy = f"http://127.0.0.1:{sealed_broker_port(NETWORK)}"

    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        assert f"{name}={proxy}" in args, args

    assert "no_proxy=127.0.0.1,localhost,127.0.0.1" in args
    # the host's own proxy never rides into a sealed sandbox — it would be
    # a path nobody meant to leave open
    assert "http://127.0.0.1:9999" not in args

    runtime.destroy(handle)


def test_runtime_non_sealed_network_keeps_forwarding_the_host_proxy(
    fake_docker, tmp_path, monkeypatch
):
    from torve.adapters.runtime.docker import DockerRuntime

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    runtime = DockerRuntime(docker_bin=str(fake_docker.script), network="host")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    runtime.create(sealed_spec(), workspace)
    args = fake_docker.run_args()

    # host mode forwards the proxy convention by NAME — the value rides the
    # invoking environment, never the spec (D-4b)
    assert "HTTPS_PROXY" in args
    assert "http://127.0.0.1:9999" not in args


# ....................... #
# End to end against the real daemon (RFC 0021 §6): a sandbox on the
# internal network cannot reach an undeclared host, and the refusal names
# the destination.


def _docker_available() -> bool:
    from test_runtime_conformance import docker_available

    return docker_available()


def test_sealed_docker_run_cannot_reach_an_undeclared_host(tmp_path, monkeypatch):
    if not _docker_available():
        pytest.skip("docker daemon not available")

    from torve.adapters.runtime.docker import DockerRuntime

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    network = f"torve-sealed-{uuid.uuid4().hex[:8]}"
    config = BrokerConfig(
        adapter="local",
        mode="sealed",
        network=network,
        providers={PROVIDER: BrokerProvider(upstream="https://api.example.com", key_env=KEY_ENV)},
    )
    broker = LocalBroker(config)
    handle = broker.open(
        "T-0106", routing_for("https://api.example.com"), BrokerBudget(tokens=1000)
    )
    runtime = DockerRuntime(network=network)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    try:
        sandbox = runtime.create(sealed_spec(), workspace)

        try:
            routes = runtime.exec(sandbox, "cat /proc/net/route", 30)
            lines = routes.output.splitlines()

            assert not any(line.split()[1] == "00000000" for line in lines[1:]), routes.output

            probe = (
                "python -c \""
                "import os,re,socket;"
                "m=re.match(r'http://([^:]+):(\\d+)',os.environ['http_proxy']);"
                "s=socket.create_connection((m.group(1),int(m.group(2))),timeout=5);"
                "s.sendall(b'CONNECT undeclared.example.com:443 HTTP/1.1\\r\\n"
                "Host: undeclared.example.com:443\\r\\n\\r\\n');"
                "chunks=[];\n"
                "while True:\n"
                "    data=s.recv(4096)\n"
                "    if not data: break\n"
                "    chunks.append(data)\n"
                "print(b''.join(chunks).decode())\""
            )
            refused = runtime.exec(sandbox, probe, 30)

            assert refused.exit_code == 0, refused.output
            assert "403" in refused.output, refused.output
            assert "undeclared.example.com" in refused.output, refused.output
        finally:
            runtime.destroy(sandbox)
    finally:
        usage = broker.close(handle)
        assert usage.refusals.get("containment", 0) >= 1
