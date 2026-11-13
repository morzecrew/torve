"""The evidence package (S-0122/D-5, S-0122/D-2): what the host measured about
one candidate, written as `.torve/review/evidence.yaml` for the critic.

Every reading here is produced by the host or the throwaway gates sandbox the
battery ran in — never by the executor. The package holds the base and head
shas, the diff's size per path, the paths the change wrote outside its
contract's scope (advisory) and inside its fence (blocking elsewhere), every
gate result with the suite's convicted, inherited and flaky ids, and the
changed and added test functions run at the head and on the base. On the base
means the candidate's own test files over the base tree's source, as
`red-on-base` ran them (S-0081): a test green there would have passed before a
line was written. Each row the unit owns gets a status from those runs:

- `proved`: red, erroring or absent on the base, green on the head;
- `held`: green on both — a check the change did not have to turn green;
- `broken`: green on the base, red on the head;
- `failing`: red on both, or red on the head with nothing to compare;
- `unlinked`: no check links to the row;
- `not_run`: a check links to it and could not be run.

A test links to a row when the test function's own source names the row's
id; a row's `check` command links to it by construction. Acceptance commands
the battery did not already run are run here and listed with their exit code
and a tail. An AST diff of the existing test functions the change edited flags
removed asserts, added skips, loosened assertions, changed expected values
and deleted tests; greps flag added text that addresses a reviewer or a model,
source that branches on running under test, and touches to the test harness.
The executor's one channel, a `torve abort` record, rides as `author_claim`:
a claim, labelled as one.

The rendered package is capped (24 KB by default): `held` test runs go first,
then the rows the unit does not own, then the longest lists, and the package
says it was cut. The raw outputs ride beside it as logs, never inside it.
"""

from __future__ import annotations

import ast
import codecs
import configparser
import re
import shlex
import shutil
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml

from torve.base.globs import matches, overlap
from torve.base.shell import clip, run_command
from torve.config import layout
from torve.domain.attempt import GateResult
from torve.domain.task import InheritedDecision, Task
from torve.gates.acceptance import selected_nothing
from torve.gates.context import GateContext, GitError, git
from torve.gates.suite import collection_errors, extract_base, failing_ids, settled_ids

# ----------------------- #

SCHEMA = "torve.evidence/v1"

#: The rendered package's ceiling (S-0122/D-5).
MAX_BYTES = 24_000

#: The tail of an acceptance command's output the package keeps.
TAIL_BYTES = 2_000

#: The most entries of one flag kind the package lists; the rest are counted.
FLAG_CAP = 20

#: A suite gate's timeout when the manifest names none, as the gate's own.
DEFAULT_TIMEOUT = 600.0

Reading = Literal["pass", "fail", "error", "absent", "not_run"]
Status = Literal["proved", "held", "broken", "failing", "unlinked", "not_run"]

# Worst first: a row with several links takes the worst status among them.
_STATUS_ORDER: tuple[Status, ...] = ("broken", "failing", "proved", "held", "not_run")

_RED: frozenset[Reading] = frozenset({"fail", "error"})

#: What the package adds to the manifest's test launcher: the short summary
#: names failures, errors and passes, so a test that never ran reads as such.
_REPORT_CHARS = "-rfEp"
_PASSED = re.compile(r"^PASSED (\S+)", re.MULTILINE)

# Added text that addresses whoever judges the change rather than whoever
# reads the code: a reviewer, a critic, a model. Case-blind, whole words.
_SUSPICIOUS = re.compile(
    r"(?i)\b(?:reviewers?|critic|ignore (?:all |any )?(?:previous|prior|above)"
    r"|AI review(?:er)?|language model|as an AI|LLMs?|disregard (?:the )?instructions)\b"
)

# Source that can tell it is running under a test, or names one.
_UNDER_TEST = re.compile(
    r"PYTEST_CURRENT_TEST|inspect\.stack|sys\._getframe|[\"']_?pytest[\"'] in sys\.modules"
    r"|sys\.modules\[[\"']_?pytest[\"']\]|\bconftest\b|\btest_\w+\.py\b"
)
_ARGV_PYTEST = re.compile(r"sys\.argv.*pytest|pytest.*sys\.argv")

# The test harness: files whose edit changes how every test runs.
_HARNESS_NAMES = frozenset({"conftest.py", "pytest.ini", "sitecustomize.py", "usercustomize.py"})

_SKIP_NAMES = ("skip", "skipif", "xfail", "skipTest")


# ....................... #


@dataclass(frozen=True)
class FileStat:
    path: str
    kind: Literal["src", "test"]
    added: int
    removed: int


@dataclass(frozen=True)
class GateReading:
    name: str
    outcome: str
    state: str
    duration_s: float
    convicted: tuple[str, ...] = ()
    inherited: tuple[str, ...] = ()
    flaky: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckedTest:
    id: str
    added: bool
    base: Reading
    head: Reading
    status: Status
    rows: tuple[str, ...] = ()


@dataclass(frozen=True)
class RowCheck:
    row: str
    grade: str
    owned: bool
    status: Status
    checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandRun:
    command: str
    exit_code: int | None
    tail: str


@dataclass(frozen=True)
class EditedTest:
    test: str
    change: Literal[
        "assert_removed", "skip_added", "assertion_loosened", "expected_changed", "test_deleted"
    ]
    detail: str


@dataclass(frozen=True)
class Flag:
    kind: Literal["suspicious_text", "special_casing", "harness_touch"]
    path: str
    line: int | None
    text: str


@dataclass(frozen=True)
class AuthorClaim:
    decision: str
    kind: str
    claim: str
    evidence: str


@dataclass
class EvidencePackage:
    """One candidate's measurements. `logs` holds the raw outputs by file
    name; it is staged beside the rendered package and never inside it."""

    candidate: str
    base: str | None
    head: str
    files: list[FileStat] = field(default_factory=list)
    outside_files: list[str] = field(default_factory=list)
    fence_breaches: list[str] = field(default_factory=list)
    gates: list[GateReading] = field(default_factory=list)
    tests: list[CheckedTest] = field(default_factory=list)
    rows: list[RowCheck] = field(default_factory=list)
    acceptance: list[CommandRun] = field(default_factory=list)
    test_changes: list[EditedTest] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    author_claim: list[AuthorClaim] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)

    # ....................... #

    def failing_items(self) -> dict[str, Any]:
        """What a revision must answer (S-0122/D-8): the rows and tests that
        are red at the head, the acceptance commands that failed, the edits
        to existing tests and every flag — the package's own words, so the
        revision reads measurements and never a model's account of them."""

        items: dict[str, Any] = {
            "rows": [
                _asdict(row) for row in self.rows if row.status in ("broken", "failing", "not_run")
            ],
            "tests": [_asdict(run) for run in self.tests if run.head in _RED],
            "acceptance": [_asdict(run) for run in self.acceptance if run.exit_code != 0],
            "test_changes": [_asdict(change) for change in self.test_changes],
            "flags": [_asdict(flag) for flag in self.flags],
            "fence_breaches": list(self.fence_breaches),
        }

        return {key: value for key, value in items.items() if value}


# ....................... #


def _asdict(item: Any) -> dict[str, Any]:
    """A record as the package writes it: empty tuples and None left out, so
    a row with nothing to say costs its name and no more."""

    out: dict[str, Any] = {}

    for name in item.__dataclass_fields__:
        value = getattr(item, name)

        if value is None or value == ():
            continue

        out[name] = list(value) if isinstance(value, tuple) else value

    return out


# ----------------------- #
# The diff, read from the patch the battery judged.


def _patch_sections(patch: str) -> Iterator[tuple[str, list[str]]]:
    """(path, lines) per file section of a unified diff, the new-side path."""

    path: str | None = None
    lines: list[str] = []

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            if path is not None:
                yield path, lines

            path = _new_path(line)
            lines = []
            continue

        if path is not None:
            lines.append(line)

    if path is not None:
        yield path, lines


def _new_path(header: str) -> str:
    """The new-side path of a `diff --git` header. Git C-quotes a path that
    holds a quote, a backslash, a control or a non-ASCII byte."""

    if header.endswith('"') and ' "b/' in header:
        quoted = header[header.rindex(' "b/') + 2 : -1].encode("utf-8")
        return codecs.escape_decode(quoted)[0].decode("utf-8", "replace")[2:]

    return header.split(" b/", 1)[-1].strip() if " b/" in header else ""


def added_lines(patch: str) -> dict[str, list[tuple[int, str]]]:
    """New-side (line number, text) of every added line, by path."""

    found: dict[str, list[tuple[int, str]]] = {}

    for path, lines in _patch_sections(patch):
        rows = found.setdefault(path, [])

        for number, kind, text in _hunk_lines(lines):
            if kind == "+":
                rows.append((number, text))

    return found


def _hunk_lines(lines: list[str]) -> Iterator[tuple[int, str, str]]:
    """(new-side line, `+`, `-` or ` `, text) for each line inside a hunk.
    Lines before the first hunk are the section's header — `--- a/x` and
    `+++ b/x` among them — and are no change."""

    number = 0
    inside = False

    for line in lines:
        if line.startswith("@@ "):
            hunk = re.search(r"\+(\d+)", line)
            number = int(hunk.group(1)) if hunk else 0
            inside = True

        elif inside and line[:1] in ("+", "-", " "):
            yield number, line[:1], line[1:]
            number += line[:1] != "-"


def diff_stats(patch: str, test_patterns: Iterable[str]) -> list[FileStat]:
    """Lines added and removed per path, each path a test or source file by
    the manifest's test patterns."""

    patterns = list(test_patterns)
    stats: list[FileStat] = []

    for path, lines in _patch_sections(patch):
        kinds = Counter(kind for _, kind, _ in _hunk_lines(lines))
        added, removed = kinds["+"], kinds["-"]
        kind: Literal["src", "test"] = "test" if matches(path, patterns) else "src"
        stats.append(FileStat(path=path, kind=kind, added=added, removed=removed))

    return stats


# ----------------------- #
# Test functions, read as Python.


def functions_of(source: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module's test functions by their node id's tail: `test_x` at
    module level and `TestY::test_x` inside a test class."""

    try:
        tree = ast.parse(source)

    except (SyntaxError, ValueError):
        return {}

    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            found[node.name] = node

        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, ast.FunctionDef | ast.AsyncFunctionDef
                ) and member.name.startswith("test"):
                    found[f"{node.name}::{member.name}"] = member

    return found


def _function_source(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The function's lines, decorators included."""

    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    lines = source.splitlines()

    return "\n".join(lines[first - 1 : node.end_lineno or node.lineno])


def _row_pattern(row_id: str) -> re.Pattern[str]:
    """The row's id as a whole token: a row never links an id that merely
    starts with its own (its tenth sibling, or a dotted sub-row), and a
    document-local id never links the same id under a document prefix."""

    return re.compile(rf"(?<![\w/-]){re.escape(row_id)}(?!\d|\.\d)")


def _same(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


# ....................... #


def _asserts(node: ast.AST) -> list[ast.AST]:
    """Each assertion in a test: an `assert` statement or an `assert*` call."""

    found: list[ast.AST] = []

    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            found.append(child.test)

        elif isinstance(child, ast.Call):
            name = _call_name(child.func)

            if name.startswith("assert") and name != "assert_":
                found.append(child)

    return found


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr

    if isinstance(func, ast.Name):
        return func.id

    return ""


def _skips(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The skip and xfail markers a test carries or calls."""

    found: set[str] = set()

    for decorator in node.decorator_list:
        text = ast.unparse(decorator)
        found.update(name for name in _SKIP_NAMES if re.search(rf"\b{name}\b", text))

    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _call_name(child.func) in _SKIP_NAMES:
            found.add(_call_name(child.func))

    return found


def _operators(assertions: list[ast.AST]) -> Counter[str]:
    counted: Counter[str] = Counter()

    for assertion in assertions:
        for child in ast.walk(assertion):
            if isinstance(child, ast.Compare):
                counted.update(type(op).__name__ for op in child.ops)

            elif isinstance(child, ast.Call) and _call_name(child.func) == "approx":
                counted["approx"] += 1

    return counted


def _tolerances(assertions: list[ast.AST]) -> list[float]:
    found: list[float] = []

    for assertion in assertions:
        for child in ast.walk(assertion):
            if isinstance(child, ast.Call) and _call_name(child.func) == "approx":
                for keyword in child.keywords:
                    if keyword.arg in ("rel", "abs") and isinstance(keyword.value, ast.Constant):
                        value = keyword.value.value

                        if isinstance(value, int | float) and not isinstance(value, bool):
                            found.append(float(value))

    return found


def _constants(assertions: list[ast.AST]) -> Counter[str]:
    """The literal values the assertions compare against."""

    counted: Counter[str] = Counter()

    for assertion in assertions:
        for child in ast.walk(assertion):
            if isinstance(child, ast.Constant) and child.value is not None:
                counted[repr(child.value)] += 1

    return counted


def edit_of(
    test: str,
    base: ast.FunctionDef | ast.AsyncFunctionDef,
    head: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[EditedTest]:
    """What an edit to an existing test did to its strength."""

    changes: list[EditedTest] = []
    before, after = _asserts(base), _asserts(head)

    if len(after) < len(before):
        changes.append(
            EditedTest(test, "assert_removed", f"{len(before)} assertions -> {len(after)}")
        )

    if added := sorted(_skips(head) - _skips(base)):
        changes.append(EditedTest(test, "skip_added", ", ".join(added)))

    ops_before, ops_after = _operators(before), _operators(after)
    looser = ops_after["In"] + ops_after["approx"] > ops_before["In"] + ops_before["approx"]

    if ops_after["Eq"] < ops_before["Eq"] and looser:
        changes.append(EditedTest(test, "assertion_loosened", "== -> in or approx"))

    tol_before, tol_after = _tolerances(before), _tolerances(after)

    if tol_before and tol_after and max(tol_after) > max(tol_before):
        changes.append(
            EditedTest(
                test,
                "assertion_loosened",
                f"approx tolerance {max(tol_before):g} -> {max(tol_after):g}",
            )
        )

    gone = _constants(before) - _constants(after)
    new = _constants(after) - _constants(before)

    if gone and new:
        changes.append(
            EditedTest(
                test,
                "expected_changed",
                f"{', '.join(sorted(gone))} -> {', '.join(sorted(new))}",
            )
        )

    return changes


# ----------------------- #
# Running tests and checks.


def _reading(exit_code: int | None, output: str, node: str) -> Reading:
    """One test's reading from one run over several ids."""

    if exit_code is None:
        return "error"

    file = node.split("::", 1)[0]

    if file in collection_errors(output):
        return "error"

    def named(ids: Iterable[str]) -> bool:
        return any(one == node or one.startswith(f"{node}[") for one in ids)

    red = failing_ids(output)

    if named(red):
        return "fail"

    # Exit 1 is "some test failed": an id it did not name may have passed.
    # Only an id the summary names as passed ran green: a skipped, xfailed
    # or deselected test exits the same and proved nothing. Any other code
    # (interrupted, usage error, nothing collected) read nothing.
    if exit_code == 0 or (exit_code == 1 and red):
        return "pass" if named(_PASSED.findall(output)) else "not_run"

    return "error"


def _check_reading(exit_code: int | None, output: str) -> Reading:
    if exit_code is None or selected_nothing(exit_code, output):
        return "error"

    return "pass" if exit_code == 0 else "fail"


def _status(base: Reading, head: Reading) -> Status:
    """One link's status from its two readings (S-0122/D-5)."""

    if head == "not_run":
        return "not_run"

    if head == "pass":
        if base in ("fail", "error", "absent"):
            return "proved"

        return "held" if base == "pass" else "not_run"

    return "broken" if base == "pass" else "failing"


def _worst(statuses: Iterable[Status]) -> Status:
    present = set(statuses)

    if not present:
        return "unlinked"

    return next(status for status in _STATUS_ORDER if status in present)


def _timeout(ctx: GateContext) -> float:
    """The suite gate's own timeout, so a package run is bounded as the
    battery's runs are."""

    for gate in ctx.manifest.resolved_gates():
        if gate.builtin == "suite" and gate.timeout:
            return gate.timeout

    return DEFAULT_TIMEOUT


def _log_name(*parts: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", "-".join(parts)).strip("_") + ".log"


# ----------------------- #
# Rows.


def package_rows(task: Task | None, changed: Iterable[str]) -> list[tuple[InheritedDecision, bool]]:
    """The rows the package judges, each with whether the unit owns it
    (S-0122/D-5): a contract that names the rows it delivers owns those alone;
    otherwise a row owns when its paths meet the contract's scope (an
    unconstrained scope owns every row with paths). A LOCKED row the unit does
    not own joins as a constraint when the diff touches its paths."""

    if task is None:
        return []

    scope = task.scope.allow
    delivers = set(task.delivers)
    paths = list(changed)
    rows: list[tuple[InheritedDecision, bool]] = []

    for row in task.decisions:
        if not row.paths:
            continue

        owned = row.id in delivers if delivers else not scope or overlap(row.paths, scope)

        if owned:
            rows.append((row, True))

        elif row.grade == "LOCKED" and any(matches(path, row.paths) for path in paths):
            rows.append((row, False))

    return rows


def _engine_paths(task: Task | None, specs: str) -> list[str]:
    """The engine's own files for the task, which no scope governs."""

    if task is None:
        return []

    globs = [f"{layout.TORVE_DIR}/tasks/{task.id}/**", f"{layout.TORVE_DIR}/execution/**"]

    if task.spec:
        globs.append(f"{specs.rstrip('/')}/{task.spec}/execution/**")

    return globs


# ----------------------- #
# Flags.


def _harness_changed(ctx: GateContext, path: str) -> bool:
    """Whether a configuration file's test-runner section changed: any edit
    to `pytest.ini`, and the pytest tables of `pyproject.toml`, `setup.cfg`
    and `tox.ini` compared on both sides."""

    name = PurePosixPath(path).name

    if name in _HARNESS_NAMES or name.endswith(".pth"):
        return True

    if name not in ("pyproject.toml", "setup.cfg", "tox.ini"):
        return False

    head = _read(ctx.root / path)
    base = _base_source(ctx, path)

    return _pytest_section(name, base) != _pytest_section(name, head)


def _pytest_section(name: str, text: str | None) -> object:
    if text is None:
        return None

    if name == "pyproject.toml":
        try:
            tool = tomllib.loads(text).get("tool", {})

        except tomllib.TOMLDecodeError:
            return text

        return {key: value for key, value in tool.items() if key.startswith("pytest")}

    parser = configparser.ConfigParser(interpolation=None)

    try:
        parser.read_string(text)

    except configparser.Error:
        return text

    section = "tool:pytest" if name == "setup.cfg" else "pytest"

    return dict(parser[section]) if parser.has_section(section) else None


def flags_for(ctx: GateContext, stats: list[FileStat]) -> list[Flag]:
    """The greps (S-0122/the-evidence-package): added text that addresses a
    reviewer or a model, added source that can tell it runs under test, and
    touches to the test harness. A kind past its cap is counted, not listed."""

    flags: list[Flag] = []
    kinds = {stat.path: stat.kind for stat in stats}

    for path, lines in added_lines(ctx.patch).items():
        if path not in kinds:
            continue  # the engine's own files

        for number, text in lines:
            if _SUSPICIOUS.search(text):
                flags.append(Flag("suspicious_text", path, number, text.strip()[:200]))

            if kinds.get(path) == "src" and (_UNDER_TEST.search(text) or _ARGV_PYTEST.search(text)):
                flags.append(Flag("special_casing", path, number, text.strip()[:200]))

    for entry in ctx.diff:
        if entry.path in kinds and _harness_changed(ctx, entry.path):
            flags.append(Flag("harness_touch", entry.path, None, f"{entry.status} {entry.path}"))

    capped: list[Flag] = []

    for kind in ("suspicious_text", "special_casing", "harness_touch"):
        same = [flag for flag in flags if flag.kind == kind]
        capped += same[:FLAG_CAP]

        if len(same) > FLAG_CAP:
            capped.append(Flag(same[0].kind, "", None, f"{len(same) - FLAG_CAP} more not listed"))

    return capped


# ----------------------- #
# Sources on both sides.


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")

    except (OSError, UnicodeDecodeError):
        return None


def _base_source(ctx: GateContext, path: str) -> str | None:
    """The file as the merge base holds it, read on the host from git's
    object store; None when the base lacks it."""

    if ctx.merge_base is None:
        return None

    try:
        return git(ctx.root, "show", f"{ctx.merge_base}:{path}")

    except GitError:
        return None


# ....................... #


@dataclass
class _Tests:
    """The changed and added test functions of a diff, and the edits to the
    existing ones."""

    ids: list[str] = field(default_factory=list)
    added: set[str] = field(default_factory=set)
    sources: dict[str, str] = field(default_factory=dict)
    changes: list[EditedTest] = field(default_factory=list)


def _changed_tests(ctx: GateContext, stats: list[FileStat]) -> _Tests:
    found = _Tests()
    tests = {stat.path for stat in stats if stat.kind == "test"}

    for entry in ctx.diff:
        if entry.path not in tests or not entry.path.endswith(".py"):
            continue

        if PurePosixPath(entry.path).name == "conftest.py":
            continue

        before_text = _base_source(ctx, entry.old_path or entry.path)
        after_text = None if entry.status == "D" else _read(ctx.root / entry.path)
        before = functions_of(before_text) if before_text is not None else {}
        after = functions_of(after_text) if after_text is not None else {}

        for name in before:
            if name not in after:
                found.changes.append(
                    EditedTest(f"{entry.old_path or entry.path}::{name}", "test_deleted", "")
                )

        for name, node in after.items():
            node_id = f"{entry.path}::{name}"
            previous = before.get(name)

            if previous is not None and _same(previous, node):
                continue

            found.ids.append(node_id)
            found.sources[node_id] = _function_source(cast(str, after_text), node)

            if previous is None:
                found.added.add(node_id)
            else:
                found.changes += edit_of(node_id, previous, node)

    return found


def _overlay_tests(ctx: GateContext, stats: list[FileStat], base_dir: Path) -> set[str]:
    """Copy the candidate's changed test files over the extracted base tree,
    conftest and harness files excepted (S-0122/D-3): the base keeps its own.
    Returns the files that could not be placed — a symlink, or a file the
    host cannot read — whose tests then read `absent` on the base."""

    missing: set[str] = set()

    for stat in stats:
        if stat.kind != "test" or PurePosixPath(stat.path).name in _HARNESS_NAMES:
            continue

        source = ctx.root / stat.path

        if not source.exists() and not source.is_symlink():
            continue  # deleted at the head: the base keeps its own copy

        if source.is_symlink() or not source.is_file():
            missing.add(stat.path)
            continue

        try:
            text = source.read_bytes()

        except OSError:
            missing.add(stat.path)
            continue

        target = base_dir / stat.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text)

    return missing


def _changed_paths(ctx: GateContext) -> list[str]:
    """Every path the change touched, a rename's old path among them, less
    the engine's own files: what the rows and the fence are judged over."""

    engine = _engine_paths(ctx.task, ctx.specs)
    touched = [*ctx.changed_paths, *(e.old_path for e in ctx.diff if e.old_path)]

    return [path for path in dict.fromkeys(touched) if not matches(path, engine)]


def evidence_budget(ctx: GateContext) -> float:
    """The longest `build_evidence` over *ctx* can take, in seconds: an upper
    bound, as the battery's own is, so a lease that waits out the pass also
    waits out its package. The head and base test runs and the base's
    extraction, each row check on both sides, each acceptance command."""

    checks = sum(1 for row, _ in package_rows(ctx.task, _changed_paths(ctx)) if row.check)
    acceptance = len(ctx.task.acceptance) if ctx.task is not None else 0

    return _timeout(ctx) * (3 + 2 * checks + acceptance)


# ....................... #


def _run(ctx: GateContext, command: str, timeout: float) -> tuple[int | None, str]:
    result = run_command(command, ctx.root, timeout, retry_flaky=False, execute=ctx.execute)
    return result.exit_code, result.output


def build_evidence(
    ctx: GateContext, results: Iterable[GateResult], *, candidate: str
) -> EvidencePackage:
    """The package for *candidate* over the battery's own context: the same
    checkout, merge base and executor the gates ran with, so every reading
    here was taken where the gates took theirs (S-0122/D-2)."""

    results = list(results)
    engine = _engine_paths(ctx.task, ctx.specs)
    stats = [
        stat
        for stat in diff_stats(ctx.patch, ctx.manifest.tests.patterns)
        if not matches(stat.path, engine)
    ]
    changed = _changed_paths(ctx)
    package = EvidencePackage(candidate=candidate, base=ctx.merge_base, head=ctx.head_sha)
    package.files = stats
    timeout = _timeout(ctx)

    # The fence and the scope: what the change wrote outside its files and
    # tests is advisory (S-0122/D-12); what it wrote inside the fence the
    # scope gate already convicts, and is listed here so the critic sees it.
    if ctx.task is not None:
        allow, deny = ctx.task.scope.allow, ctx.task.scope.deny

        if allow:
            package.outside_files = [path for path in changed if not matches(path, allow)]

        if deny:
            package.fence_breaches = [path for path in changed if matches(path, deny)]

    for result in results:
        convicted, flaky = settled_ids(result.output) if result.name == "suite" else ([], [])
        package.gates.append(
            GateReading(
                name=result.name,
                outcome=result.outcome,
                state=result.state,
                duration_s=result.duration_s,
                convicted=tuple(convicted),
                inherited=tuple(result.inherited_failures),
                flaky=tuple(flaky),
            )
        )

        if result.output:
            package.logs[_log_name("gate", result.name)] = result.output

    tests = _changed_tests(ctx, stats)
    package.test_changes = tests.changes
    rows = package_rows(ctx.task, changed)
    checked = [(row, owned) for row, owned in rows if row.check]

    head: dict[str, Reading] = dict.fromkeys(tests.ids, "not_run")
    base: dict[str, Reading] = dict.fromkeys(tests.ids, "not_run")
    check_head: dict[str, Reading] = {row.id: "not_run" for row, _ in checked}
    check_base: dict[str, Reading] = {row.id: "not_run" for row, _ in checked}
    runner = ctx.manifest.tests.launcher(ctx.root).strip()

    if tests.ids and runner:
        command = f"{runner} {_REPORT_CHARS} {' '.join(shlex.quote(node) for node in tests.ids)}"
        code, output = _run(ctx, command, timeout)
        package.logs["tests-head.log"] = f"$ {command}  [exit {code}]\n{output}"
        head = {node: _reading(code, output, node) for node in tests.ids}

    for row, _ in checked:
        code, output = _run(ctx, cast(str, row.check), timeout)
        package.logs[_log_name("check", row.id, "head")] = f"$ {row.check}  [exit {code}]\n{output}"
        check_head[row.id] = _check_reading(code, output)

    if (tests.ids and runner) or checked:
        base_dir, problem = extract_base(ctx, timeout)

        if base_dir is None:
            package.notes.append(f"nothing was run on the base: {problem}")

        else:
            try:
                # The candidate's tests over the base's source, as `red-on-base`
                # read them (S-0081): a test green there would have passed
                # before a line was written. The base's own harness stays.
                missing = _overlay_tests(ctx, stats, base_dir)
                base.update(
                    {node: "absent" for node in tests.ids if node.split("::")[0] in missing}
                )
                ran = [node for node in tests.ids if base[node] == "not_run"]
                where = shlex.quote(str(base_dir.relative_to(ctx.root.resolve())))

                if ran and runner:
                    ids = " ".join(shlex.quote(node) for node in ran)
                    command = f"cd {where} && {runner} {_REPORT_CHARS} {ids}"
                    code, output = _run(ctx, command, timeout)
                    package.logs["tests-base.log"] = f"$ {command}  [exit {code}]\n{output}"
                    base.update({node: _reading(code, output, node) for node in ran})

                for row, _ in checked:
                    command = f"cd {where} && {row.check}"
                    code, output = _run(ctx, command, timeout)
                    package.logs[_log_name("check", row.id, "base")] = (
                        f"$ {command}  [exit {code}]\n{output}"
                    )
                    check_base[row.id] = _check_reading(code, output)

            finally:
                shutil.rmtree(base_dir, ignore_errors=True)

    if tests.ids and not runner:
        package.notes.append("the manifest names no `tests.run`, so no test was run")

    links = {
        row.id: [node for node in tests.ids if _row_pattern(row.id).search(tests.sources[node])]
        for row, _ in rows
    }

    package.tests = [
        CheckedTest(
            id=node,
            added=node in tests.added,
            base=base[node],
            head=head[node],
            status=_status(base[node], head[node]),
            rows=tuple(row.id for row, _ in rows if node in links[row.id]),
        )
        for node in tests.ids
    ]

    by_id = {run.id: run for run in package.tests}

    for row, owned in rows:
        statuses = [by_id[node].status for node in links[row.id]]
        checks = list(links[row.id])

        if row.check:
            statuses.append(_status(check_base[row.id], check_head[row.id]))
            checks.append(f"check: {row.check}")

        package.rows.append(
            RowCheck(
                row=row.id,
                grade=row.grade,
                owned=owned,
                status=_worst(statuses),
                checks=tuple(checks),
            )
        )

    package.acceptance = _acceptance(ctx, results, timeout, package.logs)
    package.flags = flags_for(ctx, stats)
    package.author_claim = _author_claims(ctx.log_text)

    return package


# ....................... #


def _acceptance(
    ctx: GateContext, results: list[GateResult], timeout: float, logs: dict[str, str]
) -> list[CommandRun]:
    """The contract's acceptance commands, each run once here unless the
    battery's acceptance gate passed: that gate runs every one of them and
    stops only at a red one. Read from the gate's outcome, never its output,
    which a candidate's test can print a forged `$ command` header into."""

    if ctx.task is None:
        return []

    gates = {gate.name for gate in ctx.manifest.gates if gate.builtin == "acceptance"}

    if any(result.name in gates and result.outcome in ("pass", "flaky") for result in results):
        return []

    runs: list[CommandRun] = []

    for index, command in enumerate(ctx.task.acceptance, start=1):
        code, output = _run(ctx, command, timeout)
        logs[_log_name("acceptance", str(index))] = f"$ {command}  [exit {code}]\n{output}"
        runs.append(CommandRun(command=command, exit_code=code, tail=clip(output, TAIL_BYTES)))

    return runs


def _author_claims(log_text: str | None) -> list[AuthorClaim]:
    """The executor's `torve abort` records (S-0122/D-14): its one channel to
    the judge, shown as the author's claim and never as a measurement."""

    if not log_text:
        return []

    try:
        loaded: Any = yaml.safe_load(log_text)

    except yaml.YAMLError:
        return []

    if not isinstance(loaded, dict):
        return []

    entries: Any = cast("dict[str, Any]", loaded).get("entries") or []
    claims: list[AuthorClaim] = []

    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue

        item = cast("dict[str, Any]", entry)

        if item.get("action") != "halted":
            continue

        claims.append(
            AuthorClaim(
                decision=str(item.get("decision") or ""),
                kind=str(item.get("kind") or ""),
                claim=str(item.get("claim") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    return claims


# ----------------------- #
# Rendering, under the budget.


def _document(pkg: EvidencePackage, dropped: dict[str, int] | None) -> dict[str, Any]:
    """The rendered shape. *dropped* None is a package rendered whole; a
    mapping, even an empty one, is a package cut to fit, and says what went."""

    totals: dict[str, dict[str, int]] = {}

    for stat in pkg.files:
        total = totals.setdefault(stat.kind, {"files": 0, "added": 0, "removed": 0})
        total["files"] += 1
        total["added"] += stat.added
        total["removed"] += stat.removed

    document: dict[str, Any] = {
        "schema": SCHEMA,
        "candidate": pkg.candidate,
        "base": pkg.base,
        "head": pkg.head,
        "diff": {"totals": totals, "files": [_asdict(stat) for stat in pkg.files]},
        "outside_files": pkg.outside_files,
        "fence_breaches": pkg.fence_breaches,
        "gates": [_asdict(gate) for gate in pkg.gates],
        "tests": [_asdict(run) for run in pkg.tests],
        "rows": [_asdict(row) for row in pkg.rows],
        "acceptance": [_asdict(run) for run in pkg.acceptance],
        "test_changes": [_asdict(change) for change in pkg.test_changes],
        "flags": [_asdict(flag) for flag in pkg.flags],
        "author_claim": [_asdict(claim) for claim in pkg.author_claim],
        "notes": pkg.notes,
        "truncated": dropped is not None,
    }

    if dropped:
        document["dropped"] = dropped

    return document


def _dump(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


# The lists the last resort halves, longest first.
_HALVED = (
    "tests",
    "test_changes",
    "flags",
    "files",
    "outside_files",
    "fence_breaches",
    "gates",
    "rows",
    "author_claim",
    "acceptance",
    "notes",
)


def render_evidence(pkg: EvidencePackage, max_bytes: int = MAX_BYTES) -> str:
    """The package as `torve.evidence/v1` YAML, at most *max_bytes* (S-0122/D-5).

    Over budget, `held` test runs are dropped first — a check green on both
    sides says least — then the rows the unit does not own, then output
    tails are clipped, then the longest list is halved until it fits. What
    was dropped is counted under `dropped`, and `truncated` says so."""

    text = _dump(_document(pkg, None))

    if len(text.encode("utf-8")) <= max_bytes:
        return text

    dropped: dict[str, int] = {}

    def fits(candidate: EvidencePackage) -> str | None:
        rendered = _dump(_document(candidate, dropped))
        return rendered if len(rendered.encode("utf-8")) <= max_bytes else None

    if held := sum(1 for run in pkg.tests if run.status == "held"):
        pkg = replace(pkg, tests=[run for run in pkg.tests if run.status != "held"])
        dropped["held_tests"] = held

        if (rendered := fits(pkg)) is not None:
            return rendered

    if foreign := sum(1 for row in pkg.rows if not row.owned):
        pkg = replace(pkg, rows=[row for row in pkg.rows if row.owned])
        dropped["rows_not_owned"] = foreign

        if (rendered := fits(pkg)) is not None:
            return rendered

    pkg = replace(
        pkg, acceptance=[replace(run, tail=clip(run.tail, 400)) for run in pkg.acceptance]
    )

    while (rendered := fits(pkg)) is None:
        name = max(_HALVED, key=lambda one: len(getattr(pkg, one)))
        items = getattr(pkg, name)

        if not items:
            # Nothing left to halve: the fixed fields alone are over budget.
            return (
                _dump(_document(pkg, dropped)).encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
            )

        keep = len(items) // 2
        dropped[name] = dropped.get(name, 0) + len(items) - keep
        pkg = replace(pkg, **{name: items[:keep]})

    return rendered
