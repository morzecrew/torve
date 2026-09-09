"""The specification as a model (RFC 0053 §5.1, D-53.1; RFC 0056 D-56.1):
one pydantic `Document` every reader consumes — planner, importer, check,
health, show, the intake lint, standing inheritance — and, since RFC
0056, the shape of the file itself: a document is this model dumped as
YAML, and nothing parses anything else into it.

This module owns the shape and nothing about the storage: `config/spec.py`
loads and checks it, `config/rfc_emit.py` writes it, and `domain/rfc.py`
still owns the vocabularies. It imports pydantic, the vocabularies and the
task contract only (D-53.14), so extracting it into its own distribution
is a packaging act and never a rewrite.

Every list below is a list of one model with `extra="forbid"`: an unknown
key is refused with the key named, never ignored (D-53.3). Prose is
`DesignSection.md`, a string the engine never parses (D-56.3).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from torve.domain.rfc import Grade, Implementation, Kind, Status
from torve.domain.task import Task

# ----------------------- #

SCHEMA_VERSION = 2  # D-56.1: the YAML document; 1 was the markdown document

Coverage = Literal["governed", "ungoverned", "retired"]
CheckState = Literal["shadow", "blocking"]
QuestionStatus = Literal["open", "settled"]

FINGERPRINT_LENGTH = 16


# ----------------------- #


def fingerprint(text: str, grade: str, paths: list[str]) -> str:
    """The content fingerprint of a row (D-53.5, D-53.16): text, grade and
    paths — the three fields a contract copies at mint — and nothing a human
    reads beside them. A text-only change is editorial drift, a grade or
    paths change is an amendment (§5.4); both are told apart by which
    field moved, which is why the fields are hashed in a fixed order rather
    than as one blob."""

    material = "\n".join([text.strip(), grade, " ".join(sorted(paths))])

    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def rule_fingerprint(grade: str, paths: list[str]) -> str:
    """The half of the stamp that moves only when the rule moves: grade and
    paths, never text — what tells editorial drift from a hand-edited rule."""

    material = "\n".join([grade, " ".join(sorted(paths))])

    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


# ----------------------- #


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ....................... #


class Decision(Item):
    """One decision row, whole (D-56.2): grade, text and paths — the three
    fields a contract copies at mint — with its consequence, rationale,
    citations, check and the stamp the tool last wrote on it."""

    id: str
    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)
    consequence: str = ""
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)
    check: str | None = None  # authored, never derived (D-53.15)
    check_state: CheckState = "shadow"  # promoted per row by amendment (D-54.4)
    check_twin: str | None = None  # the test that proves the check can fail (D-54.4)
    superseded_by: str | None = None
    # `<content>/<rule>` as `stamp` writes it, empty until the tool has
    # changed the row once; `fingerprint_drift` reads it (D-53.5).
    fingerprint: str = ""

    def content_fingerprint(self) -> str:
        return fingerprint(self.text, self.grade, self.paths)

    def stamp(self) -> str:
        """The value the tool records on a row it just changed."""

        return f"{self.content_fingerprint()}/{rule_fingerprint(self.grade, self.paths)}"


# ....................... #


class Invariant(Item):
    """A statement with the paths it holds over and the command that proves
    it."""

    id: str
    statement: str
    paths: list[str] = Field(default_factory=list)
    check: str


# ....................... #


class Alternative(Item):
    """One rejected option: the negative space every executor re-proposes
    when nobody told it the option was closed."""

    option: str
    rejected_because: str
    cites: list[str] = Field(default_factory=list)


# ....................... #


class Question(Item):
    """One entry of a `yaml questions` fence."""

    id: str
    text: str
    status: QuestionStatus = "open"
    settled_by: str | None = None


# ....................... #


class Change(Item):
    """One typed edit an amendment made (D-53.4): the prior value is read at
    amendment time, which is the only moment it exists."""

    subject: str
    field: str
    before: Any = None
    after: Any = None


# ....................... #


class Amendment(Item):
    """One amendment: its number and date, the typed diff (empty for an
    execution finding that changed nothing typed), and the words."""

    id: str
    at: date | None = None
    title: str = ""
    changes: list[Change] = Field(default_factory=list)
    md: str = ""


# ....................... #


class DesignSection(Item):
    """One prose section: the key a log cites, the heading a reader sees,
    and a markdown body the engine never parses (D-53.3, D-56.3). Order is
    the list's."""

    key: str
    heading: str
    md: str = ""


# ....................... #


class Phase(Item):
    """One mintable unit of the phasing: what `torve plan` derives a
    contract from."""

    phase: int = Field(ge=1)
    title: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    acceptance: list[str] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)
    tier_variant: str = ""
    character: Literal["", "structural", "routine"] = ""


# ....................... #


class Document(Item):
    """One specification document, and the shape of its file (D-56.1): the
    header fields, the prose as keyed sections, the typed lists. `path` and
    `archived` are the loader's, never written; `archived` marks a document
    loaded from the archive (D-53.8): every identifier it defines still
    resolves, and nothing inherits from it."""

    id: str
    title: str
    kind: Kind = "design"
    status: Status
    implementation: Implementation = "none"
    depends_on: list[str] = Field(default_factory=list)
    informed_by: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    amended_by: list[str] = Field(default_factory=list)
    retired: list[str] = Field(default_factory=list)
    owner: str
    description: str
    schema_version: int = SCHEMA_VERSION
    path: str = Field(default="", exclude=True)
    archived: bool = Field(default=False, exclude=True)

    sections: list[DesignSection] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    phasing: list[Phase] = Field(default_factory=list)
    contract_example: Task | None = None
    amendments: list[Amendment] = Field(default_factory=list)
    # The editorial lane's record (D-53.4): `torve rfc fix` appends the
    # before and after here, never an amendment number.
    editorial: list[Change] = Field(default_factory=list)

    def decision(self, identifier: str) -> Decision | None:
        return next((d for d in self.decisions if d.id == identifier), None)

    def defined_identifiers(self) -> set[str]:
        """Every identifier this document defines: its number, its rows,
        its invariants, its questions, its amendments, and its retired ids."""

        ids = {self.id}
        ids.update(d.id for d in self.decisions)
        ids.update(i.id for i in self.invariants)
        ids.update(q.id for q in self.questions)
        ids.update(a.id for a in self.amendments)
        ids.update(self.retired)

        return ids


# ....................... #


class Corpus(Item):
    """Every document the loader found — the corpus path and, when one
    exists, the archive beside it — with the joins a reader asks for."""

    documents: list[Document] = Field(default_factory=list)

    def document(self, number: str) -> Document | None:
        return next((d for d in self.documents if d.id == number), None)

    def decision(self, identifier: str) -> tuple[Document, Decision] | None:
        for doc in self.documents:
            row = doc.decision(identifier)

            if row is not None:
                return doc, row

        return None

    def standing(self) -> list[Document]:
        """Documents whose rows may be inherited: accepted and not archived
        (D-A.10, D-53.8)."""

        return [d for d in self.documents if d.status == "accepted" and not d.archived]

    def defined_identifiers(self) -> set[str]:
        ids: set[str] = set()

        for doc in self.documents:
            ids |= doc.defined_identifiers()

        return ids


# ----------------------- #

# One corpus citation as it appears in a `cites` list: a decision (dotted,
# or the charter's bare `D-n`), an invariant, a question, an amendment, or
# a document number.
CITATION = re.compile(r"^(?:D-[A-Za-z0-9]+(?:\.\d+[a-z]?)?|I-\d+\.\d+|Q-\d+\.\d+|A-\d+|\d{4})$")


def is_citation(value: str) -> bool:
    return bool(CITATION.match(value))
