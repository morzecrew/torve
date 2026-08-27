"""`user-facing-text` — the audience rule of RFC 0011 §5a (D-11.11): a string
shown to whoever runs the command carries no corpus identifiers, because that
reader has no corpus and the reference rots invisibly on every amendment.

Scanned, over the changed Python files under the cli and gates packages
(input `diff`): every string constant except the surfaces addressed to
whoever edits the line — module and class docstrings, and docstrings of
private, nested and non-CLI functions. Public module-level function
docstrings in the cli package stay in scope because Typer renders them as
help text. Comments never enter the AST, so they are structurally exempt —
D-11.12: references belong there, and displaced ones move up into the module
docstring, not out of the file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from torve.config.manifest import Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

PREFIXES = ("src/torve/cli/", "src/torve/gates/")

# The sabotage module is scenario data by nature: its strings are log entries
# and diffs a gate under test must judge, decision identifiers included.
DATA_MODULES = ("src/torve/gates/sabotage.py",)

# Joined from fragments so no constant in this module matches the assembled
# pattern — the gate scans string values, its own included. Corpus RFC
# numbers are zero-padded to four digits; public standards ("RFC 3339") are
# cited unpadded and are not corpus identifiers.
IDENTIFIERS = re.compile(
    "|".join(
        (
            r"RFC 0[0-9]{3}",
            r"\bD-[A-Za-z0-9][A-Za-z0-9.]*",
            chr(0xA7),  # the section mark
            r"\brfcs" + "/",
        )
    )
)


# ....................... #


def _first_string(body: list[ast.stmt]) -> ast.Constant | None:
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value
    return None


# ....................... #


def _exempt_nodes(tree: ast.Module, cli: bool) -> set[int]:
    """The docstrings addressed to whoever edits the line, by node identity."""
    exempt: set[int] = set()
    module_doc = _first_string(tree.body)
    if module_doc is not None:
        exempt.add(id(module_doc))
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = _first_string(owner.body)
        if doc is None:
            continue
        rendered_as_help = (
            cli
            and not isinstance(owner, ast.ClassDef)
            and owner in tree.body
            and not owner.name.startswith("_")
        )
        if not rendered_as_help:
            exempt.add(id(doc))
    return exempt


# ....................... #


def _check_file(rel: str, text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []  # not this gate's finding; acceptance owns broken code
    exempt = _exempt_nodes(tree, cli=rel.startswith(PREFIXES[0]))
    problems: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in exempt
        ):
            found = IDENTIFIERS.search(node.value)
            if found is not None:
                problems.append(
                    f"{rel}:{node.lineno}: user-facing string cites {found.group(0)!r} — "
                    "whoever runs this has no corpus to resolve it; say what the "
                    "command does here, and move the reference into the module docstring"
                )
    return problems


# ....................... #


def check_user_facing_text(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    candidates = sorted(
        {
            p
            for p in [*ctx.changed_paths, *ctx.untracked]
            if p.endswith(".py") and p.startswith(PREFIXES) and p not in DATA_MODULES
        }
    )
    problems: list[str] = []
    checked = 0
    for rel in candidates:
        target = Path(ctx.root) / rel
        if not target.is_file():
            continue  # deleted in this diff
        checked += 1
        problems += _check_file(rel, target.read_text(encoding="utf-8", errors="replace"))

    if problems:
        return BuiltinOutcome("fail", "\n".join(problems))
    return BuiltinOutcome(
        "pass", f"{checked} changed file(s) keep internal identifiers out of user-facing text"
    )
