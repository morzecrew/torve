"""The image-as-input mechanism (S-0017): digest identity into config_hash
and the attempt record, tier images, `torve sandbox digest`, and the doctor's
image checks. The engine does not build any more (S-0063/D-11), so a case that
needs an image bakes one — `docker buildx bake` under a throwaway tag, which is
the same definition an operator's `just images` builds. Docker-backed cases skip without a daemon, like the runtime
conformance battery.

Carries two sections of its own since S-0041: the transfer ledger the
OpenSandbox adapter books per attempt (S-0041/D-5), and the live conformance leg
that runs the runtime battery a third time against a real server named by
TORVE_OPENSANDBOX_TEST_DOMAIN, plus the two assertions only a live server
can answer (S-0041/D-3)."""

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
    harness = TierConfig(adapter="harness", provider="deepseek", image="torve-agent:dsh")
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
    # No `build_image` to refuse: the port lost it with S-0063/D-11, so a
    # runtime that cannot build is every runtime.
    assert not hasattr(runtime, "build_image")


# ....................... #
# build and doctor, against the daemon


# Building a sandbox image is an operator's act, not an attempt's (S-0063/D-11):
# the engine has no way to build one, so "does this definition build" is what
# `just images` answers. Six images over this host's proxied egress took the
# acceptance battery past its 900s bound — twice, because `coverage-delta` runs
# the suite too and both raced the same bake.
#
# What still runs on every attempt is `tests/test_sandbox_defs.py`: every
# definition inherits the base, none carries its own copy of the CLI layer,
# `bake.hcl` names a target for each, and the base bakes what the wheel
# declares. Those are the drafting errors. What is gated below is whether a
# pinned npm version still resolves, which surfaces the moment anyone builds.
#
# `TORVE_IMAGE_TESTS=1 uv run pytest tests/test_sandbox_images.py` runs them.
builds_images = pytest.mark.skipif(
    not os.environ.get("TORVE_IMAGE_TESTS"),
    reason="set TORVE_IMAGE_TESTS=1 to build sandbox images",
)


def bake(target: str, tag: str, *, cwd: Path | None = None) -> None:
    """Build one definition under a throwaway tag, the way `just images`
    builds it under its own (S-0063/D-8).

    The tag is a throwaway on purpose: building under the production tag and
    `rmi`-ing it in cleanup deleted the host's live agent images from inside
    the acceptance battery once, and three mid-queue dispatches failed before
    the phantom was found.
    """

    proc = subprocess.run(
        [
            "docker",
            "buildx",
            "bake",
            "--file",
            "bake.hcl",
            target,
            "--set",
            f"{target}.tags={tag}",
            "--set",
            f"{target}.output=type=docker",
        ],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr[-4000:]


def unbake(*tags: str) -> None:
    subprocess.run(["docker", "rmi", "-f", *tags], capture_output=True, check=False)


def seed_repo(tmp_path: Path, config: dict[str, object]) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, **config}), encoding="utf-8"
    )
    return root


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_sandbox_digest_reports_an_identity_that_tracks_content(tmp_path):
    """The digest is what joins `config_hash`, so it has to move when the
    image's content does and hold still when it does not. The engine reads
    it; something else built it (S-0063/D-11)."""

    import json

    root = seed_repo(tmp_path, {})
    definition = root / "sandboxes" / "probe"
    definition.mkdir(parents=True)
    tag = "probe-sandbox"

    def build(label: str) -> None:
        definition.joinpath("Dockerfile").write_text(
            f"FROM python:3.13-slim\nLABEL torve.probe={label}\n", encoding="utf-8"
        )
        proc = subprocess.run(
            ["docker", "build", "-t", tag, str(definition)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr[-2000:]

    def reported() -> str:
        result = CliRunner().invoke(
            app, ["sandbox", "digest", "probe", "--root", str(root), "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        return str(json.loads(result.stdout)["images"][0]["digest"])

    try:
        build("one")
        digest_one = reported()

        assert digest_one.startswith("sha256:")
        # Same definition -> same identity.
        assert reported() == digest_one

        build("two")
        # Changed definition -> changed identity: the drift the hash sees.
        assert reported() != digest_one

    finally:
        unbake(tag)


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


def test_sandbox_digest_refuses_an_unknown_definition(tmp_path):
    root = seed_repo(tmp_path, {})
    result = CliRunner().invoke(app, ["sandbox", "digest", "ghost", "--root", str(root)])
    assert result.exit_code == 3, result.output


# ....................... #
# definition conventions (S-0033/tests): the publishable definitions pin
# their harness versions behind defaulted ARGs (S-0033/D-3) and keep the
# toolkit under /opt/torve/ with one transition revision of old-path
# symlinks (S-0033/D-4). The ARG-pin check is the regex-level test the RFC
# names; the toolkit contract joins the docker-gated battery below.

REPO_ROOT = Path(__file__).resolve().parents[1]

# name -> (npm package, version ARG): the three publishable definitions.
PUBLISHABLE = {
    "claude": ("@anthropic-ai/claude-code", "CLAUDE_VERSION"),
    "dsh": ("@deepseek-ai/dsh", "DSH_VERSION"),
    "mimo": ("@mimo-ai/cli", "MIMO_VERSION"),
}


def _definition_dockerfile(name: str) -> Path:
    return REPO_ROOT / "sandboxes" / name / "Dockerfile"


def test_harness_installs_ride_pinned_default_args():
    # S-0033/D-3: every harness install in a publishable definition consumes a
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


# The toolkit contract per image (S-0033/tests): what a profile's command
# template depends on. `answer` must exit 0 inside the container — for dsh
# the reporter the RFC names, for claude the plugin clone the build pins
# (its bookkeeping is rendered at dispatch now, S-0061/D-6, so the image
# no longer carries settings.json) — and every old path must survive as a
# symlink for the transition revision.
TOOLKIT = {
    "claude": {
        "answer": "test -d /opt/torve/seed/.claude/plugins/marketplaces/caveman",
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
@builds_images
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("name", ["claude", "dsh"])
def test_toolkit_contract_answers_in_the_container(name):
    """What CI publishes is what the battery built (S-0033/D-5): the
    definition bakes through the same file an operator's `just images` uses,
    then answers."""

    tag = f"{name}-toolkit-probe"
    bake(name, tag)

    try:
        probe = subprocess.run(
            ["docker", "run", "--rm", tag, "sh", "-c", _toolkit_check(name)],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr

    finally:
        unbake(tag)


# ....................... #
# The door (S-0066/D-1): an attempt's inputs are what its profile declared, so
# the files the worktree carries for other readers are shut out — by each
# image's own switch, because the three harnesses have three channels and
# nothing in the engine composes them (S-0063/D-3).
#
# Two checks for two failures. The switch going missing is a regression a file
# read answers, and it is the one that matters: a door quietly reopened is a
# regime digest that claims more than it knows. The switch going *stale* — a
# flag renamed or a value no longer accepted by a bumped harness — is the trap
# only the built image can answer, which is why the probe below builds one.

# What each image's own door reads like in its own `run`.
DOOR = {
    "claude": [
        "--setting-sources user",
        "--mcp-config '{\"mcpServers\":{}}'",
        "--strict-mcp-config",
    ],
    "dsh": ["candidates: []", '--patch "$DOOR"'],
    "mimo": ["--disable-root"],
}


@pytest.mark.parametrize("name", sorted(DOOR))
def test_every_seated_definition_shuts_its_own_door(name: str) -> None:
    run = (REPO_ROOT / "sandboxes" / name / "toolkit" / "run").read_text(encoding="utf-8")

    for switch in DOOR[name]:
        assert switch in run, f"{name} no longer shuts the door with {switch}"


def test_the_door_is_shut_before_the_seat_s_own_equipment() -> None:
    """Order is load-bearing in both directions. claude's `--strict-mcp-config`
    keeps every server `--mcp-config` names, so the empty document is the door
    and `equip`'s own servers are appended after it rather than replaced by it.
    dsh takes the last `--patch` that speaks about an entry, so a door that
    narrows a list has to be patched before an item that configures the same
    one."""

    for name in ("claude", "dsh"):
        run = (REPO_ROOT / "sandboxes" / name / "toolkit" / "run").read_text(encoding="utf-8")
        door = run.index(DOOR[name][-1])

        assert door < run.index("torve-equip.args"), f"{name} equips before it shuts the door"


# The door's switches as a command line the harness itself parses. `--help`
# exits before anything dials, and the flags are still validated: measured on
# claude 2.1.x, `--setting-sources bogus --help` exits 1. dsh's door is a
# `--patch` overlay naming entries its profile carries rather than a flag, so
# what it would prove here is a boot with a credential — it is read above.
DOOR_PROBE = {
    "claude": [
        "claude",
        "--setting-sources",
        "user",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--strict-mcp-config",
        "--help",
    ],
    "mimo": ["mimo", "run", "--disable-root", ".claude", "--help"],
}


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("name", sorted(DOOR_PROBE))
def test_the_door_switches_are_accepted_by_the_built_harness(name: str) -> None:
    """Probed rather than reasoned about: an undocumented knob that does
    nothing moves the regime digest without moving the behaviour, which is
    worse than not setting it."""

    tag = f"{name}-door-probe-{uuid.uuid4().hex[:8]}"
    bake(name, tag)

    try:
        probe = subprocess.run(
            ["docker", "run", "--rm", "-e", "HOME=/tmp", tag, *DOOR_PROBE[name]],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr[-2000:]

    finally:
        unbake(tag)


# ....................... #
# The engine's CLI inside the image (S-0017/A-2): the prompt tells an attempt
# to record divergence and to poll for notes with `torve`, so the image has to
# have it — installed in an environment of its own, and readable by the uid
# the sandbox actually runs as, which is the host's, never root's.
#
# It comes from the base now (S-0063/D-7), so this proves the inheritance
# rather than one definition's copy: the probe is an image that installs no
# CLI of its own.


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
def test_the_engine_cli_answers_in_the_container_as_a_sandbox_uid():
    tag = "mimo-cli-probe"
    bake("mimo", tag)

    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                # Not root: `docker run --user <host uid>` is how every
                # sandbox starts, and uv installs its interpreter under
                # root's home by default — which reads as "permission
                # denied" on the first verb an agent tries.
                "--user",
                "65534:65534",
                "-e",
                "HOME=/tmp",
                tag,
                "sh",
                "-c",
                "torve --version && torve log notes --root /tmp --format json",
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr
        # A sandbox with no channel says so and exits clean: the verb is
        # usable before anything is wired.
        assert '"channel": false' in probe.stdout

    finally:
        unbake(tag)


# ....................... #
# The battery's dependency layer (S-0035/D-2): pyproject.toml and uv.lock baked
# by `uv sync --all-extras --no-install-project` into a fixed
# UV_PROJECT_ENVIRONMENT, keyed to the lock's bytes so an attempt with an
# unchanged lock reconciles the delta with zero package downloads.
#
# It is no longer a convenience. The context is the repository root
# (S-0063/D-8), so the two inputs are always there and the guard that let a
# definition build without them is gone: a bake that cannot find the lockfile
# fails, which is what a missing lockfile should do.

LAYER_IMAGE = "battery-layer-probe"


def test_battery_bakes_the_lockfile_keyed_dependency_layer():
    # S-0035/D-2 at the text level, so the layer cannot silently vanish where
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


def _remove_battery(tag: str) -> None:
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
def test_unchanged_lockfile_downloads_nothing_in_an_attempt():
    # The conformance case: the baked venv checked with `uv sync --check`
    # over the exact lock bytes the bake consumed, inside a container whose
    # only network is none. A green check on a routeless container means
    # reconciling an unchanged lockfile performed no package downloads —
    # the attempt-side mirror of the bake's own sync set.
    bake("battery", LAYER_IMAGE)

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
@builds_images
@pytest.mark.timeout(1800)
def test_the_layer_is_keyed_to_the_lockfile_bytes(tmp_path):
    # A lockfile change rebuilds the layer — and the rebuild is governed by
    # the new bytes the moment the layer re-runs: a staged lock that stops
    # parsing fails the build rather than serving a stale warm lie from
    # cache. (A cached good build never re-runs the sync at all.)
    #
    # Built with `docker build` against the definition's real bytes and a
    # context of its own, because the bake's context is the repository root
    # and this case needs a lockfile the repository must not have. `ARG BASE`
    # is what makes that possible from outside bake (S-0063/D-7).
    base_tag = "sandbox-base-probe"
    probe_tag = "battery-lock-probe"
    bake("base", base_tag)

    context = tmp_path / "battery-context"
    context.mkdir()
    shutil.copy(_definition_dockerfile("battery"), context / "Dockerfile")
    shutil.copy(REPO_ROOT / "pyproject.toml", context / "pyproject.toml")
    context.joinpath("uv.lock").write_text("not a lockfile {{{\n", encoding="utf-8")

    try:
        built = subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"BASE={base_tag}",
                "-t",
                probe_tag,
                str(context),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        assert built.returncode != 0
        assert "uv.lock" in built.stdout + built.stderr

    finally:
        _remove_battery(probe_tag)
        _remove_battery(base_tag)


def test_the_layer_is_no_longer_optional():
    # It used to be: the definition guarded on the two project inputs being
    # in the context, so `torve sandbox build battery` produced a thin image
    # and only a staged context produced the warm one. The context is the
    # repository root now (S-0063/D-8), so there is one battery image and it
    # has the layer — a build that cannot find the lockfile fails instead of
    # quietly shipping something thinner than the operator asked for.
    text = _definition_dockerfile("battery").read_text(encoding="utf-8")

    assert "if [ -f pyproject.toml ]" not in text
    assert re.search(r"^COPY pyproject\.toml uv\.lock ", text, re.MULTILINE)


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
def test_the_baked_battery_carries_the_layer():
    tag = "battery-warm-probe"
    bake("battery", tag)

    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                tag,
                "sh",
                "-c",
                "uv --version >/dev/null && test -d /opt/torve/project/.venv",
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr

    finally:
        _remove_battery(tag)


# ....................... #
# The derived-cache volume at the runtime adapters (S-0035/the-derived-cache-volume,
# S-0035/D-4/D-35.5): the Docker adapter mounts what the runner names and
# points the toolchain cache homes at the mount; the opensandbox adapter
# refuses the field loudly. The arg-construction cases need no daemon;
# the cold/warm conformance case (S-0035/D-1) does.

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

    # Empty (the default) is cold exactly as today (S-0035/D-4): no exports.
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
    # S-0035/D-5: a loud refusal, never a quiet cold fallback — a warm tier on
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


# The wall-clock-only doctrine (S-0035/D-1), measured: the same battery over
# the same tree — populating the volume, reading it back warm, and running
# again after the operator's `docker volume rm` — must decide identically.
BATTERY = "/opt/torve/project/.venv/bin/mypy /work/t.py && /opt/torve/project/.venv/bin/ruff check /work/t.py"


@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
def test_deleting_the_cache_volume_changes_nothing_but_wall_clock(tmp_path):
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.config.runconfig import CACHE_MOUNT

    volume = "torve-cache-conformance-0"
    runtime = DockerRuntime()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "t.py").write_text("x: int = 1\nprint(x)\n", encoding="utf-8")

    bake("battery", LAYER_IMAGE)

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
        # in anything but the seconds it spent (S-0035/D-1).
        delete_volume()
        deleted = run_pass("torve-cache-conformance-a3")
        assert deleted[0] == 0, deleted[1]
        assert deleted[1] == populating[1]
    finally:
        delete_volume()
        _remove_battery(LAYER_IMAGE)


# ....................... #
# The transfer ledger (S-0041/the-transfer-measured, S-0041/D-5): a transferring runtime books
# each leg's wire bytes and seconds against the task its sandbox is labelled
# with, and the attempt-row builders drain the booking into a `transfer`
# block beside the agent block. A mounting runtime — Docker — transfers
# nothing and its rows lack the key outright: absent stays absent (S-0004/D-6),
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
    # The agent's sandbox and its -gates battery (S-0003/D-8) move the same tree
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
# The live leg (S-0041/live-conformance, S-0041/D-3): the same conformance battery, a
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
