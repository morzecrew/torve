"""Rendering plumbing and the component vocabulary for every command
(RFC 0011; RFC 0018 §3): plain detection, the stdout/stderr consoles, raw
JSON emission, code-carrying failures, and the shared components — header,
table, verdict marks, failure detail, closing line — every verb renders
through (D-18.3, never bespoke per-verb string assembly).

Results go to stdout and diagnostics to stderr, never mixed (D-11.6).
`--plain` is implied by `CI`, a non-TTY stdout or `--format json`, and
`NO_COLOR` is honoured by Rich natively (D-11.5). Styling is applied through
renderables and style parameters, never inline markup in data strings —
`markup=False` stays so bracketed data cannot inject styling, and colour is
never the only carrier of a distinction (D-18.4).
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# ----------------------- #


class Format(StrEnum):
    TEXT = "text"
    JSON = "json"


# The fixed colour semantics (RFC 0018 §4) — one vocabulary, applied via
# style parameters only.
STYLE_PASS = "green"
STYLE_FAIL = "red"
STYLE_WARN = "yellow"
STYLE_DIM = "dim"
STYLE_ID = "cyan"

# The published verdict vocabulary (D-18.5): stable across releases for the
# same reason exit codes are — people learn it.
OUTCOME_MARKS = {
    "pass": "✓",
    "flaky": "≈",
    "skipped": "∅",
    "bypassed": "⤳",
    "fail": "✗",
    "error": "!",
}
OUTCOME_STYLES = {
    "pass": STYLE_PASS,
    "flaky": STYLE_WARN,
    "skipped": STYLE_DIM,
    "bypassed": STYLE_WARN,
    "fail": STYLE_FAIL,
    "error": STYLE_FAIL,
}


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


# ....................... #


def header(console: Console, verb: str, subject: str, regime: str | None = None) -> None:
    """`torve <verb> · <subject> · config <hash>` (RFC 0018 §3) — what ran,
    on what, under which regime where one exists."""
    line = Text()
    line.append(f"torve {verb}", style="bold")
    line.append(f" · {subject}")
    if regime:
        line.append(" · config ", style=STYLE_DIM)
        line.append(regime, style=STYLE_ID)
    console.print(line)


def make_table(*columns: str, title: str | None = None) -> Table:
    """The house table: simple rules, dim headers, no outer border — rows are
    the content, the frame is not."""
    table = Table(box=box.SIMPLE_HEAD, title=title, title_style="bold",
                  title_justify="left", header_style=STYLE_DIM, pad_edge=False,
                  expand=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


def add_rows_truncated(table: Table, rows: list[tuple[Text | str, ...]],
                       limit: int = 50) -> None:
    """At most `limit` rows, then one explicit `… N more` line (D-18.8) —
    the full data is always in the JSON."""
    for row in rows[:limit]:
        table.add_row(*row)
    if len(rows) > limit:
        table.add_row(Text(f"… {len(rows) - limit} more", style=STYLE_DIM))


def mark(outcome: str) -> Text:
    """The verdict mark with its style — mark and word both carry the
    distinction, colour never alone (D-18.4)."""
    return Text(OUTCOME_MARKS.get(outcome, "?"), style=OUTCOME_STYLES.get(outcome, ""))


def styled(value: str, style: str) -> Text:
    return Text(value, style=style)


def failure_detail(console: Console, text: str, limit: int = 40) -> None:
    """A failing row's expansion (RFC 0018 §3): indented, capped, never
    interleaved with other rows."""
    lines = text.splitlines()
    for line in lines[:limit]:
        console.print(Text(f"      {line}"))
    if len(lines) > limit:
        console.print(Text(f"      … {len(lines) - limit} more line(s)", style=STYLE_DIM))


def closing(console: Console, text: str, style: str = "") -> None:
    """Outcome and what happens now — the last line of every verb."""
    console.print(Text(text, style=style))
