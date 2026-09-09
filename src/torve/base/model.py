"""One configuration for every model torve reads from a file or writes to the
record (S-0059/D-6): extras are refused, and a field's docstring — the words
that were a comment beside it — travels into the schema `torve init` derives
as the property's description. `Field(description=...)` is never written:
one text, one place.
"""

from __future__ import annotations

from pydantic import ConfigDict

# ----------------------- #

STRICT = ConfigDict(extra="forbid", use_attribute_docstrings=True)
