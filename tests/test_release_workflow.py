"""The release workflow's `images` job (S-0112/D-1, S-0112/D-4).

The job builds container images from the tag, checks each one runs the release
the tag names, and pushes them to the registry. A unit test cannot run any of
that, so what is held here is the shape the decisions fix: the job waits for
`publish`, and no tag is pushed until every image has been checked.

Nothing here proves a real image passes; that is the job's own run. What it
proves is that the checks cannot be reordered after a push without turning this
red.
"""

from pathlib import Path

import yaml

RELEASE = Path(".github/workflows/release.yaml")


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
