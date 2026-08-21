from __future__ import annotations

from pathlib import Path

import pytest

from torve.context import build_context
from torve.manifest import load_manifest
from torve.sabotage import Repo


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    return Repo(root)


def context_for(repo: Repo, base: str = "main"):
    manifest = load_manifest(repo.root / "gates.yaml")
    return build_context(repo.root, manifest, base=base)
