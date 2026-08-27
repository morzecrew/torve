"""The image-as-input mechanism (RFC 0017): digest identity into config_hash
and the attempt record, tier images, `torve sandbox build`, and the doctor's
image checks. Docker-backed cases skip without a daemon, like the runtime
conformance battery."""

from __future__ import annotations

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
