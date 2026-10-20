"""`verify-tests` — a verifier may not land a test that proves nothing
(S-0116/D-11).

A verify candidate adds tests and no implementation, so every test it wrote
must fail on that candidate: a test that already passes has proved nothing
about the work still to come. The gate convicts three ways — a verifier file
that does not collect, an entry row no test cites, and a visible or held-out
test that passes on the candidate — and names each row and test it convicted.

The held-out properties of the task's latest `verify.holdout` event are
written into this gate's own checkout under `<tests_dir>/holdout/` and removed
when it ends, exactly as `held-out` writes them: the gate's run is the only
place they exist.
"""

from __future__ import annotations

import re
import shlex

from torve.base.shell import run_command
from torve.config.manifest import DEFAULT_TEST_RUN, Gate
from torve.gates.context import GateContext
from torve.gates.contract import NO_TASK, BuiltinOutcome
from torve.gates.held_out import cited_rows, remove_holdout, write_holdout

# ----------------------- #

# How the verifier's tests are collected and run: the manifest's `tests.run`.
TEST_COMMAND = DEFAULT_TEST_RUN

# pytest's short-summary failure line, and a collected node id.
# A setup or teardown error is a test that did not pass, as a failure is.
_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$", re.MULTILINE)


# ....................... #


def _command(ctx: GateContext) -> str:
    declared = ctx.manifest.tests.run

    return TEST_COMMAND if declared == DEFAULT_TEST_RUN else declared


# ....................... #


def _visible_tests(ctx: GateContext) -> list[str]:
    """The verifier's own test files, in its worktree. Read off the
    acceptance command that names the file (S-0116/D-4 mints
    `<tests.run> --collect-only <file>`), with a diff-era fallback for a
    contract whose acceptance does not name one."""

    found: list[str] = []
    prefix = ctx.tests_dir.strip("/") + "/"

    for command in ctx.task.acceptance if ctx.task is not None else []:
        for token in shlex.split(command):
            if token.endswith(".py") and "/" in token and (ctx.root / token).is_file():
                found.append(token)

    if not found:
        for entry in ctx.diff:
            path = entry.path

            if (
                path.endswith(".py")
                and path.startswith(prefix)
                and "/holdout/" not in path
                and (ctx.root / path).is_file()
            ):
                found.append(path)

    return sorted(dict.fromkeys(found))


# ....................... #


def _collected(output: str) -> list[str]:
    """The node ids pytest collected, one per line under `--collect-only -q`."""

    return [line.strip() for line in output.splitlines() if "::" in line and not line[:1].isspace()]


# ....................... #


def check_verify_tests(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is None:
        return NO_TASK

    if ctx.task.role != "verify":
        return BuiltinOutcome("skipped", "not a verify task")

    visible = _visible_tests(ctx)
    holdout = [f"{ctx.tests_dir}/holdout/{one.name}" for one in ctx.holdout]
    texts = {f"{ctx.tests_dir}/holdout/{one.name}": one.text for one in ctx.holdout}
    texts.update({path: (ctx.root / path).read_text(encoding="utf-8") for path in visible})
    previous: dict[str, bytes | None] = {}

    try:
        paths = sorted({*visible, *holdout})
        if not visible:
            return BuiltinOutcome(
                "fail", "the verifier wrote no test file — nothing proves the row"
            )

        previous = write_holdout(ctx, holdout, texts)

        collect = run_command(
            f"{_command(ctx)} --collect-only {' '.join(shlex.quote(path) for path in paths)}",
            ctx.root,
            gate.timeout or 600.0,
            retry_flaky=False,
            execute=ctx.execute,
        )

        if collect.exit_code != 0:
            return BuiltinOutcome(
                "fail",
                f"the verifier's file does not collect:\n{collect.output}",
                exit_code=collect.exit_code,
            )

        # `-rfE` names every failure and error in the short summary whatever
        # the manifest's own command asks for, so the parse below sees them.
        result = run_command(
            f"{_command(ctx)} -rfE {' '.join(shlex.quote(path) for path in paths)}",
            ctx.root,
            gate.timeout or 600.0,
            retry_flaky=False,
            execute=ctx.execute,
        )

    finally:
        remove_holdout(ctx, previous)

    collected = _collected(collect.output)

    if not collected:
        # A collection that names no test — a manifest command whose output
        # lists none — would read every test as failing and pass the gate.
        return BuiltinOutcome(
            "fail", f"no collected test could be read from:\n{collect.output}", exit_code=1
        )

    failed = {nodeid for nodeid, _message in _FAILED.findall(result.output)}
    passing = [nodeid for nodeid in collected if nodeid not in failed]

    cited: set[str] = set()

    for text in texts.values():
        cited.update(cited_rows(text))

    uncited = [one.id for one in ctx.task.decisions if one.id not in cited]

    findings: list[str] = []

    if uncited:
        findings.append("rows no test cites:\n" + "\n".join(f"  {row}" for row in uncited))

    if passing:
        findings.append(
            "tests that pass on the candidate:\n" + "\n".join(f"  {one}" for one in passing)
        )

    if findings:
        return BuiltinOutcome("fail", "\n\n".join(findings), exit_code=1)

    return BuiltinOutcome("pass", f"verify-tests: {len(paths)} test(s) all fail, every row cited")
