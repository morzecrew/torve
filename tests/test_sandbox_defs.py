"""The image definitions as reviewed artefacts (S-0017/the-image-is-an-input-not-an-environment, S-0063/D-6).

They live at `sandboxes/<name>/` and build to `<name>-sandbox` through
`bake.hcl`, which is where the layer five of them used to copy is a real
dependency instead of a test holding the copies in step (S-0063/D-7).

What is worth a test here is what a build cannot tell you: that every
definition inherits the base rather than a stock image, that the base bakes
exactly the project inputs the wheel declares, and that `bake.hcl` names a
target for every definition — a definition nothing builds is a definition
that quietly stops existing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEFINITIONS = Path("sandboxes")
BAKE = Path("bake.hcl")
BASE = DEFINITIONS / "base" / "Dockerfile"
# The battery image is the gates' socket image and installs no harness; it
# inherits the base like the rest, because the CLI and uv are what it needed
# the duplicated block for.
AGENTS = ("claude", "codex", "dsh", "mimo", "opencode")
EVERY = (*AGENTS, "battery")


@pytest.mark.parametrize("name", EVERY)
def test_every_definition_inherits_the_base(name: str) -> None:
    """S-0063/D-7: one base, so the CLI layer is built once. A definition
    that names a stock image instead is a sandbox with no `torve` in it, and
    the prompt tells an attempt to call `torve log divergence`."""

    definition = (DEFINITIONS / name / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG BASE=sandbox-base" in definition
    assert "FROM ${BASE}" in definition


@pytest.mark.parametrize("name", EVERY)
def test_no_definition_carries_its_own_copy_of_the_cli_layer(name: str) -> None:
    """The duplication S-0063/D-7 removed, kept removed. This is the shape
    the old pin test guarded, inverted: not "are the copies alike" but "is
    there a copy at all"."""

    definition = (DEFINITIONS / name / "Dockerfile").read_text(encoding="utf-8")

    assert "uv venv --python 3.13 /opt/torve/cli" not in definition
    assert "/opt/torve/build-context" not in definition


def test_the_base_installs_the_cli_where_a_sandbox_can_reach_it() -> None:
    block = BASE.read_text(encoding="utf-8")

    # Its own environment, not the workspace's: the repository under work
    # may not be a Python project at all.
    assert "uv venv --python 3.13 /opt/torve/cli" in block
    assert "ln -sf /opt/torve/cli/bin/torve /usr/local/bin/torve" in block
    # Locked, so the image's dependencies are the repository's reviewed set
    # and a rebuild is a visible regime change rather than a resolution.
    assert "--locked" in block
    # And the build proves the verb exists rather than assuming it.
    assert "torve --version" in block
    # As a uid that is not the builder's: a sandbox runs as the host's uid.
    assert "nobody" in block


def test_the_base_bakes_every_input_the_wheel_declares() -> None:
    """The list moved from `project_inputs` staging a context into a `COPY`
    in the base, and a hand-kept list in a Dockerfile goes stale the same way
    a hand-kept list in Python did — which is what happened the first time a
    forced include was added."""

    from torve.cli.sandbox import PROJECT_INPUTS, project_inputs

    block = BASE.read_text(encoding="utf-8")
    declared = project_inputs(Path("."))

    assert set(declared) == set(PROJECT_INPUTS), (
        "the wheel declares inputs the base does not name — add them to the "
        f"COPY lines in {BASE} and to PROJECT_INPUTS"
    )

    for one in declared:
        assert re.search(rf"^COPY .*\b{re.escape(one)}\b", block, re.MULTILINE), (
            f"{BASE} bakes no {one!r}, which the wheel needs to build"
        )


def test_bake_names_a_target_for_every_definition() -> None:
    """A definition `bake.hcl` does not name is a definition nothing builds
    — the drafting gate checks the same thing for a proposed one."""

    baked = BAKE.read_text(encoding="utf-8")

    for name in EVERY:
        assert f'target "{name}"' in baked
        assert f"{name}-sandbox" in baked


def test_the_base_is_not_a_sandbox_anyone_can_run() -> None:
    """It carries no harness, so no seat may name it and nothing resolves
    its digest at dispatch. It is a definition directory all the same, which
    is why the listing has to exclude it by name."""

    from torve.cli.sandbox import definition_names

    listed = definition_names(Path("."))

    assert set(EVERY) <= set(listed)
    assert "base" not in listed
    assert (DEFINITIONS / "base" / "Dockerfile").is_file()


def test_an_image_tag_names_its_definition_back() -> None:
    """S-0063/D-6: the tag and the directory are the same fact, so `doctor`
    can ask whether an image it finds is still defined here."""

    from torve.adapters.runtime.plugins import harness_kind
    from torve.cli.sandbox import image_tag

    for name in EVERY:
        assert harness_kind(image_tag(name)) == name
        assert harness_kind(f"ghcr.io/morzecrew/{image_tag(name)}:2.1.252") == name

    # A reference that is not one of ours answers nothing rather than
    # guessing: a stock base is nobody's definition to check.
    assert harness_kind("python:3.13-slim") == ""
    assert harness_kind("ghcr.io/morzecrew/torve-agent:0.1.1") == ""


def test_a_consuming_repository_keeps_its_own_hook(tmp_path: Path) -> None:
    """S-0063/D-6: `sandboxes/` is torve's source; `.torve/sandbox/` is where
    a repository torve works on puts a definition of its own, which is what
    S-0055/D-30 says about everything torve owns there."""

    from torve.cli.sandbox import definition_names, definitions_root

    consumer = tmp_path / "repo"
    (consumer / ".torve" / "sandbox" / "house").mkdir(parents=True)
    (consumer / ".torve" / "sandbox" / "house" / "Dockerfile").write_text(
        "FROM scratch\n", encoding="utf-8"
    )

    assert definitions_root(consumer) == consumer / ".torve" / "sandbox"
    assert definition_names(consumer) == ["house"]
