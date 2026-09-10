"""Plugin seeding, rendered from the declaration (S-0061/D-5, S-0061/D-6).

The three files Claude Code reads were kept by hand beside a `git checkout`
that pinned the same ref a third time, and nothing checked that the three
agreed — ponytail's ref was `v4.9.0` and its cache directory `4.9.0`. They
come off one list now, so they cannot.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from torve.adapters.runtime.docker import DockerRuntime
from torve.adapters.runtime.plugins import (
    SEED_ROOT,
    UnseededHarness,
    harness_kind,
    plugin_name,
    seed_files,
)
from torve.application.ports import SandboxHandle, SandboxSpec
from torve.config.agents import Plugin

# ----------------------- #

CAVEMAN = Plugin(source="github:JuliusBrussee/caveman", ref="81536f57b330")
PONYTAIL = Plugin(source="github:DietrichGebert/ponytail", ref="v4.9.0")


def spec(image: str, *plugins: Plugin) -> SandboxSpec:
    return SandboxSpec(
        name="s", image=image, labels={}, timeout_s=60, plugins=tuple(plugins)
    )


# ....................... #
# Harness identity is the image (S-0017/D-4), so the tag is the key


@pytest.mark.parametrize(
    ("image", "kind"),
    [
        ("torve-agent:claude", "claude"),
        ("registry.example.com/org/torve-agent:claude", "claude"),
        ("registry.example.com:5000/org/torve-agent:claude", "claude"),
        ("torve-agent:claude@sha256:abc", "claude"),
        ("python:3.13-slim", "3.13-slim"),
        ("torve-agent", ""),
        ("", ""),
    ],
)
def test_the_tag_is_what_survives_a_push(image, kind):
    """A push renames the repository and keeps the tag, so the tag names the
    definition under `.torve/sandbox/` the image was built from."""

    assert harness_kind(image) == kind


def test_a_plugin_is_named_by_the_last_segment_of_its_source():
    assert plugin_name(CAVEMAN) == "caveman"
    assert plugin_name(Plugin(source="caveman")) == "caveman"


# ....................... #
# The rendering


def test_the_three_files_agree_because_they_come_off_one_list():
    rendered = seed_files("torve-agent:claude", [CAVEMAN, PONYTAIL])

    settings = json.loads(rendered[f"{SEED_ROOT}/.claude/settings.json"])
    installed = json.loads(rendered[f"{SEED_ROOT}/.claude/plugins/installed_plugins.json"])
    known = json.loads(rendered[f"{SEED_ROOT}/.claude/plugins/known_marketplaces.json"])

    assert settings["enabledPlugins"] == {"caveman@caveman": True, "ponytail@ponytail": True}

    # The install path and the version are the declared ref, in both files —
    # this is the pair that disagreed while they were kept by hand.
    (entry,) = installed["plugins"]["ponytail@ponytail"]
    assert entry["version"] == PONYTAIL.ref
    assert entry["installPath"].endswith(f"/ponytail/ponytail/{PONYTAIL.ref}")

    assert known["caveman"]["source"] == {"source": "github", "repo": "JuliusBrussee/caveman"}


def test_a_harness_with_no_renderer_and_no_plugins_asks_for_nothing():
    """Every harness without a declaration renders nothing, so the refusal
    below is about equipment somebody asked for, never about the harness."""

    assert seed_files("torve-agent:codex", []) == {}
    assert seed_files("", []) == {}


def test_a_harness_with_no_renderer_refuses_a_declaration():
    """S-0061/D-6: an attempt quietly missing its equipment measures a regime
    nobody configured, and the record would say it ran with plugins it never
    had."""

    with pytest.raises(UnseededHarness, match="not a harness torve can seed") as excinfo:
        seed_files("torve-agent:codex", [CAVEMAN])

    assert "claude" in str(excinfo.value)  # it names what it can seed


# ....................... #
# The docker adapter writes them where the harness looks


class RecordingDocker(DockerRuntime):
    """Every `docker` invocation, without a daemon."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    def _run(self, *args: str, timeout=None, stdin=None):  # type: ignore[no-untyped-def]
        self.calls.append((args, stdin))
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")


def test_the_adapter_writes_the_rendered_files_as_root_into_the_seed():
    runtime = RecordingDocker()
    runtime._seed_plugins(SandboxHandle(id="c1", name="s"), spec("torve-agent:claude", CAVEMAN))

    written = {
        args[-1].split("cat > ", 1)[1].split(" ", 1)[0]: stdin
        for args, stdin in runtime.calls
        if stdin is not None
    }

    assert set(written) == set(seed_files("torve-agent:claude", [CAVEMAN]))
    # As root, because the seed is readable by every uid and writable by none:
    # nothing inside the attempt may edit what it was equipped with.
    assert all("root" in args for args, _ in runtime.calls)


def test_the_adapter_writes_nothing_when_nothing_is_declared():
    runtime = RecordingDocker()
    runtime._seed_plugins(SandboxHandle(id="c1", name="s"), spec("torve-agent:claude"))

    assert runtime.calls == []


def test_a_failed_write_is_an_infrastructure_failure_not_a_silent_run():
    class FailingDocker(RecordingDocker):
        def _run(self, *args: str, timeout=None, stdin=None):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(
                args=list(args), returncode=1, stdout="", stderr="no such file"
            )

    from torve.adapters.runtime.docker import DockerError

    with pytest.raises(DockerError, match="no such file"):
        FailingDocker()._seed_plugins(
            SandboxHandle(id="c1", name="s"), spec("torve-agent:claude", CAVEMAN)
        )


# ....................... #
# The image stopped keeping what is rendered


def test_the_claude_image_keeps_the_clone_and_not_the_bookkeeping():
    """The clones stay baked, because a fetch at dispatch would put the
    network inside every attempt; the files that say which are installed are
    the runtime's now."""

    definition = Path(__file__).resolve().parents[1] / ".torve" / "sandbox" / "claude"
    dockerfile = (definition / "Dockerfile").read_text(encoding="utf-8")

    assert "git clone" in dockerfile
    assert not list(definition.glob("seed-*.json"))
    assert "installed_plugins.json" not in dockerfile
