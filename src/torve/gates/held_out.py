"""`held-out` — the properties a verifier hid from the implementer, run
against the candidate that claims to satisfy them (S-0116/D-10).

The files of the task's latest `verify.holdout` event are written into this
gate's checkout under `<tests_dir>/holdout/`, run as the only suite, and
removed when the gate ends — green or red. Nothing else in the battery
collects or reports them: they exist for the length of this run alone.

The output is deliberately thin. A held-out property is a row's proof, not a
place for the implementer to read the test's source, so a failure is reported
as the failing test's identifier, its message and the rows it cites — never a
traceback, never the file. That is what teaches the next attempt which row
failed and why, without handing it the assertion to special-case.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import shutil

from torve.base.shell import run_command
from torve.config.manifest import DEFAULT_TEST_RUN, Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

# How the held-out files are run: the manifest's `tests.run` (S-0106/D-7), or
# the engine default when the manifest leaves it alone.
TEST_COMMAND = DEFAULT_TEST_RUN

# pytest's short-summary lines: `FAILED path::test - message` and
# `ERROR path - message`, the latter what a property that errors at setup or
# collection leaves. The ERRORS section's own banner lines start with `_`, so
# neither pattern reaches the traceback under them (S-0116/D-10).
_SUMMARY = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$", re.MULTILINE)

# A corpus row: the decision ids a test's text cites.
_ROW = re.compile(r"\bS-\d+/D-\d+\b")


# ....................... #


def _command(ctx: GateContext) -> str:
    declared = ctx.manifest.tests.run

    return TEST_COMMAND if declared == DEFAULT_TEST_RUN else declared


# ....................... #


def cited_rows(text: str) -> list[str]:
    """The decision ids a test's text cites, in order, deduplicated."""

    seen: dict[str, None] = {}

    for match in _ROW.finditer(text):
        seen.setdefault(match.group(0), None)

    return list(seen)


# ....................... #


def holdout_paths(ctx: GateContext) -> list[str]:
    """Where this gate writes the held-out files: under `<tests_dir>/holdout/`
    in its own checkout, one entry per recorded file name."""

    return [f"{ctx.tests_dir}/holdout/{one.name}" for one in ctx.holdout]


# ....................... #


def write_holdout(
    ctx: GateContext, paths: list[str], texts: dict[str, str]
) -> dict[str, bytes | None]:
    """Write the held-out files into the checkout, remembering what each one
    replaced — a path under `<tests_dir>/holdout/` may already hold a property
    a landed document committed (S-0116/D-14), and this run may not destroy
    it."""

    previous: dict[str, bytes | None] = {}

    for path in paths:
        target = ctx.root / path
        previous[path] = target.read_bytes() if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(texts[path], encoding="utf-8")

    return previous


# ....................... #


def remove_holdout(ctx: GateContext, previous: dict[str, bytes | None]) -> None:
    """Remove the files this run wrote, and only those (S-0116/D-10): the ones
    that were already there are restored, and the directory goes only if this
    run left it empty. The bytecode the run's own import left behind goes with
    the files — it is nobody's property and no repository tracks it."""

    directory = ctx.root / ctx.tests_dir / "holdout"

    for path, before in previous.items():
        target = ctx.root / path

        if before is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(before)

    if directory.is_dir():
        for cache in directory.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)

    with contextlib.suppress(OSError):
        directory.rmdir()


# ....................... #


def check_held_out(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is not None and ctx.task.role == "verify":
        # A verifier's own held-out properties fail by design until its entry's
        # implement candidate lands; `verify-tests` judges them for it
        # (S-0116/D-11), and a red here would refuse every verify candidate.
        return BuiltinOutcome("skipped", "a verify task's properties are verify-tests' to judge")

    if not ctx.holdout:
        # S-0116/D-10: no files in the latest event means nothing to hold out —
        # a skipped gate, never a green one, because the gate judged nothing.
        return BuiltinOutcome("skipped", "the task's latest record carries no held-out files")

    paths = holdout_paths(ctx)
    texts = {path: one.text for path, one in zip(paths, ctx.holdout, strict=True)}
    previous: dict[str, bytes | None] = {}

    try:
        previous = write_holdout(ctx, paths, texts)

        command = f"{_command(ctx)} {' '.join(shlex.quote(path) for path in paths)}"
        result = run_command(
            command, ctx.root, gate.timeout or 600.0, retry_flaky=False, execute=ctx.execute
        )

    finally:
        # S-0116/D-10: the files exist for this run and no other.
        remove_holdout(ctx, previous)

    failures: list[str] = []

    for verdict, nodeid, message in _SUMMARY.findall(result.output):
        file = nodeid.split("::", 1)[0]
        rows = cited_rows(texts.get(file, ""))
        failure = f"{verdict} {nodeid}"

        if message.strip():
            failure += f" — {message.strip()}"

        if rows:
            failure += "\n  cites: " + ", ".join(rows)

        failures.append(failure)

    if result.exit_code == 0:
        return BuiltinOutcome("pass", f"held-out: all {len(paths)} file(s) passed")

    header = f"held-out: {len(failures)} failing of {len(paths)} file(s)"

    if failures:
        return BuiltinOutcome("fail", "\n\n".join([header, *failures]), exit_code=result.exit_code)

    # A red the short summary does not explain — a run that judged nothing, or
    # one whose output pytest could not summarise. Its raw output is not
    # reported: a traceback is where the property's source would leak, and
    # S-0116/D-10 keeps the report to identifiers and messages.
    return BuiltinOutcome(
        "fail",
        f"{header}\n\nthe run exited {result.exit_code} without naming a failing test",
        exit_code=result.exit_code,
    )
