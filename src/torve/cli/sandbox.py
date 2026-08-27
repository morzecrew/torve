"""`torve sandbox` — image definitions as reviewed artefacts (RFC 0017 §2).

Definitions live under `.torve/sandbox/<name>/` (D-17.2), build to the tag
`torve-agent:<name>`, and the reported digest is the identity that joins
`config_hash` at dispatch (D-17.1). Building is an operator action — the
engine never builds mid-run (D-17.3) — and images stay thin (D-17.8): base
runtime, harness, git, uv; everything task-specific arrives via the
workspace. Parsing and rendering only (D-15.6).
"""

from __future__ import annotations

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


@sandbox_app.command("build")
def build(
    name: Annotated[
        str | None, typer.Argument(help="One definition to build; omit to build every definition.")
    ] = None,
    runtime_name: Annotated[RuntimeName | None, typer.Option("--runtime")] = None,
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Build image definitions and report their digests. The digest is the
    image's identity: it joins the run's configuration hash at dispatch, so
    a rebuild is a visible regime change, never a silent one."""

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

        built.append({"name": entry, "tag": image_tag(entry), "digest": digest})

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "images": built})
        return

    console = out(fmt)
    header(console, "sandbox build", f"{len(built)} image(s)")
    table = make_table("name", "tag", "digest")

    for image in built:
        table.add_row(image["name"], Text(image["tag"], STYLE_ID), Text(image["digest"], STYLE_ID))

    console.print(table)
