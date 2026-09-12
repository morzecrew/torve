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

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

DEFINITIONS = Path("sandboxes")
MANIFESTS = Path(".torve/harnesses")


class _Tolerant(yaml.SafeLoader):
    """dsh's overlay carries `!!js process.env...`, which is dsh's own tag and
    not this test's business — the shape around it is."""


_Tolerant.add_constructor("tag:yaml.org,2002:js", lambda loader, node: node.value)


def _render_dsh_model(spec: dict[str, Any], *, model: str, brokered: bool) -> Any:
    """The overlay the shipped `equip` writes for this knob.

    The generator is lifted out of the script and run, rather than
    reimplemented here: a copy of the rendering logic would pass while the image
    shipped something else, which is exactly how `reasoningEfforts: false`
    survived a green suite.
    """

    script = (DEFINITIONS / "dsh" / "toolkit" / "equip").read_text(encoding="utf-8")
    body = script.split("python3 - \"$MODEL\" <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    env = {
        **os.environ,
        "DSH_MODEL": json.dumps(spec),
        "TORVE_MODEL": model,
    }
    env.pop("TORVE_BROKER_URL", None)

    if brokered:
        env["TORVE_BROKER_URL"] = "http://127.0.0.1:1/route"

    with tempfile.TemporaryDirectory() as scratch:
        written = Path(scratch) / "model.yml"
        subprocess.run([sys.executable, "-c", body, str(written)], env=env, check=True)

        return yaml.load(written.read_text(encoding="utf-8"), Loader=_Tolerant)


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


# The definitions carrying the two scripts the engine invokes (S-0063/I-2).
# claude landed in phase 2 and dsh and mimo in phase 3; `battery` runs no agent
# and `codex` and `opencode` have no seat, so neither carries one yet.
SEATED = ("claude", "dsh", "mimo")


@pytest.mark.parametrize("name", SEATED)
def test_every_seated_definition_answers_the_seam(name: str) -> None:
    """S-0063/I-2: the engine invokes `/opt/torve/equip` and then
    `/opt/torve/run` and knows nothing else about either, so a definition a
    seat can name has to carry both."""

    definition = DEFINITIONS / name

    for script in ("run", "equip"):
        assert (definition / "toolkit" / script).is_file(), f"{name} carries no {script}"

    dockerfile = (definition / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY toolkit/ /opt/torve/" in dockerfile
    assert "chmod +x /opt/torve/run /opt/torve/equip" in dockerfile


@pytest.mark.parametrize("name", SEATED)
def test_the_seam_reads_what_the_engine_names_and_nothing_else(name: str) -> None:
    """The contract is five variables (S-0063/D-2). A script reaching for a
    sixth is a harness's shape leaking back into the engine's vocabulary —
    which is the mistake S-0062/D-2 made once and phase 3 exists to catch."""

    NAMED = {
        "TORVE_PROMPT",
        "TORVE_MODEL",
        "TORVE_EQUIPMENT",
        "TORVE_EQUIP_ROOT",
        "TORVE_OUTPUT",
        "TORVE_BROKER_URL",
        "TORVE_BROKER_TOKEN",
    }
    scripts = "".join(
        (DEFINITIONS / name / "toolkit" / script).read_text(encoding="utf-8")
        for script in ("run", "equip")
    )
    reached = set(re.findall(r"TORVE_[A-Z_]+", scripts))

    assert reached <= NAMED, f"{name} reads {sorted(reached - NAMED)}, which the engine never sets"


def test_each_harness_answers_the_manifest_its_own_way() -> None:
    """The finding phase 3 exists for, kept where it can be read: one manifest,
    three translations, and no variable had to change to admit them."""

    equip = {
        name: (DEFINITIONS / name / "toolkit" / "equip").read_text(encoding="utf-8")
        for name in SEATED
    }

    # claude has a session flag per kind.
    assert "--plugin-dir" in equip["claude"]
    # dsh has one channel and every kind travels it.
    assert "--patch" in equip["dsh"]
    # mimo has no session channel at all: its equipment is installed state,
    # so its `equip` runs a command and writes an empty argument file.
    assert '"mimo", "plugin"' in equip["mimo"]
    assert "--plugin-dir" not in equip["mimo"]
    assert "--patch" not in equip["mimo"]


def test_no_definition_bakes_a_model(name: str = "dsh") -> None:
    """S-0063/D-15: a model is a provider, an API dialect, a catalog entry and a
    default — values an operator chose, not facts about the image. The dsh image
    baked seven of them, which made the fleet's roster a property of the
    harness and every new model a rebuild."""

    definition = DEFINITIONS / name

    assert not list(definition.glob("*.yml")), (
        f"{name} bakes a model file; a model is the seat's `env` (S-0063/D-15)"
    )
    assert "/opt/torve/overlays" not in (definition / "Dockerfile").read_text(encoding="utf-8")

    # And the generator that replaced them reads the knob rather than a roster.
    equip = (definition / "toolkit" / "equip").read_text(encoding="utf-8")

    assert "DSH_MODEL" in equip


def test_the_dsh_knob_renders_every_endpoint_fact_the_overlays_carried() -> None:
    """The regression retiring the overlays left behind, and the reason it was
    invisible: the knob carried the *model* facts and dropped the *endpoint*
    ones, which five of the seven deleted files had carried identical copies of.

    Measured against the ModelStudio token plan on 2026-09-11 — the endpoint
    returns 33 reasoning tokens for a two-word reply, HTTP 400s the `developer`
    role, and returns thinking as `reasoning_content` — while the seat's own
    trace read `reasoningTokens: 0` across 33 requests, because this generator
    wrote `reasoningEfforts: false` no matter what the seat asked for.
    """

    spec = json.loads(
        yaml.safe_load(MANIFESTS.joinpath("dsh.yaml").read_text())["env"]["DSH_MODEL"]
    )

    # The knob says them,
    assert spec["reasoning"] == "medium"
    assert spec["timeout_ms"] and spec["stream_idle_timeout_ms"]
    assert spec["compat"] == {"supportsDeveloperRole": False, "thinkingFormat": "deepseek"}

    # and the generator renders them rather than a constant.
    rendered = _render_dsh_model(spec, model="qwen3.8-flash", brokered=True)
    route = rendered[0]["config"]["providers"][spec["provider"]]

    assert route["timeoutMs"] == spec["timeout_ms"]
    assert route["streamIdleTimeoutMs"] == spec["stream_idle_timeout_ms"]
    assert route["compat"] == spec["compat"]
    assert route["reasoning"] == "medium"

    # The map is what makes any effort reachable; `false` strips the capability.
    efforts = route["models"][0]["reasoningEfforts"]

    assert efforts is not False
    assert {"low", "medium", "high"} <= set(map(str, efforts))

    # A seat that names no effort still gets a well-formed catalog.
    off = _render_dsh_model({**spec, "reasoning": ""}, model="m", brokered=True)

    assert off[0]["config"]["providers"][spec["provider"]]["models"][0]["reasoningEfforts"] is False


@pytest.mark.parametrize("name", ("dsh", "mimo"))
def test_a_skill_reaches_the_harness_that_reads_one(name: str) -> None:
    """S-0063/D-16, measured: dsh watches `.agents/skills`, mimo reads
    `.mimocode/skill/`. Both take the kind now, so S-0062/D-10's prompt
    paragraph stands for no harness this repository builds."""

    from torve.config.agents import load_harness

    equip = (DEFINITIONS / name / "toolkit" / "equip").read_text(encoding="utf-8")
    expected = {"dsh": ".agents/skills", "mimo": ".mimocode/skill"}[name]

    assert expected in equip
    assert "skill" in load_harness(Path("."), name).kinds


def test_dsh_installs_before_it_patches() -> None:
    """S-0063/D-17, measured against 0.1.1-rc.2: `--patch` configures an entry
    the profile already carries and refuses an unknown id with `patch: entry
    "..." not found`. An item that would add a plugin has to install it first."""

    equip = (DEFINITIONS / "dsh" / "toolkit" / "equip").read_text(encoding="utf-8")
    install = equip.index('"dsh", "plugin"')
    patch = equip.index("fragments.append")

    assert install < patch, "the install has to come before the patch it configures"


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

    from torve.cli.sandbox import harness_kind, image_tag

    for name in EVERY:
        assert harness_kind(image_tag(name)) == name


@pytest.mark.parametrize(
    ("image", "kind"),
    [
        ("claude-sandbox:2.1.252", "claude"),
        ("ghcr.io/morzecrew/claude-sandbox:2.1.252", "claude"),
        ("registry.example.com:5000/org/claude-sandbox:2.1.252", "claude"),
        ("claude-sandbox@sha256:abc", "claude"),
        ("claude-sandbox", "claude"),
        # Not one of ours: a stock base and the engine's own published image
        # answer nothing rather than naming a harness by its version, which is
        # what the old `torve-agent:<name>` spelling did (S-0063/D-6).
        ("python:3.13-slim", ""),
        ("ghcr.io/morzecrew/torve-agent:0.1.1", ""),
        ("", ""),
    ],
)
def test_the_name_is_what_survives_a_push(image: str, kind: str) -> None:
    """A push changes the repository prefix and the version tag, so the name in
    the middle is what names the definition under `sandboxes/`."""

    from torve.cli.sandbox import harness_kind

    assert harness_kind(image) == kind


def test_the_claude_image_keeps_no_bookkeeping() -> None:
    """The hand-kept copy of the harness's own installed-plugins state is gone:
    `--plugin-dir` reaches the same result through a supported flag, so
    S-0061/D-6's renderer retired with the road that fed it (S-0062/D-9)."""

    definition = DEFINITIONS / "claude"
    dockerfile = (definition / "Dockerfile").read_text(encoding="utf-8")

    assert "installed_plugins.json" not in dockerfile
    assert not list(definition.glob("seed-*.json"))


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


def test_no_definition_bakes_a_plugin() -> None:
    """S-0063/D-9: a plugin is equipment, fetched host-side into a cache the
    seat mounts. The claude image used to carry pinned clones so the retired
    renderer could write bookkeeping beside them; two copies of a repository
    with one reader was the whole cost of keeping them."""

    for name in SEATED:
        dockerfile = (DEFINITIONS / name / "Dockerfile").read_text(encoding="utf-8")

        assert "git clone" not in dockerfile, f"{name} bakes a clone"
        assert "/opt/torve/seed" not in dockerfile


def test_no_seated_definition_reads_a_mounted_credential() -> None:
    """S-0063/D-18: the seats this repository dispatches to take a credential
    by variable name — one token for one attempt, nothing persisted. The
    volume route stays for a harness with no env form; where one is used it is
    mounted read-write, because a harness that cannot persist a refreshed
    token does not fail, it hangs."""

    for name in SEATED:
        run = (DEFINITIONS / name / "toolkit" / "run").read_text(encoding="utf-8")

        assert "/auth/.credentials.json" not in run, f"{name} reads a mounted credential"


def test_a_manifest_declares_its_dialects_and_names_no_credential() -> None:
    """Under a broker the seat names none at all (S-0021/D-1): the run-scoped
    token arrives as `TORVE_BROKER_TOKEN` and the image's own `run` maps it
    onto the harness's variable, so the real key never leaves the host.

    Unbrokered, a seat names its variable and it is forwarded by name
    (S-0063/D-18) — which route a seat takes is the run's business, not this
    file's. A manifest names no credential at all now: a credential is the
    provider's, and what a manifest owes instead is the dialects its harness
    speaks (S-0064/D-4, S-0064/D-9)."""

    from torve.config.agents import load_harness

    for name in ("claude-subscription", "dsh"):
        manifest = load_harness(Path("."), name)

        assert manifest.api, f"{name} names no dialect its harness speaks"
        # The auth fields keep their model defaults because neither manifest
        # names one: they stay for a harness with no env form, such as codex.
        assert manifest.auth_volume == "torve-auth"


def test_no_equip_writes_to_a_path_the_repository_owns() -> None:
    """S-0063/D-19. `.agents/skills` is the convention a repository keeps its
    own reviewed skills in — this one tracks six, including a `flag-dont-flip`
    the equipment also ships. Writing there overwrote it, and `git add -A`
    committed the overwrite before any gate could object."""

    for name in SEATED:
        equip = (DEFINITIONS / name / "toolkit" / "equip").read_text(encoding="utf-8")
        code = [
            line
            for line in equip.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        assert not any(".agents/skills" in line for line in code), (
            f"{name} writes where the repository owns"
        )


@pytest.mark.parametrize("name", ("dsh", "mimo"))
def test_a_harness_reading_the_workspace_declares_its_root(name: str) -> None:
    """One declaration, two readers: the engine excludes it and names it, and
    `equip` writes where it was told. A path written in two places drifts."""

    from torve.config.agents import load_harness

    manifest = load_harness(Path("."), name)
    equip = (DEFINITIONS / name / "toolkit" / "equip").read_text(encoding="utf-8")

    assert manifest.equip_root
    assert "TORVE_EQUIP_ROOT" in equip
    # And refuses rather than guessing when the engine named none.
    assert "equip_root" in equip


def test_claude_declares_a_root_outside_the_workspace() -> None:
    """`--add-dir` does not load skills — measured, a session given five
    through it listed only claude's built-ins. Skills load from a skills root,
    and claude's is under HOME, which in a sandbox is `/tmp`: outside the
    workspace, so nothing lands in the repository and there is nothing to hide
    from the commit (S-0063/D-19)."""

    from torve.config.agents import load_harness

    root = load_harness(Path("."), "claude-subscription").equip_root

    assert root.startswith("~")

    # The flag is named in a comment saying why it is not used; what matters is
    # that no code path composes it.
    equip = (DEFINITIONS / "claude" / "toolkit" / "equip").read_text(encoding="utf-8")
    code = [ln for ln in equip.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]

    assert not any("--add-dir" in line for line in code)


@pytest.mark.parametrize("name", SEATED)
def test_every_seated_harness_declares_where_equipment_lands(name: str) -> None:
    """One root per harness, declared: equipment never mixes with the skills a
    repository keeps for itself, whichever convention that harness reads."""

    from torve.config.agents import load_harness

    manifest = {"claude": "claude-subscription"}.get(name, name)

    assert load_harness(Path("."), manifest).equip_root, f"{name} declares no equip_root"
