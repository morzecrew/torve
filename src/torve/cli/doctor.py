"""`torve doctor` — preflight checks, rendered per RFC 0011 §5: each check
names what it looked for, what it found, and what to do about it. The forze
schema pin is D-12.7: a mismatch must be a check, not a symptom.
"""

from __future__ import annotations

import typer

from torve.cli.console import STYLE_FAIL, Format, emit_json, mark, out, styled
from torve.cli.options import FormatOption
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


def doctor(fmt: FormatOption = Format.TEXT) -> None:
    """Preflight checks: configuration and environment readiness. Today: the
    forze schema pin — a mismatch must be a check, not a symptom discovered
    through adapter behaviour. A failed check is a configuration error
    (exit 3), not a red gate."""
    from torve.application.migrate import check_forze_pin

    ok, message = check_forze_pin()
    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "ok": ok,
                   "checks": [{"name": "forze-pin", "ok": ok, "detail": message}]})
    else:
        verdict = mark("pass" if ok else "fail")
        out(fmt).print(verdict + styled(f" {message}", "" if ok else STYLE_FAIL))
    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)
