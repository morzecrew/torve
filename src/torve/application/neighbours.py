"""A phase's neighbours (S-0104/D-1): the literal paths a change most often
reaches beyond its own scope, derived at mint and at refresh and carried as
`scope.contingent`. One hop only, and only what the tree already shows: the
importers of the files the phase's globs match, the first-party modules those
files import, the tests paired with them at any depth under `tests/`, their
packages' `__init__`, and whatever the document's other phases' scopes match.

D-11: the import graph reads Python. A repository with no Python still gets
the rules that are not language-bound — the pairing, the document union and,
once phase 2 adds it, the gate manifest's `scope.derived` map.
"""

from __future__ import annotations

import ast
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from torve.application.intake import paired_test_files, tree_files
from torve.base.globs import matches

if TYPE_CHECKING:
    from torve.domain.spec import Corpus, Document

# ----------------------- #


def _matched(path: Path, globs: list[str]) -> bool:
    """The intake's rule, verbatim (S-0105/D-4): one reading of a scope glob
    for both, so a neighbour is the same set of paths wherever it is asked."""

    return matches(path.as_posix(), globs)


def _module_names(files: list[Path]) -> dict[str, Path]:
    """Dotted module name -> tree-relative path, for every `.py` file. A file
    whose directories are packages takes the dotted name from the outermost
    such directory; a bare file takes its own stem."""

    packages = {p.parent for p in files if p.name == "__init__.py"}
    names: dict[str, Path] = {}

    for path in files:
        if path.suffix != ".py":
            continue

        parts = list(path.parts)

        if path.name == "__init__.py":
            if len(parts) > 1:
                names[".".join(parts[:-1])] = path

            continue

        cut = len(parts) - 1

        while cut > 0 and Path(*parts[:cut]) in packages:
            cut -= 1

        names[".".join(parts[cut:]).removesuffix(".py")] = path

    return names


def _package_of(path: Path, dotted: dict[Path, str]) -> str:
    here = dotted.get(path, "")

    return here if path.name == "__init__.py" else here.rpartition(".")[0]


def _imports(path: Path, tree: Path, dotted: dict[Path, str]) -> set[str]:
    """The dotted names *path* imports, best effort: `import a.b`, and from
    `from a.b import c` both `a.b` and `a.b.c`, since either may resolve.
    *path* is tree-relative; it is read under *tree*."""

    try:
        parsed = ast.parse((tree / path).read_text(encoding="utf-8"))

    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()

    found: set[str] = set()

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""

            if node.level:
                package = _package_of(path, dotted)
                parts = package.split(".") if package else []
                keep = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*keep, base] if base else keep)

            found.add(base)
            found.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )

    return found


def _resolve(dotted: str, names: dict[str, Path]) -> Path | None:
    """The file a dotted name names, longest known module first; None when
    nothing in the tree answers to it (a third-party or stdlib import)."""

    parts = dotted.split(".") if dotted else []

    while parts:
        candidate = ".".join(parts)

        if candidate in names:
            return names[candidate]

        parts.pop()

    return None


def _inits(path: Path, known: set[Path]) -> list[Path]:
    """The `__init__.py` of every package *path* sits in — the module's own
    package and each above it — among the files the tree actually holds."""

    return [init for parent in path.parents if (init := parent / "__init__.py") in known]


# ....................... #


def contingent_paths(
    tree: Path,
    scope: list[str],
    *,
    document: Document | None = None,
    phase: int | None = None,
    derived: dict[str, list[str]] | None = None,
    pair: list[str] | None = None,
) -> list[str]:
    """The literal paths a phase's change is likely to reach beyond its scope
    (S-0104/D-1), sorted. *tree* is the repository root the globs are read
    over; *document* and *phase* contribute the document union — every path the
    document's other phases' scopes match. *derived* is the manifest's
    `scope.derived` map (S-0104/D-5), applied both ways: a rider joins when its
    source is matched, a source when one of its riders is. Paths the phase's own
    globs already match are never contingent: they are the scope, not beyond
    it."""

    files = tree_files(tree)
    own = {p.as_posix() for p in files if _matched(p, scope)}
    found: set[str] = set()

    matched = [p for p in files if p.as_posix() in own]

    names = _module_names(files)

    if names:
        dotted = {path: name for name, path in names.items()}
        known = set(files)
        imported = {p: _imports(p, tree, dotted) for p in files if p.suffix == ".py"}
        matched_py = {p for p in matched if p.suffix == ".py"}

        for path in (p for p in files if p.suffix == ".py" and p not in matched_py):
            if any(_resolve(name, names) in matched_py for name in imported[path]):
                found.add(path.as_posix())

        for path in matched_py:
            for name in imported[path]:
                target = _resolve(name, names)

                if target is not None:
                    found.add(target.as_posix())

            found.update(init.as_posix() for init in _inits(path, known))

    # S-0106/D-7: the pairing reads the manifest's `pair` templates, so a Go
    # module brings the `_test.go` beside it, not only a `tests/` file.
    all_names = [p.as_posix() for p in files]

    for path in matched:
        found.update(paired_test_files(path.as_posix(), all_names, pair))

    # S-0104/D-5, D-11: the derived map is not language-bound, so it applies
    # even in a repository with no Python — a golden rides with its producer.
    if derived:
        for source, riders in derived.items():
            if source in own or any(rider in own for rider in riders):
                found.update(riders)
                found.add(source)

    if document is not None:
        for entry in document.phasing:
            if entry.phase == phase:
                continue

            found.update(p.as_posix() for p in files if _matched(p, entry.scope))

    return sorted(found - own)


# ....................... #
# S-0119/D-12: a test file is run by an acceptance command only when the
# command launches the tests and names the file, a directory above it, or no
# path at all — mentioning it, as a type checker or a linter does, runs
# nothing.

# The pytest options that take the next token as their value, so it is not
# read as a path argument.
_VALUE_OPTIONS = frozenset(
    {
        "-k",
        "-m",
        "-p",
        "-c",
        "-o",
        "-W",
        "-n",
        "--maxfail",
        "--rootdir",
        "--junitxml",
        "--cov",
        "--cov-report",
        "--ignore",
        "--deselect",
        "--tb",
        "--confcutdir",
    }
)


def _argv(command: str) -> list[str]:
    try:
        return shlex.split(command)

    except ValueError:
        return command.split()


def _launch_at(argv: list[str], launcher: str) -> int | None:
    """Where the command stops being the launcher and starts being the tests'
    arguments: after the manifest's `tests.run`, `-m pytest`, or `pytest` —
    in that order, so a configured launcher wins over the heuristic."""

    wanted = _argv(launcher)

    if wanted:
        for start in range(len(argv) - len(wanted) + 1):
            if argv[start : start + len(wanted)] == wanted:
                return start + len(wanted)

    for start in range(len(argv) - 1):
        if argv[start] == "-m" and argv[start + 1] == "pytest":
            return start + 2

    for start, token in enumerate(argv):
        if token == "pytest":
            return start + 1

    return None


def acceptance_runs(command: str, test: str, launcher: str) -> bool:
    """Whether *command* runs the test file at *test* (S-0119/D-12). It must
    launch the tests — as `pytest`, `-m pytest`, or the manifest's
    `tests.run` in *launcher* — and either name *test*, name a directory above
    it, or name no path at all. `mypy` or `ruff` over the path mentions it and
    runs nothing."""

    argv = _argv(command)
    start = _launch_at(argv, launcher)

    if start is None:
        return False

    test = test.replace("\\", "/").lstrip("./")
    named_a_path = False
    skip = False

    for token in argv[start:]:
        if skip:
            skip = False
            continue

        if token.startswith("-"):
            skip = token in _VALUE_OPTIONS
            continue

        named_a_path = True
        candidate = token.split("::", 1)[0].replace("\\", "/").rstrip("/").lstrip("./")

        if not candidate or test == candidate or test.startswith(f"{candidate}/"):
            return True

    return not named_a_path


# ....................... #
# S-0119/D-11: the callers a phase's change reaches outside its scope, while
# the phasing is still a draft — the importer no phase owns, the phases that
# own the rest, and the tests nothing runs.


def _test_config(root: Path) -> tuple[list[str], str]:
    """The manifest's `tests.patterns` and `tests.run`, its defaults when the
    repository carries no manifest (the same reading the intake does)."""

    from torve.config import layout
    from torve.config.manifest import DEFAULT_TEST_PATTERNS, DEFAULT_TEST_RUN, load_manifest

    path = layout.gates_file(root)

    if not path.is_file():
        return list(DEFAULT_TEST_PATTERNS), DEFAULT_TEST_RUN

    try:
        tests = load_manifest(path).tests

    except (OSError, ValueError):
        return list(DEFAULT_TEST_PATTERNS), DEFAULT_TEST_RUN

    return list(tests.patterns), tests.run


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _shown(paths: set[str]) -> str:
    """At most ten sorted paths, the count carrying the rest (S-0119/D-11)."""

    return ", ".join(sorted(paths)[:10])


def phase_reach(corpus: Corpus, root: Path) -> list[str]:
    """The warning lines of S-0119/D-11: for each phase of a document not
    archived, not superseded and with implementation `none`, the non-test
    Python files outside its scope that import a file its scope matches —
    one line for those no phase owns and one per other phase that owns some —
    and the test files importing such a file that no acceptance command of the
    document runs. Each line names the document, the phase, the count and at
    most ten sorted paths."""

    files = tree_files(root)
    names = _module_names(files)

    if not names:
        return []

    patterns, launcher = _test_config(root)
    dotted = {path: name for name, path in names.items()}
    imported = {path: _imports(path, root, dotted) for path in files if path.suffix == ".py"}
    lines: list[str] = []

    def is_test(path: Path) -> bool:
        return _matched(path, patterns)

    for doc in corpus.documents:
        if doc.archived or doc.superseded_by or doc.implementation != "none":
            continue

        acceptance = [command for phase in doc.phasing for command in phase.acceptance]

        for phase in doc.phasing:
            matched = [
                path for path in files if path.suffix == ".py" and _matched(path, phase.scope)
            ]

            if not matched:
                continue

            importers = [
                path
                for path in files
                if path.suffix == ".py"
                and path not in matched
                and any(_resolve(name, names) in matched for name in imported[path])
            ]

            unowned: set[str] = set()
            owned: dict[int, set[str]] = {}
            unrun: set[str] = set()

            for path in importers:
                if is_test(path):
                    if not any(
                        acceptance_runs(command, path.as_posix(), launcher)
                        for command in acceptance
                    ):
                        unrun.add(path.as_posix())

                    continue

                owners = [
                    other
                    for other in doc.phasing
                    if other.phase != phase.phase and _matched(path, other.scope)
                ]

                if not owners:
                    unowned.add(path.as_posix())

                for other in owners:
                    owned.setdefault(other.phase, set()).add(path.as_posix())

            where = f"{doc.id}: phase {phase.phase} ({phase.title})"

            if unowned:
                lines.append(
                    f"{where} reaches {_count(len(unowned), 'file')} no phase owns: "
                    f"{_shown(unowned)}"
                )

            for other in doc.phasing:
                if other.phase == phase.phase or other.phase not in owned:
                    continue

                reached = owned[other.phase]
                lines.append(
                    f"{where} reaches {_count(len(reached), 'file')} phase {other.phase} owns: "
                    f"{_shown(reached)}"
                )

            if unrun:
                lines.append(
                    f"{where} leaves {_count(len(unrun), 'test')} of its files unrun by every "
                    f"acceptance: {_shown(unrun)}"
                )

    return lines
