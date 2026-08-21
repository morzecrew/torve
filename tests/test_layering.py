"""Sabotage for the `layering` gate (RFC 0015 §6.2) — one red case per
contract, plus the green twin.

This suite lives in the repository, not in the shipped sabotage set: layering
is a self-development gate and `import-linter` is a dev dependency (D-15.10),
so a consuming repository's `torve gates check` never depends on it.

Each case seeds a scratch package with its own import-linter contracts
mirroring the three shapes in pyproject.toml, then runs the gate exactly as
the manifest would.
"""

from __future__ import annotations

import shutil
import textwrap

import pytest

from torve.config import layout
from torve.config.manifest import load_manifest
from torve.gates.context import build_context
from torve.gates.runner import run_gates

pytestmark = pytest.mark.skipif(
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
    "pkg/cli/__init__.py": "",
    "pkg/cli/main.py": "from pkg.adapters.runtime import docker  # noqa: F401\n",
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


def test_domain_importing_an_adapter_reddens(repo):
    seed(repo, {"pkg/domain/thing.py":
                "from pkg.adapters.runtime import docker  # noqa: F401\nTHING = 1\n"})
    assert outcome(repo) == "fail"


def test_gate_importing_the_application_reddens(repo):
    seed(repo, {"pkg/gates/check.py":
                "from pkg.application import service  # noqa: F401\n"})
    assert outcome(repo) == "fail"


def test_adapter_importing_an_adapter_reddens(repo):
    seed(repo, {"pkg/adapters/runtime/docker.py":
                "from pkg.adapters.workspace import git  # noqa: F401\n"})
    assert outcome(repo) == "fail"


def test_clean_package_passes(repo):
    seed(repo, {})
    assert outcome(repo) == "pass"
