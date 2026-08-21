"""Presentation layer (RFC 0015 §2). `app` and `main` are re-exported for the
console script and the tests; everything else is per-command modules.
"""

from torve.cli.main import app, main

# ----------------------- #

__all__ = ["app", "main"]
