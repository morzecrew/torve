"""Where a decision came from (S-0044 S-0044/D-8, S-0047/a-source-is-provenance-not-a-document).

A source is any provenance carrying zero or more decisions: a specification
document, an incident, an audit, a review finding, an operator's ask. The
corpus is one shape of this and not the privileged one — an incident that
settles something settles it as surely as a document does, and before this
existed the only way for it to count was to become a document first.

Everything that is not a document is a file of its own (S-0060/D-1),
`.torve/sources/<kind>/<slug>.yaml`, so a source identifier resolves to
something a person can open. It carries no decisions (S-0060/D-2): rows that
stand are the corpus's alone, and a source that settled some names the
document holding them.

The task is the execution unit and cites its source for provenance, never
for parsing (S-0044/D-8): `Task.spec` names the document whose rows it
inherits (S-0059/D-1) and `Task.source` what asked for the work
(S-0060/D-3).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from torve.base.clock import INSTANT_PATTERN
from torve.base.model import STRICT
from torve.domain.vocabulary import SOURCE_KINDS, SourceKind

# ----------------------- #
# The source grammar, defined here and imported by whatever validates one.
# `domain/spec.py` and `domain/task.py` reach this module and never the other
# way, so the identifier is spelled once (S-0059/D-5's rule for words, read
# for a grammar).

# A document is a source without being filed as one, so it owns no directory.
FILED_KINDS = tuple(kind for kind in SOURCE_KINDS if kind != "specification")
SLUG_PATTERN = r"[a-z0-9][a-z0-9.-]*"
SLUG = re.compile(f"^{SLUG_PATTERN}$")

# A source identifier: a document, `S-NNNN` (S-0058/D-1), or `<kind>/<slug>`
# for everything else (S-0060/D-1). The `S-` prefix is what tells them apart,
# so a document needs no namespace of its own.
FILED_PATTERN = rf"(?:{'|'.join(FILED_KINDS)})/{SLUG_PATTERN}"
SOURCE_PATTERN = rf"^(?:S-\d{{4}}|{FILED_PATTERN})$"
FILED_ID = re.compile(f"^{FILED_PATTERN}$")
SOURCE_ID = re.compile(SOURCE_PATTERN)

# Where the filed sources live, under `.torve/` beside the corpus.
SOURCES_DIR = "sources"


class Source(BaseModel):
    """One provenance, identified so a record can join to it, and — for
    everything but a document — a file a person can open (S-0060/D-1).

    `id` is stable under renaming (S-0047/D-4): the corpus's is the document
    identifier, `S-NNNN` (S-0058/D-1), which never changes and is never
    reused (S-0016/D-17); every other kind's is `<kind>/<slug>` and must
    equal the path its file sits at. `ref` is where the source currently
    lives, and `ref` is the field that moves when a thing is renamed — which
    is the whole reason the id is not the path.
    """

    model_config = STRICT

    id: str
    """`S-NNNN` for a document, `<kind>/<slug>` for a filed source."""
    kind: SourceKind
    """What this provenance is: a specification, an incident, an audit, a review or an ask."""
    ref: str = ""
    """Where it lives — a URL, an issue, a commit, a person; the field that moves."""
    title: str = ""
    """What it is, in a line."""
    at: str = Field(default="", pattern=f"^$|{INSTANT_PATTERN}")
    """When it arrived, `YYYY-MM-DDTHH:MM:SSZ` (S-0058/D-7); empty when nobody said."""
    summary: str = ""
    """What it said, in prose. Never rules — those are the corpus's (S-0060/D-2)."""
    settled_by: list[str] = Field(default_factory=list)
    """The documents holding the rows this source settled, if it settled any (S-0060/D-2)."""


# ....................... #


def is_source_id(value: str) -> bool:
    """Whether *value* is a source identifier the grammar admits: a document
    or a filed source (S-0060/D-1)."""

    return bool(SOURCE_ID.match(value))


def filed_kind(identifier: str) -> str:
    """The kind half of a filed source identifier; the empty string for a
    document, which is a source without being filed as one."""

    return identifier.split("/", 1)[0] if FILED_ID.match(identifier) else ""


# ....................... #


def corpus_source_id(reference: str) -> str:
    """The source id of one corpus document: its own identifier, `S-NNNN`
    (S-0058/D-1), from any spelling of it — the caller does not need the file,
    because the file's name is not part of the identity."""

    from torve.domain.spec import document_id

    return document_id(reference)
