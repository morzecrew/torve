"""`torve doctor` — preflight checks, rendered per RFC 0011 §5: each check
names what it looked for, what it found, and what to do about it.
"""

from __future__ import annotations

import typer

from torve.cli.console import Format, emit_json, out
from torve.cli.options import FormatOption
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


def doctor(fmt: FormatOption = Format.TEXT) -> None:
    """Preflight checks. Today: the forze pin (D-12.7) — a schema mismatch
    must be a check, not a symptom discovered through adapter behaviour. A
    failed check is a configuration error (exit 3), not a red gate."""
    from torve.application.migrate import check_forze_pin

    ok, message = check_forze_pin()
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "ok": ok,
                   "checks": [{"name": "forze-pin", "ok": ok, "detail": message}]})
    else:
        out(fmt).print(("ok    " if ok else "FAIL  ") + message)
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)
