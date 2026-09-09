"""Presentation layer (S-0015/layers). `app` and `main` are re-exported for the
console script and the tests; everything else is per-command modules.
"""

from torve.cli.main import app, main

# ----------------------- #

__all__ = ["app", "main"]
