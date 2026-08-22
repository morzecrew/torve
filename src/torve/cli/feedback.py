"""`torve feedback` — the two hand-entered telemetry fields (RFC 0004 §6),
appended after merge to their own stream. Parsing and rendering only (D-15.6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from torve.application.telemetry import append_record, feedback_record
from torve.cli.console import Format, closing, emit_json, out
from torve.cli.options import FormatOption, RootOption
from torve.config import layout
from torve.domain.states import EXIT_OK

# ----------------------- #


def feedback(
    task_id: Annotated[str, typer.Argument()],
    human_minutes: Annotated[int, typer.Option(
        "--human-minutes", min=0,
        help="Minutes a human spent on this task after the engine parked it.")],
    rework: Annotated[bool, typer.Option(
        "--rework/--no-rework",
        help="Whether review sent the work back for rework.")] = False,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Record a ReviewFeedback entry for a task (RFC 0004 §6): appended, never
    updated — keyed by task id, the latest entry wins at analysis time."""
    root = root.resolve()
    record = feedback_record(task_id, human_minutes, rework)
    append_record(layout.feedback_file(root), record)
    if fmt is Format.JSON:
        emit_json(record)
    else:
        closing(out(fmt),
                f"{task_id}: {human_minutes} human minute(s), "
                f"rework={'yes' if rework else 'no'} — appended")
    raise typer.Exit(EXIT_OK)
