"""Where a decision came from (RFC 0044 D-44.8, RFC 0047 §5.1).

A source is any provenance carrying zero or more decisions: a specification
document, an incident, an audit, a review finding, an operator's ask. The
corpus is one shape of this and not the privileged one — an incident that
settles something settles it as surely as a document does, and before this
existed the only way for it to count was to become an RFC first.

The task is the execution unit and cites its source for provenance, never
for parsing (D-44.8): `Task.rfc` stays a path to the document a contract was
written against, and the source id is what a record joins on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ----------------------- #

SourceKind = Literal["specification", "incident", "audit", "review", "operator"]

# The corpus's own namespace. A namespace is the importer's: `rfc/0044` is
# this one, `incident/…` and `ask/…` are others when their importers exist.
CORPUS_NAMESPACE = "rfc"


# ....................... #


@dataclass(frozen=True)
class Source:
    """One provenance, identified so a record can join to it.

    `id` is `<namespace>/<slug>` and is stable under renaming (D-47.4): the
    corpus's slug is the document number, which never changes and is never
    reused (D-A.6). `ref` is where the source currently lives, and `ref` is
    the field that moves when a file is renamed — which is the whole reason
    the id is not the path.
    """

    id: str
    kind: SourceKind
    ref: str
    title: str = ""


# ....................... #


def corpus_source_id(number: str) -> str:
    """The source id of one corpus document, from its number alone — the
    caller does not need the file, because the file's name is not part of
    the identity."""

    return f"{CORPUS_NAMESPACE}/{number}"
