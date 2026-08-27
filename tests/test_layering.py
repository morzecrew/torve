"""Sabotage for the `layering` gate (RFC 0015 §6.2): one red case, one green
twin, plus the front door's import-weight rule (§9).

This suite lives in the repository, not in the shipped sabotage set: layering
is a self-development gate and `import-linter` is a dev dependency (D-15.10),
so a consuming repository's `torve gates check` never depends on it.

What is checked here is the *wiring* — that the manifest entry runs
import-linter against this repository's contracts and that a violation
reddens. import-linter tests its own contract engine (A-46); a case per
contract type re-tested the dependency, not the gate.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap

import pytest

from torve.config import layout
from torve.config.manifest import load_manifest
from torve.gates.context import build_context
from torve.gates.runner import run_gates

# Scoped to the gate cases, not the module: the front-door check below runs
# everywhere, import-linter installed or not.
needs_linter = pytest.mark.skipif(
    shutil.which("lint-imports") is None, reason="import-linter not installed"
)

MANIFEST = {
    "schema_version": 1,
    "scope": {"allow": [], "deny": []},
    "gates": [{
        "name": "layering",
        # The scratch package is importable from the repository root.
        "run": "PYTHONPATH=. lint-imports",
        "state": "blocking",
        "origin": "rfc/0015",
        "input": "worktree",
        "timeout": 120,
    }],
}

PYPROJECT = textwrap.dedent("""\
    [tool.importlinter]
    root_package = "pkg"

    [[tool.importlinter.contracts]]
    name = "Layers"
    type = "layers"
    layers = ["pkg.cli", "pkg.adapters", "pkg.application", "pkg.domain"]

    [[tool.importlinter.contracts]]
    name = "Gates stand alone"
    type = "forbidden"
    source_modules = ["pkg.gates"]
    forbidden_modules = ["pkg.application", "pkg.adapters"]

    [[tool.importlinter.contracts]]
    name = "Adapters are independent"
    type = "independence"
    modules = ["pkg.adapters.runtime", "pkg.adapters.workspace"]

    [[tool.importlinter.contracts]]
    name = "The RFC format stays at the planner"
    type = "forbidden"
    source_modules = ["pkg.gates", "pkg.adapters.runtime"]
    forbidden_modules = ["pkg.config.rfc_parse"]
    """)

CLEAN = {
    "pkg/__init__.py": "",
    "pkg/domain/__init__.py": "",
    "pkg/domain/thing.py": "THING = 1\n",
    "pkg/application/__init__.py": "",
    "pkg/application/service.py": "from pkg.domain import thing  # noqa: F401\n",
    "pkg/adapters/__init__.py": "",
    "pkg/adapters/runtime/__init__.py": "",
    "pkg/adapters/runtime/docker.py": (
        "from pkg.application import service  # noqa: F401\n"),
    "pkg/adapters/workspace/__init__.py": "",
    "pkg/adapters/workspace/git.py": "WORKSPACE = 1\n",
    "pkg/gates/__init__.py": "",
    "pkg/gates/check.py": "from pkg.domain import thing  # noqa: F401\n",
    "pkg/config/__init__.py": "",
    "pkg/config/rfc_parse.py": "FORMAT = 1\n",
    "pkg/cli/__init__.py": "",
    # The CLI reading the format is the planner's side of the line (A-19).
    "pkg/cli/main.py": ("from pkg.adapters.runtime import docker  # noqa: F401\n"
                        "from pkg.config import rfc_parse  # noqa: F401\n"),
}


def seed(repo, overrides):
    repo.seed(MANIFEST)
    repo.write("pyproject.toml", PYPROJECT)
    for rel, content in {**CLEAN, **overrides}.items():
        repo.write(rel, content)
    repo.commit("scratch package")


def outcome(repo) -> str:
    manifest = load_manifest(layout.gates_file(repo.root))
    report = run_gates(build_context(repo.root, manifest, base="main"),
                       only={"layering"})
    return report.results[0].outcome


@needs_linter
def test_a_contract_violation_reddens(repo):
    # The layers contract is the load-bearing one; a domain module reaching
    # for an adapter is the inversion every other contract is a variation on.
    seed(repo, {"pkg/domain/thing.py":
                "from pkg.adapters.runtime import docker  # noqa: F401\nTHING = 1\n"})
    assert outcome(repo) == "fail"


@needs_linter
def test_clean_package_passes(repo):
    seed(repo, {})
    assert outcome(repo) == "pass"


# ....................... #


def test_import_torve_stays_cheap():
    # RFC 0015 §9: the gates-only path must not pay for the runner. With the
    # front door gone (A-45) this holds because nothing in `torve/__init__.py`
    # imports downward — this is the check that keeps it that way.
    code = (
        "import sys, torve\n"
        "heavy = [m for m in sys.modules\n"
        "         if m.startswith(('torve.application', 'torve.adapters', 'torve.cli'))]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
