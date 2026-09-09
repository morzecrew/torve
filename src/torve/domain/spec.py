"""The specification as a model (S-0053/the-model, S-0053/D-1; S-0056 S-0056/D-1;
S-0057 S-0057/D-1): one pydantic `Document` every reader consumes — planner,
importer, check, health, show, the intake lint, standing inheritance —
and the shape of its storage: a document is a directory of four YAML
files, each holding the slice of this model that one writer owns
(`FILE_FIELDS`), and nothing parses anything else into it.

This module owns the shape and nothing about the storage: `config/spec.py`
loads and checks it, `config/spec_emit.py` writes it, and `domain/rfc.py`
still owns the vocabularies. It imports pydantic, the vocabularies and the
task contract only (S-0053/D-14), so extracting it into its own distribution
is a packaging act and never a rewrite.

Every list below is a list of one model with `extra="forbid"`: an unknown
key is refused with the key named, never ignored (S-0053/D-3). Prose is
`DesignSection.md`, a string the engine never parses (S-0056/D-3).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from torve.domain.rfc import Grade, Implementation, Kind, Status
from torve.domain.task import Task

# ----------------------- #

SCHEMA_VERSION = 3  # S-0057/D-1: the directory of four files; 2 was the one YAML file

Coverage = Literal["governed", "ungoverned", "retired"]
CheckState = Literal["shadow", "blocking"]
QuestionStatus = Literal["open", "settled"]

FINGERPRINT_LENGTH = 16

# ----------------------- #
# The one grammar (S-0058 S-0058/D-1): a document is `S-NNNN`, and everything
# inside it is `S-NNNN/<local>` — `D-n`, `I-n`, `Q-n`, `A-n`, `P-n`, or a
# prose key. Inside its own document an item is written by the local half
# alone; in memory every identifier is global, and the writer strips the
# document's own prefix on the way out.
DOCUMENT_ID = re.compile(r"^S-\d{4}$")
LOCAL_ID = re.compile(r"^[DIQA]-\d+$")
CITATION = re.compile(r"^S-\d{4}(?:/(?:[DIQAP]-\d+|[a-z0-9][a-z0-9-]*))?$")
# A key a section or a design entry may carry: never a family shape.
SECTION_KEY = re.compile(r"^(?![DIQAP]-\d+$)[a-z0-9][a-z0-9-]*$")


def document_id(reference: str) -> str:
    """`S-0057` from what a caller has: `S-0057`, `0057`, `57`, a directory
    name, or a path ending in one (a contract's `rfc`, a legacy file name).
    Raises `ValueError` when no number is there."""

    name = reference.strip().rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".yaml").removesuffix(".md")

    if DOCUMENT_ID.match(name):
        return name

    digits = re.match(r"(?:S-)?(\d{1,4})(?![\d])", name)

    if digits is None:
        raise ValueError(f"{reference!r} names no document")

    return f"S-{int(digits.group(1)):04d}"


def number_of(document: str) -> str:
    """The four digits of a document identifier."""

    return document_id(document)[2:]


def qualify(document: str, identifier: str) -> str:
    """A local identifier made global under *document*; a global one is
    returned as it is."""

    return f"{document}/{identifier}" if LOCAL_ID.match(identifier) else identifier


def local_of(document: str, identifier: str) -> str:
    """The local half of one of *document*'s own identifiers; any other
    identifier as it is."""

    return (
        identifier.removeprefix(f"{document}/")
        if identifier.startswith(f"{document}/")
        else identifier
    )


def is_citation(value: str) -> bool:
    return bool(CITATION.match(value))


# ----------------------- #


def fingerprint(text: str, grade: str, paths: list[str]) -> str:
    """The content fingerprint of a row (S-0053/D-5, S-0053/D-16): text, grade and
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
    """One decision row, whole (S-0056/D-2): grade, text and paths — the three
    fields a contract copies at mint — with its consequence, rationale,
    citations, check and the stamp the tool last wrote on it."""

    id: str
    grade: Grade
    text: str
    paths: list[str] = Field(default_factory=list)
    consequence: str = ""
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)
    check: str | None = None  # authored, never derived (S-0053/D-15)
    check_state: CheckState = "shadow"  # promoted per row by amendment (S-0054/D-4)
    check_twin: str | None = None  # the test that proves the check can fail (S-0054/D-4)
    superseded_by: str | None = None
    # `<content>/<rule>` as `stamp` writes it, empty until the tool has
    # changed the row once; `fingerprint_drift` reads it (S-0053/D-5).
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
    """One typed edit an amendment made (S-0053/D-4): the prior value is read at
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
    """One prose section: the key a log cites and a markdown body the
    engine never parses (S-0053/D-3, S-0056/D-3). The heading is the key and the
    number is the position (S-0057/D-2); order is the list's."""

    key: str
    md: str = ""


def heading_of(key: str) -> str:
    """The heading a renderer shows for a section key: dashes to spaces,
    the first letter raised (S-0057/D-2)."""

    words = key.replace("-", " ").strip()

    return words[:1].upper() + words[1:]


# ....................... #

# The divergence vocabulary as the log spells it (S-0001/decisions); the same
# words `domain/events.py` validates a recorded entry against.
EntryGrade = Literal["LOCKED", "ASSUMED", "OPEN", "UNLISTED"]
EntryKind = Literal["contradicted", "departed", "resolved", "blocked"]
EntryClass = Literal["discovery", "spec-gap", "drift", "irreducible"]
EntryAction = Literal["halted", "departed", "decided"]


class LogEntry(Item):
    """One divergence entry as the task log carries it and a landing keeps
    it (S-0057/D-7): the row it cites, what reality said, the evidence, what
    the executor did. `class` is the log's key; the field is `entry_class`
    because the word is Python's."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, serialize_by_alias=True)

    decision: str
    grade: EntryGrade
    kind: EntryKind = "resolved"
    entry_class: EntryClass = Field(default="discovery", alias="class")
    at: str = ""
    attempt: int = Field(default=1, ge=1)
    claim: str
    evidence: str
    action: EntryAction
    proposal: str = ""
    notes: str = ""


class Landing(Item):
    """What one task found, kept beside the rows it informs (S-0057/D-7): the
    task, its phase and attempt, the commit when the lander knew it (the
    runner's rides in the candidate commit whose trailers name the task,
    so it leaves the field empty), when, by whom, and the log's entries."""

    task: str
    phase: int = 0
    attempt: int = Field(default=1, ge=1)
    commit: str = ""
    at: date | None = None
    agent: str = ""
    entries: list[LogEntry] = Field(default_factory=list)


class TaskLog(Item):
    """The task log's own shape, `.torve/tasks/T-NNNN/log.yaml` (S-0001
    §6): the pin, the derived drift count and the entries — what `torve
    init` writes the log's schema from (S-0057/D-5)."""

    schema_version: int
    task: str
    repo: str = ""
    base_sha: str = ""
    drift_count: int = 0
    entries: list[LogEntry] = Field(default_factory=list)


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
    """One specification document, joined from its directory's files
    (S-0057/D-1): the header fields, the prose as keyed sections, the typed
    lists. `path` names the directory and `archived` marks a document
    loaded from the archive (S-0053/D-8): every identifier it defines still
    resolves, and nothing inherits from it. Both are the loader's, never
    written."""

    id: str = Field(pattern=DOCUMENT_ID.pattern)
    title: str
    kind: Kind = "design"
    status: Status
    implementation: Implementation = "none"
    depends_on: list[str] = Field(default_factory=list)
    informed_by: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
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
    # The editorial lane's record (S-0053/D-4): `torve spec fix` appends the
    # before and after here, never an amendment number.
    editorial: list[Change] = Field(default_factory=list)
    # What execution found (S-0057/D-7): appended at landing, never by hand.
    landings: list[Landing] = Field(default_factory=list)

    @model_validator(mode="after")
    def _qualify(self) -> Document:
        """Every identifier global in memory (S-0058/D-1): an item written by
        its local half alone is this document's, and is read as such."""

        me = self.id

        for row in self.decisions:
            row.id = qualify(me, row.id)
            row.cites = [qualify(me, c) for c in row.cites]

            if row.superseded_by:
                row.superseded_by = qualify(me, row.superseded_by)

        for invariant in self.invariants:
            invariant.id = qualify(me, invariant.id)

        for question in self.questions:
            question.id = qualify(me, question.id)

            if question.settled_by:
                question.settled_by = qualify(me, question.settled_by)

        for alternative in self.alternatives:
            alternative.cites = [qualify(me, c) for c in alternative.cites]

        for amendment in self.amendments:
            amendment.id = qualify(me, amendment.id)

            for change in amendment.changes:
                change.subject = qualify(me, change.subject)

        for change in self.editorial:
            change.subject = qualify(me, change.subject)

        for landing in self.landings:
            for entry in landing.entries:
                entry.decision = qualify(me, entry.decision)

        self.retired = [qualify(me, r) for r in self.retired]

        return self

    def amended_by(self) -> list[str]:
        """The amendment identifiers, derived — never a field (S-0057/D-1)."""

        return [a.id for a in self.amendments]

    def decision(self, identifier: str) -> Decision | None:
        wanted = qualify(self.id, identifier)

        return next((d for d in self.decisions if d.id == wanted), None)

    def local(self, identifier: str) -> str:
        """One of this document's identifiers as its file writes it."""

        return local_of(self.id, identifier)

    def defined_identifiers(self) -> set[str]:
        """Every identifier this document defines, global (S-0058/D-1): itself,
        its rows, invariants, questions, amendments, phases, prose keys and
        its retired ids."""

        ids = {self.id}
        ids.update(d.id for d in self.decisions)
        ids.update(i.id for i in self.invariants)
        ids.update(q.id for q in self.questions)
        ids.update(a.id for a in self.amendments)
        ids.update(f"{self.id}/P-{p.phase}" for p in self.phasing)
        ids.update(f"{self.id}/{s.key}" for s in self.sections)
        ids.update(self.retired)

        return ids


# ....................... #

# The directory's files by the hand that writes each (S-0057/D-1): the author's
# document and rows, the tool's amendments, the landing's execution. A key
# belongs to exactly one file; the loader joins them into one `Document`
# and the writer splits it back.
DOCUMENT_FILE = "document.yaml"
DECISIONS_FILE = "decisions.yaml"
AMENDMENTS_FILE = "amendments.yaml"
EXECUTION_FILE = "execution.yaml"
FILES = (DOCUMENT_FILE, DECISIONS_FILE, AMENDMENTS_FILE, EXECUTION_FILE)
FILE_FIELDS: dict[str, tuple[str, ...]] = {
    DOCUMENT_FILE: (
        "id",
        "title",
        "kind",
        "status",
        "implementation",
        "depends_on",
        "informed_by",
        "supersedes",
        "superseded_by",
        "owner",
        "description",
        "schema_version",
        "sections",
        "alternatives",
        "questions",
        "phasing",
        "contract_example",
    ),
    DECISIONS_FILE: ("decisions", "invariants", "retired"),
    AMENDMENTS_FILE: ("amendments", "editorial"),
    EXECUTION_FILE: ("landings",),
}


def file_of(field_name: str) -> str | None:
    """Which of the four files carries a field, or None for a field no
    file carries (the loader's own)."""

    return next((name for name, fields in FILE_FIELDS.items() if field_name in fields), None)


# ....................... #


class Corpus(Item):
    """Every document the loader found — the corpus path and, when one
    exists, the archive beside it — with the joins a reader asks for."""

    documents: list[Document] = Field(default_factory=list)

    def document(self, reference: str) -> Document | None:
        """One document by any spelling of its identifier — `S-0057`,
        `0057`, a directory name, a contract's path; None when none."""

        try:
            wanted = document_id(reference)
        except ValueError:
            return None

        return next((d for d in self.documents if d.id == wanted), None)

    def decision(self, identifier: str) -> tuple[Document, Decision] | None:
        """One row by its global identifier."""

        for doc in self.documents:
            row = next((d for d in doc.decisions if d.id == identifier), None)

            if row is not None:
                return doc, row

        return None

    def standing(self) -> list[Document]:
        """Documents whose rows may be inherited: accepted and not archived
        (S-0016/D-20, S-0053/D-8)."""

        return [d for d in self.documents if d.status == "accepted" and not d.archived]

    def defined_identifiers(self) -> set[str]:
        ids: set[str] = set()

        for doc in self.documents:
            ids |= doc.defined_identifiers()

        return ids
