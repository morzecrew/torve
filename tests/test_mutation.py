from __future__ import annotations

import ast

from torve.gates import mutation

MODULE = "src/pkg/mod.py"
TEST = "tests/test_mod.py"

SOURCE = """\
def f(a, b):
    while a < b and b != 0:
        a = a + 1
    if a == b:
        return a
    return 0
"""

# The whole module is new, so every line is a changed line and every operator
# has a line to edit.
PATCH_ALL = (
    f"diff --git a/{MODULE} b/{MODULE}\n"
    f"--- /dev/null\n+++ b/{MODULE}\n"
    "@@ -0,0 +1,6 @@\n" + "".join(f"+{line}\n" for line in SOURCE.splitlines())
)

# One line changed, the rest context: the mutants may only land on line 3.
PATCH_PARTIAL = (
    f"diff --git a/{MODULE} b/{MODULE}\n"
    f"--- a/{MODULE}\n+++ b/{MODULE}\n"
    "@@ -1,6 +1,6 @@\n"
    " def f(a, b):\n"
    "     while a < b and b != 0:\n"
    "-        a = a + 1\n"
    "+        a = a + 2\n"
    "     if a == b:\n"
    "         return a\n"
    "     return 0\n"
)

# A changed source line beside a changed test file that would otherwise mutate.
PATCH_BOTH = (
    PATCH_PARTIAL + f"diff --git a/{TEST} b/{TEST}\n"
    f"--- /dev/null\n+++ b/{TEST}\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_f():\n"
    "+    assert 1 + 1 == 2\n"
)


def test_each_operator_produces_its_edit_on_a_fixture():
    found = mutation.mutants(PATCH_ALL, {MODULE: SOURCE})
    first = {}
    for mutant in found:
        first.setdefault(mutant.operator, mutant)

    assert set(first) == {
        "comparison",
        "boolean",
        "arithmetic",
        "constant",
        "condition",
        "return",
    }
    assert first["comparison"].edit == "a < b -> a <= b"
    assert first["boolean"].edit == "a < b and b != 0 -> a < b or b != 0"
    assert first["arithmetic"].edit == "a + 1 -> a - 1"
    assert first["constant"].edit == "0 -> 1"
    assert first["condition"].edit == "a < b and b != 0 -> not (a < b and b != 0)"
    assert first["return"].edit == "return a -> return None"


def test_no_operator_mutates_an_unchanged_line():
    found = mutation.mutants(PATCH_PARTIAL, {MODULE: SOURCE})

    assert found
    assert {mutant.line for mutant in found} == {3}
    assert {mutant.operator for mutant in found} == {"arithmetic", "constant"}


def test_no_operator_mutates_a_test_file():
    found = mutation.mutants(PATCH_BOTH, {MODULE: SOURCE, TEST: "def test_f():\n    pass\n"})

    assert found
    assert {mutant.path for mutant in found} == {MODULE}


def test_the_operator_sample_is_the_same_for_one_diff():
    first = mutation.mutants(PATCH_ALL, {MODULE: SOURCE}, budget=4)
    again = mutation.mutants(PATCH_ALL, {MODULE: SOURCE}, budget=4)

    assert len(first) == 4
    assert [m.edit for m in first] == [m.edit for m in again]
    assert [(m.path, m.line, m.operator) for m in first] == [
        (m.path, m.line, m.operator) for m in again
    ]


def test_an_operator_edit_lands_after_a_non_ascii_character():
    # The AST reports columns in bytes, the source is indexed by characters: a
    # non-ASCII character before the node must not shift the replaced span.
    source = 'def f(a):\n    s = "é"; return a + 1\n'
    patch = (
        f"diff --git a/{MODULE} b/{MODULE}\n"
        f"--- /dev/null\n+++ b/{MODULE}\n"
        "@@ -0,0 +1,2 @@\n" + "".join(f"+{line}\n" for line in source.splitlines())
    )

    found = mutation.mutants(patch, {MODULE: source})
    arithmetic = next(m for m in found if m.operator == "arithmetic")

    assert arithmetic.edit == "a + 1 -> a - 1"
    assert arithmetic.source == 'def f(a):\n    s = "é"; return a - 1\n'


def test_removing_not_keeps_the_operand_one_node():
    # Without its parentheses the operand would rebind to the `and` around it,
    # a second edit the operator does not name.
    source = "def f(x, y, z):\n    return z and not (x or y)\n"
    patch = (
        f"diff --git a/{MODULE} b/{MODULE}\n"
        f"--- /dev/null\n+++ b/{MODULE}\n"
        "@@ -0,0 +1,2 @@\n" + "".join(f"+{line}\n" for line in source.splitlines())
    )

    found = mutation.mutants(patch, {MODULE: source})
    removed = next(m for m in found if m.edit.startswith("not (x or y) -> "))

    assert ast.dump(ast.parse(removed.source)) == ast.dump(
        ast.parse("def f(x, y, z):\n    return z and (x or y)\n")
    )


def test_a_zero_budget_samples_nothing():
    assert mutation.mutants(PATCH_ALL, {MODULE: SOURCE}, budget=0) == []
