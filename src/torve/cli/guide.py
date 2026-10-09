"""`torve guide` — the text of a shipped skill, read from the installed package
(S-0100/D-2). Listing and printing only (S-0015/D-6): the skills and their
references resolve against package data, so what a session reads is the text of
the torve the repository pins, never a copy beside it that can drift.

`torve guide` lists them, `torve guide <skill>` prints its `SKILL.md`, and
`torve guide <skill> <reference>` prints one file under the skill's
`references/` directory.
"""

from __future__ import annotations

from typing import Annotated

import typer

from torve.application.skills import available, description, read_reference, read_skill
from torve.cli.console import fail, out
from torve.domain.states import EXIT_CONFIG

# ----------------------- #


def guide_cmd(
    skill: Annotated[
        str | None,
        typer.Argument(help="A shipped skill to print; omit to list them."),
    ] = None,
    reference: Annotated[
        str | None,
        typer.Argument(help="A file under the skill's references directory."),
    ] = None,
) -> None:
    """Print a shipped skill, or one of its references, from the installed package."""

    console = out()

    if skill is None:
        for name in available():
            console.print(f"{name}  {description(name)}".rstrip())
        return

    try:
        text = read_skill(skill) if reference is None else read_reference(skill, reference)
    except LookupError as exc:
        raise fail(str(exc), EXIT_CONFIG) from None

    console.print(text, end="")


# ....................... #
