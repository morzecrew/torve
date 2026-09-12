"""Sabotage for the `lint` gate: one red case per half, and a green twin.

The gate exists because the battery had no style check at all for a run with a
task contract. `ruff check` sat in the `acceptance` gate's fallback `commands`
— the list a contract-less run falls back to — so every task-contract run
linted whatever its own contract named, and no contract in this repository
named ruff. The formatter half was never checked anywhere, which is how an
unformatted file landed and then came back twice.

What is checked here is the *wiring*: that the manifest entry runs ruff against
the worktree and that each half reddens on its own. ruff tests its own rules.
"""

from __future__ import annotations

import shutil

import pytest

from torve.config import layout
from torve.config.manifest import load_manifest
from torve.gates.context import build_context
from torve.gates.runner import run_gates

needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")

MANIFEST = {
    "schema_version": 1,
    "scope": {"allow": [], "deny": []},
    "gates": [
        {
            "name": "lint",
            "run": "ruff check . && ruff format --check .",
            "state": "blocking",
            "origin": "S-0015",
            "input": "worktree",
            "timeout": 120,
        }
    ],
}

CLEAN = "VALUE = 1\n"

# Formatted, and ruff's linter has nothing to say about it — the file that
# proves a red case is red for the reason it claims.
LINTS = "import os\n\nVALUE = 1\n"

# Lints clean and is not what the formatter would write: the half that had no
# gate at all, and the shape the reverted line actually took.
UNFORMATTED = (
    'VALUE = (\n    "a string short enough that the formatter would join these lines"\n)\n'
)


def seed(repo, body: str):
    repo.seed(MANIFEST)
    # `Repo.seed` plants `print('changed')`, which the formatter would rewrite —
    # a fine demonstration that the gate works, and not the case under test.
    repo.write("src/app.py", 'print("changed")\n')
    repo.write("pkg/thing.py", body)
    repo.commit("scratch package")


def outcome(repo) -> str:
    manifest = load_manifest(layout.gates_file(repo.root))
    report = run_gates(build_context(repo.root, manifest, base="main"), only={"lint"})
    return report.results[0].outcome


@needs_ruff
def test_a_lint_violation_reddens(repo):
    seed(repo, LINTS)
    assert outcome(repo) == "fail"


@needs_ruff
def test_an_unformatted_file_reddens(repo):
    """The half nothing checked. `ruff check` passes on this file, so a battery
    running only the linter calls it clean."""

    seed(repo, UNFORMATTED)
    assert outcome(repo) == "fail"


@needs_ruff
def test_clean_and_formatted_passes(repo):
    seed(repo, CLEAN)
    assert outcome(repo) == "pass"
