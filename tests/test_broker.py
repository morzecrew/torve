"""S-0021 phase 1: the broker port, its local adapter in endpoint mode, and
the runner's custody wiring — a brokered run's sandbox holds no provider
key. The local adapter is exercised for real over loopback (a fake upstream
provider on an ephemeral port); a sandbox reaching the broker over the
Docker default bridge is integration-tested in the same skips the rest of
the suite uses. S-0041 phase 2 joins it: remote endpoint mode — the
configured bind, the advertised routes, and the pass-through refusal a
remote run gets instead of sealed mode's topology.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import opensandbox_stub
import pytest
import yaml
from conftest import seam
from forze.application.execution import DepsRegistry, ExecutionRuntime
from pydantic import ValidationError
from typer.testing import CliRunner

from torve.adapters.agent.harness import HarnessAgent
from torve.adapters.broker import build_broker
from torve.adapters.broker.local import LocalBroker
from torve.adapters.broker.none import NoneBroker
from torve.adapters.eventstore.document import mock_module
from torve.adapters.runtime.opensandbox import OpenSandboxRuntime
from torve.application.eventlog import burn_sink, event_log
from torve.application.ports import (
    PROXY_ENV,
    AgentContext,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    BurnEvent,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
)
from torve.application.telemetry import broker_block, config_hash
from torve.base import naming
from torve.cli import app
from torve.config.runconfig import (
    BrokerConfig,
    BrokerProvider,
    OpenSandboxConfig,
    ProvidersConfig,
    RunnerConfig,
    RuntimeConfig,
    TierConfig,
)
from torve.domain.events import EventKind
from torve.domain.states import TaskState
from torve.domain.task import Budget, Scope, Task

# ----------------------- #

KEY_ENV = "TORVE_BROKER_TEST_KEY"
PROVIDER = "test-vendor"


# ....................... #


def broker_config(upstream: str, adapter: str = "local", **overrides) -> BrokerConfig:
    return BrokerConfig(
        adapter=adapter,
        providers={PROVIDER: BrokerProvider(upstream=upstream, key_env=KEY_ENV)},
        **overrides,
    )


def routing_for(upstream: str) -> BrokerRouting:
    return BrokerRouting(
        routes=(BrokerRoute(provider=PROVIDER, upstream=upstream, key_env=KEY_ENV),)
    )


def broker_post(url: str, token: str, body: bytes = b"{}") -> tuple[int, str]:
    request = Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")

    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


# ....................... #
# Configuration (S-0021/D-1, S-0021/D-2, S-0021/D-3, S-0021/D-9)


def test_none_broker_is_the_phase_one_default():
    assert RunnerConfig().broker.adapter == "none"
    assert RunnerConfig().broker.mode == "endpoint"


def test_a_seat_cannot_name_a_credential_at_all():
    """S-0064/D-9 closed this structurally. There used to be a validator refusing
    `api_key_env` on a brokered seat — a second channel for the key is the leak
    the broker exists to remove — and the field it refused no longer exists: a
    credential is a property of the provider, so there is nowhere to name one."""

    with pytest.raises(ValidationError, match="api_key_env"):
        TierConfig(adapter="api", provider=PROVIDER, api_key_env=[KEY_ENV])


def test_a_brokered_seat_is_handed_no_provider_key():
    """What the retired refusal was protecting, now a property of the code that
    hands a sandbox its environment rather than a check somewhere else."""

    from torve.config.providers import Model, Provider, Route
    from torve.config.runconfig import credential_names

    record = Provider(
        name=PROVIDER,
        key_env=KEY_ENV,
        routes={"openai": Route(base_url="https://p.test/v1")},
        models={"fast": Model()},
    )
    tier = TierConfig(adapter="api", provider=PROVIDER, api=["openai"], model="fast")
    brokered = RunnerConfig(
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        broker=broker_config("http://127.0.0.1:1"),
        provider_records={PROVIDER: record},
    )

    assert credential_names(brokered, tier) == ()

    # Unbrokered, the same seat is handed the provider's variable by name.
    direct = RunnerConfig(
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        provider_records={PROVIDER: record},
    )

    assert credential_names(direct, tier) == (KEY_ENV,)


def test_opensandbox_adapter_is_refused_until_a_server_exists():
    # S-0021/D-2 / S-0021/out-of-scope: the adapter is named and deliberately unbuilt —
    # condition-gated on a live server, never a prerequisite.
    with pytest.raises(ValidationError, match="opensandbox"):
        BrokerConfig(adapter="opensandbox")


def test_sealed_mode_requires_a_named_internal_network():
    # S-0021/D-3's phasing: endpoint closes custody now; sealed adds
    # containment — and configuring it must name the internal network the
    # sandbox joins, not silently run as an endpoint.
    with pytest.raises(ValidationError, match="internal Docker network"):
        BrokerConfig(adapter="local", mode="sealed")

    sealed = BrokerConfig(
        adapter="local", mode="sealed", network="torve-sealed", pass_through=["pypi.org"]
    )
    assert sealed.mode == "sealed"
    assert sealed.network == "torve-sealed"


def test_broker_provider_requires_wire_facts():
    with pytest.raises(ValidationError, match="http\\(s\\) base URL"):
        BrokerProvider(upstream="api.example.com", key_env=KEY_ENV)

    with pytest.raises(ValidationError, match="key_env"):
        BrokerProvider(upstream="https://api.example.com", key_env="")


# ....................... #
# The port's adapters (S-0021/D-2)


def test_none_broker_is_today_behavior_named():
    broker = NoneBroker()
    handle = broker.open("run-1", routing_for("http://127.0.0.1:1"), BrokerBudget())
    assert broker.name == "none"
    assert handle.base_urls == {}
    assert broker.usage(handle) == broker.close(handle)
    assert broker.close(handle).requests == 0  # idempotent


def test_build_broker_selects_the_adapter():
    assert build_broker(broker_config("http://127.0.0.1:1", adapter="none")).name == "none"
    assert build_broker(broker_config("http://127.0.0.1:1")).name == "local"


# ....................... #
# The local adapter: routing, injection, metering (S-0021/D-4, S-0021/D-5, S-0021/D-7)


def test_local_broker_routes_injects_and_meters(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget())

    assert handle.url_for(PROVIDER) is not None
    assert handle.url_for("other-vendor") is None
    assert handle.token

    status, body = broker_post(handle.url_for(PROVIDER) + "/v1/chat/completions", handle.token)
    assert status == 200
    assert json.loads(body)["model"] == "fake-model-9"

    # The wire credential is the provider key, injected by the broker — the
    # sandbox's run token never travels past it (S-0001/D-13).
    assert state["auth"] == ["Bearer k-123-secret"]
    assert state["paths"] == ["/v1/chat/completions"]
    assert state["requests"] == 1

    usage = broker.close(handle)
    assert usage.requests == 1
    assert usage.tokens_per_provider == {PROVIDER: 5}
    assert usage.cost_usd == 0.01
    assert usage.refusals == {}
    assert usage.wall_time_s >= 0


def test_wire_refuses_an_unrouted_provider(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget())

    unrouted = handle.url_for(PROVIDER).replace(f"/{PROVIDER}", "/other-vendor") + "/v1/x"
    status, body = broker_post(unrouted, handle.token)
    assert status == 403
    assert json.loads(body)["error"]["cause"] == "routing"
    assert state["requests"] == 0  # nothing reached the provider

    usage = broker.close(handle)
    assert usage.refusals == {"routing": 1}
    assert usage.refused_providers == {"other-vendor": 1}


def test_wire_refuses_without_the_run_token(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget())

    status, _ = broker_post(handle.url_for(PROVIDER) + "/v1/x", token="forged")
    assert status == 401

    usage = broker.close(handle)
    assert usage.refusals == {"auth": 1}


def test_budget_exhaustion_refuses_mid_run(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 50}
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget(tokens=50))

    # The first request measures 50 tokens — exactly the bound, so the
    # second is refused in progress (S-0021/D-6).
    assert broker_post(handle.url_for(PROVIDER) + "/v1/x", handle.token)[0] == 200
    status, body = broker_post(handle.url_for(PROVIDER) + "/v1/x", handle.token)
    assert status == 429
    assert json.loads(body)["error"]["cause"] == "budget"

    usage = broker.close(handle)
    assert usage.requests == 1
    assert usage.refusals == {"budget": 1}


def test_a_zero_budget_refuses_everything(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget(tokens=0))

    status, _ = broker_post(handle.url_for(PROVIDER) + "/v1/x", handle.token)
    assert status == 429


def test_open_refuses_a_missing_key(upstream, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    _, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")

    with pytest.raises(RuntimeError, match=KEY_ENV):
        broker.open("run-1", routing_for(upstream_url), BrokerBudget())


def test_the_broker_keeps_counts_and_metadata_never_bodies(upstream, monkeypatch):
    # S-0021/D-7: request and response bodies are read to forward and meter,
    # then discarded — the broker's state after close is counts only.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    marker = "prompt-that-must-not-be-kept"
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget())

    broker_post(handle.url_for(PROVIDER) + "/v1/x", handle.token, body=marker.encode())

    usage = broker.close(handle)
    serialized = json.dumps(broker_block(broker.name, usage))

    assert state["body"] == marker  # the provider saw it...
    assert marker not in serialized  # ...the broker kept none of it
    assert set(broker_block(broker.name, usage)) == {
        "adapter",
        "requests",
        "tokens_per_provider",
        "cost_usd",
        "wall_time_s",
        "refusals",
    }


# ....................... #
# Remote endpoint mode (S-0041/D-6): `broker.bind` replaces the bridge-gateway
# derivation, `broker.advertise` is the address the sandboxes are told, the
# provider routes keep the run token across the hop, and the pass-through
# leg — sealed mode's topology-authenticated relay — is refused loudly with
# the destination named.


def free_bind() -> str:
    """A loopback port just released, to be taken by the broker."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe.getsockname()[1]}"


def connect_through(host: str, port: int, authority: str) -> tuple[str, bytes]:
    """CONNECT through the broker and read the full refusal — status line
    and Content-Length sized body — so the JSON naming the destination can
    be asserted."""

    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(10)
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

    sock.close()

    return head.split(b"\r\n", 1)[0].decode("ascii", "replace"), rest


def absolute_get(host: str, port: int, target: str, authority: str) -> tuple[str, bytes]:
    """A plain-http absolute-URI request through the broker's proxy port,
    read to close (the forward-proxy form a sandbox's http client sends)."""

    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(10)
    sock.sendall(
        f"GET {target} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n".encode("ascii")
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


def test_remote_endpoint_publishes_routes_at_the_advertised_address(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    bind = free_bind()
    broker = LocalBroker(
        broker_config(upstream_url, bind=bind, advertise="broker.example.net:9443")
    )
    handle = broker.open("run-remote", routing_for(upstream_url), BrokerBudget())

    # The route reaches the sandbox at the advertised address, verbatim —
    # the NAT/hostname split is the whole point of the second knob.
    assert handle.url_for(PROVIDER) == "http://broker.example.net:9443/test-vendor"

    # Provider routes keep the run token unchanged (S-0041/D-2): on the bind
    # socket the broker is the same token-authenticated reverse proxy, the
    # key injected from its own environment and never handed over.
    status, body = broker_post(f"http://{bind}/{PROVIDER}/v1/chat/completions", handle.token)
    assert status == 200
    assert json.loads(body)["model"] == "fake-model-9"
    assert state["auth"] == ["Bearer k-123-secret"]

    assert broker_post(f"http://{bind}/{PROVIDER}/v1/x", token="forged")[0] == 401

    usage = broker.close(handle)
    assert usage.requests == 1
    assert usage.refusals == {"auth": 1}


def test_remote_endpoint_advertises_bind_verbatim_without_a_split(monkeypatch, upstream):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    bind = free_bind()
    broker = LocalBroker(broker_config(upstream_url, bind=bind))
    handle = broker.open("run-remote", routing_for(upstream_url), BrokerBudget())

    assert handle.url_for(PROVIDER) == f"http://{bind}/{PROVIDER}"

    broker.close(handle)


def test_remote_endpoint_refuses_connect_naming_the_destination(monkeypatch, upstream):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    bind = free_bind()
    host, _, port = bind.partition(":")
    broker = LocalBroker(broker_config(upstream_url, bind=bind))
    handle = broker.open("run-remote", routing_for(upstream_url), BrokerBudget())

    status, body = connect_through(host, int(port), "pypi.org:443")
    assert "403" in status

    refusal = json.loads(body)["error"]
    assert refusal["cause"] == "pass_through"
    assert refusal["destination"] == "pypi.org:443"
    # The rule travels with the refusal — no corpus coordinates on the wire.
    assert "remote endpoint" in refusal["message"]
    assert "token-authenticated" in refusal["message"]

    usage = broker.close(handle)
    assert usage.refusals == {"pass_through": 1}
    assert usage.refused_providers == {"pypi.org:443": 1}


def test_remote_endpoint_refuses_an_absolute_uri_naming_the_destination(monkeypatch, upstream):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    bind = free_bind()
    host, _, port = bind.partition(":")
    broker = LocalBroker(broker_config(upstream_url, bind=bind))
    handle = broker.open("run-remote", routing_for(upstream_url), BrokerBudget())

    status, body = absolute_get(host, int(port), "http://example.org/simple/", "example.org")
    assert "403" in status

    refusal = json.loads(body)["error"]
    assert refusal["cause"] == "pass_through"
    assert refusal["destination"] == "http://example.org/simple/"

    usage = broker.close(handle)
    assert usage.refusals == {"pass_through": 1}


def test_local_endpoint_connect_refusal_stays_routing(upstream, monkeypatch):
    # The no-bind endpoint behaviour is unchanged: a broker with a loopback
    # host override is not in remote mode, and its CONNECT refusal keeps
    # counting under routing.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-local", routing_for(upstream_url), BrokerBudget())

    route = urlsplit(handle.url_for(PROVIDER))
    status, body = connect_through("127.0.0.1", route.port or 0, "pypi.org:443")
    assert "403" in status
    assert json.loads(body)["error"]["cause"] == "routing"

    usage = broker.close(handle)
    assert usage.refusals == {"routing": 1}


def test_the_remote_bind_joins_the_egress_regime(tmp_path):
    # S-0021/D-8: where the broker listens and what it advertises are part of
    # what a number was measured under.
    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    local = RunnerConfig(broker=broker_config("https://api.example.com"))
    remote = RunnerConfig(
        broker=broker_config(
            "https://api.example.com", bind="0.0.0.0:8321", advertise="broker.example.net:8321"
        )
    )

    assert config_hash(manifest, tmp_path, local) != config_hash(manifest, tmp_path, remote)


def test_advertised_address_reaches_the_sandbox_proxy_env(tmp_path, monkeypatch):
    # The runtime composes the proxy env from the advertised address
    # instead of the Docker-gateway derivation — verbatim, and instead of
    # the runner's own (loopback, meaningless out there) proxy.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    config = RunnerConfig(
        runtime=RuntimeConfig(adapter="opensandbox"),
        broker=BrokerConfig(
            adapter="local", bind="0.0.0.0:8321", advertise="broker.example.net:9443"
        ),
    )
    runtime = OpenSandboxRuntime(config.runtime.opensandbox, sdk=opensandbox_stub)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = SandboxSpec(
        name="torve-remote-proxy",
        image="python:3.13-slim",
        labels=naming.labels("T-9253", "r", Path.cwd()),
        timeout_s=60,
        workdir=str(tmp_path / "remote"),
    )
    handle = runtime.create(spec, workspace)

    try:
        recorded = opensandbox_stub.REGISTRY[handle.id].env
        assert recorded["http_proxy"] == "http://broker.example.net:9443"
        assert recorded["HTTPS_PROXY"] == "http://broker.example.net:9443"
        assert recorded["all_proxy"] == "http://broker.example.net:9443"
        # The broker's own address is excluded: the provider routes speak
        # to it directly, run token in hand.
        assert recorded["NO_PROXY"] == "127.0.0.1,localhost,broker.example.net"
    finally:
        runtime.destroy(handle)

    opensandbox_stub.REGISTRY.clear()


def test_sandbox_proxy_env_stays_forwarded_without_a_bind(tmp_path, monkeypatch):
    # The adapter forwards the host's own proxy variables by name, so the
    # test owns that environment rather than inheriting it: on a developer
    # machine behind a proxy the lowercase names are set, and the assertion
    # below would read the host's configuration as something torve composed.
    for name in PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = SandboxSpec(
        name="torve-local-proxy",
        image="python:3.13-slim",
        labels=naming.labels("T-9254", "r", Path.cwd()),
        timeout_s=60,
        workdir=str(tmp_path / "remote"),
    )
    handle = runtime.create(spec, workspace)

    try:
        recorded = opensandbox_stub.REGISTRY[handle.id].env
        assert recorded["HTTP_PROXY"] == "http://127.0.0.1:9999"
        assert "http_proxy" not in recorded  # nothing composed without a remote endpoint
        assert "no_proxy" not in recorded
    finally:
        runtime.destroy(handle)

    opensandbox_stub.REGISTRY.clear()


# ....................... #
# The tier command's substitution (S-0021/the-port)


def harness_ctx(tmp_path: Path, tier: TierConfig, handle: BrokerHandle | None) -> AgentContext:
    workspace = tmp_path / "wt"
    workspace.mkdir(parents=True, exist_ok=True)
    task = Task(id="T-9199", intent="x", scope=Scope(allow=["src/**"]), decisions=[])
    return AgentContext(
        task=task,
        attempt=1,
        workspace=workspace,
        handle=SandboxHandle(id="h", name="h"),
        runtime=None,  # type: ignore[arg-type]  # only _command is exercised
        workdir=str(workspace),
        timeout_s=30.0,
        broker=handle,
    ), HarnessAgent(tier)


def test_a_brokered_seat_dials_the_route_and_is_told_which_variable_holds_the_key(tmp_path):
    """S-0064/D-8. The broker's two variables are gone; what an image gets is
    one base URL and the name of one variable, and the run-scoped token rides in
    the variable that name points at."""

    tier = TierConfig(adapter="api", provider=PROVIDER, model="m", image="probe-sandbox")
    handle = BrokerHandle(
        token="run-token", base_urls={PROVIDER: "http://127.0.0.1:9999/test-vendor"}
    )
    ctx, agent = harness_ctx(tmp_path, tier, handle)
    env = agent._env(ctx)

    assert env["TORVE_BASE_URL"] == "http://127.0.0.1:9999/test-vendor"
    assert env["TORVE_API_KEY_ENV"] == "TORVE_RUN_TOKEN"
    assert env["TORVE_RUN_TOKEN"] == "run-token"
    assert "TORVE_BROKER_URL" not in env and "TORVE_BROKER_TOKEN" not in env


def test_a_direct_seat_dials_its_record_and_names_its_provider_s_variable(tmp_path):
    """The same two variables, different values — which is the point. An image
    has nothing to branch on, so mimo's refusal of a brokered seat became a
    deletion rather than an implementation.

    The credential is named, never carried: `docker -e NAME` reads the value out
    of the invoking environment, so the secret never transits torve or the spec.
    """

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="m",
        image="probe-sandbox",
        base_url="https://vendor.example/v1",
        key_env="VENDOR_API_KEY",
    )
    ctx, agent = harness_ctx(tmp_path, tier, None)
    env = agent._env(ctx)

    assert env["TORVE_BASE_URL"] == "https://vendor.example/v1"
    assert env["TORVE_API_KEY_ENV"] == "VENDOR_API_KEY"
    assert "TORVE_RUN_TOKEN" not in env

    # The none adapter's handle routes nothing, which is the same as no handle.
    routes_nothing, agent = harness_ctx(
        tmp_path / "none", tier, BrokerHandle(token="", base_urls={})
    )

    assert agent._env(routes_nothing)["TORVE_BASE_URL"] == "https://vendor.example/v1"


def test_the_seam_carries_what_the_record_measured(tmp_path):
    """The numbers an image would otherwise have had to be rebuilt to change
    (S-0064/D-7), in torve's own units — and absent rather than zero where
    nobody measured one."""

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="short",
        model_id="vendor/long-slug-nobody-wants-to-type",
        image="probe-sandbox",
        api=["openai"],
        context_window=1000000,
        max_tokens=65536,
        reasoning="medium",
        request_timeout_s=600,
    )
    ctx, agent = harness_ctx(tmp_path, tier, None)
    env = agent._env(ctx)

    # What travels is the id, not the shorthand the seat writes (S-0064/D-3).
    assert env["TORVE_MODEL"] == "vendor/long-slug-nobody-wants-to-type"
    assert env["TORVE_PROVIDER"] == PROVIDER and env["TORVE_API"] == "openai"
    assert env["TORVE_CONTEXT_WINDOW"] == "1000000"
    assert env["TORVE_REASONING"] == "medium"
    assert env["TORVE_REQUEST_TIMEOUT_S"] == "600"
    assert "TORVE_STREAM_IDLE_TIMEOUT_S" not in env


def test_harness_refuses_a_provider_the_broker_does_not_route(tmp_path):
    """The refusal that survives the placeholders: a brokered run whose
    routing is missing the seat's provider is a configuration error, not a
    seat that quietly reaches the provider itself."""

    tier = TierConfig(
        adapter="api",
        provider="unrouted-vendor",
        route="unrouted-vendor.openai",
        model="m",
        image="probe-sandbox",
    )
    ctx, agent = harness_ctx(
        tmp_path, tier, BrokerHandle(token="t", base_urls={PROVIDER: "http://127.0.0.1:1/x"})
    )

    with pytest.raises(ValueError, match="but not the tier's route"):
        agent._command(ctx)


def test_the_command_is_the_image_s_two_scripts(tmp_path):
    """S-0063/D-1: the engine names the attempt and invokes `equip` then
    `run`; what a harness does with either is the image's."""

    tier = TierConfig(adapter="api", provider=PROVIDER, model="m", image="probe-sandbox")
    ctx, agent = harness_ctx(
        tmp_path, tier, BrokerHandle(token="t", base_urls={PROVIDER: "http://127.0.0.1:1/x"})
    )
    command = agent._command(ctx)

    assert command.endswith("/opt/torve/equip && /opt/torve/run")
    assert "TORVE_PROMPT=" in command
    # The token is exported, not spliced into a flag someone has to quote.
    assert "TORVE_RUN_TOKEN=t" in command


# ....................... #
# The regime hash (S-0021/D-8) and the doctor (S-0021/D-9)


def test_config_hash_moves_with_the_broker_block(tmp_path):
    manifest = tmp_path / "gates.yaml"
    manifest.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    plain = RunnerConfig()
    brokered = RunnerConfig(broker=broker_config("https://api.example.com"))

    assert config_hash(manifest, tmp_path, plain) != config_hash(manifest, tmp_path, brokered)
    assert config_hash(manifest, tmp_path, brokered) == config_hash(manifest, tmp_path, brokered)

    # The routing is part of the regime: a different route table is a
    # different regime, and key names (never values) are what moves it.
    rerouted = RunnerConfig(
        broker=BrokerConfig(
            adapter="local",
            providers={
                PROVIDER: BrokerProvider(upstream="https://api.other.example", key_env=KEY_ENV)
            },
        )
    )
    assert config_hash(manifest, tmp_path, brokered) != config_hash(manifest, tmp_path, rerouted)


def _doctor_repo(tmp_path: Path, config: dict, record: dict | None = None) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, **config}), encoding="utf-8"
    )

    # The wire facts the broker routes on are a provider record now
    # (S-0064/D-1), and `broker.providers` in the configuration is refused.
    if record is not None:
        (root / ".torve" / "providers").mkdir()
        (root / ".torve" / "providers" / f"{PROVIDER}.yaml").write_text(
            yaml.safe_dump(record), encoding="utf-8"
        )

    return root


def _seated(tmp_path: Path, config: dict, record: dict) -> Path:
    """A repository whose record has a seat on it. The broker routes what seats
    reach rather than what the records declare (S-0064/D-4), so a provider
    nothing is seated on is deliberately not routed."""

    root = _doctor_repo(tmp_path, config, record=record)
    (root / ".torve" / "harnesses").mkdir()
    (root / ".torve" / "harnesses" / "fake.yaml").write_text("adapter: fake\n", encoding="utf-8")
    (root / ".torve" / "harnesses" / "h.yaml").write_text(
        "adapter: api\napi: [openai]\nimage: i\n", encoding="utf-8"
    )
    body = yaml.safe_load((root / ".torve" / "config.yaml").read_text())
    body["tiers"] = {
        "planner": {"harness": "fake"},
        "reviewer": {"harness": "fake"},
        "executor": {"harness": "h", "provider": PROVIDER, "model": "fast"},
    }
    (root / ".torve" / "config.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    return root


def test_doctor_names_the_none_broker_and_its_ceiling(tmp_path):
    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "opensandbox"}})
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["broker"]["ok"] is True
    assert "none" in checks["broker"]["detail"]
    assert "credential-custody requirement unmet" in checks["broker"]["detail"]


def test_doctor_names_the_local_broker_in_force(tmp_path):
    root = _seated(
        tmp_path,
        {"runtime": {"adapter": "opensandbox"}, "broker": {"adapter": "local"}},
        {
            "key_env": KEY_ENV,
            "routes": {"openai": {"base_url": "https://api.example.com"}},
            "models": {"fast": {}},
        },
    )
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["broker"]["ok"] is True
    assert "local" in checks["broker"]["detail"]
    assert PROVIDER in checks["broker"]["detail"]


# ....................... #
# The runner's custody wiring (S-0021/D-1, S-0021/D-6): host-side, with the tier
# command running on the host against the loopback broker


class HostRuntime:
    """The sandbox's network view is the host's — the loopback broker is
    reachable exactly as in a host-mode Docker sandbox."""

    def __init__(self) -> None:
        self.cwd = "/tmp"
        self.specs: list[object] = []

    def create(self, spec, workspace: Path) -> SandboxHandle:
        self.cwd = str(workspace)
        self.specs.append(spec)
        return SandboxHandle(id=f"h-{uuid.uuid4().hex[:8]}", name=spec.name)

    def exec(self, handle, command: str, timeout_s: float) -> ExecResult:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=self.cwd,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            check=False,
        )
        return ExecResult(
            exit_code=proc.returncode,
            output=(proc.stdout or "") + (proc.stderr or ""),
            duration_s=0.0,
        )

    def sync_out(self, handle, workspace: Path) -> None:
        pass

    def destroy(self, handle) -> None:
        pass

    def list_torve_sandboxes(self):
        return []

    def destroy_by_id(self, sandbox_id: str) -> None:
        pass

    def resolve_image(self, image: str) -> None:
        return None


class HostVcs:
    """The landing hook's git surface: a stub commit that never pushes."""

    def commit_all(
        self, worktree, message: str, author: str | None = None, sign_key: str | None = None
    ) -> str:
        return "0" * 40

    def changed_names(self, worktree) -> list[str]:
        return []

    def push(
        self, worktree, branch: str, token: str | None = None, supersede: bool = False
    ) -> bool:
        return False

    def republish_branch(self, root, branch: str, token: str | None = None) -> bool:
        return False

    def landed_shas(self, worktree, task_id: str) -> list[str]:
        return []

    def revert(self, worktree, shas: list[str]) -> bool:
        return True


def _runner_deps(runtime, agent, broker):
    from torve.application.dispatch import RunDeps

    return RunDeps(
        workspace=None,  # type: ignore[arg-type]  # only attempt/gates/land hooks run
        runtime=runtime,
        agent=agent,
        vcs=HostVcs(),
        scm=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        broker=broker,
    )


def _drive_task(tmp_path: Path, config: RunnerConfig, task: Task, deps) -> object:
    from torve.application.runner import drive_attempts, real_hooks
    from torve.application.runstate import RunState

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".torve" / "skills").mkdir(parents=True)
    (worktree / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\ngates: []\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-q", "--no-gpg-sign", "-m", "base"], check=True
    )

    state = RunState(task_id=task.id, path=tmp_path / f"{task.id}.state.json")
    state.transition(TaskState.CLAIMED, "test claim")
    hooks = real_hooks(tmp_path, task, config, deps, worktree)

    return asyncio.run(drive_attempts(state, task, config, hooks)), worktree


def _two_request_body() -> str:
    """Two requests through the broker, from what the image was told
    (S-0063/D-5): the token and the URL are variables now."""

    return (
        'python3 -c "import os,urllib.request,json;'
        "H={'Authorization':'Bearer '+os.environ['TORVE_RUN_TOKEN'],"
        "'Content-Type':'application/json'};"
        "D=json.dumps({'model':'x'}).encode();"
        "U=os.environ['TORVE_BASE_URL']+'/v1/chat/completions';"
        "print(urllib.request.urlopen(urllib.request.Request(U,data=D,headers=H)).read().decode());"
        'print(urllib.request.urlopen(urllib.request.Request(U,data=D,headers=H)).read().decode())"'
    )


def test_brokered_attempt_escalates_cost_anomaly_on_budget_refusal(tmp_path, upstream, monkeypatch):
    # S-0021/D-6 end to end: the budget is held by the broker, the refusal
    # happens mid-attempt, and the run escalates cost_anomaly in progress.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        env=seam(_two_request_body(), monkeypatch),
    )
    config = RunnerConfig(
        poison_ceiling=3,
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        providers=ProvidersConfig(default=[PROVIDER]),
        broker=broker_config(upstream_url),
    )
    task = Task(
        id="T-9101",
        intent="overspend",
        scope=Scope(allow=["src/**"]),
        decisions=[],
        budget=Budget(tokens=5),  # one request reports 5 tokens -> the second is refused
        tier="executor",
    )
    runtime = HostRuntime()
    deps = _runner_deps(runtime, HarnessAgent(tier), LocalBroker(config.broker, host="127.0.0.1"))

    final, _worktree = _drive_task(tmp_path, config, task, deps)

    assert final.state is TaskState.ESCALATED
    assert final.escalation.reason == "cost_anomaly"
    assert "token budget" in final.escalation.detail
    # The sandbox spec carried no key name: the broker is the one channel
    # (S-0021/D-1) — the tier's key env is not forwarded into the sandbox.
    assert runtime.specs[0].env_passthrough == ()
    assert KEY_ENV not in str(runtime.specs[0])


def test_brokered_attempt_reaches_ready_and_records_both_costs(tmp_path, upstream, monkeypatch):
    # S-0021/D-5: the adapter's self-reported cost and the broker's measured
    # cost both ride the run's record — with a divergence past tolerance
    # visible as an engine event.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        env=seam(
            # S-0063/D-5: the broker's URL and the run-scoped token reach the image
            # as two variables, where a placeholder in a shell string carried them.
            'python3 -c "import os,urllib.request,json;'
            "H={'Authorization':'Bearer '+os.environ['TORVE_RUN_TOKEN'],"
            "'Content-Type':'application/json'};"
            "D=json.dumps({'model':'x'}).encode();"
            "print(urllib.request.urlopen(urllib.request.Request("
            "os.environ['TORVE_BASE_URL']+'/v1/chat/completions',data=D,headers=H)).read().decode());"
            "print(json.dumps({'total_cost_usd':0.5,'model':'fake-model-9'}));"
            "print('ok')\" "
            "&& mkdir -p src && echo FEATURE = True > src/feature.py",
            monkeypatch,
        ),
        image="probe-sandbox",
    )
    # S-0064/D-8: the route and the run-scoped token reach the image as
    # `TORVE_BASE_URL` and whatever `TORVE_API_KEY_ENV` names, which is the
    # same pair a direct seat gets with different values.
    config = RunnerConfig(
        poison_ceiling=3,
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        providers=ProvidersConfig(default=[PROVIDER]),
        broker=broker_config(upstream_url),
    )
    task = Task(
        id="T-9102",
        intent="happy path",
        scope=Scope(allow=["src/**"]),
        decisions=[],
        tier="executor",
    )
    runtime = HostRuntime()
    deps = _runner_deps(runtime, HarnessAgent(tier), LocalBroker(config.broker, host="127.0.0.1"))

    final, _worktree = _drive_task(tmp_path, config, task, deps)

    assert final.state is TaskState.READY, final.history
    # The attempt record's agent block carries the broker's counts beside
    # the adapter's report (S-0021/D-5) — the adapter claimed 0.5, the broker
    # measured 0.01 from the provider's response.
    record = _last_attempt_record(tmp_path)
    assert record["agent"]["cost_usd"] == 0.5
    assert record["agent"]["broker"]["adapter"] == "local"
    assert record["agent"]["broker"]["requests"] == 1
    assert record["agent"]["broker"]["cost_usd"] == 0.01
    assert record["agent"]["broker"]["tokens_per_provider"] == {PROVIDER: 5}


def _last_attempt_record(root: Path) -> dict:
    telemetry = root / ".torve" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert records, "no telemetry written"
    events = [
        r for r in records if r.get("kind") == "engine" and r.get("event") == "cost_divergence"
    ]
    assert events, "the divergence past tolerance must be an engine event (S-0021/D-5)"
    return next(r for r in records if r.get("kind") != "engine")


# ....................... #
# End to end against the real Docker daemon: a sandbox on the default bridge
# reaching the broker at the bridge gateway, holding no key (S-0021/tests).


def _docker_available() -> bool:
    from test_runtime_conformance import docker_available

    return docker_available()


def test_brokered_docker_run_sandbox_holds_no_key(repo, upstream, monkeypatch):
    if not _docker_available():
        pytest.skip("docker daemon not available")

    from test_run_integration import seed_run_repo

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}
    seed_run_repo(repo)

    from torve.adapters.runtime.docker import DockerRuntime
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.dispatch import RunDeps
    from torve.application.runner import run_task
    from torve.config.runconfig import RuntimeConfig
    from torve.gates.context import load_task
    from torve.gates.sabotage import TASK_ID

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        env=seam(
            'python3 -c "import os,urllib.request,json;'
            "H={'Authorization':'Bearer '+os.environ['TORVE_RUN_TOKEN'],"
            "'Content-Type':'application/json'};"
            "D=json.dumps({'model':'x'}).encode();"
            "print(urllib.request.urlopen(urllib.request.Request("
            "os.environ['TORVE_BASE_URL']+'/v1/chat/completions',data=D,headers=H)).read().decode())\" "
            "&& mkdir -p src && echo FEATURE = True > src/feature.py",
            monkeypatch,
        ),
    )
    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        providers=ProvidersConfig(default=[PROVIDER]),
        broker=broker_config(upstream_url),
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=HarnessAgent(tier),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
        broker=LocalBroker(config.broker),  # the sandbox reaches it at the bridge gateway
    )
    task = load_task(repo.root / ".torve" / "tasks" / TASK_ID / "contract.yaml")

    run_state = run_task(repo.root, task, config, deps)

    assert run_state.state is TaskState.READY, run_state.history
    record = json.loads((repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()[-1])
    assert record["agent"]["broker"]["adapter"] == "local"
    assert record["agent"]["broker"]["requests"] == 1


def test_brokered_docker_budget_refusal_escalates_cost_anomaly(repo, upstream, monkeypatch):
    if not _docker_available():
        pytest.skip("docker daemon not available")

    from test_run_integration import seed_run_repo

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}
    seed_run_repo(repo)

    from torve.adapters.runtime.docker import DockerRuntime
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.dispatch import RunDeps
    from torve.application.runner import run_task
    from torve.config.runconfig import RuntimeConfig
    from torve.gates.context import load_task
    from torve.gates.sabotage import TASK_ID

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        env=seam(_two_request_body(), monkeypatch),
    )
    config = RunnerConfig(
        runtime=RuntimeConfig(sandbox_timeout=300, agent_timeout=90),
        poison_ceiling=2,
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
        providers=ProvidersConfig(default=[PROVIDER]),
        broker=broker_config(upstream_url),
    )
    deps = RunDeps(
        workspace=GitWorkspace(repo.root),
        runtime=DockerRuntime(),
        agent=HarnessAgent(tier),
        vcs=GitVcs(),
        scm=NullScm(),
        store=open_store,
        broker=LocalBroker(config.broker),
    )
    contract = repo.root / ".torve" / "tasks" / TASK_ID / "contract.yaml"
    document = yaml.safe_load(contract.read_text(encoding="utf-8"))
    document["budget"] = {"tokens": 5}
    contract.write_text(yaml.safe_dump(document), encoding="utf-8")
    task = load_task(contract)

    run_state = run_task(repo.root, task, config, deps)

    assert run_state.state is TaskState.ESCALATED
    assert run_state.escalation.reason == "cost_anomaly"


def test_none_broker_dispatches_a_real_tier_with_no_provider_table():
    """The phase-1 default must not demand routing it will never enforce:
    a real harness tier dispatches under `none` with an empty
    broker.providers — the regression that broke every shadow run the day
    phase 1 landed."""
    from torve.application.dispatch import run_routing
    from torve.config.runconfig import RunnerConfig
    from torve.domain.task import Task

    config = RunnerConfig.model_validate(
        {
            "schema_version": 1,
            "tiers": {
                "executor": {
                    "adapter": "harness",
                    "image": "probe-sandbox",
                    "model": "m",
                    "provider": "deepseek",
                }
            },
        }
    )
    routing = run_routing(config, Task(id="T-0001", intent="x", decisions=[]), review_on=False)
    assert routing.routes == ()


def test_the_none_handle_leaves_the_command_untouched(tmp_path):
    """The none adapter opens a routeless handle (S-0021/D-9), and a seat under
    it reaches its provider directly. The command is what it always is —
    the image's two scripts — and neither broker variable is set.

    This is the second half of the regression that broke every real-tier run
    when S-0021 phase 1 landed: a routeless handle must not read as a broker."""

    tier = TierConfig(adapter="api", provider=PROVIDER, model="m")
    ctx, agent = harness_ctx(tmp_path, tier, BrokerHandle(token="", base_urls={}))
    command = agent._command(ctx)

    assert command.endswith("/opt/torve/equip && /opt/torve/run")
    assert "TORVE_BROKER" not in command


def test_forward_strips_a_lowercase_authorization_header(upstream, monkeypatch):
    """Node harnesses send `authorization` lowercase; the copy loop kept it
    beside the injected `Authorization`, and the upstream read the run token
    — the 401 that surfaced the first time a real harness went through the
    broker. The incoming credential must never forward, whatever its case."""
    import http.client

    state, base = upstream
    monkeypatch.setenv(KEY_ENV, "real-provider-key")
    broker = LocalBroker(broker_config(base))
    handle = broker.open("T-0001", routing_for(base), BrokerBudget(tokens=None))

    try:
        from urllib.parse import urlsplit

        route = urlsplit(handle.base_urls[PROVIDER])
        conn = http.client.HTTPConnection(route.hostname, route.port, timeout=10)
        # skip_host/putheader keeps the lowercase spelling on the wire.
        conn.putrequest("POST", f"{route.path}/chat/completions")
        conn.putheader("authorization", f"Bearer {handle.token}")
        conn.putheader("Content-Length", "2")
        conn.endheaders()
        conn.send(b"{}")
        answer = conn.getresponse()
        assert answer.status == 200, answer.read()

        assert state["auth"][-1] == "Bearer real-provider-key", state["auth"]
    finally:
        broker.close(handle)


def test_burn_is_emitted_per_call_and_sums_to_the_close_aggregate(upstream, monkeypatch):
    """S-0045 S-0045/D-3: the per-call events and the run's aggregate are the
    same numbers seen twice, so a liveness read and a cost read cannot
    disagree about one run."""

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _state, upstream_url = upstream
    seen: list[BurnEvent] = []
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget(), seen.append)

    for _ in range(3):
        broker_post(handle.url_for(PROVIDER) + "/v1/chat/completions", handle.token)

    usage = broker.close(handle)

    assert len(seen) == 3
    assert {event.provider for event in seen} == {PROVIDER}
    assert sum(event.tokens for event in seen) == usage.tokens_per_provider[PROVIDER]
    assert sum(event.cost_usd or 0 for event in seen) == pytest.approx(usage.cost_usd)


def test_a_refused_call_burns_nothing(upstream, monkeypatch):
    """Liveness must not read a refusal as work: nothing reached a provider,
    so nothing was metered and nothing is emitted."""

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _state, upstream_url = upstream
    seen: list[BurnEvent] = []
    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget(), seen.append)

    unrouted = handle.url_for(PROVIDER).replace(f"/{PROVIDER}", "/other-vendor") + "/v1/x"
    broker_post(unrouted, handle.token)
    broker.close(handle)

    assert seen == []


def test_a_sink_that_raises_never_breaks_the_wire(upstream, monkeypatch):
    """An observer that can break a run is not an observer (S-0045/D-3): the
    request still succeeds and the aggregate is still right."""

    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _state, upstream_url = upstream

    def hostile(_event: BurnEvent) -> None:
        raise RuntimeError("the observer is broken")

    broker = LocalBroker(broker_config(upstream_url), host="127.0.0.1")
    handle = broker.open("run-1", routing_for(upstream_url), BrokerBudget(), hostile)
    status, _body = broker_post(handle.url_for(PROVIDER) + "/v1/chat/completions", handle.token)
    usage = broker.close(handle)

    assert status == 200
    assert usage.requests == 1


def test_the_burn_sink_records_seat_consumed_events():
    """The bridge the manager wires: metering arrives in the log as the
    worker's own record of what a seat cost."""

    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            sink = burn_sink(
                log,
                asyncio.get_running_loop(),
                partition="morzecrew/torve",
                task_id="T-9500",
                seat="executor",
            )
            sink(BurnEvent(provider="test-vendor", tokens=5, cost_usd=0.01))
            await asyncio.sleep(0.05)

            recorded = await log.history("T-9500")

            assert [event.kind for event in recorded] == [EventKind.SEAT_CONSUMED]
            assert recorded[0].typed_payload().model_dump() == {
                "seat": "test-vendor",
                "tokens": 5,
                "cost_usd": 0.01,
            }

    asyncio.run(scenario())
