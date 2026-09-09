"""The loader of the specification model (RFC 0053 §5.1–§5.2, D-53.1,
D-53.2): the markdown corpus as it stands — frontmatter, the decision
table, the phasing fence, the contract example, amendment headings — plus
the five fenced kinds of §5.2, read into `torve.domain.spec.Document`.

Phase 1 of RFC 0053 (D-53.13): this module lands *beside* `rfc_parse`, and
the shared halves of the format are still read through it so the parity
test has one thing to assert against. Nothing else switches to the model
in this phase; every reader stays on the parser until phase 2, and the
parser is deleted in phase 5 once the archive has landed.

What this module owns that the parser never did:

- the fenced kinds, validated against their models with unknown keys
  refused (D-53.3);
- prose sections keyed by heading slug, sliced with fences respected — a
  `## ` line inside a code block is illustration, never a heading (the
  defect RFC 0054 §3 recorded against the parser);
- the amendment entries with their typed `changes` fence;
- the archive beside the corpus path (D-53.8), whose documents load as
  `archived=True` and still define every identifier they ever did;
- number derivation over corpus *and* archive (D-53.10);
- citation resolution over both, refused by name when a `cites` entry
  names nothing (RFC 0053 §6).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from pydantic import BaseModel, ValidationError

from torve.config import rfc_parse
from torve.domain.spec import (
    FENCE_KINDS,
    Alternative,
    Amendment,
    Change,
    Corpus,
    Decision,
    DecisionDetail,
    DesignSection,
    Document,
    Invariant,
    Phase,
    Question,
    is_citation,
)

# ----------------------- #

ARCHIVE_RELATIVE = Path("archive") / "rfcs"

FENCE = re.compile(r"^```ya?ml[ \t]+([a-z-]+)[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
FENCE_SPAN = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.M)
HEADING_NUMBER = re.compile(r"^\d+[a-z]?(\.\d+)*\.?\s+")
AMENDMENT_HEADING = re.compile(r"^### (A-\d+)\b(.*)$", re.M)
AMENDMENT_TITLE = re.compile(r"^\s*—\s*(\d{4}-\d{2}-\d{2})?\s*—?\s*(.*)$")

ModelT = TypeVar("ModelT", bound=BaseModel)

FENCE_MODELS: dict[str, type[BaseModel]] = {
    "decision-details": DecisionDetail,
    "invariants": Invariant,
    "alternatives": Alternative,
    "questions": Question,
    "changes": Change,
}


# ----------------------- #


class SpecError(ValueError):
    """A document that does not load: each message names the file, the
    fence or section, and the field — the shape the corpus check reports."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# ----------------------- #


def slugify(heading: str) -> str:
    """The stable key of a prose section: the heading with its number
    stripped, lowercased, punctuation folded — what a log cites instead of
    a section number that moves when a paragraph is added."""

    bare = HEADING_NUMBER.sub("", heading).strip()

    return re.sub(r"[^a-z0-9]+", "-", bare.lower()).strip("-")


# ....................... #


def fence_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in FENCE_SPAN.finditer(text)]


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


# ....................... #


def headings(text: str) -> list[tuple[int, int, str]]:
    """Every `##`/`###` heading outside a fence: (position, level, raw)."""

    spans = fence_spans(text)

    return [
        (m.start(), len(m.group(1)), m.group(2))
        for m in HEADING.finditer(text)
        if not _inside(m.start(), spans)
    ]


# ....................... #


def fences(text: str, name: str) -> list[tuple[str, str]]:
    """Every fence of one kind as (kind, body)."""

    return [(m.group(1), m.group(2)) for m in FENCE.finditer(text) if m.group(1) == name]


# ....................... #


def _load_fence(
    kind: str, body: str, model: type[ModelT], where: str
) -> tuple[list[ModelT], list[str]]:
    """One fence body as validated entries; a problem names the fence, the
    entry and the field, and an unknown key is a problem, never a skip."""

    try:
        raw: Any = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return [], [f"{where}: `yaml {kind}` fence is not YAML — {exc}"]

    if raw is None:
        return [], []

    if not isinstance(raw, list):
        return [], [f"{where}: `yaml {kind}` fence must be a list of entries"]

    items: list[ModelT] = []
    problems: list[str] = []

    for index, entry in enumerate(cast("list[object]", raw)):
        try:
            items.append(model.model_validate(entry))
        except ValidationError as exc:
            for error in exc.errors():
                loc = ".".join(str(part) for part in error["loc"]) or "entry"
                problems.append(f"{where}: `yaml {kind}` entry {index + 1}, {loc}: {error['msg']}")

    return items, problems


# ....................... #


def load_fences(text: str, where: str) -> tuple[dict[str, list[Any]], list[str]]:
    """Every typed fence in a document, by kind. `changes` fences are read
    per amendment by `load_amendments`; here they only count as known."""

    found: dict[str, list[Any]] = {kind: [] for kind in FENCE_KINDS}
    problems: list[str] = []

    for match in FENCE.finditer(text):
        kind = match.group(1)

        if kind == "contract-example":
            continue

        if kind not in FENCE_MODELS:
            problems.append(
                f"{where}: `yaml {kind}` is not a fenced kind the model knows "
                f"({', '.join(FENCE_KINDS)})"
            )
            continue

        if kind == "changes":
            continue

        items, fence_problems = _load_fence(kind, match.group(2), FENCE_MODELS[kind], where)
        found[kind].extend(items)
        problems.extend(fence_problems)

    return found, problems


# ....................... #


def load_sections(text: str) -> list[DesignSection]:
    """Prose sections keyed by heading slug, fence-aware. The body runs to
    the next heading of any level; fences inside stay in the body."""

    found = headings(text)
    sections: list[DesignSection] = []

    for order, (pos, level, raw) in enumerate(found):
        end = found[order + 1][0] if order + 1 < len(found) else len(text)
        body_start = text.find("\n", pos)
        body = text[body_start + 1 : end].strip("\n") if body_start != -1 else ""
        sections.append(
            DesignSection(key=slugify(raw), heading=raw, level=level, order=order, md=body)
        )

    return sections


# ....................... #


def load_amendments(text: str, where: str) -> tuple[list[Amendment], list[str]]:
    """The `### A-n — date — title` entries under `## Amendments`, each with
    the typed diff its `yaml changes` fence carries (D-53.4) and its words."""

    section = rfc_parse.AMENDMENTS_SECTION.search(text)

    if not section:
        return [], []

    tail = text[section.end() :]
    spans = fence_spans(tail)
    marks = [m for m in AMENDMENT_HEADING.finditer(tail) if not _inside(m.start(), spans)]
    entries: list[Amendment] = []
    problems: list[str] = []

    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(tail)
        body = tail[mark.end() : end].strip("\n")
        title_match = AMENDMENT_TITLE.match(mark.group(2))
        at: date | None = None
        title = ""

        if title_match:
            if title_match.group(1):
                at = date.fromisoformat(title_match.group(1))
            title = title_match.group(2).strip()

        changes: list[Change] = []

        for _, fence_body in fences(body, "changes"):
            items, fence_problems = _load_fence(
                "changes", fence_body, Change, f"{where} {mark.group(1)}"
            )
            changes.extend(items)
            problems.extend(fence_problems)

        entries.append(
            Amendment(id=mark.group(1), at=at, title=title, changes=changes, body_md=body)
        )

    return entries, problems


# ....................... #


def _join_details(
    rows: list[rfc_parse.DecisionRow], details: list[DecisionDetail], where: str
) -> tuple[list[Decision], list[str]]:
    by_id: dict[str, DecisionDetail] = {}
    problems: list[str] = []

    for detail in details:
        if detail.id in by_id:
            problems.append(f"{where}: `yaml decision-details` names {detail.id} twice")
        by_id[detail.id] = detail

    known = {row.identifier for row in rows}

    for identifier in by_id:
        if identifier not in known:
            problems.append(
                f"{where}: `yaml decision-details` names {identifier}, which the table does not define"
            )

    decisions: list[Decision] = []

    for row in rows:
        joined = by_id.get(row.identifier)
        decisions.append(
            Decision(
                id=row.identifier,
                grade=cast("Any", row.grade),
                text=row.text,
                paths=list(row.paths),
                consequence=row.consequence,
                rationale=joined.rationale if joined else "",
                cites=list(joined.cites) if joined else [],
                check=joined.check if joined else None,
                superseded_by=joined.superseded_by if joined else None,
            )
        )

    return decisions, problems


# ....................... #


def load_document(path: Path, *, archived: bool = False) -> Document:
    """One document from its file. Raises `SpecError` naming every problem
    found, so a document that does not load says why in one pass."""

    text = path.read_text(encoding="utf-8")
    where = path.name
    problems: list[str] = []
    fm = rfc_parse.parse_frontmatter(text)

    if fm is None:
        raise SpecError([f"{where}: no YAML frontmatter"])

    rows = rfc_parse.decision_table(text)
    found, fence_problems = load_fences(text, where)
    problems.extend(fence_problems)
    decisions, join_problems = _join_details(rows, found["decision-details"], where)
    problems.extend(join_problems)
    amendments, amendment_problems = load_amendments(text, where)
    problems.extend(amendment_problems)

    phasing: list[Phase] = []

    try:
        entries = rfc_parse.parse_phasing(text) or []
        phasing = [Phase.model_validate(entry.model_dump()) for entry in entries]
    except (ValueError, ValidationError) as exc:
        problems.append(f"{where}: Phasing — {exc}")

    contract_example = None

    try:
        contract_example = rfc_parse.parse_contract_example(text)
    except (ValueError, ValidationError) as exc:
        problems.append(f"{where}: Contract example — {exc}")

    if problems:
        raise SpecError(problems)

    fields: dict[str, Any] = {
        key: fm.get(key)
        for key in (
            "id",
            "title",
            "kind",
            "status",
            "implementation",
            "depends_on",
            "informed_by",
            "supersedes",
            "superseded_by",
            "amended_by",
            "retired",
            "owner",
            "description",
            "schema_version",
        )
        if fm.get(key) is not None
    }

    for key in ("depends_on", "informed_by", "supersedes", "amended_by", "retired"):
        if key in fields:
            fields[key] = [str(item) for item in cast("list[object]", fields[key])]

    if "id" in fields:
        fields["id"] = str(fields["id"])

    if fields.get("superseded_by") is not None:
        fields["superseded_by"] = str(fields["superseded_by"])

    try:
        return Document(
            **fields,
            path=str(path),
            archived=archived,
            decisions=decisions,
            invariants=found["invariants"],
            alternatives=found["alternatives"],
            questions=found["questions"],
            phasing=phasing,
            contract_example=contract_example,
            sections=load_sections(text),
            amendments=amendments,
        )
    except ValidationError as exc:
        raise SpecError(
            [
                f"{where}: frontmatter {'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ]
        ) from exc


# ....................... #


def archive_dir(rfc_dir: Path) -> Path:
    """The archive beside the corpus path (D-53.8): `archive/rfcs/` under
    the corpus path's parent. May not exist; that is not a problem."""

    return rfc_dir.parent / ARCHIVE_RELATIVE


# ....................... #


def next_number(rfc_dir: Path) -> int:
    """The maximum over corpus and archive, plus one (D-53.10): a number
    retired into the archive is still a number that was cited."""

    taken = [int(n) for n in rfc_parse.rfc_files(rfc_dir)]
    archive = archive_dir(rfc_dir)

    if archive.is_dir():
        taken += [int(n) for n in rfc_parse.rfc_files(archive)]

    return max(taken, default=0) + 1


# ....................... #


def check_citations(corpus: Corpus) -> list[str]:
    """Every `cites` entry of every row and alternative resolves to an
    identifier the corpus or the archive defines; anything else is a typo,
    refused by name (RFC 0053 §6)."""

    defined = corpus.defined_identifiers()
    problems: list[str] = []

    for doc in corpus.documents:
        where = Path(doc.path).name if doc.path else doc.id

        for row in doc.decisions:
            for cite in row.cites:
                if not is_citation(cite):
                    problems.append(f"{where}: {row.id} cites {cite!r}, which is not an identifier")
                elif cite not in defined:
                    problems.append(f"{where}: {row.id} cites {cite}, which nothing defines")

        for alternative in doc.alternatives:
            for cite in alternative.cites:
                if not is_citation(cite):
                    problems.append(
                        f"{where}: alternative {alternative.option[:40]!r} cites {cite!r}, "
                        "which is not an identifier"
                    )
                elif cite not in defined:
                    problems.append(
                        f"{where}: alternative {alternative.option[:40]!r} cites {cite}, "
                        "which nothing defines"
                    )

    return problems


# ....................... #


def load_corpus(rfc_dir: Path) -> Corpus:
    """The corpus path and the archive beside it as one `Corpus`. Every
    document's problems are collected before anything is raised, and an
    unresolvable citation anywhere is a problem for the whole load."""

    problems: list[str] = []
    documents: list[Document] = []

    for source, archived in ((rfc_dir, False), (archive_dir(rfc_dir), True)):
        if not source.is_dir():
            continue

        for _, path in sorted(rfc_parse.rfc_files(source).items()):
            try:
                documents.append(load_document(path, archived=archived))
            except SpecError as exc:
                problems.extend(exc.problems)

    corpus = Corpus(documents=documents)
    problems.extend(check_citations(corpus))

    if problems:
        raise SpecError(problems)

    return corpus
