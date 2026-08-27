"""`source-layout` — the module naming rule of RFC 0015 (D-15.5), over the
changed Python files under `src/` (input `diff`). A path rule, not an import
rule, so it lives beside the other file-level checks rather than in
`layering`.

RFC 0014's separator form was script-checked here until A-44 retired that
half: width, placement and labelling are house style a reviewer reads at a
glance, and a linter for them costs more than the drift it caught. Whether a
module should split, and whether a separator helps, stay review (D-14.10).
"""

from __future__ import annotations

from pathlib import Path

from torve.config.manifest import Gate
from torve.gates.context import GateContext
from torve.gates.contract import BuiltinOutcome

# ----------------------- #

# Names that admit anything accumulate everything (D-15.5).
FORBIDDEN_MODULE_NAMES = frozenset({"models.py", "utils.py", "helpers.py", "common.py", "base.py"})


# ....................... #


def check_source_layout(gate: Gate, ctx: GateContext) -> BuiltinOutcome:
    candidates = sorted(
        {
            p
            for p in [*ctx.changed_paths, *ctx.untracked]
            if p.endswith(".py") and p.startswith("src/")
        }
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

    if problems:
        return BuiltinOutcome("fail", "\n".join(problems))

    return BuiltinOutcome("pass", f"{checked} changed source file(s) are named for what they hold")
