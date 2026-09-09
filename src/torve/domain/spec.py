"""The specification as a model (RFC 0053 §5.1, D-53.1): one pydantic
`Document` every reader consumes — planner, importer, check, health, show,
the intake lint, standing inheritance — and never a parser's rows.

This module owns the shape and nothing about the storage: `config/spec.py`
loads it from the markdown corpus, and `domain/rfc.py` still owns the
vocabularies. It imports pydantic, the vocabularies and the task contract
only (D-53.14), so extracting it into its own distribution is a packaging
act and never a rewrite.

The typed additions to a document are the five fenced kinds of RFC 0053
§5.2 — `decision-details`, `invariants`, `alternatives`, `questions`,
`changes` — each a list of one model below with `extra="forbid"`: an
unknown key is refused with the key named, never ignored (D-53.3). Prose
sections become `DesignSection`s keyed by heading slug and are typed no
further.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from torve.domain.rfc import Grade, Implementation, Kind, Status
from torve.domain.task import Task

# ----------------------- #

FENCE_KINDS = ("decision-details", "invariants", "alternatives", "questions", "changes")

Coverage = Literal["governed", "ungoverned", "retired"]
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


# ----------------------- #


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ....................... #


class Decision(Item):
    """One decision-table row, joined with its `decision-details` entry
    when the document carries one. `consequence` is the table's fifth cell;
    `rationale`, `cites`, `check` and `superseded_by` come from the fence."""

    id: str
    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)
    consequence: str = ""
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)
    check: str | None = None  # authored, never derived (D-53.15)
    superseded_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fingerprint(self) -> str:
        return fingerprint(self.text, self.grade, self.paths)


# ....................... #


class DecisionDetail(Item):
    """One entry of a `yaml decision-details` fence (RFC 0053 §5.2)."""

    id: str
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)
    check: str | None = None
    superseded_by: str | None = None


# ....................... #


class Invariant(Item):
    """One entry of a `yaml invariants` fence: a statement with the paths it
    holds over and the command that proves it."""

    id: str
    statement: str
    paths: list[str] = Field(default_factory=list)
    check: str


# ....................... #


class Alternative(Item):
    """One entry of a `yaml alternatives` fence: the negative space every
    executor re-proposes when nobody told it the option was closed."""

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
    """One `### A-n` entry of the Amendments section: heading, the typed
    diff from its `yaml changes` fence (empty for an execution finding that
    changed nothing typed), and the words, untouched."""

    id: str
    at: date | None = None
    title: str = ""
    changes: list[Change] = Field(default_factory=list)
    body_md: str = ""


# ....................... #


class DesignSection(Item):
    """One prose section, keyed by heading slug — the anchor a log cites —
    and typed no further (D-53.3)."""

    key: str
    heading: str
    level: int = Field(ge=2, le=3)
    order: int = 0
    md: str = ""


# ....................... #


class Phase(Item):
    """One mintable unit of the Phasing fence, field for field the loader's
    `PhasingEntry` (unchanged by RFC 0053) so a contract minted from either
    is the same contract."""

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
    """One specification document: the frontmatter as fields, the typed
    sections as lists, the prose as keyed sections. `archived` marks a
    document loaded from the archive (D-53.8): every identifier it defines
    still resolves, and nothing inherits from it."""

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
    schema_version: int
    path: str = ""
    archived: bool = False

    decisions: list[Decision] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    phasing: list[Phase] = Field(default_factory=list)
    contract_example: Task | None = None
    sections: list[DesignSection] = Field(default_factory=list)
    amendments: list[Amendment] = Field(default_factory=list)

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
