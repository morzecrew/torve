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
from pathlib import Path
from typing import TYPE_CHECKING

from torve.application.intake import NOT_THE_TREE, paired_test_files
from torve.base.globs import matches

if TYPE_CHECKING:
    from torve.domain.spec import Document

# ----------------------- #


def _tree_files(tree: Path) -> list[Path]:
    """Every file under *tree*, as tree-relative paths, minus the directories
    that are not the tree under judgment (the same set the intake lint skips)."""

    return sorted(
        p.relative_to(tree)
        for p in tree.rglob("*")
        if p.is_file() and NOT_THE_TREE.isdisjoint(p.parts)
    )


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

    files = _tree_files(tree)
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
