"""RFC 0021 phase 1: the broker port, its local adapter in endpoint mode, and
the runner's custody wiring — a brokered run's sandbox holds no provider
key. The local adapter is exercised for real over loopback (a fake upstream
provider on an ephemeral port); a sandbox reaching the broker over the
Docker default bridge is integration-tested in the same skips the rest of
the suite uses.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from torve.adapters.agent.harness import HarnessAgent
from torve.adapters.broker import build_broker
from torve.adapters.broker.local import LocalBroker
from torve.adapters.broker.none import NoneBroker
from torve.application.ports import (
    AgentContext,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    ExecResult,
    SandboxHandle,
)
from torve.application.telemetry import broker_block, config_hash
from torve.cli import app
from torve.config.runconfig import (
    BrokerConfig,
    BrokerProvider,
    ProvidersConfig,
    RunnerConfig,
    TierConfig,
)
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


@pytest.fixture
def upstream():
    """(state, base_url) — a fake provider on loopback: reports a usage block
    and a cost, and records what it saw (authorization, path, request count)."""

    state: dict[str, object] = {
        "auth": [],
        "paths": [],
        "requests": 0,
        "usage": {"total_tokens": 5},
        "cost": 0.01,
        "body": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            state["body"] = body.decode("utf-8", errors="replace")
            state["auth"].append(self.headers.get("Authorization", ""))
            state["paths"].append(self.path)
            state["requests"] = int(state["requests"]) + 1
            payload = json.dumps(
                {
                    "usage": state["usage"],
                    "total_cost_usd": state["cost"],
                    "model": "fake-model-9",
                }
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

    yield state, f"http://127.0.0.1:{server.server_address[1]}"

    server.shutdown()
    server.server_close()


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
# Configuration (D-21.1, D-21.2, D-21.3, D-21.9)


def test_none_broker_is_the_phase_one_default():
    assert RunnerConfig().broker.adapter == "none"
    assert RunnerConfig().broker.mode == "endpoint"


def test_brokered_tier_naming_api_key_env_is_refused():
    # D-21.1: a non-empty api_key_env under a broker is a refused
    # configuration, not a warning — a second channel for the key is the
    # leak the broker exists to remove.
    tier = TierConfig(adapter="api", provider=PROVIDER, command="run", api_key_env=[KEY_ENV])
    with pytest.raises(ValidationError, match="brokered tier names no credential"):
        RunnerConfig(
            tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier},
            broker=broker_config("http://127.0.0.1:1"),
        )


def test_none_broker_allows_the_existing_key_name_channel():
    # Under `none` — today's behaviour, named — the tier keeps naming its
    # key's env var exactly as before (D-21.9: none stays legal).
    tier = TierConfig(adapter="api", provider=PROVIDER, command="run", api_key_env=[KEY_ENV])
    config = RunnerConfig(
        tiers={"planner": TierConfig(), "reviewer": TierConfig(), "executor": tier}
    )
    assert config.tiers["executor"].api_key_env == [KEY_ENV]


def test_opensandbox_adapter_is_refused_until_a_server_exists():
    # D-21.2 / RFC 0021 §8: the adapter is named and deliberately unbuilt —
    # condition-gated on a live server, never a prerequisite.
    with pytest.raises(ValidationError, match="opensandbox"):
        BrokerConfig(adapter="opensandbox")


def test_sealed_mode_is_phase_two():
    # D-21.3's phasing: endpoint closes custody now; sealed is containment,
    # and configuring it before it exists must be a refusal, not a silent
    # endpoint run.
    with pytest.raises(ValidationError, match="phase 2"):
        BrokerConfig(adapter="local", mode="sealed")


def test_broker_provider_requires_wire_facts():
    with pytest.raises(ValidationError, match="http\\(s\\) base URL"):
        BrokerProvider(upstream="api.example.com", key_env=KEY_ENV)

    with pytest.raises(ValidationError, match="key_env"):
        BrokerProvider(upstream="https://api.example.com", key_env="")


# ....................... #
# The port's adapters (D-21.2)


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
# The local adapter: routing, injection, metering (D-21.4, D-21.5, D-21.7)


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
    # sandbox's run token never travels past it (D-4b).
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
    # second is refused in progress (D-21.6).
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
    # D-21.7: request and response bodies are read to forward and meter,
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
# The tier command's substitution (RFC 0021 §5.1)


def harness_ctx(tmp_path: Path, tier: TierConfig, handle: BrokerHandle | None) -> AgentContext:
    workspace = tmp_path / "wt"
    workspace.mkdir()
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


def test_harness_substitutes_the_broker_url_and_token(tmp_path):
    tier = TierConfig(
        adapter="api", provider=PROVIDER, command="run --url {broker_url} --token {broker_token}"
    )
    handle = BrokerHandle(
        token="run-token", base_urls={PROVIDER: "http://127.0.0.1:9999/test-vendor"}
    )
    ctx, agent = harness_ctx(tmp_path, tier, handle)

    command = agent._command(ctx)
    assert command == "run --url http://127.0.0.1:9999/test-vendor --token run-token"


def test_harness_refuses_broker_placeholders_with_no_broker(tmp_path):
    tier = TierConfig(adapter="api", provider=PROVIDER, command="run --url {broker_url}")
    ctx, agent = harness_ctx(tmp_path, tier, None)

    with pytest.raises(ValueError, match="no broker handle"):
        agent._command(ctx)


def test_harness_refuses_broker_placeholders_under_the_none_broker(tmp_path):
    tier = TierConfig(adapter="api", provider=PROVIDER, command="run --token {broker_token}")
    ctx, agent = harness_ctx(tmp_path, tier, BrokerHandle(token="", base_urls={}))

    with pytest.raises(ValueError, match="'none'"):
        agent._command(ctx)


def test_harness_refuses_a_provider_the_broker_does_not_route(tmp_path):
    tier = TierConfig(adapter="api", provider="unrouted-vendor", command="run --url {broker_url}")
    ctx, agent = harness_ctx(
        tmp_path, tier, BrokerHandle(token="t", base_urls={PROVIDER: "http://127.0.0.1:1/x"})
    )

    with pytest.raises(ValueError, match="but not the tier's provider"):
        agent._command(ctx)


def test_harness_without_placeholders_runs_unchanged_under_a_broker(tmp_path):
    tier = TierConfig(adapter="api", provider=PROVIDER, command="claude -p $(cat {prompt})")
    ctx, agent = harness_ctx(
        tmp_path, tier, BrokerHandle(token="t", base_urls={PROVIDER: "http://127.0.0.1:1/x"})
    )

    assert agent._command(ctx) == "claude -p $(cat .torve/tmp/prompt.md)"


# ....................... #
# The regime hash (D-21.8) and the doctor (D-21.9)


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


def _doctor_repo(tmp_path: Path, config: dict) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, **config}), encoding="utf-8"
    )
    return root


def test_doctor_names_the_none_broker_and_its_ceiling(tmp_path):
    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "opensandbox"}})
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["broker"]["ok"] is True
    assert "none" in checks["broker"]["detail"]
    assert "credential-custody requirement unmet" in checks["broker"]["detail"]


def test_doctor_names_the_local_broker_in_force(tmp_path):
    root = _doctor_repo(
        tmp_path,
        {
            "runtime": {"adapter": "opensandbox"},
            "broker": {
                "adapter": "local",
                "providers": {
                    PROVIDER: {"upstream": "https://api.example.com", "key_env": KEY_ENV}
                },
            },
        },
    )
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["broker"]["ok"] is True
    assert "local" in checks["broker"]["detail"]
    assert PROVIDER in checks["broker"]["detail"]


# ....................... #
# The runner's custody wiring (D-21.1, D-21.6): host-side, with the tier
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

    def build_image(self, context, tag: str) -> str:
        raise NotImplementedError


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
    from torve.application.runner import RunDeps

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


def _two_request_command() -> str:
    return (
        'python -c "import urllib.request,json;'
        "H={'Authorization':'Bearer {broker_token}','Content-Type':'application/json'};"
        "D=json.dumps({'model':'x'}).encode();"
        "print(urllib.request.urlopen(urllib.request.Request('{broker_url}/v1/chat/completions',data=D,headers=H)).read().decode());"
        "print(urllib.request.urlopen(urllib.request.Request('{broker_url}/v1/chat/completions',data=D,headers=H)).read().decode())\""
    )


def test_brokered_attempt_escalates_cost_anomaly_on_budget_refusal(tmp_path, upstream, monkeypatch):
    # D-21.6 end to end: the budget is held by the broker, the refusal
    # happens mid-attempt, and the run escalates cost_anomaly in progress.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}

    tier = TierConfig(
        adapter="api", provider=PROVIDER, model="fake-model-9", command=_two_request_command()
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
    # (D-21.1) — the tier's key env is not forwarded into the sandbox.
    assert runtime.specs[0].env_passthrough == ()
    assert KEY_ENV not in str(runtime.specs[0])


def test_brokered_attempt_reaches_ready_and_records_both_costs(tmp_path, upstream, monkeypatch):
    # D-21.5: the adapter's self-reported cost and the broker's measured
    # cost both ride the run's record — with a divergence past tolerance
    # visible as an engine event.
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    state, upstream_url = upstream
    state["usage"] = {"total_tokens": 5}

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        command=(
            'python -c "import urllib.request,json;'
            "H={'Authorization':'Bearer {broker_token}','Content-Type':'application/json'};"
            "D=json.dumps({'model':'x'}).encode();"
            "print(urllib.request.urlopen(urllib.request.Request('{broker_url}/v1/chat/completions',data=D,headers=H)).read().decode());"
            'print(\'{\\"total_cost_usd\\": 0.5, \\"model\\": \\"fake-model-9\\"}\');'
            "print('ok')\" "
            "&& mkdir -p src && echo FEATURE = True > src/feature.py"
        ),
    )
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
    # the adapter's report (D-21.5) — the adapter claimed 0.5, the broker
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
    assert events, "the divergence past tolerance must be an engine event (D-21.5)"
    return next(r for r in records if r.get("kind") != "engine")


# ....................... #
# End to end against the real Docker daemon: a sandbox on the default bridge
# reaching the broker at the bridge gateway, holding no key (RFC 0021 §6).


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
    from torve.application.runner import RunDeps, run_task
    from torve.config.runconfig import RuntimeConfig
    from torve.gates.context import load_task
    from torve.gates.sabotage import TASK_ID

    tier = TierConfig(
        adapter="api",
        provider=PROVIDER,
        model="fake-model-9",
        command=(
            'python -c "import urllib.request,json;'
            "H={'Authorization':'Bearer {broker_token}','Content-Type':'application/json'};"
            "D=json.dumps({'model':'x'}).encode();"
            "print(urllib.request.urlopen(urllib.request.Request('{broker_url}/v1/chat/completions',data=D,headers=H)).read().decode())\" "
            "&& mkdir -p src && echo FEATURE = True > src/feature.py"
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
    from torve.application.runner import RunDeps, run_task
    from torve.config.runconfig import RuntimeConfig
    from torve.gates.context import load_task
    from torve.gates.sabotage import TASK_ID

    tier = TierConfig(
        adapter="api", provider=PROVIDER, model="fake-model-9", command=_two_request_command()
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
    from torve.application.runner import run_routing
    from torve.config.runconfig import RunnerConfig
    from torve.domain.task import Task

    config = RunnerConfig.model_validate(
        {
            "schema_version": 1,
            "tiers": {
                "executor": {
                    "adapter": "harness",
                    "command": "x {prompt}",
                    "model": "m",
                    "provider": "deepseek",
                    "api_key_env": ["DEEPSEEK_API_KEY"],
                }
            },
        }
    )
    routing = run_routing(config, Task(id="T-0001", intent="x", decisions=[]), review_on=False)
    assert routing.routes == ()
