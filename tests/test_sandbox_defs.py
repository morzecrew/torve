"""The image definitions as reviewed artefacts (RFC 0017 §2, D-17.2).

The engine's own CLI now ships inside every agent image, because the prompt
tells an attempt to call it — `torve log divergence` for the intake, `torve
log notes` for the poll — and a command the sandbox does not have is a
prompt instruction that cannot be followed.

The block that installs it is copied into five Dockerfiles, so the one
thing worth a test is that they are still one block. A shared base image is
what removes the duplication; until then this is what keeps the copies from
drifting into five slightly different sandboxes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DEFINITIONS = Path(".torve/sandbox")
FRAGMENT = DEFINITIONS / "_torve-cli.dockerfile"
# The battery image is the gates' socket image and installs no harness and
# no CLI: gates run the engine from the workspace's own environment.
AGENTS = ("claude", "codex", "dsh", "mimo", "opencode")


@pytest.mark.parametrize("name", AGENTS)
def test_every_agent_image_carries_the_cli_block_verbatim(name):
    definition = (DEFINITIONS / name / "Dockerfile").read_text(encoding="utf-8")

    assert definition.endswith(FRAGMENT.read_text(encoding="utf-8")), (
        f"{name}'s Dockerfile has drifted from {FRAGMENT} — edit the fragment "
        "and copy it into every definition, or the sandboxes stop being alike"
    )


def test_the_block_installs_the_cli_where_a_sandbox_can_reach_it():
    block = FRAGMENT.read_text(encoding="utf-8")

    # Its own environment, not the workspace's: the repository under work
    # may not be a Python project at all.
    assert "uv venv --python 3.13 /opt/torve/cli" in block
    assert "ln -sf /opt/torve/cli/bin/torve /usr/local/bin/torve" in block
    # Locked, so the image's dependencies are the repository's reviewed set
    # and a rebuild is a visible regime change rather than a resolution.
    assert "--locked" in block
    # And the build proves the verb exists rather than assuming it.
    assert "torve --version" in block


def test_a_definition_without_the_project_still_builds():
    # The guard is what keeps `docker build .torve/sandbox/<name>` working
    # for anyone who has not staged a context (D-17.2's reviewed artefact
    # stays buildable by hand).
    block = FRAGMENT.read_text(encoding="utf-8")

    assert "if [ -f /opt/torve/build-context/pyproject.toml ]" in block
    assert "rm -rf /opt/torve/build-context" in block


def test_every_definition_the_build_verb_lists_is_a_directory():
    from torve.cli.sandbox import definition_names

    # The fragment sits beside the definitions and is not one of them.
    listed = definition_names(Path("."))

    assert set(AGENTS) <= set(listed)
    assert "_torve-cli.dockerfile" not in listed


def test_the_staged_inputs_follow_the_wheel_rather_than_a_list():
    from torve.cli.sandbox import project_inputs

    staged = project_inputs(Path("."))

    # Every forced include the wheel declares: a wheel that ships migrations
    # or skills as package data cannot build without them, and this list
    # went stale the first time one was added.
    assert "src" in staged
    assert "skills" in staged
    assert "migrations" in staged
    assert "uv.lock" in staged


def test_the_staged_context_carries_the_definition_and_the_project(tmp_path):
    from torve.cli.sandbox import staged_context

    definition = tmp_path / "definition"
    definition.mkdir()
    (definition / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (definition / "seed.json").write_text("{}\n", encoding="utf-8")

    root = tmp_path / "repo"
    (root / "src" / "torve").mkdir(parents=True)
    (root / "src" / "torve" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "torve" / "__pycache__").mkdir()
    (root / "src" / "torve" / "__pycache__" / "stale.pyc").write_bytes(b"\x00")
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'torve'\n[tool.hatch.build.targets.wheel]\npackages = [\"src/torve\"]\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / "secrets.env").write_text("KEY=value\n", encoding="utf-8")

    with staged_context(definition, root) as context:
        staged = {str(one.relative_to(context)) for one in context.rglob("*") if one.is_file()}

        assert "Dockerfile" in staged
        assert "seed.json" in staged
        assert "pyproject.toml" in staged
        assert "uv.lock" in staged
        assert "src/torve/__init__.py" in staged
        # The context is a copy of what the image bakes, not of the
        # repository: a build must not be able to send what it does not need.
        assert not any(one.startswith(".venv") for one in staged)
        assert "secrets.env" not in staged
        assert not any(one.endswith(".pyc") for one in staged)

    # And it is scratch: the directory does not outlive the build.
    assert not context.exists()
