"""`suite` — the whole suite runs once, and a red is attributed to the change
that caused it (S-0118/D-1).

The first of the entry's `commands` is the suite, run once over the head with
no blanket re-run. A green ends the gate there. A red names its failing node
ids in pytest's short summary, and each is settled by two more readings:

1. the failing tests' files are rerun at the head, in their own order. An id
   that is green now was flaky.
2. the files holding the ids still red are run on the merge base. An id green
   there is the attempt's regression and convicts; an id red there too is
   inherited and never convicts (S-0118/D-4).

An id with no base reading — the base tree lacks its file, or the base could
not run it — convicts, and so does a red whose output names no failing id at
all (S-0118/D-2). The entry's later `commands` are reports: they run right
after the suite's own run, before any rerun, over what it wrote, and their
exit codes never convict (S-0118/D-3).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from torve.base.shell import run_command
from torve.config import layout
from torve.config.manifest import Gate
from torve.domain.vocabulary import GateOutcome
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

#: The prefix of the directory one run extracts the base tree into, under the
#: checkout's `.torve/tmp` — inside the workspace, so the gate sandbox can see
#: it. The host extracts; the reading runs in the sandbox, never on the host
#: (S-0118/D-11). Each run makes its own, so two batteries on one checkout
#: never read each other's base.
BASE_NAME = "suite-base"

#: pytest's short summary, one line per failure or collection error:
#: `FAILED tests/test_a.py::test_b - AssertionError: ...`. A line naming a
#: file but no `::` is a collection error, not a test id: nothing to attribute,
#: and D-2 keeps that red a conviction.
_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


# ....................... #


def failing_ids(output: str) -> list[str]:
    """The failing node ids pytest's short summary names, in the order they
    first appear. A collection error names a file and no test, and is left to
    the caller's fail-closed reading (S-0118/D-2)."""

    seen: dict[str, None] = {}

    for match in _SUMMARY.finditer(output):
        node = match.group(1)

        if "::" in node:
            seen[node] = None

    return list(seen)


def collection_errors(output: str) -> list[str]:
    """The files pytest's short summary names with no test id: a module that
    did not collect. Nothing reads them on the base, so each keeps the suite
    red (S-0118/D-2), however the named failures beside it settle."""

    return list(
        dict.fromkeys(m.group(1) for m in _SUMMARY.finditer(output) if "::" not in m.group(1))
    )


# The suite's own verdict lines, read back by the evidence package
# (S-0122/D-5): the gate result carries the inherited ids as a field and the
# convicted and flaky ones only as these sentences, which `check_suite` below
# writes and nothing else does.
_CONVICTED = re.compile(
    r"^the change broke \d+ test\(s\) the base tree passes \(or cannot run\): (.+)$",
    re.MULTILINE,
)
_FLAKY = re.compile(
    r"^flaky: \d+ test\(s\) passed on the rerun, not convicting: (.+)$", re.MULTILINE
)


def settled_ids(output: str) -> tuple[list[str], list[str]]:
    """(convicted, flaky): the ids a suite result's verdict lines name, the
    last of each kind, since the head's own output precedes them."""

    def last(pattern: re.Pattern[str]) -> list[str]:
        found = pattern.findall(output)
        return [one.strip() for one in found[-1].split(", ")] if found else []

    return last(_CONVICTED), last(_FLAKY)


# ....................... #


@dataclass(frozen=True)
class Attribution:
    """How the head's failing ids are settled: flaky (green on the rerun),
    inherited (red on the base too), convicted (red at the head and not red on
    the base)."""

    flaky: tuple[str, ...]
    inherited: tuple[str, ...]
    convicted: tuple[str, ...]


def attribution(
    head: Iterable[str],
    rerun: Iterable[str] | None,
    base: Iterable[str] | None,
) -> Attribution:
    """The pure attribution (S-0118/D-1, D-2): a function of the three
    readings alone.

    `rerun` None is a rerun that produced no reading — every head failure is
    still red. `base` None is a base that could not run the tests — nothing
    clears a failure, so every id still red convicts.
    """

    rerun_ids = None if rerun is None else set(rerun)
    base_ids = None if base is None else set(base)

    flaky: list[str] = []
    still: list[str] = []

    for node in dict.fromkeys(head):
        if rerun_ids is not None and node not in rerun_ids:
            flaky.append(node)
        else:
            still.append(node)

    inherited: list[str] = []
    convicted: list[str] = []

    for node in still:
        if base_ids is not None and node in base_ids:
            inherited.append(node)
        else:
            convicted.append(node)

    return Attribution(tuple(flaky), tuple(inherited), tuple(convicted))


# ....................... #


def _files_of(ids: Iterable[str]) -> list[str]:
    """The files the ids live in, each once, in the order the ids named them."""

    return list(dict.fromkeys(node.split("::", 1)[0] for node in ids))


def _run_files(ctx: GateContext, files: list[str], checkout: Path | None = None) -> str:
    """The manifest's test launcher over a list of files — the same seam
    `red-on-base` uses (S-0106/D-7) — as *checkout* launches them, the
    candidate's by default (S-0127/D-16)."""

    launcher = ctx.manifest.tests.launcher(checkout or ctx.root)

    return f"{launcher} {' '.join(shlex.quote(path) for path in files)}"


def _with_base(command: str, ctx: GateContext) -> str | None:
    """`{base}` replaced with the battery's merge base, or None when the
    command asks for one and no base is resolvable."""

    if "{base}" not in command:
        return command

    if ctx.merge_base is None:
        return None

    return command.replace("{base}", ctx.merge_base)


# ....................... #


def archive_tree(repo: Path, sha: str, destination: Path, timeout: float) -> str | None:
    """Extract the tree of *sha* from *repo* into *destination*, on the host,
    with git's hooks off: None when it landed, else why it did not. The one
    mechanism behind every base tree the engine builds — the suite's base
    reading (S-0118/D-11), the evidence package's base runs and the critic's
    `.torve/review/base/` (S-0122/D-5, S-0122/D-6) — so the three read the
    same bytes. Bounded by *timeout*, so a slow archive cannot outlast its
    caller; a failure leaves *destination* removed."""

    destination.mkdir(parents=True, exist_ok=True)

    # Its own session, so a timeout kills the pipeline's `git` and `tar` with
    # the shell: an orphaned `tar` would write into *destination* after it is
    # removed.
    with subprocess.Popen(
        f"git -c core.hooksPath=/dev/null -C {shlex.quote(str(repo))} archive "
        f"{shlex.quote(sha)} | tar -x -C {shlex.quote(str(destination))}",
        shell=True,  # nosec B602 — the sha comes from git, the paths are quoted
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as archive:
        try:
            _, stderr = archive.communicate(timeout=timeout)

        except subprocess.TimeoutExpired:
            os.killpg(archive.pid, signal.SIGKILL)
            archive.communicate()
            shutil.rmtree(destination, ignore_errors=True)
            return f"the base tree was not extracted within {timeout:.0f}s"

    if archive.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        return f"the base tree could not be extracted: {stderr.strip()}"

    return None


def extract_base(ctx: GateContext, timeout: float) -> tuple[Path | None, str | None]:
    """Extract the merge base tree into a fresh directory in the checkout, on
    the host: the gate sandbox runs no git (S-0113/D-2), and a bind mount shows
    the sandbox what the host writes into the workspace before it starts.
    Returns (directory, None), or (None, why) when the tree could not be built
    (S-0118/D-11).

    The checkout is the candidate's, so its `.torve/tmp` is refused unless it
    resolves where it is written: a symlink the candidate planted there would
    send the host's extraction outside the workspace. The archive is bounded
    by the caller's timeout, so a slow one cannot outlast the battery."""

    if ctx.merge_base is None:
        return None, "no merge base is resolvable"

    root = ctx.root.resolve()
    parent = root / layout.TORVE_DIR / "tmp"

    if any(path.is_symlink() for path in (parent.parent, parent)):
        return None, f"{parent.relative_to(root)} is a symlink in the checkout"

    parent.mkdir(parents=True, exist_ok=True)

    if parent.resolve() != parent:
        return None, f"{parent.relative_to(root)} resolves outside the checkout"

    destination = Path(tempfile.mkdtemp(prefix=f"{BASE_NAME}-", dir=parent))
    problem = archive_tree(ctx.root, ctx.merge_base, destination, timeout)

    return (None, problem) if problem is not None else (destination, None)


# ....................... #


def check_suite(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    commands = gate.commands

    if not commands or not commands[0].strip():
        # A skip would read as a pass to the battery: no suite ran, so nothing
        # can clear the gate (S-0118/D-2).
        return BuiltinOutcome("fail", "the suite gate names no command, so no test ran")

    timeout = gate.timeout or 600.0
    base_dir: Path | None = None

    try:
        suite_command = _with_base(commands[0], ctx)

        if suite_command is None:
            return BuiltinOutcome(
                "fail", "the suite command asks for the battery's base, and none is resolvable"
            )

        head = run_command(suite_command, ctx.root, timeout, retry_flaky=False, execute=ctx.execute)
        sections = [
            f"$ {suite_command}  [exit {head.exit_code}, {head.duration_s:.1f}s]\n{head.output}"
        ]

        # D-3: reports run right after the suite's own run and before any
        # rerun, so they read what the whole suite wrote. Their exit codes are
        # a reading, never a conviction.
        for report in commands[1:]:
            command = _with_base(report, ctx)

            if command is None:
                sections.append(f"$ {report}  [report, not run: no base is resolvable]")
                continue

            result = run_command(command, ctx.root, timeout, retry_flaky=False, execute=ctx.execute)
            sections.append(f"$ {command}  [report, exit {result.exit_code}]\n{result.output}")

        output = "\n".join(sections)

        if head.exit_code == 0:
            return BuiltinOutcome("pass", output, exit_code=0)

        head_ids = failing_ids(head.output)
        uncollected = collection_errors(head.output)

        if not head_ids:
            return BuiltinOutcome(
                "fail",
                f"{output}\n\nthe suite is red and names no failing test: nothing can clear it",
                exit_code=head.exit_code,
            )

        if uncollected:
            return BuiltinOutcome(
                "fail",
                f"{output}\n\n{len(uncollected)} file(s) did not collect, and nothing can "
                f"clear that: {', '.join(uncollected)}",
                exit_code=head.exit_code,
            )

        # 1. Rerun the failing tests' files at the head, keeping each file's
        # own order. A green now is flaky; no reading at all keeps every id
        # red (fail closed).
        rerun_files = _files_of(head_ids)
        rerun_command = _run_files(ctx, rerun_files)
        rerun = run_command(
            rerun_command, ctx.root, timeout, retry_flaky=False, execute=ctx.execute
        )
        rerun_reading = _reading(rerun.exit_code, rerun.output, rerun=True)
        output += f"\n\n$ {rerun_command}  [head rerun, exit {rerun.exit_code}]\n{rerun.output}"

        # A base reading first with no base says which ids are still red; it
        # is the same pure rule, asked before the tree exists.
        still_red = attribution(head_ids, rerun_reading, None).convicted

        base_reading: set[str] | None = None

        if still_red:
            # The base is extracted only now: a green suite, or a red the
            # rerun cleared, never pays for archiving the whole tree.
            base_dir, base_note = extract_base(ctx, timeout)

            if base_dir is not None:
                # A file the base tree lacks has no reading and its ids
                # convict (D-2). Running it would make pytest refuse the whole
                # command, so only the files the base holds are run — the
                # inherited reds beside a new failing file are still read.
                present = [
                    node for node in still_red if (base_dir / node.split("::", 1)[0]).is_file()
                ]

                if present:
                    base_command = (
                        f"cd {shlex.quote(str(base_dir.relative_to(ctx.root.resolve())))} && "
                        f"{_run_files(ctx, _files_of(present), base_dir)}"
                    )
                    base = run_command(
                        base_command, ctx.root, timeout, retry_flaky=False, execute=ctx.execute
                    )
                    base_reading = _reading(base.exit_code, base.output)
                    output += (
                        f"\n\n$ {base_command}  [base run, exit {base.exit_code}]\n{base.output}"
                    )
                else:
                    base_reading = set()
                    output += "\n\nno still-red file exists on the base tree"
            else:
                output += f"\n\nthe base tree was not run: {base_note}"

        settled = attribution(head_ids, rerun_reading, base_reading)

        if settled.flaky:
            output += (
                f"\n\nflaky: {len(settled.flaky)} test(s) passed on the rerun, "
                f"not convicting: {', '.join(settled.flaky)}"
            )

        if settled.inherited:
            output += (
                f"\n\ninherited: {len(settled.inherited)} test(s) fail on the base too, "
                f"not convicting: {', '.join(settled.inherited)}"
            )

        if settled.convicted:
            return BuiltinOutcome(
                "fail",
                f"{output}\n\nthe change broke {len(settled.convicted)} test(s) the base "
                f"tree passes (or cannot run): {', '.join(settled.convicted)}",
                exit_code=head.exit_code,
                inherited_failures=list(settled.inherited),
            )

        outcome: GateOutcome = "flaky" if settled.flaky else "pass"

        return BuiltinOutcome(
            outcome,
            output,
            exit_code=0,
            flaky_commands=[suite_command] if settled.flaky else [],
            inherited_failures=list(settled.inherited),
        )

    finally:
        if base_dir is not None:
            shutil.rmtree(base_dir, ignore_errors=True)


def _reading(exit_code: int | None, output: str, *, rerun: bool = False) -> set[str] | None:
    """One rerun's reading: green is an empty set, a red names its failing
    ids, and a run that produced neither (timeout, collection error) has no
    reading at all — None, so nothing is cleared.

    A head rerun that names one failure and cannot collect another file has
    no reading either: the ids it did not name are unread, not green, and
    clearing them would call a conviction flaky (S-0118/D-2). A base run's
    named reds still read: an id it did not name is absent from its reading
    and convicts by default, so dropping the named ones would only convict the
    inherited."""

    if exit_code == 0:
        return set()

    # A collection error names a file and no `::`: the ids in that file were
    # never read, so the run cannot say they passed.
    if rerun and collection_errors(output):
        return None

    ids = failing_ids(output)

    return set(ids) if ids else None
