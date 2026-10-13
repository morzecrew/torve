"""`red-on-base` — a test written for a change that the change did not have to
make pass (S-0081).

A test that is green against the base tree proves nothing about the attempt: it
would have passed before a line was written. The gate parses the test functions
out of every changed test file, runs the ones that differ from their base
version against the base tree's source, and convicts when every one of them
passes there.

What qualifies is parsed syntax, never the diff's own file list (S-0081/D-2): a
move, a rename, a reformat or a comment leaves every test function's shape
unchanged and qualifies nothing, so the gate is not routed around by leaving
tests alone and a refactor of the suite pays nothing.

Under a document whose change type is `refactor` the gate turns around
(S-0106/D-6): a refactor is judged by whether behaviour held, so the changed
test files and the inherited checks run on the base tree and on the candidate,
and a green on one side against a red on the other convicts as "behaviour
changed under a refactor". Both green passes; both red leaves the failure to
acceptance, as a red candidate always does.
"""

from __future__ import annotations

import ast
import shlex

import pathspec

from torve.base.shell import run_command
from torve.config.manifest import Gate
from torve.gates.context import GateContext, GitError, git
from torve.gates.contract import BuiltinOutcome, spec

# ----------------------- #

# How the qualifying files are run, on the base tree and on the attempt's own.
# A module constant rather than a manifest field: the entry that names this
# gate carries no place to put a command, and a repository whose suite needs a
# different launcher patches this one line (S-0081/D-5).
TEST_COMMAND = "python3 -m pytest -q -p no:cacheprovider"

# The base tree is a `git archive` extracted into a directory outside the
# repository, with the attempt's own qualifying test files copied over it. No
# worktree is registered, nothing under the repository is written, and the
# directory is removed by the trap whichever way the script leaves
# (S-0081/D-5).
SCRIPT = """set -e
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
git archive {base} | tar -x -C "$work"
{copies}
cd "$work"
{steps}
"""


# ....................... #


def _test_functions(source: str) -> dict[str, str]:
    """Every test function in a module, by qualified name, mapped to its parsed
    shape — formatting, comments and everything around it stripped."""

    try:
        tree = ast.parse(source)

    except SyntaxError:
        return {}

    functions: dict[str, str] = {}

    def collect(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                collect(node.body, f"{prefix}{node.name}.")

            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                functions[prefix + node.name] = ast.dump(node)

    collect(tree.body, "")

    return functions


# ....................... #


def _at_base(ctx: GateContext, path: str) -> str:
    """The file as the base tree holds it; empty when it holds it not at all."""

    try:
        return git(ctx.root, "show", f"{ctx.merge_base}:{path}")

    except GitError:
        return ""


# ....................... #


def _qualifying(ctx: GateContext, tests: pathspec.GitIgnoreSpec) -> list[str]:
    """The changed test files holding a test function whose shape differs from
    the base tree's, or that the base tree does not have at all."""

    qualifying: list[str] = []

    for entry in ctx.diff:
        if entry.status == "D" or not tests.match_file(entry.path):
            continue

        candidate = ctx.root / entry.path

        if not candidate.is_file():
            continue

        now = _test_functions(candidate.read_text(encoding="utf-8", errors="replace"))
        # A rename is judged against where the file came from, so moving a
        # suite qualifies none of it.
        before = _test_functions(_at_base(ctx, entry.old_path or entry.path))

        if any(before.get(name) != shape for name, shape in now.items()):
            qualifying.append(entry.path)

    return qualifying


# ....................... #


def _changed_tests(ctx: GateContext, tests: pathspec.GitIgnoreSpec) -> list[str]:
    """Every changed test file (S-0106/D-6): a refactor is judged by whether
    behaviour held, so a test file is run whether or not its functions changed
    — S-0081/D-2's shape test is what the feature reading needs, not this one."""

    return [
        entry.path
        for entry in ctx.diff
        if entry.status != "D"
        and tests.match_file(entry.path)
        and (ctx.root / entry.path).is_file()
    ]


# ....................... #


def _checks(ctx: GateContext) -> list[str]:
    """The inherited rows' checks (S-0106/D-6): a task battery reads its
    contract's rows and a document-branch battery the document's, the same
    two sources `run_gates` builds `decision:<id>` gates from (S-0106/D-2)."""

    rows = [*(ctx.task.decisions if ctx.task else []), *ctx.inherited]

    return [row.check for row in rows if row.check]


# ....................... #


def _steps(files: list[str], checks: list[str]) -> list[str]:
    """What a tree is asked to run at the end of the scratch script: the
    changed test files, then every inherited check. One list, so the base tree
    and this attempt's own are asked the same question (S-0106/D-6)."""

    steps: list[str] = []

    if files:
        steps.append(f"{TEST_COMMAND} {' '.join(shlex.quote(path) for path in files)}")

    steps.extend(checks)

    return steps


# ....................... #


def _script(ctx: GateContext, files: list[str], checks: list[str]) -> str:
    copies = "\n".join(
        f'mkdir -p "$work"/{shlex.quote(str(parent))} && cp {shlex.quote(path)} '
        f'"$work"/{shlex.quote(path)}'
        for path in files
        for parent in [path.rsplit("/", 1)[0] if "/" in path else "."]
    )

    return SCRIPT.format(
        base=ctx.merge_base,
        copies=copies,
        steps="\n".join(_steps(files, checks)),
    )


# ....................... #


def _held(gate: Gate, ctx: GateContext, files: list[str], checks: list[str]) -> BuiltinOutcome:
    """The refactor reading (S-0106/D-6): run the changed test files and the
    inherited checks on the base tree and on this attempt's own, and convict
    only a disagreement between the two — behaviour that held passes, whichever
    way the trees run, and a red on both sides is acceptance's to judge."""

    if not files and not checks:
        return BuiltinOutcome(
            "skipped", "a refactor changes no test file and inherits no check to run on both trees"
        )

    timeout = gate.timeout or 600.0
    named = ", ".join(files) if files else "the inherited checks"
    # Neither side's red is the flaky case here; a re-run would only buy two
    # more trees to compare.
    base = run_command(
        _script(ctx, files, checks), ctx.root, timeout, retry_flaky=False, execute=ctx.execute
    )
    candidate = run_command(
        " && ".join(_steps(files, checks)),
        ctx.root,
        timeout,
        retry_flaky=False,
        execute=ctx.execute,
    )

    # A run that timed out compared nothing: a slow base extraction against a
    # green candidate is not behaviour that changed, so neither side convicts.
    if base.exit_code is None or candidate.exit_code is None:
        side = "the base tree" if base.exit_code is None else "this attempt's own"
        return BuiltinOutcome(
            "skipped",
            f"no comparison under the refactor: {named} timed out on {side}\n"
            f"{base.output}{candidate.output}",
        )

    base_green = base.exit_code == 0
    candidate_green = candidate.exit_code == 0

    # D-6 convicts a disagreement between green and red, never a difference in
    # exit code: two reds (a test failure against a collection error, say) are
    # both acceptance's to judge, and the gate leaves them there.
    if base_green == candidate_green:
        state = "green" if base_green else "red"
        return BuiltinOutcome(
            "pass",
            f"behaviour held under the refactor: {named} are {state} on both trees\n{base.output}",
        )

    return BuiltinOutcome(
        "fail",
        f"behaviour changed under a refactor: {named} are "
        f"{'green' if base_green else 'red'} on the base tree and "
        f"{'green' if candidate_green else 'red'} on this attempt's own\n"
        f"{base.output}{candidate.output}",
        exit_code=candidate.exit_code or base.exit_code,
    )


# ....................... #


def check_red_on_base(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.merge_base is None:
        return BuiltinOutcome("skipped", "no base is resolvable; there is no base tree to run on")

    patterns = ctx.manifest.tests.patterns

    if not patterns:
        return BuiltinOutcome("skipped", "no test patterns configured; nothing to parse")

    tests = spec(patterns)

    if ctx.refactor:
        return _held(gate, ctx, _changed_tests(ctx, tests), _checks(ctx))

    source = [e.path for e in ctx.diff if not tests.match_file(e.path)]

    if not source:
        # A contract whose whole job is adding tests is not convicted for it
        # (S-0081/D-4).
        return BuiltinOutcome("skipped", "the diff changes no source outside the test patterns")

    files = _qualifying(ctx, tests)

    if not files:
        return BuiltinOutcome("skipped", "no test function differs from its base version")

    timeout = gate.timeout or 600.0
    named = ", ".join(files)
    # A red here is the healthy case, so the flaky re-run would double the
    # cost of every passing attempt.
    base = run_command(
        _script(ctx, files, []), ctx.root, timeout, retry_flaky=False, execute=ctx.execute
    )

    if base.exit_code != 0:
        return BuiltinOutcome(
            "pass",
            f"the changed tests fail on the base tree, as tests for a change should: {named}\n"
            f"{base.output}",
        )

    candidate = run_command(
        " && ".join(_steps(files, [])), ctx.root, timeout, retry_flaky=False, execute=ctx.execute
    )

    if candidate.exit_code != 0:
        # One failing test is never filed under two gate names; this one is
        # the acceptance gate's (S-0081/D-3).
        return BuiltinOutcome(
            "skipped",
            f"the changed tests fail on this attempt's own tree, which acceptance judges: {named}",
        )

    return BuiltinOutcome(
        "fail",
        f"every changed test passes on the base tree, so the change under it is unproven: "
        f"{named}\n{base.output}",
        exit_code=base.exit_code,
    )
