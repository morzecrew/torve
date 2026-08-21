"""The curated lazy front door (RFC 0015 §5, D-15.7)."""

from __future__ import annotations

import subprocess
import sys

import pytest

import torve


def test_all_matches_the_export_table():
    assert sorted(torve.__all__) == sorted([*torve._EXPORTS, "__version__"])


def test_every_export_resolves():
    for name in torve.__all__:
        assert getattr(torve, name) is not None


def test_unknown_name_raises():
    with pytest.raises(AttributeError):
        torve.nonsense  # noqa: B018


def test_import_torve_stays_cheap():
    # RFC 0015 §9: the gates-only path must not pay for the runner.
    code = (
        "import sys, torve\n"
        "heavy = [m for m in sys.modules\n"
        "         if m.startswith(('torve.application', 'torve.adapters', 'torve.cli'))]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
