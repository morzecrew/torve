"""`evidence` — every behavioural row is proved by a test the change turned
green (S-0115/D-1 to D-4, D-9).

A test function proves a row when its own source carries the row's full
identifier as a whole token: its docstring, its comments and its body, never a
module or class docstring above it, so one citation at the top of a file cannot
vouch for every test in it (D-1). Only functions whose parsed shape differs from
the base tree's qualify, read as `red-on-base` reads them (S-0081/D-2). The gate
matches the identifiers of the rows this battery carries, so it needs no
citation grammar of its own.

Each citing function is run twice — on the base tree, built exactly as
`red-on-base` builds it, and on the candidate. A row is proved when one of its
functions is red on the base tree and green on the candidate (D-2). A row that
carries a `check` is judged by its own `decision:<id>` gate, and this gate does
not run it again.

A row is behavioural unless every one of its paths is documentation: a `*.md`
file or a path under `pages/` (D-3). A behavioural row with no proving test and
no check fails the gate, named; a battery carrying no behavioural row at all is
skipped. In a verify attempt the battery skips too: those tests are red by
design and `verify-tests` judges them. In an implement attempt the test
functions of the verifier's file the contract denies count as changed, so the
rows they prove read as proved on the attempt that turns them green (D-9).

The output is one line per carried row, whether it passed or failed:

    row S-0115/D-1: pass — tests/test_row_evidence.py::test_a_row_is_proved
    row S-0115/D-3: fail — no test cites the row
    row S-0115/D-5: check — judged by its decision gate
    row S-0115/D-8: documentation — no test required

`evidence_reading` parses that output back to rows for the lane's joined table
(D-5) and the review prompt (D-10).
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass

import pathspec

from torve.base.shell import run_command
from torve.config.manifest import DEFAULT_TEST_RUN, Gate
from torve.domain.task import InheritedDecision
from torve.gates.context import GateContext, GitError, git
from torve.gates.contract import BuiltinOutcome, spec

# ----------------------- #

# How the citing functions are run: the manifest's `tests.run` (S-0106/D-7),
# or the engine default. The module constant is the seam a test points at its
# own interpreter.
TEST_COMMAND = DEFAULT_TEST_RUN

# A row's full identifier as a whole token, so row 2 of a document never
# matches row 21's citation.
_ROW = re.compile(r"\bS-\d{4}/D-\d+\b")

# One result line, and the marker each scratch run leaves with its exit code.
_READING = re.compile(
    r"^row (S-\d{4}/D-\d+): (pass|fail|check|documentation)(?: — (.*))?$", re.MULTILINE
)
_MARKER = "__torve_evidence__"
_RESULT = re.compile(rf"{_MARKER} (\d+) (\d+)")

# The base tree, built as `red-on-base` builds it (S-0081/D-5): a `git archive`
# extracted outside the repository, with the candidate's test files copied over
# it, and removed by the trap whichever way the script leaves.
SCRIPT = """work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
git archive {base} | tar -x -C "$work" || exit 90
{copies}
cd "$work" || exit 91
{steps}
"""


# ....................... #


@dataclass(frozen=True)
class RowEvidence:
    """One carried row's verdict, as `evidence_reading` reads it back."""

    row: str
    """The row's global identifier, exactly as the battery names it."""
    state: str
    """`pass`, `fail`, `check` or `documentation`."""
    tests: tuple[str, ...] = ()
    """The tests that proved the row; empty unless the state is `pass`."""
    detail: str = ""
    """The line's prose after the verdict."""

    @property
    def proved(self) -> bool:
        return self.state == "pass"


# ....................... #


@dataclass(frozen=True)
class _Function:
    """A test function: its pytest node suffix, its parsed shape, and the rows
    its own source cites."""

    node: str
    shape: str
    rows: frozenset[str]


@dataclass(frozen=True)
class _Citation:
    row: str
    file: str
    node: str

    @property
    def test(self) -> str:
        return f"{self.file}::{self.node}"


# ....................... #


def _test_functions(source: str) -> dict[str, _Function] | None:
    """Every test function in a module, by dotted qualified name. None when the
    source does not parse at all; a module that parses but names no test is an
    empty mapping.

    A function's citations are read from its own source segment — its docstring,
    comments and body — so a module or a class docstring above it proves
    nothing on its behalf (D-1).
    """

    try:
        tree = ast.parse(source)

    except SyntaxError:
        return None

    functions: dict[str, _Function] = {}

    def collect(body: list[ast.stmt], dotted: str, nodes: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                collect(node.body, f"{dotted}{node.name}.", f"{nodes}{node.name}::")

            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                segment = ast.get_source_segment(source, node) or ""
                functions[dotted + node.name] = _Function(
                    node=nodes + node.name,
                    shape=ast.dump(node),
                    rows=frozenset(_ROW.findall(segment)),
                )

    collect(tree.body, "", "")

    return functions


# ....................... #


def _at_base(ctx: GateContext, path: str) -> str:
    """The file as the base tree holds it; empty when it holds it not at all."""

    try:
        return git(ctx.root, "show", f"{ctx.merge_base}:{path}")

    except GitError:
        return ""


# ....................... #


def _citations(
    ctx: GateContext, tests: pathspec.GitIgnoreSpec, carried: set[str]
) -> dict[tuple[str, str, str], _Citation]:
    """The changed test functions citing a carried row, by (file, node, row).

    A function qualifies when its parsed shape differs from the base tree's, or
    — in an implement attempt — when it lives in a file the contract denies the
    implementer (D-9): the verifier's file already sits on the attempt's base,
    so nothing about it looks changed.
    """

    found: dict[tuple[str, str, str], _Citation] = {}

    def add(path: str, function: _Function) -> None:
        for row in function.rows & carried:
            found[(path, function.node, row)] = _Citation(row=row, file=path, node=function.node)

    for entry in ctx.diff:
        if entry.status == "D" or not tests.match_file(entry.path):
            continue

        candidate = ctx.root / entry.path

        if not candidate.is_file():
            continue

        now = _test_functions(candidate.read_text(encoding="utf-8", errors="replace"))

        if not now:
            continue

        before = _test_functions(_at_base(ctx, entry.old_path or entry.path)) or {}

        for name, function in now.items():
            earlier = before.get(name)

            if earlier is None or earlier.shape != function.shape:
                add(entry.path, function)

    if ctx.task is not None and ctx.task.role == "implement":
        for path in ctx.task.scope.deny:
            candidate = ctx.root / path

            if not candidate.is_file():
                continue

            for function in (
                _test_functions(candidate.read_text(encoding="utf-8", errors="replace")) or {}
            ).values():
                add(path, function)

    return found


# ....................... #


def _command(ctx: GateContext) -> str:
    declared = ctx.manifest.tests.run

    return TEST_COMMAND if declared == DEFAULT_TEST_RUN else declared


def _step(command: str, node: str, index: int) -> str:
    return f"{command} {shlex.quote(node)} >/dev/null 2>&1; printf '{_MARKER} {index} %s\\n' \"$?\""


def _steps(command: str, nodes: list[str]) -> str:
    return "\n".join(_step(command, node, index) for index, node in enumerate(nodes))


def _copies(files: list[str]) -> str:
    return "\n".join(
        f'mkdir -p "$work"/{shlex.quote(parent)} && '
        f'cp {shlex.quote(path)} "$work"/{shlex.quote(path)}'
        for path in files
        for parent in [path.rsplit("/", 1)[0] if "/" in path else "."]
    )


def _results(output: str) -> dict[int, int]:
    return {int(index): int(code) for index, code in _RESULT.findall(output)}


# ....................... #


def _rows(ctx: GateContext) -> list[InheritedDecision]:
    """The rows this battery carries: a task's contract rows and a document
    battery's inherited ones, deduplicated by identifier in the order read."""

    rows: list[InheritedDecision] = [
        *(ctx.task.decisions if ctx.task is not None else []),
        *ctx.inherited,
    ]
    unique: dict[str, InheritedDecision] = {}

    for row in rows:
        unique.setdefault(row.id, row)

    return list(unique.values())


def _documentation(path: str) -> bool:
    return path.endswith(".md") or path == "pages" or path.startswith("pages/")


def _behavioural(row: InheritedDecision) -> bool:
    """A row with no paths is not tied to code, so it is documentation too."""

    return any(not _documentation(path) for path in row.paths)


# ....................... #


def check_evidence(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    if ctx.task is not None and ctx.task.role == "verify":
        return BuiltinOutcome(
            "skipped",
            "a verify attempt's tests are red by design; verify-tests judges them",
        )

    rows = _rows(ctx)
    behavioural = [row for row in rows if _behavioural(row)]

    if not behavioural:
        return BuiltinOutcome("skipped", "the battery carries no behavioural row")

    if ctx.merge_base is None:
        return BuiltinOutcome("skipped", "no base is resolvable; there is no base tree to run on")

    patterns = ctx.manifest.tests.patterns

    if not patterns:
        return BuiltinOutcome("skipped", "no test patterns configured; nothing to parse")

    judged = [row for row in behavioural if not row.check]
    carried = {row.id for row in judged}
    citations = _citations(ctx, spec(patterns), carried)

    by_row: dict[str, list[str]] = {}

    for citation in citations.values():
        by_row.setdefault(citation.row, []).append(citation.test)

    for row_id, tests in by_row.items():
        by_row[row_id] = sorted(dict.fromkeys(tests))

    nodes = sorted({test for row_id in carried for test in by_row.get(row_id, [])})
    proved: set[str] = set()

    if nodes:
        timeout = gate.timeout or 300.0
        command = _command(ctx)
        files = sorted({node.split("::", 1)[0] for node in nodes})
        base = run_command(
            SCRIPT.format(base=ctx.merge_base, copies=_copies(files), steps=_steps(command, nodes)),
            ctx.root,
            timeout,
            retry_flaky=False,
            execute=ctx.execute,
        )

        if base.exit_code is None:
            return BuiltinOutcome(
                "skipped",
                "the base tree run timed out; nothing was compared\n" + base.output,
            )

        base_codes = _results(base.output)
        red = [node for index, node in enumerate(nodes) if base_codes.get(index) not in (0, None)]

        if red:
            candidate = run_command(
                _steps(command, red),
                ctx.root,
                timeout,
                retry_flaky=False,
                execute=ctx.execute,
            )

            if candidate.exit_code is None:
                return BuiltinOutcome(
                    "skipped",
                    "the candidate run timed out; nothing was compared\n" + candidate.output,
                )

            green = _results(candidate.output)
            proved = {node for index, node in enumerate(red) if green.get(index) == 0}

    lines: list[str] = []
    unproved: list[str] = []

    for row in rows:
        if not _behavioural(row):
            lines.append(f"row {row.id}: documentation — no test required")

        elif row.check:
            lines.append(f"row {row.id}: check — judged by its decision gate")

        else:
            proving = [test for test in by_row.get(row.id, []) if test in proved]

            if proving:
                lines.append(f"row {row.id}: pass — {', '.join(proving)}")

            else:
                citing = by_row.get(row.id, [])
                detail = (
                    "no test cites the row"
                    if not citing
                    else f"none of its {len(citing)} citing test(s) was red on the base tree "
                    "and green on the candidate"
                )
                lines.append(f"row {row.id}: fail — {detail}")
                unproved.append(row.id)

    output = "\n".join(lines)

    if unproved:
        return BuiltinOutcome("fail", output, exit_code=1)

    return BuiltinOutcome("pass", output)


# ....................... #


def evidence_reading(text: str) -> list[RowEvidence]:
    """The rows one `evidence` result names, in the order it named them. A
    result that names none — a skipped battery, a gate that never ran — reads
    as no rows, so its callers leave their output as it was (D-5, D-10)."""

    found: list[RowEvidence] = []

    for match in _READING.finditer(text):
        state = match.group(2)
        detail = (match.group(3) or "").strip()
        tests = (
            tuple(part.strip() for part in detail.split(",") if part.strip())
            if state == "pass"
            else ()
        )
        found.append(RowEvidence(row=match.group(1), state=state, tests=tests, detail=detail))

    return found
