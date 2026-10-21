"""The release workflow's `images` job (S-0112/D-1, S-0112/D-4).

The job builds container images from the tag, checks each one runs the release
the tag names, and pushes them to the registry. A unit test cannot run any of
that, so what is held here is the shape the decisions fix: the job waits for
`publish`, and no tag is pushed until every image has been checked.

Nothing here proves a real image passes; that is the job's own run. What it
proves is that the checks cannot be reordered after a push without turning this
red.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

RELEASE = Path(".github/workflows/release.yaml")
SCRIPT = Path(".github/scripts/release-images.sh")


def _steps() -> list[dict]:
    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    return workflow["jobs"]["images"]["steps"]


def _text(step: dict) -> str:
    """Everything a step runs or invokes, so a push is found wherever it is."""

    return " ".join(str(step.get(one, "")) for one in ("name", "uses", "run"))


def _index(steps: list[dict], needle: str) -> int:
    return next(i for i, step in enumerate(steps) if needle in _text(step))


def test_the_images_job_runs_after_publish() -> None:
    """S-0112/D-4: the release publishes the images after `publish`, so a
    failure here leaves PyPI as it was rather than a package pinned to an image
    that was never pushed."""

    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    images = workflow["jobs"]["images"]

    assert images["needs"] == "publish"


def test_one_images_job_runs_per_tag_and_none_is_cancelled() -> None:
    """`push` asks the registry before each push, so two runs over one tag
    could both find a tag missing and push it twice (S-0112/D-1)."""

    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    concurrency = workflow["jobs"]["images"]["concurrency"]

    assert "github.ref" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_the_job_may_write_to_the_package_registry() -> None:
    """A push needs `packages: write`; the workflow grants nothing else."""

    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    permissions = workflow["jobs"]["images"]["permissions"]

    assert permissions["packages"] == "write"


def test_the_tag_decides_the_version_the_images_are_built_with() -> None:
    """S-0112/D-1: every image is tagged with the release's version, which is
    the tag's. The build step reads it from the step that computes it, never
    from the tree — a tree that does not describe the tag builds another
    version."""

    steps = _steps()
    computed = _index(steps, "compute-version-from-tag.sh")
    built = _index(steps, "release-images.sh build")

    assert computed < built
    assert "steps.ver.outputs.version" in steps[built]["env"]["VERSION"]


def test_every_check_precedes_every_push() -> None:
    """S-0112/D-4: the images are built and loaded, each is checked, and only
    then is anything pushed. A push step before the check would publish a tag
    nothing had proven."""

    steps = _steps()
    check = _index(steps, "release-images.sh check")

    pushed = [
        i
        for i, step in enumerate(steps)
        if "docker push" in _text(step) or "release-images.sh push" in _text(step)
    ]

    assert pushed, "the images job pushes nothing"
    assert check < min(pushed)


def test_the_build_loads_and_never_pushes() -> None:
    """The build and check run against images loaded into this runner
    (`--load`), so the bytes checked are the bytes pushed. A `--push` on the
    bake would publish before the checks ran."""

    built = _text(_steps()[_index(_steps(), "release-images.sh build")])

    assert "release-images.sh build" in built
    assert "push" not in built

    # The flags live in the script's own `build` branch, not the step's text;
    # its comment says why, so only the commands are read.
    branch = SCRIPT.read_text(encoding="utf-8").split("\nbuild)", 1)[1].split(";;", 1)[0]
    commands = "\n".join(line for line in branch.splitlines() if not line.strip().startswith("#"))

    assert "--load" in commands
    assert "--push" not in commands


# The script's `check` against a fake `docker` and `gh` on the PATH: every image
# runs the release, carries no harness label, and the registry answers as each
# case sets it.

_FAKE_DOCKER = """#!/bin/sh
case "$1" in
run) echo "$FAKE_VERSION" ;;
inspect) echo "<no value>" ;;
esac
"""

_FAKE_GH = """#!/bin/sh
for last; do :; done
case "$FAKE_GH:$last" in
*:orgs/morzecrew) exit 0 ;;
empty:*) exit 0 ;;
published:*) echo "$FAKE_VERSION" ;;
nearmiss:*) echo "$FAKE_VERSION" | tr . - ;;
missing:*) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
*) echo "gh: Server Error (HTTP 502)" >&2; exit 1 ;;
esac
"""


def _check(tmp_path: Path, registry_answer: str) -> subprocess.CompletedProcess[str]:
    fakes = tmp_path / "bin"
    fakes.mkdir()

    for name, body in (("docker", _FAKE_DOCKER), ("gh", _FAKE_GH)):
        (fakes / name).write_text(body, encoding="utf-8")
        (fakes / name).chmod(0o755)

    env = {
        "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}",
        "VERSION": "0.5.0",
        "REGISTRY": "ghcr.io/morzecrew",
        "FAKE_VERSION": "0.5.0",
        "FAKE_GH": registry_answer,
    }

    return subprocess.run(
        ["bash", str(SCRIPT), "check"], capture_output=True, text=True, env=env, check=False
    )


@pytest.mark.parametrize("answer", ["empty", "missing"])
def test_the_check_runs_without_a_revision_and_passes_a_first_release(
    tmp_path: Path, answer: str
) -> None:
    """The workflow passes REVISION to `build` only; `check` must not stop on
    it. A package the organisation never published answers 404, which is a
    first release, not a failure."""

    done = _check(tmp_path, answer)

    assert done.returncode == 0, done.stderr


def test_the_check_names_a_tag_already_published_and_passes(tmp_path: Path) -> None:
    """A rerun after a partial push finds some tags published. The check names
    them and passes, and `push` skips them, so the rerun publishes the rest."""

    done = _check(tmp_path, "published")

    assert done.returncode == 0, done.stderr
    assert "already published" in done.stderr


def test_the_check_stops_when_the_registry_cannot_answer(tmp_path: Path) -> None:
    """A failed registry query once read as an empty tag list, so the check
    passed and `push` could move a published tag."""

    done = _check(tmp_path, "broken")

    assert done.returncode == 1
    assert "cannot ask the registry" in done.stderr


# The script's `push` against a fake `docker` that records each push and fails
# the first `FAKE_FAILURES` of them, as GHCR's `unknown blob` failed the 0.6.0
# release's `codex-sandbox` push once.

_FAKE_PUSHING_DOCKER = """#!/bin/sh
case "$1" in
inspect) echo "<no value>" ;;
push)
  echo "$2" >> "$PUSHES"
  count=$(wc -l < "$PUSHES")
  [ "$count" -gt "${FAKE_FAILURES:-0}" ] || { echo "unknown blob" >&2; exit 1; }
  ;;
esac
"""


def _push(
    tmp_path: Path, registry_answer: str, failures: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fakes = tmp_path / "bin"
    fakes.mkdir()

    for name, body in (("docker", _FAKE_PUSHING_DOCKER), ("gh", _FAKE_GH)):
        (fakes / name).write_text(body, encoding="utf-8")
        (fakes / name).chmod(0o755)

    pushes = tmp_path / "pushes"
    pushes.touch()
    env = {
        "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}",
        "VERSION": "0.5.0",
        "REGISTRY": "ghcr.io/morzecrew",
        "FAKE_VERSION": "0.5.0",
        "FAKE_GH": registry_answer,
        "FAKE_FAILURES": str(failures),
        "PUSHES": str(pushes),
        "PUSH_RETRY_SECONDS": "0",
    }

    done = subprocess.run(
        ["bash", str(SCRIPT), "push"], capture_output=True, text=True, env=env, check=False
    )

    return done, pushes.read_text(encoding="utf-8").split()


def test_the_push_skips_every_tag_already_published(tmp_path: Path) -> None:
    done, pushes = _push(tmp_path, "published")

    assert done.returncode == 0, done.stderr
    assert pushes == []
    assert "not pushed again" in done.stderr


def test_the_push_reads_a_published_tag_as_a_fixed_string(tmp_path: Path) -> None:
    """`0-5-0` is not `0.5.0`: read as a pattern, the tag's dots matched it and
    the push skipped an image nobody had published."""

    done, pushes = _push(tmp_path, "nearmiss")

    assert done.returncode == 0, done.stderr
    assert pushes != []
    assert "not pushed again" not in done.stderr


def test_the_push_retries_a_push_the_registry_refuses(tmp_path: Path) -> None:
    done, pushes = _push(tmp_path, "empty", failures=2)

    assert done.returncode == 0, done.stderr
    # The first reference is tried three times and pushed on the third.
    assert pushes[0] == pushes[1] == pushes[2]
    assert len(pushes) == len(set(pushes)) + 2


def test_the_push_stops_after_three_refusals(tmp_path: Path) -> None:
    done, pushes = _push(tmp_path, "empty", failures=1000)

    assert done.returncode != 0
    assert len(pushes) == 3
    assert "refused three times" in done.stderr


def test_the_push_stops_when_the_registry_cannot_answer(tmp_path: Path) -> None:
    done, pushes = _push(tmp_path, "broken")

    assert done.returncode == 1
    assert pushes == []
    assert "cannot ask the registry" in done.stderr
