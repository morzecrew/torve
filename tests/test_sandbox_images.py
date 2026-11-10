"""The image-as-input mechanism (S-0017): digest identity into config_hash
and the attempt record, tier images, `torve sandbox digest`, and the doctor's
image checks. The engine does not build any more (S-0063/D-11), so a case that
needs an image bakes one — `docker buildx bake` under a throwaway tag, which is
the same definition an operator's `just images` builds. Docker-backed cases skip
without a daemon, like the runtime conformance battery."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from test_run_loop import MockRuntime
from typer.testing import CliRunner

from torve.application.telemetry import (
    config_hash,
)
from torve.base import naming
from torve.cli.main import app
from torve.config.runconfig import (
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
    import uuid

    root = seed_repo(tmp_path, {})
    # The tag is the daemon's, not this run's: another suite on the same host
    # building and removing a fixed `probe-sandbox` turned this test red.
    name = f"probe-{uuid.uuid4().hex[:12]}"
    definition = root / "sandboxes" / name
    definition.mkdir(parents=True)
    tag = f"{name}-sandbox"

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
            app, ["sandbox", "digest", name, "--root", str(root), "--format", "json"]
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

# name -> (npm package, version ARG): the two publishable definitions.
PUBLISHABLE = {
    "claude": ("@anthropic-ai/claude-code", "CLAUDE_VERSION"),
    "dsh": ("@deepseek-ai/dsh", "DSH_VERSION"),
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
        if (_definition_dockerfile(name).parent / "harness" / "package-lock.json").is_file():
            # A locked tree (dsh): `npm ci` installs what the lock holds, and
            # the build refuses a lock whose harness is not the ARG.
            assert "npm ci" in text, f"{name}: a locked harness installs with npm ci"
            assert re.search(rf'= "\${{{arg}}}"', text), (
                f"{name}: the build does not check the lock against ${{{arg}}}"
            )
            continue
        assert re.search(rf"npm install -g {re.escape(package)}@\${{{arg}}}", text), (
            f"{name}: the install does not consume ${{{arg}}}"
        )


def test_a_locked_harness_lock_holds_the_arg_s_version():
    """dsh's carets let a rebuild of the same version pull a newer cordis that
    stopped the profile booting (2026-09-28), so its tree is a lockfile. The
    lock, the manifest and the ARG name one version, or a bump moved one of
    three and the build's check is all that stands between it and a silent
    harness change."""

    harness = REPO_ROOT / "sandboxes" / "dsh" / "harness"
    arg = re.search(
        r"^ARG\s+DSH_VERSION=(\S+)\s*$",
        _definition_dockerfile("dsh").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    manifest = json.loads((harness / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((harness / "package-lock.json").read_text(encoding="utf-8"))

    assert arg is not None
    assert manifest["dependencies"]["@deepseek-ai/dsh"] == arg.group(1)
    assert lock["packages"]["node_modules/@deepseek-ai/dsh"]["version"] == arg.group(1)


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
        # The `/opt/dsh` transition symlinks and the overlays they pointed at
        # went at 484e22f3, when the image stopped baking models (S-0063/D-15);
        # this entry kept asking for them in the opt-in battery nobody ran.
        "symlinks": {},
    },
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
    # The keys 0.2.1-rc.5's entries read. `candidates: []` and `roots:` stood
    # here until then and matched a door dsh never opened a file for.
    "dsh": [
        "instructionFileCandidates: []",
        "localInstructionFileCandidates: []",
        "includeDefaultRoots: false",
        "customSkillDirs:",
        '--patch "$DOOR"',
    ],
}


def _door_bytes(name: str) -> str:
    """`run`, plus the rows it patches in where a definition keeps them in a
    file: dsh's door moved out of a heredoc so the build's smoke check reads
    the same bytes the seat does, and a copy with one reader is what let a
    dropped key through in the first place."""

    toolkit = REPO_ROOT / "sandboxes" / name / "toolkit"
    parts = [(toolkit / "run").read_text(encoding="utf-8")]
    for door in (toolkit / "door.yml", toolkit / "door.env"):
        if door.is_file():
            parts.append(door.read_text(encoding="utf-8"))

    return "\n".join(parts)


@pytest.mark.parametrize("name", sorted(DOOR))
def test_every_seated_definition_shuts_its_own_door(name: str) -> None:
    shut = _door_bytes(name)

    for switch in DOOR[name]:
        assert switch in shut, f"{name} no longer shuts the door with {switch}"


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
# The trace each image emits (S-0066/D-3): a machine-readable stream whose last
# line is still the result envelope the engine parses, so the burn parser is fed
# without moving the seam. Two failures to catch — an image that stops emitting
# the per-turn lines, and one that emits them *after* the envelope, which would
# hand the engine's last-JSON-line-wins rule a turn to parse as a result.

# What each image's stream looks like in its own toolkit, and the file it is
# spelled in.
STREAM = {
    "claude": ("run", ["--output-format stream-json", "--verbose"]),
    "dsh": ("report-usage", ['"turn"', '"tool-call"', "for (const event of stream)"]),
}


def _toolkit_text(name: str, script: str) -> str:
    return (REPO_ROOT / "sandboxes" / name / "toolkit" / script).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(STREAM))
def test_every_image_emits_the_stream_the_burn_parser_reads(name: str) -> None:
    script, marks = STREAM[name]
    text = _toolkit_text(name, script)

    for mark in marks:
        assert mark in text, f"{name} no longer emits its trace with {mark}"


def test_the_result_envelope_is_still_the_last_line() -> None:
    """dsh is the one image that composes the stream itself, so it is the one
    that can get the order wrong: the per-turn lines are printed in a loop of
    their own, and the usage envelope after it. The other two are their
    harness's native stream, which ends with its own result line."""

    text = _toolkit_text("dsh", "report-usage")

    assert text.index("for (const event of stream)") < text.index("total_cost_usd: cost")


# A dsh session log in format v4, as 0.2.1-rc.5 writes one: usage on each step's
# `assistant/message`, a retried request's on the last usage chunk of its
# `assistant/attempt` stream, and every dispatched call as a `tool/call`.
_V4_LOG = [
    {"type": "session", "version": 4, "id": "session-1", "cwd": "/w"},
    {
        "type": "assistant/message",
        "data": {
            "turn": 1,
            "step": 1,
            "message": {
                "role": "assistant",
                "source": {"kind": "model", "model": "deepseek-v4.1-flash-1017"},
            },
            "usage": {
                "inputTokens": 800,
                "outputTokens": 50,
                "cacheReadTokens": 200,
                "cacheWriteTokens": 30,
            },
        },
    },
    {"type": "tool/call", "data": {"callId": "c1", "name": "bash", "arguments": "{}"}},
    {
        "type": "assistant/attempt",
        "data": {
            "stream": [
                {"type": "chunk", "chunk": {"type": "usage", "usage": {"outputTokens": 1}}},
                {
                    "type": "chunk",
                    "chunk": {"type": "usage", "usage": {"inputTokens": 100, "outputTokens": 7}},
                },
            ]
        },
    },
    {"type": "tool/call", "data": {"callId": "c2", "name": "read", "arguments": "{}"}},
    {
        "type": "tool/call",
        "data": {"callId": "c3", "name": "bash", "arguments": '{"command": "uv run pytest -q"}'},
    },
    {
        "type": "assistant/message",
        "data": {"usage": {"inputTokens": 200, "outputTokens": 9, "cacheReadTokens": 900}},
    },
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("compressed", [False, True])
def test_report_usage_reads_the_v4_log_into_what_the_parsers_take(
    tmp_path: Path, compressed: bool
) -> None:
    """The seam end to end, without an image: the shipped reporter over a v4
    log under `$DSH_HOME`, and its output through the engine's own parsers.
    Until 0.2.1-rc.5 the reporter read the v0 file under `~/.dsh`, which no
    0.1.7+ dsh writes — a bump would have reported nothing, and no error."""

    from torve.adapters.agent.harness import parse_burn, parse_metadata, parse_tool_calls

    if compressed and shutil.which("zstd") is None:
        pytest.skip("zstd not available")

    session = tmp_path / "home" / "sessions" / "--w--" / "session-1"
    session.mkdir(parents=True)
    log = session / "session.v4.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in _V4_LOG), encoding="utf-8")

    if compressed:
        subprocess.run(["zstd", "-q", "--rm", str(log), "-o", f"{log}.zstd"], check=True)

    reporter = REPO_ROOT / "sandboxes" / "dsh" / "toolkit" / "report-usage"
    env = {**os.environ, "DSH_HOME": str(tmp_path / "home")}
    output = subprocess.run(
        ["node", str(reporter)], env=env, capture_output=True, text=True, check=True
    ).stdout

    meta = parse_metadata(output)

    assert meta.model_version == "deepseek-v4.1-flash-1017"
    assert (meta.input_tokens, meta.output_tokens) == (1100, 66)
    assert (meta.cache_read_tokens, meta.cache_creation_tokens) == (1100, 30)
    assert json.loads(output.splitlines()[-1])["tool_counts"] == {"bash": 2, "read": 1}

    trace = tmp_path / "trace.log"
    trace.write_text(output, encoding="utf-8")
    burn = parse_burn(trace)

    assert burn is not None
    assert (burn.turns, burn.tool_calls) == (3, 3)
    calls = parse_tool_calls(trace)
    assert [fact["name"] for fact in calls] == ["bash", "read", "bash"]
    # The arguments reach the engine, so the call reads as the test run it was.
    assert calls[2]["input"] == {"command": "uv run pytest -q"}

    # Priced, a cache write bills as input: (1100 + 30) in at 1, 66 out at 2,
    # 1100 cache reads at 0.1.
    priced = subprocess.run(
        ["node", str(reporter)],
        env={**env, "DSH_PRICE_IN": "1", "DSH_PRICE_OUT": "2", "DSH_PRICE_CACHE": "0.1"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert json.loads(priced.splitlines()[-1])["total_cost_usd"] == 0.001372

    # No session at all is no output and a clean exit, never an invented zero.
    empty = subprocess.run(
        ["node", str(reporter)],
        env={**os.environ, "DSH_HOME": str(tmp_path / "nowhere")},
        capture_output=True,
        text=True,
        check=True,
    )

    assert empty.stdout == ""


# The format switches as a command line the harness itself parses, the way the
# door's are: `--help` validates the flags and exits before anything dials. The
# pair is probed together because it is one switch — measured on claude 2.1.x,
# `-p --output-format stream-json` without `--verbose` refuses to start.
@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@builds_images
@pytest.mark.timeout(1800)
def test_the_stream_switches_are_accepted_by_the_built_harness() -> None:
    tag = f"claude-stream-probe-{uuid.uuid4().hex[:8]}"
    bake("claude", tag)

    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "HOME=/tmp",
                tag,
                "claude",
                "--output-format",
                "stream-json",
                "--verbose",
                "--help",
            ],
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
    tag = "dsh-cli-probe"
    bake("dsh", tag)

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
# points the toolchain cache homes at the mount. The arg-construction cases need no daemon;
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
# Nested mode's live round trip (S-0111/D-1..D-3): a daemon of the sandbox's
# own, and a compose project over it. Behind TORVE_IMAGE_TESTS because it pulls
# the dind image and starts a privileged container.


@builds_images
@pytest.mark.skipif(not docker_available(), reason="docker daemon not available")
@pytest.mark.timeout(1800)
def test_a_nested_sandbox_runs_a_compose_project_over_its_own_daemon(tmp_path):
    """S-0111/D-1, D-2, D-5: the side container's daemon answers the sandbox, the
    base image's `docker compose` reaches it, and a project started there lives
    and dies with the sandbox — the sandbox's published ports answer on its own
    `localhost`, because it shares the side container's namespace."""

    from torve.adapters.runtime.docker import DockerRuntime
    from torve.application.ports import NestedDocker, SandboxSpec

    tag = "base-sandbox-nested-probe"
    volume = f"torve-dind-conformance-{uuid.uuid4().hex[:8]}"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "compose.yaml").write_text(
        "services:\n"
        "  ping:\n"
        "    image: alpine:3.20\n"
        "    command: [sh, -c, 'echo nested-round-trip']\n"
        "  web:\n"
        "    image: alpine:3.20\n"
        r"    command: [sh, -c, 'while true; do printf"
        r' "HTTP/1.0 200 OK\r\n\r\nnested-port" | nc -l -p 80; done'
        "']\n"
        "    ports: ['18080:80']\n",
        encoding="utf-8",
    )

    bake("base", tag)
    runtime = DockerRuntime()
    spec = SandboxSpec(
        name=f"torve-nested-{uuid.uuid4().hex[:8]}",
        image=tag,
        labels=naming.labels("T-9907", uuid.uuid4().hex, Path.cwd()),
        timeout_s=900,
        nested=NestedDocker(image="docker:29.7.2-dind", volume=volume, ready_s=180),
    )

    try:
        handle = runtime.create(spec, workspace)
        try:
            version = runtime.exec(handle, "docker compose version", 120)
            assert version.exit_code == 0, version.output

            up = runtime.exec(handle, "docker compose up --abort-on-container-exit ping", 600)
            assert up.exit_code == 0, up.output
            assert "nested-round-trip" in up.output

            # A published port answers on the sandbox's own `localhost`: it shares
            # the side container's namespace, where the daemon binds the port.
            web = runtime.exec(handle, "docker compose up -d web", 300)
            assert web.exit_code == 0, web.output
            port = runtime.exec(
                handle,
                "curl -s --retry 20 --retry-connrefused --retry-delay 1 http://localhost:18080/",
                120,
            )
            assert "nested-port" in port.output, port.output

            # The daemon is the sandbox's own, reached at the address it was told
            # (S-0111/D-1): a TLS-only daemon on 2376 would have failed readiness.
            info = runtime.exec(handle, "docker info --format '{{.ServerVersion}}'", 60)
            assert info.exit_code == 0, info.output
        finally:
            runtime.destroy(handle)
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)
        unbake(tag)
