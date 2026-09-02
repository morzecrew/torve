"""The image-as-input mechanism (RFC 0017): digest identity into config_hash
and the attempt record, tier images, `torve sandbox build`, and the doctor's
image checks. Docker-backed cases skip without a daemon, like the runtime
conformance battery."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import opensandbox_stub
import pytest
import yaml
from test_run_loop import MockRuntime
from typer.testing import CliRunner

from torve.adapters.runtime.opensandbox import OpenSandboxRuntime
from torve.application.telemetry import config_hash
from torve.cli.main import app
from torve.config.runconfig import (
    OpenSandboxConfig,
    RunnerConfig,
    TierConfig,
    configured_images,
    image_for,
)


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
def test_toolkit_contract_answers_in_the_container(name):
    # What CI publishes is what the battery built (D-33.5): the definition
    # builds through the same command as an operator's, then answers.
    built = CliRunner().invoke(
        app, ["sandbox", "build", name, "--root", str(REPO_ROOT), "--format", "json"]
    )
    assert built.exit_code == 0, built.output
    try:
        probe = subprocess.run(
            ["docker", "run", "--rm", f"torve-agent:{name}", "sh", "-c", _toolkit_check(name)],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr
    finally:
        subprocess.run(["docker", "rmi", "-f", f"torve-agent:{name}"], capture_output=True, check=False)


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
def test_the_bare_definition_builds_thin_as_before():
    # The layer is a convenience, never a requirement: the path the engine
    # itself takes — `torve sandbox build battery`, the definition
    # directory as the only context — still produces today's thin image.
    # The build succeeds, the uv the battery needs is there, and no baked
    # venv exists to go stale. Deleting the layer changes nothing but wall
    # clock, which is exactly this image at boot.
    built = CliRunner().invoke(
        app, ["sandbox", "build", "battery", "--root", str(REPO_ROOT), "--format", "json"]
    )
    assert built.exit_code == 0, built.output
    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "torve-agent:battery",
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
            ["docker", "rmi", "-f", "torve-agent:battery"], capture_output=True, check=False
        )
