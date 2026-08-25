"""One battery, every Runtime adapter (RFC 0003; a decision logged in T-0003).

The contract under test is "workspace in, changed files out" plus lifecycle
and labels — not the mechanism. Docker runs against the real daemon (skipped
where there is none); OpenSandbox runs against the in-process SDK stub, which
executes commands through the host shell, so the adapter's tar-sync logic is
exercised for real. Integration against a live OpenSandbox server is deferred
until one exists.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import opensandbox_stub
import pytest

from torve.adapters.runtime.docker import DockerRuntime
from torve.adapters.runtime.opensandbox import OpenSandboxRuntime
from torve.application.ports import SandboxSpec
from torve.base import naming
from torve.config.runconfig import OpenSandboxConfig

# The runtime's default image: guaranteed shell + tar + python, and already
# cached wherever the integration tests run.
TEST_IMAGE = "python:3.13-slim"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(params=["docker", "opensandbox-stub"])
def runtime_case(request, tmp_path):
    """(runtime, spec factory, workspace). The stub's sandboxes are host temp
    dirs, so its specs use a host-side workdir; Docker mounts at /work."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seeded.txt").write_text("from the host\n", encoding="utf-8")

    def spec(workdir: str) -> SandboxSpec:
        return SandboxSpec(
            name=f"torve-conf-{uuid.uuid4().hex[:8]}",
            image=TEST_IMAGE,
            labels=naming.labels("T-9902", uuid.uuid4().hex, Path.cwd()),
            timeout_s=120,
            workdir=workdir,
        )

    if request.param == "docker":
        if not docker_available():
            pytest.skip("docker daemon not available")
        yield DockerRuntime(), spec("/work"), workspace
    else:
        sandbox_dir = tmp_path / "remote"
        runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
        yield runtime, spec(str(sandbox_dir)), workspace
        opensandbox_stub.REGISTRY.clear()


def test_exec_and_workspace_roundtrip(runtime_case):
    runtime, spec, workspace = runtime_case
    handle = runtime.create(spec, workspace)
    try:
        seen = runtime.exec(handle, "cat seeded.txt", 30)
        assert seen.exit_code == 0
        assert "from the host" in seen.output

        wrote = runtime.exec(handle, "echo produced-inside > out.txt", 30)
        assert wrote.exit_code == 0

        failed = runtime.exec(handle, "exit 42", 30)
        assert failed.exit_code == 42

        runtime.sync_out(handle, workspace)
        assert (workspace / "out.txt").read_text().strip() == "produced-inside"
    finally:
        runtime.destroy(handle)


def test_exec_timeout_is_not_an_exit_code(runtime_case):
    runtime, spec, workspace = runtime_case
    handle = runtime.create(spec, workspace)
    try:
        result = runtime.exec(handle, "sleep 30", 1)
        assert result.timed_out
        assert result.exit_code is None
    finally:
        runtime.destroy(handle)


def test_listing_and_destroy_by_id(runtime_case):
    runtime, spec, workspace = runtime_case
    handle = runtime.create(spec, workspace)
    infos = [i for i in runtime.list_torve_sandboxes() if i.labels.get("torve.task") == "T-9902"]
    assert infos, "created sandbox not visible to the reaper's listing"
    assert infos[0].labels[naming.LABEL_TASK] == "T-9902"

    runtime.destroy_by_id(infos[0].id)
    remaining = [i for i in runtime.list_torve_sandboxes()
                 if i.labels.get("torve.task") == "T-9902"]
    assert not remaining
    runtime.destroy(handle)  # idempotent cleanup


# ....................... #
# Authentication routes (RFC 0004 §1, §2) — Docker-only: OpenSandbox refuses
# volumes by contract, and its env passthrough resolves in the stub below.


@pytest.fixture
def docker_case(tmp_path):
    if not docker_available():
        pytest.skip("docker daemon not available")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    yield DockerRuntime(), workspace


def auth_spec(**overrides) -> SandboxSpec:
    return SandboxSpec(
        name=f"torve-auth-{uuid.uuid4().hex[:8]}",
        image=TEST_IMAGE,
        labels=naming.labels("T-9903", uuid.uuid4().hex, Path.cwd()),
        timeout_s=120,
        **overrides,
    )


def test_docker_env_passthrough_carries_the_value_not_the_spec(docker_case, monkeypatch):
    runtime, workspace = docker_case
    monkeypatch.setenv("TORVE_TEST_KEY", "s3cr3t-value")
    spec = auth_spec(env_passthrough=("TORVE_TEST_KEY", "TORVE_TEST_ABSENT"))
    assert "s3cr3t-value" not in str(spec)  # the value never enters the spec (D-4b)
    handle = runtime.create(spec, workspace)
    try:
        seen = runtime.exec(handle, 'printf "%s" "$TORVE_TEST_KEY"', 30)
        assert seen.output == "s3cr3t-value"
    finally:
        runtime.destroy(handle)


def test_docker_auth_volume_outlives_the_sandbox(docker_case):
    """The D-4.2 property: sandboxes are ephemeral, the slot's volume is not —
    a token refresh written by one run is there for the next."""
    import os

    runtime, workspace = docker_case
    volume = f"torve-test-auth-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
    # A fresh named volume mounts root-owned; seeding ownership is the
    # operator's one-time step when the slot is provisioned (§2).
    subprocess.run(
        ["docker", "run", "--rm", "-u", "0:0", "-v", f"{volume}:/auth", TEST_IMAGE,
         "chown", f"{os.getuid()}:{os.getgid()}", "/auth"],
        capture_output=True, check=True,
    )
    try:
        first = runtime.create(auth_spec(volumes={volume: "/auth"}), workspace)
        try:
            wrote = runtime.exec(first, "echo refreshed-token > /auth/credentials", 30)
            assert wrote.exit_code == 0
        finally:
            runtime.destroy(first)

        second = runtime.create(auth_spec(volumes={volume: "/auth"}), workspace)
        try:
            seen = runtime.exec(second, "cat /auth/credentials", 30)
            assert seen.output.strip() == "refreshed-token"
        finally:
            runtime.destroy(second)
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)


def test_opensandbox_refuses_volumes(tmp_path):
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = tmp_path / "ws2"
    workspace.mkdir()
    spec = SandboxSpec(
        name="torve-refuse", image=TEST_IMAGE, labels=naming.labels("T-9904", "r", Path.cwd()),
        timeout_s=60, workdir=str(tmp_path / "remote"), volumes={"torve-auth-0": "/auth"},
    )
    with pytest.raises(RuntimeError, match="no per-slot auth volumes"):
        runtime.create(spec, workspace)
    opensandbox_stub.REGISTRY.clear()


# ....................... #
# Docker inside the sandbox (RFC 0017 §2a): socket mode mounts the host
# daemon knowingly; the default mounts nothing; opensandbox refuses.


def test_docker_socket_mode_mounts_the_host_daemon(docker_case):
    _, workspace = docker_case
    runtime = DockerRuntime(docker_mode="socket")
    handle = runtime.create(auth_spec(), workspace)
    try:
        probe = runtime.exec(handle, "test -S /var/run/docker.sock", 30)
        assert probe.exit_code == 0
    finally:
        runtime.destroy(handle)


def test_docker_default_mode_mounts_no_socket(docker_case):
    runtime, workspace = docker_case
    handle = runtime.create(auth_spec(), workspace)
    try:
        probe = runtime.exec(handle, "test -e /var/run/docker.sock", 30)
        assert probe.exit_code != 0
    finally:
        runtime.destroy(handle)


def test_opensandbox_refuses_docker_in_any_mode():
    with pytest.raises(ValueError, match="refuses docker access"):
        OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub,
                           docker_mode="socket")


# ....................... #
# Network mode and the proxy convention: a host whose egress runs through a
# local proxy needs the sandbox on the host stack, seeing the same variables.


def test_docker_read_only_workspace_physically_refuses_writes(docker_case):
    # D-5.2: the reviewer cannot fix-and-approve — the mount itself refuses.
    runtime, workspace = docker_case
    (workspace / "code.py").write_text("x = 1\n", encoding="utf-8")
    handle = runtime.create(auth_spec(workspace_read_only=True), workspace)
    try:
        write = runtime.exec(handle, "touch /work/evil.py", 30)
        assert write.exit_code != 0
        read = runtime.exec(handle, "cat /work/code.py", 30)
        assert read.exit_code == 0 and "x = 1" in read.output
        # Host-side writes stay visible through the mount (the staged prompt).
        (workspace / "prompt.md").write_text("late", encoding="utf-8")
        late = runtime.exec(handle, "cat /work/prompt.md", 30)
        assert late.exit_code == 0 and late.output.strip() == "late"
    finally:
        runtime.destroy(handle)


def test_docker_network_host_shares_the_host_stack(docker_case, monkeypatch):
    _, workspace = docker_case
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    runtime = DockerRuntime(network="host")
    handle = runtime.create(auth_spec(), workspace)
    try:
        mode = subprocess.run(
            ["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", handle.id],
            capture_output=True, text=True, check=True).stdout.strip()
        assert mode == "host"
        seen = runtime.exec(handle, 'printf "%s" "$HTTPS_PROXY"', 30)
        assert seen.output == "http://127.0.0.1:9999"
    finally:
        runtime.destroy(handle)


def test_docker_default_network_forwards_no_proxy_vars(docker_case, monkeypatch):
    # A host-loopback proxy address is poison under the bridge — without the
    # network opt-in the sandbox keeps Docker's default egress untouched.
    _, workspace = docker_case
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    runtime = DockerRuntime()
    handle = runtime.create(auth_spec(), workspace)
    try:
        seen = runtime.exec(handle, 'printf "%s" "$HTTPS_PROXY"', 30)
        assert seen.output == ""
    finally:
        runtime.destroy(handle)


def test_opensandbox_forwards_proxy_and_passthrough_values(tmp_path, monkeypatch):
    # Server-side sandboxes get the same convention as Docker's host mode —
    # resolved at the API boundary; reachability is the server's business.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("TORVE_TEST_KEY", "k-123")
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = tmp_path / "ws3"
    workspace.mkdir()
    spec = SandboxSpec(
        name="torve-proxy", image=TEST_IMAGE, labels=naming.labels("T-9905", "r", Path.cwd()),
        timeout_s=60, workdir=str(tmp_path / "remote3"),
        env_passthrough=("TORVE_TEST_KEY",), env={"EXPLICIT": "wins"},
    )
    handle = runtime.create(spec, workspace)
    try:
        recorded = opensandbox_stub.REGISTRY[handle.id].env
        assert recorded["HTTPS_PROXY"] == "http://127.0.0.1:9999"
        assert recorded["TORVE_TEST_KEY"] == "k-123"
        assert recorded["EXPLICIT"] == "wins"
    finally:
        runtime.destroy(handle)
    opensandbox_stub.REGISTRY.clear()
