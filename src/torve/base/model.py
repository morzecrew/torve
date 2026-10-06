"""One configuration for every model torve reads from a file or writes to the
record (S-0059/D-6): extras are refused, and a field's docstring — the words
that were a comment beside it — travels into the schema `torve init` derives
as the property's description. `Field(description=...)` is never written:
one text, one place.
"""

from __future__ import annotations

from pydantic import AfterValidator, ConfigDict

# ----------------------- #

STRICT = ConfigDict(extra="forbid", use_attribute_docstrings=True)

# ----------------------- #


def SchemaVersion(current: int) -> AfterValidator:
    """The validator every model torve reads from YAML carries on its
    `schema_version` field (S-0095/D-5), as `Annotated[int, SchemaVersion(CURRENT)]`:
    absent reads as current, which is the field's own default, and any other
    value is refused naming the version found and the version this build
    reads — a newer torve for a newer file, the release notes' conversion for
    an older one. The loader that knows the file's own path names it,
    wrapping the refusal exactly as every other field refusal here already is.
    """

    def check(value: int) -> int:
        if value == current:
            return value

        hint = (
            "a newer torve reads this file"
            if value > current
            else "the release notes name the conversion"
        )

        raise ValueError(f"schema_version {value} found, this build reads {current} — {hint}")

    return AfterValidator(check)
