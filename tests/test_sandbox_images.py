"""The image-as-input mechanism (RFC 0017): digest identity into config_hash
and the attempt record, tier images, `torve sandbox build`, and the doctor's
image checks. Docker-backed cases skip without a daemon, like the runtime
conformance battery.

Carries two sections of its own since RFC 0041: the transfer ledger the
OpenSandbox adapter books per attempt (D-41.5), and the live conformance leg
that runs the runtime battery a third time against a real server named by
TORVE_OPENSANDBOX_TEST_DOMAIN, plus the two assertions only a live server
can answer (D-41.3)."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import opensandbox_stub
import pytest
import test_runtime_conformance as battery
import yaml
from test_run_loop import MockRuntime
from typer.testing import CliRunner

from torve.adapters.runtime.opensandbox import OpenSandboxRuntime
from torve.application.telemetry import (
    build_attempt_row,
    build_record,
    config_hash,
    record_transfer,
)
from torve.base import naming
from torve.cli.main import app
from torve.config.manifest import Manifest
from torve.config.runconfig import (
    OpenSandboxConfig,
    RunnerConfig,
    TierConfig,
    configured_images,
    image_for,
)
from torve.domain.task import Task
from torve.gates.context import GateContext
from torve.gates.runner import RunReport


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "gates.yaml"
    path.write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    return path


# ....................... #
# identity


def test_image_digest_changes_the_regime_hash(tmp_path):
    base = config_hash(manifest(tmp_path), tmp_path)
    with_image = config_hash(manifest(tmp_path), tmp_path, image_digest="sha256:aaa")
    rebuilt = config_hash(manifest(tmp_path), tmp_path, image_digest="sha256:bbb")
    assert base != with_image
    assert with_image != rebuilt
    # No digest resolved -> the hash is what it always was.
    assert base == config_hash(manifest(tmp_path), tmp_path, image_digest=None)


def test_tier_image_overrides_the_runtime_default():
    config = RunnerConfig()
    plain = TierConfig()
    harness = TierConfig(
        adapter="harness", command="run {prompt}", provider="deepseek", image="torve-agent:dsh"
    )
    assert image_for(config, plain) == config.runtime.image
    assert image_for(config, harness) == "torve-agent:dsh"
    config.tiers["executor"] = harness
    assert configured_images(config) == sorted({config.runtime.image, "torve-agent:dsh"})


def test_mock_runtime_resolution_reaches_the_attempt_record(tmp_path):
    # The agent block carries image_digest end-to-end; the run loop tests
    # exercise the loop itself, this pins only the new field's presence.
    runtime = MockRuntime()
    assert runtime.resolve_image("python:3.13-slim") == "sha256:mock-python:3.13-slim"


def test_opensandbox_resolves_digest_pinned_references_only():
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    assert runtime.resolve_image("registry.example/torve-agent@sha256:abc123") == "sha256:abc123"
    assert runtime.resolve_image("registry.example/torve-agent:latest") is None
    with pytest.raises(RuntimeError):
        runtime.build_image(Path("."), "torve-agent:x")


# ....................... #
# build and doctor, against the daemon


def seed_repo(tmp_path: Path, config: dict[str, object]) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, **config}), encoding="utf-8"
    )
    return root


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
def test_sandbox_build_reports_a_digest_that_tracks_content(tmp_path):
    root = seed_repo(tmp_path, {})
    definition = root / ".torve" / "sandbox" / "probe"
    definition.mkdir(parents=True)
    definition.joinpath("Dockerfile").write_text(
        "FROM python:3.13-slim\nLABEL torve.probe=one\n", encoding="utf-8"
    )

    first = CliRunner().invoke(
        app, ["sandbox", "build", "probe", "--root", str(root), "--format", "json"]
    )
    assert first.exit_code == 0, first.output
    again = CliRunner().invoke(
        app, ["sandbox", "build", "probe", "--root", str(root), "--format", "json"]
    )
    assert again.exit_code == 0, again.output

    import json

    digest_one = json.loads(first.stdout)["images"][0]["digest"]
    assert digest_one.startswith("sha256:")
    # Same definition -> same identity.
    assert json.loads(again.stdout)["images"][0]["digest"] == digest_one

    definition.joinpath("Dockerfile").write_text(
        "FROM python:3.13-slim\nLABEL torve.probe=two\n", encoding="utf-8"
    )
    rebuilt = CliRunner().invoke(
        app, ["sandbox", "build", "probe", "--root", str(root), "--format", "json"]
    )
    assert rebuilt.exit_code == 0, rebuilt.output
    # Changed definition -> changed identity: the drift the hash now sees.
    assert json.loads(rebuilt.stdout)["images"][0]["digest"] != digest_one

    subprocess.run(["docker", "rmi", "-f", "torve-agent:probe"], capture_output=True, check=False)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
def test_doctor_reds_on_a_configured_image_that_does_not_exist(tmp_path):
    root = seed_repo(tmp_path, {"runtime": {"image": "torve-agent:definitely-not-built"}})
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    assert result.exit_code == 3, result.output

    import json

    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image torve-agent:definitely-not-built"]
    assert image_check["ok"] is False
    assert "not present" in image_check["detail"]


def test_sandbox_build_refuses_an_unknown_definition(tmp_path):
    root = seed_repo(tmp_path, {})
    result = CliRunner().invoke(app, ["sandbox", "build", "ghost", "--root", str(root)])
    assert result.exit_code == 3, result.output


# ....................... #
# definition conventions (RFC 0033 §6): the publishable definitions pin
# their harness versions behind defaulted ARGs (D-33.3) and keep the
# toolkit under /opt/torve/ with one transition revision of old-path
# symlinks (D-33.4). The ARG-pin check is the regex-level test the RFC
# names; the toolkit contract joins the docker-gated battery below.

REPO_ROOT = Path(__file__).resolve().parents[1]

# name -> (npm package, version ARG): the three publishable definitions.
PUBLISHABLE = {
    "claude": ("@anthropic-ai/claude-code", "CLAUDE_VERSION"),
    "dsh": ("@deepseek-ai/dsh", "DSH_VERSION"),
    "mimo": ("@mimo-ai/cli", "MIMO_VERSION"),
}


def _definition_dockerfile(name: str) -> Path:
    return REPO_ROOT / ".torve" / "sandbox" / name / "Dockerfile"


def test_harness_installs_ride_pinned_default_args():
    # D-33.3: every harness install in a publishable definition consumes a
    # version ARG whose default is a literal pin — a bump is a one-line
    # reviewed diff, never a rebuild side effect.
    for name, (package, arg) in PUBLISHABLE.items():
        text = _definition_dockerfile(name).read_text(encoding="utf-8")
        declared = re.search(rf"^ARG\s+{arg}=(\S+)\s*$", text, re.MULTILINE)
        assert declared, f"{name}: no harness version ARG {arg} declared"
        default = declared.group(1)
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", default), (
            f"{name}: {arg} default {default!r} is not a literal version pin"
        )
        assert re.search(rf"npm install -g {re.escape(package)}@\${{{arg}}}", text), (
            f"{name}: the install does not consume ${{{arg}}}"
        )


# The toolkit contract per image (RFC 0033 §6): what a profile's command
# template depends on. `answer` must exit 0 inside the container — for dsh
# the reporter the RFC names, for claude the seed's settings file — and
# every old path must survive as a symlink for the transition revision.
TOOLKIT = {
    "claude": {
        "answer": "test -f /opt/torve/seed/.claude/settings.json",
        "symlinks": {"/opt/claude-seed": "/opt/torve/seed"},
    },
    "dsh": {
        "answer": "/opt/torve/report-usage --help >/dev/null 2>&1",
        "symlinks": {
            "/opt/dsh/report-usage.js": "/opt/torve/report-usage",
            "/opt/dsh/deepseek-chat.yml": "/opt/torve/overlays/deepseek-chat.yml",
            "/opt/dsh/qwen3.8-flash.yml": "/opt/torve/overlays/qwen3.8-flash.yml",
            "/opt/dsh/brokered-deepseek.yml": "/opt/torve/overlays/brokered-deepseek.yml",
            "/opt/dsh/brokered-deepseek-v4-flash.yml": (
                "/opt/torve/overlays/brokered-deepseek-v4-flash.yml"
            ),
        },
    },
    # mimo carries no toolkit utility, so nothing a profile depends on to
    # protect — an empty contract here would be a vacuous pass.
}


def _toolkit_check(name: str) -> str:
    contract = TOOLKIT[name]
    checks = [contract["answer"]]
    checks += [
        f'test -L {link} && test "$(readlink {link})" = {target}'
        for link, target in contract["symlinks"].items()
    ]
    return " && ".join(checks)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.parametrize("name", ["claude", "dsh"])
def test_toolkit_contract_answers_in_the_container(name, tmp_path):
    # What CI publishes is what the battery built (D-33.5): the definition
    # builds through the same command as an operator's, then answers. The
    # definition bytes are the repo's, the TAG is a throwaway: building
    # under the production tag and rmi-ing it in cleanup deleted the
    # host's live agent images from inside the acceptance battery — three
    # mid-queue dispatch failures before the phantom was caught.
    root = seed_repo(tmp_path, {})
    probe_name = f"{name}-probe"
    definition = root / ".torve" / "sandbox" / probe_name
    shutil.copytree(REPO_ROOT / ".torve" / "sandbox" / name, definition)

    built = CliRunner().invoke(
        app, ["sandbox", "build", probe_name, "--root", str(root), "--format", "json"]
    )
    assert built.exit_code == 0, built.output
    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                f"torve-agent:{probe_name}",
                "sh",
                "-c",
                _toolkit_check(name),
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        subprocess.run(
            ["docker", "rmi", "-f", f"torve-agent:{probe_name}"], capture_output=True, check=False
        )


# ....................... #
# The battery's dependency layer (D-35.2): pyproject.toml and uv.lock baked
# by `uv sync --all-extras --no-install-project` into a fixed
# UV_PROJECT_ENVIRONMENT, keyed to the lock's bytes so an attempt with an
# unchanged lock reconciles the delta with zero package downloads. The bake
# is context-staged — `torve sandbox build battery` (context: the definition
# directory alone) yields today's thin image, a context staged with the two
# project inputs yields the warm one — the layer is a convenience, never a
# requirement.

LAYER_IMAGE = "torve-agent:battery-layer-probe"


def test_battery_bakes_the_lockfile_keyed_dependency_layer():
    # D-35.2 at the text level, so the layer cannot silently vanish where
    # no daemon runs: the fixed environment path, a build-time sync of the
    # two project inputs under the flags that make it a dependency layer
    # (--no-install-project keeps the per-attempt source out of it), and
    # the check asserting the baked venv resolves against the lock.
    text = _definition_dockerfile("battery").read_text(encoding="utf-8")
    assert re.search(
        r"^ENV\s+UV_PROJECT_ENVIRONMENT=/opt/torve/project/\.venv\s*$", text, re.MULTILINE
    ), "battery: UV_PROJECT_ENVIRONMENT is not fixed at the layer path"
    assert re.search(r"uv sync[^\n]*--all-extras[^\n]*--no-install-project", text), (
        "battery: no lockfile-keyed uv sync at build"
    )
    assert re.search(r"uv sync[^\n]*--check", text), "battery: the layer ships no build-time check"


def _stage_battery_context(tmp_path: Path) -> Path:
    context = tmp_path / "battery-context"
    context.mkdir()
    shutil.copy(_definition_dockerfile("battery"), context / "Dockerfile")
    shutil.copy(REPO_ROOT / "pyproject.toml", context / "pyproject.toml")
    shutil.copy(REPO_ROOT / "uv.lock", context / "uv.lock")
    return context


def _build_battery(tag: str, context: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "build", "-t", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )


def _remove_battery(tag: str) -> None:
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_unchanged_lockfile_downloads_nothing_in_an_attempt(tmp_path):
    # The conformance case: the baked venv checked with `uv sync --check`
    # over the exact lock bytes the bake consumed, inside a container whose
    # only network is none. A green check on a routeless container means
    # reconciling an unchanged lockfile performed no package downloads —
    # the attempt-side mirror of the bake's own sync set.
    built = _build_battery(LAYER_IMAGE, _stage_battery_context(tmp_path))
    assert built.returncode == 0, built.stderr[-4000:]
    try:
        started = subprocess.run(
            ["docker", "run", "--rm", "-d", "--network", "none", LAYER_IMAGE, "sleep", "600"],
            capture_output=True,
            text=True,
            check=True,
        )
        container = started.stdout.strip()
        try:
            subprocess.run(
                ["docker", "exec", container, "mkdir", "-p", "/workspace"],
                capture_output=True,
                check=True,
            )

            for project_file in ("pyproject.toml", "uv.lock"):
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(REPO_ROOT / project_file),
                        f"{container}:/workspace/{project_file}",
                    ],
                    check=True,
                )

            reconciled = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "sh",
                    "-c",
                    "cd /workspace && uv sync --check --locked --all-extras --no-install-project",
                ],
                capture_output=True,
                text=True,
            )
            assert reconciled.returncode == 0, reconciled.stdout + reconciled.stderr
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
    finally:
        _remove_battery(LAYER_IMAGE)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_the_layer_is_keyed_to_the_lockfile_bytes(tmp_path):
    # A lockfile change rebuilds the layer — and the rebuild is governed by
    # the new bytes the moment the layer re-runs: a staged lock that stops
    # parsing fails the build rather than serving a stale warm lie from
    # cache. (A cached good build never re-runs the sync at all.)
    context = _stage_battery_context(tmp_path)
    context.joinpath("uv.lock").write_text("not a lockfile {{{\n", encoding="utf-8")
    built = _build_battery(LAYER_IMAGE, context)
    assert built.returncode != 0
    assert "uv.lock" in built.stdout + built.stderr
    _remove_battery(LAYER_IMAGE)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_the_bare_definition_builds_thin_as_before(tmp_path):
    # The layer is a convenience, never a requirement: the path the engine
    # itself takes — `torve sandbox build`, the definition directory as
    # the only context — still produces today's thin image. The build
    # succeeds, the uv the battery needs is there, and no baked venv
    # exists to go stale. A throwaway tag, never the production one: this
    # cleanup used to rmi the host's live battery image.
    root = seed_repo(tmp_path, {})
    definition = root / ".torve" / "sandbox" / "battery-probe"
    shutil.copytree(REPO_ROOT / ".torve" / "sandbox" / "battery", definition)

    built = CliRunner().invoke(
        app, ["sandbox", "build", "battery-probe", "--root", str(root), "--format", "json"]
    )
    assert built.exit_code == 0, built.output
    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "torve-agent:battery-probe",
                "sh",
                "-c",
                "uv --version >/dev/null && test ! -d /opt/torve/project/.venv",
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        subprocess.run(
            ["docker", "rmi", "-f", "torve-agent:battery-probe"], capture_output=True, check=False
        )


# ....................... #
# The derived-cache volume at the runtime adapters (RFC 0035 §5.2,
# D-35.4/D-35.5): the Docker adapter mounts what the runner names and
# points the toolchain cache homes at the mount; the opensandbox adapter
# refuses the field loudly. The arg-construction cases need no daemon;
# the cold/warm conformance case (D-35.1) does.

CACHE_HOMES = {
    "UV_CACHE_DIR": "/opt/torve/cache/uv",
    "MYPY_CACHE_DIR": "/opt/torve/cache/mypy",
    "RUFF_CACHE_DIR": "/opt/torve/cache/ruff",
}


def cache_spec(workspace: Path, volumes: dict[str, str], name: str = "torve-T1-r1-a1", **kw):
    from torve.application.ports import SandboxSpec

    return SandboxSpec(
        name=name,
        image=kw.pop("image", LAYER_IMAGE),
        labels={},
        timeout_s=600.0,
        volumes=volumes,
        **kw,
    )


def docker_run_args(spec, tmp_path: Path) -> list[str]:
    from torve.adapters.runtime.docker import DockerRuntime

    return DockerRuntime()._run_args(spec, tmp_path)


def test_docker_mounts_the_slot_volume_the_runner_names(tmp_path):
    from torve.config.runconfig import CACHE_MOUNT

    args = docker_run_args(cache_spec(tmp_path, {"torve-cache-2": CACHE_MOUNT}), tmp_path)

    # Slot-suffixed naming like the auth volume, mounted at the fixed
    # address — docker creates-or-reuses the named volume on the pair.
    assert f"torve-cache-2:{CACHE_MOUNT}" in args


def test_docker_points_every_toolchain_cache_home_at_the_mount(tmp_path):
    from torve.config.runconfig import CACHE_MOUNT

    args = docker_run_args(cache_spec(tmp_path, {"torve-cache-2": CACHE_MOUNT}), tmp_path)
    exported = {
        pair.split("=", 1)[0]: pair.split("=", 1)[1] for pair in args if "_CACHE_DIR=" in pair
    }

    assert exported == CACHE_HOMES
    assert all(path.startswith(CACHE_MOUNT + "/") for path in exported.values())


def test_a_cold_sandbox_carries_no_cache_wiring_at_all(tmp_path):
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.config.runconfig import CACHE_MOUNT

    # Empty (the default) is cold exactly as today (D-35.4): no exports.
    plain = docker_run_args(cache_spec(tmp_path, {}), tmp_path)
    assert not [pair for pair in plain if "_CACHE_DIR=" in pair]

    # An auth-only volume (a subscription tier, still cold) warms nothing.
    authed = DockerRuntime()._run_args(cache_spec(tmp_path, {"torve-auth-1": "/auth"}), tmp_path)
    assert not [pair for pair in authed if "_CACHE_DIR=" in pair]
    assert "torve-auth-1:/auth" in authed  # its own mount still works

    # Only the fixed address counts as the cache: a volume someone
    # pointlessly named at another path is left alone.
    other = docker_run_args(cache_spec(tmp_path, {"stray": "/elsewhere"}), tmp_path)
    assert not [pair for pair in other if "_CACHE_DIR=" in pair]
    assert CACHE_MOUNT not in other


def test_an_explicit_spec_env_wins_over_the_cache_homes(tmp_path):
    from torve.config.runconfig import CACHE_MOUNT

    args = docker_run_args(
        cache_spec(
            tmp_path,
            {"torve-cache-2": CACHE_MOUNT},
            env={"UV_CACHE_DIR": "/somewhere/else"},
        ),
        tmp_path,
    )

    assert "UV_CACHE_DIR=/somewhere/else" in args
    assert "UV_CACHE_DIR=/opt/torve/cache/uv" not in args
    # The untaken homes still point at the mount.
    assert "MYPY_CACHE_DIR=/opt/torve/cache/mypy" in args


def test_opensandbox_refuses_a_cache_volume_loudly(tmp_path):
    # D-35.5: a loud refusal, never a quiet cold fallback — a warm tier on
    # the opensandbox runtime learns about it at the first create, not
    # from a mysteriously slow attempt.
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)

    with pytest.raises(RuntimeError, match="refuses a tier's cache_volume"):
        runtime.create(cache_spec(tmp_path, {"torve-cache-0": "/opt/torve/cache"}), tmp_path)

    # The auth refusal is untouched and still distinguishes itself.
    with pytest.raises(RuntimeError, match="auth volumes"):
        runtime.create(cache_spec(tmp_path, {"torve-auth-0": "/auth"}), tmp_path)


def test_the_cache_mount_is_a_fixed_address_outside_the_workspace():
    from torve.application.ports import SandboxSpec
    from torve.config.runconfig import CACHE_MOUNT

    workdir = SandboxSpec(name="n", image="i", labels={}, timeout_s=1.0).workdir
    assert not CACHE_MOUNT.startswith(f"{workdir}/")
    assert workdir != CACHE_MOUNT


# The wall-clock-only doctrine (D-35.1), measured: the same battery over
# the same tree — populating the volume, reading it back warm, and running
# again after the operator's `docker volume rm` — must decide identically.
BATTERY = "/opt/torve/project/.venv/bin/mypy /work/t.py && /opt/torve/project/.venv/bin/ruff check /work/t.py"


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_deleting_the_cache_volume_changes_nothing_but_wall_clock(tmp_path):
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.config.runconfig import CACHE_MOUNT

    volume = "torve-cache-conformance-0"
    runtime = DockerRuntime()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "t.py").write_text("x: int = 1\nprint(x)\n", encoding="utf-8")

    built = _build_battery(LAYER_IMAGE, _stage_battery_context(tmp_path))
    assert built.returncode == 0, built.stderr[-4000:]

    def run_pass(name: str) -> tuple[int | None, str, str]:
        handle = runtime.create(cache_spec(workspace, {volume: CACHE_MOUNT}, name=name), workspace)
        try:
            battery = runtime.exec(handle, BATTERY, 600)
            homes = runtime.exec(handle, "ls /opt/torve/cache", 60)
        finally:
            runtime.destroy(handle)
        return battery.exit_code, battery.output, homes.output

    def delete_volume() -> None:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)

    try:
        delete_volume()  # start from the operator's cold truth

        populating = run_pass("torve-cache-conformance-a1")
        assert populating[0] == 0, populating[1]
        reused = run_pass("torve-cache-conformance-a2")
        assert reused[0] == 0, reused[1]

        # The caches really went through the mount — the roster's homes
        # live on the volume, not in the container's throwaway /tmp.
        assert {"mypy", "ruff"} <= set(reused[2].split())

        # Warm re-run decides identically to the cold pass that populated.
        assert reused[1] == populating[1]

        # Delete-is-always-safe: `docker volume rm` is the eviction policy,
        # and the pass that follows one is indistinguishable from the first
        # in anything but the seconds it spent (D-35.1).
        delete_volume()
        deleted = run_pass("torve-cache-conformance-a3")
        assert deleted[0] == 0, deleted[1]
        assert deleted[1] == populating[1]
    finally:
        delete_volume()
        _remove_battery(LAYER_IMAGE)


# ....................... #
# The transfer ledger (RFC 0041 §5.3, D-41.5): a transferring runtime books
# each leg's wire bytes and seconds against the task its sandbox is labelled
# with, and the attempt-row builders drain the booking into a `transfer`
# block beside the agent block. A mounting runtime — Docker — transfers
# nothing and its rows lack the key outright: absent stays absent (D-4.6),
# which is also how an attempt that synced nothing tells itself apart from
# one whose sync-out moved zero bytes.


def attempt_task(task_id: str) -> Task:
    return Task(id=task_id, decisions=[])


def ledger_spec(tmp_path: Path, task_id: str, labels: dict[str, str] | None = None):
    from torve.application.ports import SandboxSpec

    return SandboxSpec(
        name=f"torve-ledger-{uuid.uuid4().hex[:8]}",
        image=LAYER_IMAGE,
        labels=naming.labels(task_id, uuid.uuid4().hex, Path.cwd()) if labels is None else labels,
        timeout_s=60,
        workdir=str(tmp_path / "remote"),
    )


def _ledger_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "f.py").write_text("payload" * 400, encoding="utf-8")
    return workspace


def test_seed_and_sync_out_costs_ride_the_attempt_row(tmp_path):
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = _ledger_workspace(tmp_path)

    handle = runtime.create(ledger_spec(tmp_path, "T-9920"), workspace)

    try:
        wrote = runtime.exec(handle, "echo produced > out.txt", 30)
        assert wrote.exit_code == 0
        runtime.sync_out(handle, workspace)
    finally:
        runtime.destroy(handle)
        opensandbox_stub.REGISTRY.clear()

    assert (workspace / "out.txt").read_text().strip() == "produced"  # bytes really moved

    row = build_attempt_row(
        attempt_task("T-9920"),
        {"adapter": "fake"},
        verdict="agent_timeout",
        exit_code=None,
        timed_out=True,
    )
    transfer = row["transfer"]
    assert set(transfer) == {
        "seed_bytes",
        "seed_seconds",
        "sync_out_bytes",
        "sync_out_seconds",
    }
    assert transfer["seed_bytes"] > 0 and transfer["sync_out_bytes"] > 0
    assert transfer["seed_seconds"] > 0 and transfer["sync_out_seconds"] > 0

    # Draining is consuming: the booking belongs to the one row that ended
    # the attempt, and a second row for the same task sees nothing.
    again = build_attempt_row(
        attempt_task("T-9920"),
        {"adapter": "fake"},
        verdict="agent_timeout",
        exit_code=None,
        timed_out=True,
    )
    assert "transfer" not in again


def test_a_sandbox_never_synced_out_reports_its_seed_only(tmp_path):
    # The timed-out agent never reaches sync_out, but the seed leg spent
    # its bytes and seconds — the row must show the half it owes.
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = _ledger_workspace(tmp_path)

    handle = runtime.create(ledger_spec(tmp_path, "T-9921"), workspace)
    runtime.destroy(handle)
    opensandbox_stub.REGISTRY.clear()

    row = build_attempt_row(
        attempt_task("T-9921"),
        {"adapter": "fake"},
        verdict="agent_timeout",
        exit_code=None,
        timed_out=True,
    )
    assert set(row["transfer"]) == {"seed_bytes", "seed_seconds"}


def test_both_sandboxes_of_an_attempt_sum_into_one_block():
    # The agent's sandbox and its -gates battery (D-3.8) move the same tree
    # twice; the attempt paid for both trips, so the block carries the sum.
    record_transfer("T-9924", seed_bytes=100, seed_seconds=1.0)
    record_transfer("T-9924", seed_bytes=50, sync_out_bytes=60, sync_out_seconds=2.0)

    row = build_attempt_row(
        attempt_task("T-9924"),
        {"adapter": "fake"},
        verdict="gates_red",
        exit_code=1,
        timed_out=False,
    )
    assert row["transfer"] == {
        "seed_bytes": 150,
        "seed_seconds": 1.0,
        "sync_out_bytes": 60,
        "sync_out_seconds": 2.0,
    }


def test_the_gate_pass_row_carries_the_block_beside_the_agent():
    record_transfer("T-9926", seed_bytes=3, seed_seconds=0.25)

    ctx = GateContext(
        root=Path("."),
        manifest=Manifest(gates=[]),
        head_sha="x",
        base=None,
        merge_base=None,
        task=attempt_task("T-9926"),
    )
    row = build_record(ctx, RunReport(exit_code=0), "hash", agent={"adapter": "fake"})

    assert row["transfer"] == {"seed_bytes": 3, "seed_seconds": 0.25}
    # A sibling of the agent block, not inside it: the runtime measured the
    # transfer, the agent did not report it.
    assert row["agent"] == {"adapter": "fake"}


def test_a_shadow_replays_legs_drain_into_its_tasks_row():
    # Sandbox labels key on the infrastructure id, attempt rows on the task
    # id — the join drains both spellings.
    record_transfer(naming.shadow_id("T-9925"), seed_bytes=7, seed_seconds=0.5)

    row = build_attempt_row(
        attempt_task("T-9925"),
        {"adapter": "fake"},
        verdict="agent_timeout",
        exit_code=None,
        timed_out=True,
    )
    assert row["transfer"] == {"seed_bytes": 7, "seed_seconds": 0.5}


def test_a_mounting_runtime_books_nothing(tmp_path):
    # Absence for Docker is structural: the bind-mount adapter never calls
    # the ledger, so a row over a runtime that transferred nothing lacks the
    # key. The text check is the ratchet — a future transfer path in the
    # Docker adapter must pass through the ledger and show up on rows.
    import inspect

    from torve.adapters.runtime.docker import DockerRuntime

    assert "record_transfer" not in inspect.getsource(DockerRuntime)

    row = build_attempt_row(
        attempt_task("T-9927"),
        {"adapter": "docker"},
        verdict="agent_error",
        exit_code=1,
        timed_out=False,
    )
    assert "transfer" not in row

    # And an OpenSandbox spec that carries no task label cannot be
    # attributed to any row, so it books nothing — visibly.
    runtime = OpenSandboxRuntime(OpenSandboxConfig(), sdk=opensandbox_stub)
    workspace = _ledger_workspace(tmp_path)

    handle = runtime.create(ledger_spec(tmp_path, "T-9928", labels={}), workspace)
    try:
        runtime.sync_out(handle, workspace)
    finally:
        runtime.destroy(handle)
        opensandbox_stub.REGISTRY.clear()

    orphan = build_attempt_row(
        attempt_task("T-9928"),
        {"adapter": "fake"},
        verdict="agent_error",
        exit_code=1,
        timed_out=False,
    )
    assert "transfer" not in orphan


# ....................... #
# The live leg (RFC 0041 §5.1, D-41.3): the same conformance battery, a
# third time, against a real server — one environment variable away,
# skipped when unset, like the Postgres leg. It additionally asserts the
# two behaviours the stub structurally cannot vouch for: the platform's own
# timeout collecting a sandbox, and the reaper's enumeration and
# destroy-by-id working over a connection that never created the sandbox.
# A red live leg with a green stub leg is a stub defect — fix the stub,
# never the assertion.

LIVE_DOMAIN_ENV = "TORVE_OPENSANDBOX_TEST_DOMAIN"


def live_domain() -> str:
    return os.environ.get(LIVE_DOMAIN_ENV, "").strip()


@pytest.fixture
def live() -> str:
    domain = live_domain()
    if not domain:
        pytest.skip(
            "no live OpenSandbox server named — "
            f"set {LIVE_DOMAIN_ENV} to run this leg against a real one"
        )
    return domain


def live_runtime(domain: str) -> OpenSandboxRuntime:
    return OpenSandboxRuntime(OpenSandboxConfig(domain=domain))


def live_spec(task_id: str, timeout_s: float, prefix: str):
    from torve.application.ports import SandboxSpec

    return SandboxSpec(
        name=f"{prefix}-{uuid.uuid4().hex[:8]}",
        image=battery.TEST_IMAGE,
        labels=naming.labels(task_id, uuid.uuid4().hex, Path.cwd()),
        timeout_s=timeout_s,
        workdir="/work",
    )


@pytest.fixture
def live_runtime_case(live, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seeded.txt").write_text("from the host\n", encoding="utf-8")
    # The battery's (runtime, spec, workspace) triple, with a server-side
    # workdir — the files live in the sandbox, not on this host.
    return live_runtime(live), live_spec("T-9902", 120, "torve-conf"), workspace


def test_live_exec_and_workspace_roundtrip(live_runtime_case):
    battery.test_exec_and_workspace_roundtrip(live_runtime_case)


def test_live_exec_timeout_is_not_an_exit_code(live_runtime_case):
    battery.test_exec_timeout_is_not_an_exit_code(live_runtime_case)


def test_live_listing_and_destroy_by_id(live_runtime_case):
    battery.test_listing_and_destroy_by_id(live_runtime_case)


@pytest.mark.timeout(600)
def test_live_platform_timeout_collects_sandbox(live, tmp_path):
    # The reaper's backstop on every other platform: a sandbox whose host
    # process died anyway is collected by the server, on the timeout the
    # spec asked for. The stub cannot fake this — collection is the
    # platform's own scheduler, not our code.
    runtime = live_runtime(live)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seeded.txt").write_text("from the host\n", encoding="utf-8")

    handle = runtime.create(live_spec("T-9912", 20, "torve-timeout"), workspace)

    def visible() -> bool:
        return any(
            info.id == handle.id
            for info in runtime.list_torve_sandboxes()
            if info.labels.get(naming.LABEL_TASK) == "T-9912"
        )

    try:
        assert visible(), "the created sandbox never surfaced to a label-scoped listing"

        deadline = time.monotonic() + 300
        while visible() and time.monotonic() < deadline:
            time.sleep(5)

        assert not visible(), (
            "the platform's 20-second timeout did not collect "
            f"sandbox {handle.id} within 300 seconds"
        )
    finally:
        with contextlib.suppress(
            Exception
        ):  # a sandbox the platform collected may refuse a second destroy
            runtime.destroy(handle)


def test_live_destroy_by_id_across_connections(live, tmp_path):
    # The reaper's whole remote story: an enumeration opened on a fresh
    # connection sees the sandbox by its label, and killing it by id from
    # that connection actually kills it — the creating process' state is
    # not required.
    creator = live_runtime(live)
    reaper = live_runtime(live)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seeded.txt").write_text("from the host\n", encoding="utf-8")

    handle = creator.create(live_spec("T-9913", 300, "torve-xconn"), workspace)

    def visible_via_reaper() -> bool:
        return any(
            info.id == handle.id
            for info in reaper.list_torve_sandboxes()
            if info.labels.get(naming.LABEL_TASK) == "T-9913"
        )

    try:
        assert visible_via_reaper(), "a second connection's enumeration did not see the sandbox"
        # Enumeration is label-scoped: every row it returns carries the task
        # label, so a reaper pass can never sweep what it did not label.
        assert all(naming.LABEL_TASK in info.labels for info in reaper.list_torve_sandboxes())

        reaper.destroy_by_id(handle.id)

        deadline = time.monotonic() + 60
        while visible_via_reaper() and time.monotonic() < deadline:
            time.sleep(2)

        assert not visible_via_reaper(), (
            f"destroy_by_id over a second connection did not collect sandbox {handle.id}"
        )
    finally:
        with contextlib.suppress(Exception):  # the handle outlived its sandbox on a green pass
            creator.destroy(handle)
