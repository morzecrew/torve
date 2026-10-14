"""`mutation` — the AST mutator behind the `mutation` gate (S-0107/D-1).

A mutant is one edit to one AST node on one line the candidate's diff added or
changed, from six operators: a comparison flipped, a boolean operator swapped,
an arithmetic operator swapped, a constant moved by one, a condition negated,
and a return replaced by `None`. A line the diff did not touch is never
mutated, and a test file is never mutated, so every survivor names the
attempt's own code.

Mutants are enumerated in path and line order and then sampled to a budget with
a seed derived from the diff (S-0107/D-2): one change yields the same mutants
however many times it is scored.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import random
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from torve.base.globs import matches
from torve.base.shell import run_command
from torve.config.manifest import DEFAULT_TEST_PATTERNS, DEFAULT_TEST_RUN, Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome, spec

# ----------------------- #

# The default mutant budget: at most this many mutants are sampled from a
# diff, so the score costs a bounded amount (S-0107/D-2).
DEFAULT_BUDGET = 30

# How the paired tests are run. The engine's default, and the seam a test points
# at its own interpreter; a manifest naming its own `tests.run` wins (S-0106/D-7).
TEST_COMMAND = DEFAULT_TEST_RUN

# A hunk header, read only for the first line number of its new side.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# ....................... #

# One edit is a single node rebuilt with its counterpart: the operator table,
# node type to the node type it becomes (S-0107/D-1).
_COMPARISONS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

_BOOLEANS: dict[type[ast.boolop], type[ast.boolop]] = {ast.And: ast.Or, ast.Or: ast.And}

_ARITHMETIC: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div,
}


# ....................... #


@dataclass(frozen=True)
class Mutant:
    """One edit to one node on one changed line, with the file's text after it."""

    path: str
    line: int
    operator: str
    edit: str
    source: str


@dataclass(frozen=True)
class _Edit:
    """A mutant before it is applied: the node's span, its text, and its new text."""

    operator: str
    line: int
    start: int
    end: int
    old: str
    new: str


# ....................... #


def changed_lines(patch: str) -> dict[str, set[int]]:
    """The new-file line numbers *patch* adds or changes, by path.

    A line of context and a line the diff removes are not changed lines: a
    mutant on either would edit code the attempt did not write (S-0107/D-1)."""

    changed: dict[str, set[int]] = {}
    path: str | None = None
    line = 0

    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].split("\t", 1)[0]
            path = target[2:] if target.startswith("b/") else None

        elif raw.startswith("@@"):
            match = _HUNK.match(raw)
            line = int(match.group(1)) if match and path is not None else 0

        elif path is None:
            continue

        elif raw.startswith("+"):
            changed.setdefault(path, set()).add(line)
            line += 1

        elif raw.startswith(" "):
            line += 1

        # `-` and the `\ No newline` marker advance nothing.

    return changed


# ....................... #


def mutants(
    patch: str,
    sources: Mapping[str, str],
    *,
    tests: Iterable[str] = DEFAULT_TEST_PATTERNS,
    budget: int = DEFAULT_BUDGET,
) -> list[Mutant]:
    """The mutants on *patch*'s changed Python lines, sampled to *budget*.

    *sources* is the candidate's text by repo-relative path; a changed path it
    does not hold (a deletion) yields nothing, as does a path that is not
    Python, is a test file, or has no changed line (S-0107/D-1)."""

    changed = changed_lines(patch)
    patterns = tuple(tests)
    found: list[Mutant] = []

    for path in sorted(changed):
        if not path.endswith(".py") or matches(path, patterns):
            continue

        source = sources.get(path)

        if source is None:
            continue

        for edit in _edits(source, changed[path]):
            found.append(
                Mutant(
                    path=path,
                    line=edit.line,
                    operator=edit.operator,
                    edit=f"{edit.old} -> {edit.new}",
                    source=f"{source[: edit.start]}{edit.new}{source[edit.end :]}",
                )
            )

    return _sample(found, patch, budget)


# ....................... #


def _sample(found: list[Mutant], patch: str, budget: int) -> list[Mutant]:
    """*found* to at most *budget*, in path and line order, chosen by a seed
    from *patch* so one diff always samples the same mutants (S-0107/D-2)."""

    if budget <= 0:
        return []

    if len(found) <= budget:
        return found

    seed = int.from_bytes(hashlib.sha256(patch.encode("utf-8")).digest()[:8], "big")

    return sorted(
        random.Random(seed).sample(found, budget),
        key=lambda mutant: (mutant.path, mutant.line, mutant.operator),
    )


# ....................... #


def _edits(source: str, changed: set[int]) -> list[_Edit]:
    """Every one-node edit on a changed line of *source*, in line order."""

    try:
        tree = ast.parse(source)

    except SyntaxError:
        return []

    edits: list[_Edit] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            comparison = _COMPARISONS.get(type(node.ops[0]))

            if comparison is not None and node.lineno in changed:
                compared = ast.Compare(node.left, [comparison()], node.comparators)
                edits.append(_edit(source, "comparison", node, ast.unparse(compared)))

        elif isinstance(node, ast.BoolOp):
            boolean = _BOOLEANS.get(type(node.op))

            if boolean is not None and node.lineno in changed:
                joined = ast.BoolOp(boolean(), node.values)
                edits.append(_edit(source, "boolean", node, ast.unparse(joined)))

        elif (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and node.lineno in changed
        ):
            # The operand keeps its parentheses: `not` binds looser than a
            # comparison but tighter than `and`, so `z and not (x or y)` with the
            # bare operand would parse as `(z and x) or y` — a second edit.
            edits.append(_edit(source, "boolean", node, f"({ast.unparse(node.operand)})"))

        elif isinstance(node, ast.BinOp):
            arithmetic = _ARITHMETIC.get(type(node.op))

            if arithmetic is not None and node.lineno in changed:
                operated = ast.BinOp(node.left, arithmetic(), node.right)
                edits.append(_edit(source, "arithmetic", node, ast.unparse(operated)))

        elif isinstance(node, ast.Constant) and node.lineno in changed:
            moved = _moved(node.value)

            if moved is not None:
                edits.append(_edit(source, "constant", node, ast.unparse(ast.Constant(moved))))

        elif isinstance(node, ast.If | ast.While) and node.test.lineno in changed:
            negated = ast.UnaryOp(ast.Not(), node.test)
            edits.append(_edit(source, "condition", node.test, ast.unparse(negated)))

        elif isinstance(node, ast.Return) and node.value is not None and node.lineno in changed:
            edits.append(_edit(source, "return", node, "return None"))

    edits.sort(key=lambda edit: (edit.line, edit.start))

    return edits


def _edit(source: str, operator: str, node: ast.expr | ast.stmt, new: str) -> _Edit:
    """*node*'s span in *source* as an edit that replaces it with *new*."""

    lines = source.splitlines(keepends=True)
    end_lineno = node.end_lineno if node.end_lineno is not None else node.lineno
    end_col = node.end_col_offset if node.end_col_offset is not None else node.col_offset
    start = _offset(lines, node.lineno, node.col_offset)
    end = _offset(lines, end_lineno, end_col)

    return _Edit(operator, node.lineno, start, end, source[start:end], new)


def _offset(lines: list[str], lineno: int, col: int) -> int:
    """(lineno, col) as a character index into the source *lines* were split from.

    The AST reports columns in UTF-8 bytes, so the column is measured in bytes
    of its own line and converted back to characters before it joins the
    character count of the lines before it (S-0107/D-1)."""

    prefix = lines[lineno - 1].encode("utf-8")[:col].decode("utf-8", errors="ignore")

    return sum(len(text) for text in lines[: lineno - 1]) + len(prefix)


def _moved(value: object) -> bool | int | None:
    """A constant's mutant value: an int moved by one, a bool flipped. None for
    a constant the constant operator does not name."""

    if isinstance(value, bool):
        return not value

    if isinstance(value, int):
        return value + 1

    return None


# ----------------------- #
# The gate (S-0107/D-2). The mutator above enumerates; this samples, applies
# each mutant in a scratch copy of the worktree, and scores by whether the
# task's paired tests notice.


def _command(ctx: GateContext) -> str:
    """The launcher the paired tests are appended to: the manifest's `tests.run`
    (S-0106/D-7), or the module's `TEST_COMMAND` when the manifest leaves it at
    the engine default."""

    declared = ctx.manifest.tests.run

    return TEST_COMMAND if declared == DEFAULT_TEST_RUN else declared


# ....................... #


def _changed_modules(ctx: GateContext) -> list[str]:
    """The changed Python source files — a deleted file gone, a test file not
    source, and an untracked file (status A) source like any other."""

    tests = spec(ctx.manifest.tests.patterns)
    found: list[str] = []

    for entry in ctx.diff:
        if (
            entry.status != "D"
            and entry.path.endswith(".py")
            and not tests.match_file(entry.path)
            and (ctx.root / entry.path).is_file()
        ):
            found.append(entry.path)

    return found


# ....................... #


def _paired_tests(ctx: GateContext, modules: Iterable[str]) -> list[str]:
    """The existing test files pairing with *modules* under the manifest's
    `tests.pair` templates (S-0106/D-7), so a module's own test is what judges
    its mutants and nothing else runs."""

    found: set[str] = set()

    for module in modules:
        for pattern in ctx.manifest.tests.expansions(module):
            for match in ctx.root.glob(pattern):
                if match.is_file():
                    found.add(str(match.relative_to(ctx.root)))

    return sorted(found)


# ....................... #


def _script(command: str, files: list[str], found: list[Mutant], budget_s: float) -> str:
    """One shell run: copy the worktree to a scratch directory, run the paired
    tests to time a baseline, then apply each mutant in turn and rerun them
    under twice that baseline. Every mutant restores its file from its own
    encoded text first, so no edit is composed on another's.

    The scratch copy is the whole worktree without `.git`, made and removed
    inside the run, so the worktree under judgment is never written
    (S-0107/D-2)."""

    named = " ".join(shlex.quote(path) for path in files)
    lines = [
        "set -u",
        "work=$(mktemp -d)",
        "trap 'rm -rf \"$work\"' EXIT INT TERM",
        'tar -C . --exclude=.git -cf - . | tar -C "$work" -xf -',
        'cd "$work"',
        "start=$(date +%s%N)",
        f"timeout {budget_s:.0f} {command} {named} >/dev/null 2>&1",
        "baseline_code=$?",
        "baseline_ms=$(( ($(date +%s%N) - start) / 1000000 ))",
        'if [ "$baseline_code" -eq 124 ]; then echo BASELINE-TIMEOUT; exit 0; fi',
        'if [ "$baseline_code" -ne 0 ]; then echo BASELINE-RED; exit 0; fi',
        # Twice the baseline, in whole seconds, with a floor of ten: a small
        # suite's baseline is mostly interpreter start-up, which a loaded host
        # can more than double, and a mutant cut off there reads as timed out
        # rather than killed or survived.
        "t=$(( baseline_ms / 500 ))",
        '[ "$t" -lt 10 ] && t=10',
    ]

    for mutant in found:
        blob = base64.b64encode(mutant.source.encode("utf-8")).decode("ascii")
        location = shlex.quote(f"{mutant.path}:{mutant.line}")
        # A multi-line node's edit is reported on one line, so a survivor is
        # always a single `path:line — edit`.
        edit = shlex.quote(" ".join(mutant.edit.split()))
        lines.append(f"printf %s {shlex.quote(blob)} | base64 -d > {shlex.quote(mutant.path)}")
        lines.append(f'timeout "$t" {command} {named} >/dev/null 2>&1')
        lines.append("code=$?")
        lines.append('if [ "$code" -eq 124 ]; then')
        lines.append(f"  printf 'TIMEOUT\\t%s\\t%s\\n' {location} {edit}")
        lines.append('elif [ "$code" -eq 0 ]; then')
        lines.append(f"  printf 'SURVIVED\\t%s\\t%s\\n' {location} {edit}")
        lines.append("else")
        lines.append(f"  printf 'KILLED\\t%s\\n' {location}")
        lines.append("fi")

    return "\n".join(lines)


# ....................... #


def _verdict(output: str) -> BuiltinOutcome:
    """*output*'s marker lines as the score (S-0107/D-2): killed, survived and
    timed-out counts, the score, and every survivor as `path:line — edit`."""

    killed = survived = timed_out = 0
    survivors: list[str] = []

    for line in output.splitlines():
        if line == "BASELINE-TIMEOUT":
            return BuiltinOutcome("error", "the paired tests did not finish a baseline run")

        if line == "BASELINE-RED":
            return BuiltinOutcome(
                "skipped",
                "the paired tests fail on the tree as it stands, which acceptance judges",
            )

        fields = line.split("\t")

        if fields[0] == "KILLED":
            killed += 1

        elif fields[0] == "SURVIVED":
            survived += 1
            survivors.append(f"{fields[1]} — {fields[2]}")

        elif fields[0] == "TIMEOUT":
            timed_out += 1

    total = killed + survived + timed_out

    if total == 0:
        return BuiltinOutcome("skipped", "the mutation run scored no mutant")

    score = killed / total
    report = (
        f"mutation score {score:.2f}: killed {killed}, survived {survived}, "
        f"timed out {timed_out} of {total}"
    )

    if survivors:
        report += "\nsurvivors:\n" + "\n".join(survivors)

    if killed == total:
        return BuiltinOutcome("pass", report)

    return BuiltinOutcome("fail", report, exit_code=1)


# ....................... #


def check_mutation(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    modules = _changed_modules(ctx)

    if not modules:
        return BuiltinOutcome(
            "skipped", "the diff changes no Python source line, so nothing is mutated"
        )

    sources = {
        path: (ctx.root / path).read_text(encoding="utf-8", errors="replace") for path in modules
    }
    found = mutants(ctx.patch, sources, tests=ctx.manifest.tests.patterns, budget=DEFAULT_BUDGET)

    if not found:
        return BuiltinOutcome("skipped", "the changed lines hold nothing the six operators edit")

    paired = _paired_tests(ctx, modules)

    if not paired:
        return BuiltinOutcome(
            "skipped", f"no test pairs with the changed source ({', '.join(modules)})"
        )

    timeout = gate.timeout or 600.0
    result = run_command(
        _script(_command(ctx), paired, found, timeout / 2),
        ctx.root,
        timeout,
        retry_flaky=False,
        execute=ctx.execute,
    )

    if result.exit_code is None:
        return BuiltinOutcome(
            "error", f"the mutation run did not finish within {timeout:.0f}s\n{result.output}"
        )

    return _verdict(result.output)
