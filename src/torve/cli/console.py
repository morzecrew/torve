"""Rendering plumbing for every command (RFC 0011): plain detection, the
stdout/stderr consoles, raw JSON emission, and code-carrying failures.

Results go to stdout and diagnostics to stderr, never mixed (D-11.6).
`--plain` is implied by `CI`, a non-TTY stdout or `--format json`, and
`NO_COLOR` is honoured by Rich natively (D-11.5).
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum

import typer
from rich.console import Console

# ----------------------- #


class Format(StrEnum):
    TEXT = "text"
    JSON = "json"


_plain_flag = False


def set_plain(value: bool) -> None:
    global _plain_flag
    _plain_flag = value


def is_plain(fmt: Format | None = None) -> bool:
    return (_plain_flag or fmt is Format.JSON or bool(os.environ.get("CI"))
            or not sys.stdout.isatty())


def out(fmt: Format | None = None) -> Console:
    """Human results, stdout."""
    return Console(no_color=is_plain(fmt) or None, highlight=False, markup=False,
                   soft_wrap=True)


def err() -> Console:
    """Diagnostics, stderr — in both formats (D-11.6)."""
    return Console(stderr=True, no_color=is_plain() or None, highlight=False,
                   markup=False, soft_wrap=True)


def emit_json(document: dict[str, object]) -> None:
    """Exactly one JSON document on stdout and nothing else (D-11.6) —
    written raw so no console width ever wraps it."""
    sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def fail(message: str, code: int) -> typer.Exit:
    err().print(message)
    return typer.Exit(code)
