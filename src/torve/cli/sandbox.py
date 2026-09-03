"""`torve sandbox` — image definitions as reviewed artefacts (RFC 0017 §2).

Definitions live under `.torve/sandbox/<name>/` (D-17.2), build to the tag
`torve-agent:<name>`, and the reported digest is the identity that joins
`config_hash` at dispatch (D-17.1). Building is an operator action — the
engine never builds mid-run (D-17.3) — and images stay thin (D-17.8): base
runtime, harness, git, uv; everything task-specific arrives via the
workspace. Parsing and rendering only (D-15.6).

`--push` publishes a built image to a registry the server can pull from
(D-41.4). The push is a docker call made here, at the operator's command —
no registry client enters the runtime (RFC 0041 §5.2) — and the
digest-pinned reference it prints is what the run config should carry: on
a pull-from-registry platform the pinned reference is the resolution, not
a stand-in for one.
"""

from __future__ import annotations

import subprocess
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
from torve.domain.states import EXIT_CONFIG, EXIT_INFRASTRUCTURE

# ----------------------- #

sandbox_app = typer.Typer(
    no_args_is_help=True, help="Sandbox image definitions: build and identify."
)

DEFINITIONS_DIR = "sandbox"


# ....................... #


def definitions_root(root: Path) -> Path:
    from torve.config import layout

    return root / layout.TORVE_DIR / DEFINITIONS_DIR


# ....................... #


def definition_names(root: Path) -> list[str]:
    base = definitions_root(root)

    if not base.is_dir():
        return []

    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and (entry / "Dockerfile").is_file()
    )


# ....................... #


def image_tag(name: str) -> str:
    return f"torve-agent:{name}"


# ....................... #


# A push crosses the network to a registry that may be far away; it gets
# the build's own bound and no more.
PUSH_TIMEOUT_S = 1800


def _docker(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """One docker call — the same binary the Docker runtime drives, and
    the only registry client the CLI is allowed: the adapter gets none
    (RFC 0041 §5.2)."""

    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


# ....................... #


def registry_repository(reference: str) -> str:
    """A `--push` value as a validated registry repository: the tag is the
    definition's name, so one value addresses a build of every definition.
    A reference that already carries a tag or a digest is the operator's
    to correct, not ours to second-guess."""

    repository = reference.strip().rstrip("/")
    tail = repository.rsplit("/", 1)[-1]

    if not repository or "@" in repository or ":" in tail:
        raise fail(
            f"configuration error: --push {reference!r} is not a registry repository — "
            "pass the repository only (e.g. registry.example.com/org/torve-agent), "
            "no tag and no digest; each built definition is pushed as "
            "<repository>:<definition-name>",
            EXIT_CONFIG,
        )

    return repository


# ....................... #


def push_image(local_tag: str, repository: str, name: str) -> str:
    """Publish *local_tag* as `<repository>:<name>` and return the
    digest-pinned reference the registry recorded: the registry's manifest
    digest, not the local content id, is what a pull platform resolves."""

    pushed = f"{repository}:{name}"

    for step in (
        ("tag", local_tag, pushed),
        ("push", pushed),
    ):
        proc = _docker(*step, timeout=PUSH_TIMEOUT_S)

        if proc.returncode != 0:
            raise RuntimeError(
                f"docker {step[0]} failed: {proc.stderr.strip() or 'no error output'}"
            )

    # After a successful push the daemon records the manifest digest the
    # registry names the content by; that is the identity to carry.
    proc = _docker("image", "inspect", "--format", "{{index .RepoDigests 0}}", pushed, timeout=30)
    pinned = proc.stdout.strip()

    if proc.returncode != 0 or "@sha256:" not in pinned:
        raise RuntimeError(
            f"pushed {pushed} but no registry digest resolved — "
            f"{proc.stderr.strip() or 'the daemon recorded no RepoDigest for the pushed image'}"
        )

    return pinned


# ....................... #


@sandbox_app.command("build")
def build(
    name: Annotated[
        str | None, typer.Argument(help="One definition to build; omit to build every definition.")
    ] = None,
    push: Annotated[
        str | None,
        typer.Option(
            "--push",
            help=(
                "Registry repository to publish each built image to, pushed as "
                "<repository>:<definition-name> (pass no tag and no digest). The "
                "digest-pinned reference the registry records is printed beside "
                "it — that pinned reference is the identity a run config carries "
                "on a platform whose server pulls from the registry."
            ),
        ),
    ] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Build image definitions and report their digests. The digest is the
    image's identity: it joins the run's configuration hash at dispatch, so
    a rebuild is a visible regime change, never a silent one.

    With --push, each built image is also published to the named registry
    repository and the digest-pinned reference is printed for the run
    config: on a pull platform the pinned reference is the resolution."""

    # Fail on the reference before loading anything else (D-41.4: the push
    # is this command's docker call; the runtime adapters get no registry
    # client).
    repository = registry_repository(push) if push is not None else ""

    root = root.resolve()
    config = load_config(root, config_path)
    runtime = runtime_for(config, runtime_name)

    names = definition_names(root)

    if name is not None:
        if name not in names:
            listed = ", ".join(names) or "none"

            raise fail(
                f"configuration error: no definition directory with a "
                f"Dockerfile for {name!r} under {definitions_root(root)} "
                f"(defined: {listed})",
                EXIT_CONFIG,
            )

        names = [name]

    if not names:
        raise fail(
            f"configuration error: no image definitions under {definitions_root(root)}", EXIT_CONFIG
        )

    built: list[dict[str, str]] = []

    for entry in names:
        try:
            digest = runtime.build_image(definitions_root(root) / entry, image_tag(entry))

        except Exception as error:  # the build tool's failure is the message
            raise fail(f"build failed for {entry!r}: {error}", EXIT_INFRASTRUCTURE) from None

        image = {"name": entry, "tag": image_tag(entry), "digest": digest}

        if repository:
            try:
                image["pinned"] = push_image(image_tag(entry), repository, entry)

            except Exception as error:  # the build succeeded; say what failed
                raise fail(f"push failed for {entry!r}: {error}", EXIT_INFRASTRUCTURE) from None

        built.append(image)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "images": built})
        return

    console = out(fmt)
    header(console, "sandbox build", f"{len(built)} image(s)")

    if repository:
        table = make_table("name", "tag", "digest", "pinned reference")

        for image in built:
            table.add_row(
                image["name"],
                Text(image["tag"], STYLE_ID),
                Text(image["digest"], STYLE_ID),
                Text(image["pinned"], STYLE_ID),
            )

    else:
        table = make_table("name", "tag", "digest")

        for image in built:
            table.add_row(
                image["name"], Text(image["tag"], STYLE_ID), Text(image["digest"], STYLE_ID)
            )

    console.print(table)
