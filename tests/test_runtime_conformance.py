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
            labels=naming.labels("T-9902", uuid.uuid4().hex),
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
