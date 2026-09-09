"""Where a decision came from (S-0044 S-0044/D-8, S-0047/a-source-is-provenance-not-a-document).

A source is any provenance carrying zero or more decisions: a specification
document, an incident, an audit, a review finding, an operator's ask. The
corpus is one shape of this and not the privileged one — an incident that
settles something settles it as surely as a document does, and before this
existed the only way for it to count was to become an RFC first.

The task is the execution unit and cites its source for provenance, never
for parsing (S-0044/D-8): `Task.spec` names the document by identifier
(S-0059/D-1), and that identifier is the source id a record joins on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ----------------------- #

SourceKind = Literal["specification", "incident", "audit", "review", "operator"]


@dataclass(frozen=True)
class Source:
    """One provenance, identified so a record can join to it.

    `id` is stable under renaming (S-0047/D-4): the corpus's is the document
    identifier, `S-NNNN` (S-0058/D-1), which never changes and is never
    reused (S-0016/D-17); an incident's or an ask's is its importer's. `ref`
    is where the source currently lives, and `ref` is the field that moves
    when a file is renamed — which is the whole reason the id is not the path.
    """

    id: str
    kind: SourceKind
    ref: str
    title: str = ""


# ....................... #


def corpus_source_id(reference: str) -> str:
    """The source id of one corpus document: its own identifier, `S-NNNN`
    (S-0058/D-1), from any spelling of it — the caller does not need the file,
    because the file's name is not part of the identity."""

    from torve.domain.spec import document_id

    return document_id(reference)
