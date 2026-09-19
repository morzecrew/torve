"""The specification as a model (S-0053/the-model, S-0053/D-1; S-0056 S-0056/D-1;
S-0057 S-0057/D-1): one pydantic `Document` every reader consumes — planner,
importer, check, health, show, the intake lint, standing inheritance —
and the shape of its storage: a document is a directory of four YAML
files, each holding the slice of this model that one writer owns
(`FILE_FIELDS`), and nothing parses anything else into it.

This module owns the shape and nothing about the storage: `config/spec.py`
loads and checks it, `config/spec_emit.py` writes it, and
`domain/vocabulary.py` owns the words it uses (S-0059/D-5). It imports pydantic, the vocabularies and the
task contract only (S-0053/D-14), so extracting it into its own distribution
is a packaging act and never a rewrite.

Every list below is a list of one model with `extra="forbid"`: an unknown
key is refused with the key named, never ignored (S-0053/D-3). Prose is
`DesignSection.md`, a string the engine never parses (S-0056/D-3).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from torve.base.clock import INSTANT_PATTERN
from torve.base.model import STRICT
from torve.domain.task import SPEC_PATTERN, Task
from torve.domain.vocabulary import (
    Character,
    CheckState,
    EntryAction,
    EntryClass,
    EntryGrade,
    EntryKind,
    Grade,
    Implementation,
    Kind,
    QuestionStatus,
    Status,
)

# ----------------------- #

SCHEMA_VERSION = 4  # S-0058/D-4: the typed anatomy and phasing.yaml; 3 was the flat section list

FINGERPRINT_LENGTH = 16

# ----------------------- #
# The one grammar (S-0058 S-0058/D-1): a document is `S-NNNN`, and everything
# inside it is `S-NNNN/<local>` — `D-n`, `I-n`, `Q-n`, `A-n`, `P-n`, or a
# prose key. Inside its own document an item is written by the local half
# alone; in memory every identifier is global, and the writer strips the
# document's own prefix on the way out.
DOCUMENT_ID = re.compile(SPEC_PATTERN)
LOCAL_ID = re.compile(r"^[DIQA]-\d+$")
CITATION = re.compile(r"^S-\d{4}(?:/(?:[DIQAP]-\d+|[a-z0-9][a-z0-9-]*))?$")
# A key a section or a design entry may carry: never a family shape.
SECTION_KEY = re.compile(r"^(?![DIQAP]-\d+$)[a-z0-9][a-z0-9-]*$")


def document_id(reference: str) -> str:
    """`S-0057` from what a caller has: `S-0057`, `0057`, `57`, a directory
    name, or a path ending in one (a legacy file name, the path a contract
    carried before S-0059).
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
    """The base of every item a document's files hold: extras refused, and each
    field's docstring carried into the schema `torve init` derives (S-0059/D-6)."""

    model_config = STRICT


# ....................... #


class Decision(Item):
    """One decision row, whole (S-0056/D-2): grade, text and paths — the three
    fields a contract copies at mint — with its consequence, rationale,
    citations, check and the stamp the tool last wrote on it."""

    id: str
    """`D-n` inside its own document, `S-NNNN/D-n` in memory (S-0058/D-1)."""
    grade: Grade
    """LOCKED, ASSUMED or OPEN (S-0007/D-13)."""
    text: str
    """The decision itself — the sentence a contract copies at mint."""
    paths: list[str] = Field(default_factory=list)
    """The area the row governs; a LOCKED row must declare one."""
    consequence: str = ""
    """What follows from the decision — the reason handed to the executor beside the rule."""
    rationale: str = ""
    """Why the row was decided this way; never copied onto a contract."""
    cites: list[str] = Field(default_factory=list)
    """The identifiers this row builds on."""
    check: str | None = None
    """A command whose exit code judges the row; authored, never derived (S-0053/D-15)."""
    check_state: CheckState = "shadow"
    """The check's gate state, promoted per row by amendment (S-0054/D-4)."""
    check_twin: str | None = None
    """The test that proves the check can fail (S-0054/D-4)."""
    superseded_by: str | None = None
    """The row that replaced this one, once it is retired."""
    fingerprint: str = ""
    """`<content>/<rule>` as `stamp` writes it, empty until the tool has changed
    the row once; `fingerprint_drift` reads it (S-0053/D-5)."""

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
    """`I-n` inside its own document, `S-NNNN/I-n` in memory."""
    statement: str
    """What must hold, over the paths."""
    paths: list[str] = Field(default_factory=list)
    """Where it holds."""
    check: str
    """The command that proves it; exit 0 is holding."""


# ....................... #


class Alternative(Item):
    """One rejected option: the negative space every executor re-proposes
    when nobody told it the option was closed."""

    option: str
    """The option that was on the table."""
    rejected_because: str
    """Why it was closed."""
    cites: list[str] = Field(default_factory=list)
    """The identifiers the rejection rests on."""


# ....................... #


class Question(Item):
    """One entry of a `yaml questions` fence."""

    id: str
    """`Q-n` inside its own document, `S-NNNN/Q-n` in memory."""
    text: str
    """The question left open."""
    status: QuestionStatus = "open"
    """`open` until something settles it."""
    settled_by: str | None = None
    """The amendment or row that settled it."""


# ....................... #


class Change(Item):
    """One typed edit an amendment made (S-0053/D-4): the prior value is read at
    amendment time, which is the only moment it exists."""

    subject: str
    """The identifier the edit changed."""
    field: str
    """The field that moved."""
    before: Any = None
    """The prior value, read at amendment time."""
    after: Any = None
    """The value after."""


# ....................... #


class Amendment(Item):
    """One amendment: its number and date, the typed diff (empty for an
    execution finding that changed nothing typed), and the words."""

    id: str
    """`A-n`, numbered per document (S-0058/D-1)."""
    at: str = Field(default="", pattern=f"^$|{INSTANT_PATTERN}")
    """The instant it was appended, `YYYY-MM-DDTHH:MM:SSZ` (S-0058/D-7)."""
    title: str = ""
    """What the amendment did, in a line."""
    changes: list[Change] = Field(default_factory=list)
    """The typed diff; empty for an execution finding that changed nothing typed."""
    md: str = ""
    """The entry's own words, markdown."""


# ....................... #


class DesignSection(Item):
    """One prose section: the key a log cites and a markdown body the
    engine never parses (S-0053/D-3, S-0056/D-3). The heading is the key and the
    number is the position (S-0057/D-2); order is the list's."""

    key: str
    """The key a log cites, `S-NNNN/<key>`; the heading is derived from it."""
    md: str = ""
    """The markdown body the engine never parses."""


HEADINGS = {"non-goals": "Non-goals", "out-of-scope": "Out of scope"}


def heading_of(key: str) -> str:
    """The heading a renderer shows for a section key: dashes to spaces,
    the first letter raised (S-0057/D-2)."""

    if key in HEADINGS:
        return HEADINGS[key]

    words = key.replace("-", " ").strip()

    return words[:1].upper() + words[1:]


# The anatomy (S-0058/D-4): the prose every document has, as keys; a
# design is a keyed list; anything else is an extra, capped.
PROSE_FIELDS = (
    "summary",
    "motivation",
    "current_state",
    "goals",
    "non_goals",
    "tests",
    "docs",
    "out_of_scope",
    "risks",
)
REQUIRED_PROSE = ("summary", "motivation", "current_state", "goals", "non_goals", "tests", "risks")
EXTRAS_CAP = 8


def prose_key(field_name: str) -> str:
    """The section key of a typed prose field: `current_state` is cited
    as `S-NNNN/current-state`."""

    return field_name.replace("_", "-")


def routing_line(summary: str) -> str:
    """The summary's first sentence: what `spec list` shows and the pack
    routes by (S-0058/D-4)."""

    text = " ".join(summary.split())
    match = re.search(r"^(.+?[.!?])(?:\s|$)", text)

    return (match.group(1) if match else text).strip()


# ....................... #


class LogEntry(Item):
    """One divergence entry as the task log carries it and a landing keeps
    it (S-0057/D-7): the row it cites, what reality said, the evidence, what
    the executor did. `class` is the log's key; the field is `entry_class`
    because the word is Python's."""

    # STRICT plus the alias options: `class` is the log's key (S-0059/D-6)
    model_config = ConfigDict(
        extra="forbid",
        use_attribute_docstrings=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    decision: str
    """The row the entry cites, `S-NNNN/D-n`; local inside the row's own document."""
    grade: EntryGrade
    """The row's grade as the executor read it, or UNLISTED when no document lists it."""
    kind: EntryKind = "resolved"
    """What happened: contradicted, departed, resolved or blocked."""
    entry_class: EntryClass = Field(default="discovery", alias="class")
    """`class` in the log: discovery, spec-gap, drift or irreducible."""
    at: str = ""
    """The instant the entry was written (S-0058/D-7)."""
    attempt: int = Field(default=1, ge=1)
    """The attempt that wrote it."""
    claim: str
    """What reality said."""
    evidence: str
    """A `path:line` citation or a backticked command with its output (S-0005/D-4)."""
    action: EntryAction
    """What the executor did: halted, departed or decided."""
    proposal: str = ""
    """The row the executor proposes for the author to append."""
    notes: str = ""
    """Anything else the reader of the log should know."""


class GradedRow(Item):
    """One inherited row as a landing keeps it (S-0059/D-10): the identifier
    and the grade the contract carried, which is what the `Torve-Decisions`
    trailer used to say and what outlives the task directory."""

    id: str
    """The row's identifier, `S-NNNN/D-n`; local inside its own document."""
    grade: Grade
    """The grade the contract inherited at mint."""


class Landing(Item):
    """What one task found, kept beside the rows it informs (S-0057/D-7): the
    task, its phase and attempt, the commit when the lander knew it (the
    runner's rides in the candidate commit whose trailers name the task,
    so it leaves the field empty), when, by whom, and the log's entries."""

    task: str
    """The task that landed, `T-NNNN`."""
    phase: int = 0
    """The phase its contract was minted from; 0 when none was."""
    attempt: int = Field(default=1, ge=1)
    """The attempt that landed."""
    base: str = ""
    """The commit the attempt built on, from the log's pin (S-0058/D-12)."""
    commit: str = ""
    """The commit the work landed in, when the lander knew it (S-0058/D-12)."""
    at: str = Field(pattern=INSTANT_PATTERN)
    """The instant of the landing, `YYYY-MM-DDTHH:MM:SSZ` (S-0058/D-7)."""
    agent: str = ""
    """Who landed it — the agent identity the runner composes, or a session."""
    decisions: list[GradedRow] = Field(default_factory=list)
    """The rows the contract carried, with their grades (S-0059/D-10)."""
    entries: list[LogEntry] = Field(default_factory=list)
    """The task log's entries, kept here beside the rows they cite (S-0057/D-7)."""

    def file_name(self) -> str:
        """`<task>-<attempt>-<instant>.yaml`, the landing's own file under
        `execution/` (S-0058/D-6)."""

        return f"{self.task}-{self.attempt}-{self.at.replace('-', '').replace(':', '')}.yaml"


class TaskLog(Item):
    """The task log's own shape, `.torve/tasks/T-NNNN/log.yaml` (S-0001
    §6): the pin, the derived drift count and the entries — what `torve
    init` writes the log's schema from (S-0057/D-5)."""

    schema_version: int
    """The log's shape version: 2 says `base` (S-0059/D-8); 1 said `base_sha`."""
    task: str
    """The task the log belongs to, `T-NNNN`."""
    repo: str = ""
    """The repository the evidence resolves against, `owner/name` — the pin (S-0001/D-36)."""
    base: str = ""
    """The commit the work started from — the pin's other half (S-0001/D-36)."""
    drift_count: int = 0
    """The entries classed drift, declared so the gate can compare its own count."""
    entries: list[LogEntry] = Field(default_factory=list)
    """The divergence entries, in the order they were written."""


# ....................... #


class Phase(Item):
    """One mintable unit of the phasing: what `torve plan` derives a
    contract from."""

    phase: int = Field(ge=1)
    """The phase number; the tasks of one phase are disjoint and may run in parallel."""
    title: str = Field(min_length=1)
    """The contract's short name (S-0007/A-1)."""
    intent: str = Field(min_length=1)
    """One paragraph: what changes and why — never steps (S-0001/D-7)."""
    scope: list[str] = Field(min_length=1)
    """The allow globs the contract gets; a LOCKED row's paths must fit inside them."""
    acceptance: list[str] = Field(default_factory=list)
    """The acceptance commands the contract gets; exit 0 is satisfied."""
    depends_on: list[int] = Field(default_factory=list)
    """The phases that must land first."""
    tier_variant: str = ""
    """The seat variant the contract routes to, when one is named (S-0027/D-3)."""
    character: Character | None = None
    """The phase's structural or routine character, copied onto its contracts (S-0034/D-1)."""


# ....................... #


class Document(Item):
    """One specification document, joined from its directory's files
    (S-0057/D-1): the header fields, the prose as keyed sections, the typed
    lists. `path` names the directory and `archived` marks a document
    loaded from the archive (S-0053/D-8): every identifier it defines still
    resolves, and nothing inherits from it. Both are the loader's, never
    written."""

    id: str = Field(pattern=DOCUMENT_ID.pattern)
    """`S-NNNN`, minted once and never reused (S-0016/D-17)."""
    title: str
    """The document's title."""
    kind: Kind = "design"
    """A design, or a convention that owes its summary alone (S-0055/A-2)."""
    status: Status
    """draft, accepted or superseded; only an accepted document mints or inherits."""
    implementation: Implementation = "none"
    """A judgement of what landed, never progress (S-0016/D-21)."""
    depends_on: list[str] = Field(default_factory=list)
    """The documents this one builds on; an archived one is a warning (S-0053/D-8)."""
    informed_by: list[str] = Field(default_factory=list)
    """The documents this one read without depending on them."""
    supersedes: list[str] = Field(default_factory=list)
    """The documents this one replaces."""
    superseded_by: str | None = None
    """The document that replaced this one."""
    retired: list[str] = Field(default_factory=list)
    """This document's own rows that no longer stand."""
    owner: str
    """Who answers for the document."""
    schema_version: int = SCHEMA_VERSION
    """The document's shape version: 4 is the typed anatomy (S-0058/D-4)."""
    path: str = Field(default="", exclude=True)
    """The directory the document was loaded from; the loader's, never written."""
    archived: bool = Field(default=False, exclude=True)
    """Whether the document was loaded from the archive (S-0053/D-8); the loader's."""

    # The prose, typed (S-0058/D-4): required of an accepted document but
    # `docs` and `out_of_scope`; `design` a keyed list with at least one
    # entry once accepted; `sections` the extras, at most EXTRAS_CAP.
    summary: str = ""
    """What the document decides; its first sentence routes (S-0058/D-4)."""
    motivation: str = ""
    """Why now — the defect or the gap, with what it costs."""
    current_state: str = ""
    """The tree as it stands, measured: files, lines, numbers."""
    goals: str = ""
    """What the document sets out to make true."""
    non_goals: str = ""
    """What it deliberately leaves alone."""
    design: list[DesignSection] = Field(default_factory=list)
    """The design, a keyed list of sections; at least one once accepted."""
    tests: str = ""
    """What proves it, by test file."""
    docs: str = ""
    """The pages and skills that change."""
    out_of_scope: str = ""
    """What a reader might expect here and will not find."""
    risks: str = ""
    """What could go wrong, each with its mitigation."""
    sections: list[DesignSection] = Field(default_factory=list)
    """The extra sections, at most EXTRAS_CAP."""
    decisions: list[Decision] = Field(default_factory=list)
    """The rows a contract inherits."""
    invariants: list[Invariant] = Field(default_factory=list)
    """What must hold, each with the command that proves it."""
    alternatives: list[Alternative] = Field(default_factory=list)
    """The options that were closed, and why."""
    questions: list[Question] = Field(default_factory=list)
    """What is left open, for the owner."""
    after: list[str] = Field(default_factory=list)
    """The documents whose landed tree this one's work builds on, as document ids
    — never phases (S-0085/D-1, S-0085/D-5); `depends_on` keeps its one meaning,
    decision inheritance, and says nothing about trees."""
    phasing: list[Phase] = Field(default_factory=list)
    """The mintable units `torve plan` derives contracts from."""
    contract_example: Task | None = None
    """One contract as the planner would mint it, for the reader."""
    amendments: list[Amendment] = Field(default_factory=list)
    """The amendments, in order; an accepted document changes through them alone."""
    editorial: list[Change] = Field(default_factory=list)
    """The editorial lane's record (S-0053/D-4): `torve spec fix` appends the before
    and after here, never an amendment number."""
    landings: list[Landing] = Field(default_factory=list)
    """What execution found (S-0057/D-7): the execution directory's files, read by the loader."""

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

            for graded in landing.decisions:
                graded.id = qualify(me, graded.id)

        self.retired = [qualify(me, r) for r in self.retired]

        return self

    def amended_by(self) -> list[str]:
        """The amendment identifiers, derived — never a field (S-0057/D-1)."""

        return [a.id for a in self.amendments]

    def routing(self) -> str:
        """The one line that routes a reader to this document."""

        return routing_line(self.summary)

    def prose(self) -> list[DesignSection]:
        """Every prose section in reading order: the typed keys that have
        content, the design, the extras — each as a keyed section."""

        typed = [
            DesignSection(key=prose_key(name), md=getattr(self, name))
            for name in PROSE_FIELDS
            if str(getattr(self, name)).strip()
        ]
        head = [
            s
            for s in typed
            if s.key in ("summary", "motivation", "current-state", "goals", "non-goals")
        ]
        tail = [s for s in typed if s not in head]

        return [*head, *self.design, *tail, *self.sections]

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
        ids.update(f"{self.id}/{s.key}" for s in self.prose())
        ids.update(self.retired)

        return ids


# ....................... #

# The directory's files by the hand that writes each (S-0057/D-1): the author's
# document and rows, the tool's amendments, the landing's execution. A key
# belongs to exactly one file; the loader joins them into one `Document`
# and the writer splits it back.
DOCUMENT_FILE = "document.yaml"
DECISIONS_FILE = "decisions.yaml"
PHASING_FILE = "phasing.yaml"
AMENDMENTS_FILE = "amendments.yaml"
FILES = (DOCUMENT_FILE, DECISIONS_FILE, PHASING_FILE, AMENDMENTS_FILE)
# Execution is a directory (S-0058/D-6): one landing per file, written
# once, named by task, attempt and instant, read sorted by instant.
EXECUTION_DIR = "execution"
LANDING_FILE = re.compile(r"^(T-\d{4})-(\d+)-(\d{8}T\d{6}Z)(?:-\d+)?\.yaml$")
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
        "schema_version",
        *PROSE_FIELDS[:5],
        "design",
        *PROSE_FIELDS[5:],
        "sections",
        "alternatives",
        "questions",
    ),
    DECISIONS_FILE: ("decisions", "invariants", "retired"),
    PHASING_FILE: ("after", "phasing", "contract_example"),
    AMENDMENTS_FILE: ("amendments", "editorial"),
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
