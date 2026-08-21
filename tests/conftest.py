from __future__ import annotations

from pathlib import Path

import pytest

from torve import layout
from torve.context import build_context
from torve.gates.sabotage import Repo
from torve.manifest import load_manifest


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    return Repo(root)


def context_for(repo: Repo, base: str = "main"):
    manifest = load_manifest(layout.gates_file(repo.root))
    return build_context(repo.root, manifest, base=base)
