"""`torve sandbox` — image definitions as reviewed artefacts (S-0017/the-image-is-an-input-not-an-environment).

Definitions live under `sandboxes/<name>/` in the repository root and build to
`<name>-sandbox` (S-0063/D-6). They are torve's own source, published for other
repositories to pull; `.torve/sandbox/` stays the hook for a consuming
repository that defines an image of its own, and this reads whichever is there.

The engine does not build. `docker buildx bake` does, through `just images`
(S-0063/D-8), and `RuntimePort.build_image` retired with the verb that called it
(S-0063/D-11) — so S-0017/D-3's rule that no build happens mid-run is structural
rather than stated. What is left here is what the engine needs to *know*: which
definitions exist, and what digest a configured image resolves to, because that
digest joins `config_hash` at dispatch (S-0017/D-1) and a rebuild must be a
visible regime change. Parsing and rendering only (S-0015/D-6).
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_ID,
    Format,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import (
    ConfigOption,
    FormatOption,
    RootOption,
    RuntimeName,
    load_config,
    runtime_for,
)
from torve.domain.states import EXIT_CONFIG

# ----------------------- #

sandbox_app = typer.Typer(
    no_args_is_help=True, help="Sandbox image definitions: what exists, and what it resolves to."
)

# Torve's own definitions, in the repository root (S-0063/D-6).
DEFINITIONS_DIR = "sandboxes"

# Where a consuming repository puts one of its own — everything torve owns in a
# repository it works on lives under `.torve/` (S-0055/D-30), and a definition
# written by that repository is exactly that.
CONSUMER_DEFINITIONS_DIR = "sandbox"

BUILD_RECIPE = "just images"

# The base every definition inherits (S-0063/D-7). It is a definition directory
# like the others and it is not a sandbox: it contains no harness, so no seat
# can name it and nothing resolves its digest at dispatch. `bake.hcl` builds it
# as the named context the others take, which is the only place it appears.
BASE_DEFINITION = "base"

# What the base's `COPY` lines bake, held against what the wheel declares by
# `tests/test_sandbox_defs.py`. The list is in two places because a Dockerfile
# cannot read pyproject.toml; the test is what keeps them one list.
PROJECT_INPUTS = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "src",
    "skills",
    "migrations",
)


# ....................... #


def definitions_root(root: Path) -> Path:
    """The directory holding this repository's definitions.

    `sandboxes/` when it exists, `.torve/sandbox/` otherwise: one repository
    has one answer, and which one it is says whether the definitions are its
    source or its configuration.
    """

    from torve.config import layout

    own = root / DEFINITIONS_DIR

    if own.is_dir():
        return own

    return root / layout.TORVE_DIR / CONSUMER_DEFINITIONS_DIR


def definition_names(root: Path) -> list[str]:
    """Every runnable definition, in name order — the base is not one."""

    where = definitions_root(root)

    if not where.is_dir():
        return []

    return sorted(
        one.name
        for one in where.iterdir()
        if one.is_dir() and one.name != BASE_DEFINITION and (one / "Dockerfile").is_file()
    )


# S-0063/D-6: the tag and the definition directory are the same fact, which is
# what lets `doctor` ask whether an image it found is still defined here.
def image_tag(name: str) -> str:
    """The tag `bake.hcl` builds this definition to.

    `<name>-sandbox`, not `torve-agent:<name>`: the prefix said who built the
    image, which a registry path already says, and a name worth publishing is
    the point of the rename.
    """

    return f"{name}-sandbox"


def project_inputs(root: Path) -> list[str]:
    """What the base must bake for the project to install inside an image.

    The packages and forced includes are read from `pyproject.toml` rather
    than listed here: a wheel that ships migrations or skills as package data
    fails to build without them, and a hand-kept list is a list that goes
    stale the first time one is added. It went stale once already — that is
    why this reads.

    The base's `COPY` lines are the hand-kept half now, since a Dockerfile
    cannot read a TOML file; `PROJECT_INPUTS` is that half and the test holds
    the two together.
    """

    import tomllib

    manifest = root / "pyproject.toml"

    if not manifest.is_file():
        return list(PROJECT_INPUTS[:4])

    wheel = (
        tomllib.loads(manifest.read_text(encoding="utf-8"))
        .get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
    )
    packages = [str(one).split("/", 1)[0] for one in wheel.get("packages", ["src"])]
    included = [str(one) for one in wheel.get("force-include", {})]

    return [*PROJECT_INPUTS[:4], *dict.fromkeys([*packages, *included])]


# ....................... #


SANDBOX_SUFFIX = "-sandbox"


def harness_kind(image: str) -> str:
    """The harness an image is: the definition directory under `sandboxes/`
    it was built from — the inverse of `image_tag` above.

    `claude-sandbox:2.1.252` and `ghcr.io/morzecrew/claude-sandbox:2.1.252`
    are the same harness — publishing changes the repository prefix and the
    version, and the name in the middle is what survives the move.

    The `-sandbox` suffix is what makes this answerable at all. The old
    `torve-agent:<name>` spelling put the name in the version position, so
    every other image published under a `torve-agent` repository — the
    engine's own among them — read as a harness called by its version. An
    image that is not a sandbox this repository defines answers nothing,
    which is the right answer for a stock base or a third party's image.
    """

    if not image:
        return ""

    tag = image.rsplit("@", 1)[0]  # a digest pin carries the tag before it
    repository = posixpath.basename(tag).partition(":")[0]

    return repository.removesuffix(SANDBOX_SUFFIX) if repository.endswith(SANDBOX_SUFFIX) else ""


# ....................... #


def _known(root: Path, name: str) -> None:
    """Refuse a name no definition answers to, saying which do."""

    if name in definition_names(root):
        return

    listed = ", ".join(definition_names(root)) or "none"

    raise fail(
        f"configuration error: no definition directory with a Dockerfile for {name!r} "
        f"under {definitions_root(root)} (defined: {listed})",
        EXIT_CONFIG,
    )


# ....................... #


@sandbox_app.command("list")
def list_definitions(
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every image definition this repository carries, and the tag each
    builds to. Building them is `just images`; the engine never does."""

    root = root.resolve()
    names = definition_names(root)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "root": str(definitions_root(root)),
                "definitions": [{"name": one, "tag": image_tag(one)} for one in names],
            }
        )
        return

    console = out(fmt)
    header(console, "sandbox list", f"{len(names)} definition(s)")

    if not names:
        console.print(Text(f"none under {definitions_root(root)}", STYLE_ID))
        return

    table = make_table("name", "tag")

    for one in names:
        table.add_row(one, Text(image_tag(one), STYLE_ID))

    console.print(table)
    console.print(Text(f"build: {BUILD_RECIPE}", STYLE_ID))


# ....................... #


@sandbox_app.command("digest")
def digest(
    name: Annotated[
        str | None,
        typer.Argument(help="One definition to resolve; omit to resolve every definition."),
    ] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """What each definition's tag resolves to on this runtime.

    The digest is the image's identity: it joins the run's configuration hash
    at dispatch, so a rebuild is a visible regime change and an image that
    resolves to nothing has not been built here. An unresolved image is
    reported as unresolved, never invented."""

    root = root.resolve()
    config = load_config(root, config_path)
    runtime = runtime_for(config, runtime_name)

    names = definition_names(root)

    if name is not None:
        _known(root, name)
        names = [name]

    if not names:
        raise fail(
            f"configuration error: no image definitions under {definitions_root(root)}",
            EXIT_CONFIG,
        )

    resolved = [
        {"name": one, "tag": image_tag(one), "digest": runtime.resolve_image(image_tag(one)) or ""}
        for one in names
    ]

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "images": resolved})
        return

    console = out(fmt)
    header(console, "sandbox digest", f"{len(resolved)} image(s)")
    table = make_table("name", "tag", "digest")

    for image in resolved:
        table.add_row(
            image["name"],
            Text(image["tag"], STYLE_ID),
            Text(image["digest"] or f"unresolved — {BUILD_RECIPE}", STYLE_ID),
        )

    console.print(table)
