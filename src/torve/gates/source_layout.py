"""`source-layout` — the checkable half of RFC 0014: separator form, the
post-import dash, the dash ceiling, dash labels and label-free dots, over the
changed Python files under `src/` (input `diff`). RFC 0015 adds the module
naming rule here (D-15.5): a path rule, not an import rule, so it lives with
the other file-level layout checks rather than in `layering`.

Width, placement and labelling are script-checked; whether a label says
something useful, whether a module should split, and whether a dot helps are
review (D-14.10) — checking those would produce a linter nobody trusts.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from torve.config.manifest import Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

DASH = "# ----------------------- #"
DOT = "# ....................... #"
# A comment made of nothing but a dash or dot run is claiming to be a
# separator; four repeats is the floor so a bare `# ...` placeholder is not.
CANDIDATE = re.compile(r"^#\s*(-{4,}|\.{4,})\s*#?$")
# Names that admit anything accumulate everything (D-15.5).
FORBIDDEN_MODULE_NAMES = frozenset(
    {"models.py", "utils.py", "helpers.py", "common.py", "base.py"}
)


def _top_level_lines(tree: ast.Module) -> tuple[int, int]:
    """(last import line, first definition line or 0) at module level; a
    trailing `if TYPE_CHECKING:` block counts into the import preamble."""
    last_import = 0
    first_definition = 0
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom) or (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            last_import = max(last_import, node.end_lineno or node.lineno)
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and not first_definition
        ):
            first_definition = min(
                (d.lineno for d in node.decorator_list), default=node.lineno
            )
    return last_import, first_definition


def _next_content(lines: list[str], index: int) -> str:
    for line in lines[index + 1:]:
        if line.strip():
            return line.strip()
    return ""


def _check_file(rel: str, text: str) -> list[str]:
    lines = text.splitlines()
    problems: list[str] = []
    dash_lines: list[int] = []
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not CANDIDATE.match(stripped):
            continue
        if stripped not in (DASH, DOT):
            problems.append(
                f"{rel}:{number}: separator is not the 27-character form — "
                f"expected {DASH!r} or the dotted twin, at any width but this one"
            )
            continue
        following = _next_content(lines, number - 1)
        if stripped == DASH:
            dash_lines.append(number)
            if len(dash_lines) > 1 and not following.startswith(("#", "__all__")):
                problems.append(
                    f"{rel}:{number}: a dash beyond the post-import one carries no label — "
                    "it says something changes without saying what"
                )
        elif following.startswith("#"):
            problems.append(
                f"{rel}:{number}: dot separator carries a label — dots separate peers, "
                "and the peer names itself on the next line"
            )

    if len(dash_lines) >= 3:
        problems.append(
            f"{rel}: {len(dash_lines)} structural dashes — three or more is a package, "
            "not a layout choice; split the module"
        )

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return problems  # not this gate's finding; acceptance owns broken code
    last_import, first_definition = _top_level_lines(tree)
    if last_import:
        first_dash = dash_lines[0] if dash_lines else 0
        if not first_dash or first_dash < last_import or (
            first_definition and first_dash > first_definition
        ):
            problems.append(
                f"{rel}: no post-import dash closing the preamble — every module with "
                f"imports gets exactly one, after line {last_import}"
            )
    return problems


def check_source_layout(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    candidates = sorted(
        {p for p in [*ctx.changed_paths, *ctx.untracked]
         if p.endswith(".py") and p.startswith("src/")}
    )
    problems: list[str] = []
    checked = 0
    for rel in candidates:
        target = Path(ctx.root) / rel
        if not target.is_file():
            continue  # deleted in this diff
        checked += 1
        if target.name in FORBIDDEN_MODULE_NAMES:
            problems.append(
                f"{rel}: module named {target.name!r} — a name that admits anything "
                "accumulates everything; name the module for what it holds"
            )
        problems += _check_file(rel, target.read_text(encoding="utf-8", errors="replace"))

    if problems:
        return BuiltinOutcome("fail", "\n".join(problems))
    return BuiltinOutcome("pass", f"{checked} changed source file(s) observe the layout")
