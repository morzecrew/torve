"""The specification's storage, owned by the package (RFC 0007 §3a,
D-7.12; RFC 0056 D-56.1): a document is one YAML file in the `Document`
model's own shape, loaded by the model's validator and checked here for
what the model cannot say — identifier continuity, the dependency graph,
citations into the archive, path rot, fingerprint drift, and the comment
the file must not carry.

Nothing in this module parses anything but YAML. The markdown loader
that stood here through RFC 0053 converted every document once and was
deleted with them (D-56.5); `config/rfc_emit.py` is the one writer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from torve.domain.rfc import GRADES
from torve.domain.spec import (
    SCHEMA_VERSION,
    Corpus,
    Document,
    is_citation,
)

# ----------------------- #

RFC_FILENAME = re.compile(r"^(\d{4})-([a-z0-9-]+)\.yaml$")
NUMBER_ONLY = re.compile(r"^\d{4}(\.yaml|\.md)?$")

# The one comment a document carries: its first line, naming the schema an
# editor validates it against (D-56.6). Everything else is meaning outside
# the model and is refused (D-56.4).
SCHEMA_HEADER = "# yaml-language-server: $schema="
SCHEMA_RELATIVE = "schema/document.json"

URI_OR_PROTOCOL_RELATIVE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
LOCAL_LINK = re.compile(r"\[[^\]]*\]\((?!#)([^)\s]+)")
LINE_CITE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9_]+):(\d+)")
FENCED_BLOCK = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)
DECISION_CITE = re.compile(r"\bD-[A-Za-z0-9]+\.\d+[a-z]?\b")

ARCHIVE_RELATIVE = Path("archive") / "rfcs"


# ----------------------- #


class SpecError(ValueError):
    """A document that does not load: each message names the file, the
    list entry and the field — the shape the corpus check reports."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    count: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


# ----------------------- #
# Files


def archive_dir(rfc_dir: Path) -> Path:
    return rfc_dir.parent / ARCHIVE_RELATIVE


def rfc_files(rfc_dir: Path) -> dict[str, Path]:
    """Zero-padded id -> file. Duplicate numbers are reported by check."""

    found: dict[str, Path] = {}

    for path in sorted(rfc_dir.glob("*.yaml")):
        match = RFC_FILENAME.match(path.name)

        if match:
            found.setdefault(match.group(1), path)

    return found


def archive_files(rfc_dir: Path) -> dict[str, Path]:
    """Zero-padded id -> file, over the archive; empty when there is none."""

    archive = archive_dir(rfc_dir)

    return rfc_files(archive) if archive.is_dir() else {}


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def schema_header(archived: bool = False) -> str:
    """The first line of a document: the schema, relative to where the
    document lives."""

    relative = f"../../rfcs/{SCHEMA_RELATIVE}" if archived else SCHEMA_RELATIVE

    return f"{SCHEMA_HEADER}{relative}"


# ----------------------- #
# Loading


def load_document(path: Path, *, archived: bool = False) -> Document:
    """One document from its file: YAML into the model, every refusal
    named by file, entry and field (D-53.3). A document of another schema
    version is refused with the conversion named, never half-read."""

    where = path.name

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError([f"{where}: not YAML — {str(exc).splitlines()[0]}"]) from None

    if not isinstance(raw, dict):
        raise SpecError([f"{where}: the document is not a mapping"])

    data = cast("dict[str, Any]", raw)
    version = data.get("schema_version")

    if version != SCHEMA_VERSION:
        raise SpecError(
            [
                (
                    f"{where}: schema_version {version!r} — this loader reads {SCHEMA_VERSION}; "
                    "a markdown document converts once through RFC 0056 phase 1"
                )
            ]
        )

    for key in ("path", "archived"):
        if key in data:
            raise SpecError([f"{where}: {key} is the loader's, never written"])

    try:
        return Document.model_validate({**data, "path": str(path), "archived": archived})
    except ValidationError as exc:
        raise SpecError(
            [
                f"{where}: {'.'.join(str(p) for p in e['loc']) or 'document'}: {e['msg']}"
                for e in exc.errors()
            ]
        ) from None


def load_corpus(rfc_dir: Path) -> Corpus:
    """The corpus path and the archive beside it as one `Corpus`. Every
    document's problems are collected before anything is raised, and an
    unresolvable citation anywhere is a problem for the whole load."""

    problems: list[str] = []
    documents: list[Document] = []

    for source, archived in ((rfc_dir, False), (archive_dir(rfc_dir), True)):
        if not source.is_dir():
            continue

        for _, path in sorted(rfc_files(source).items()):
            try:
                documents.append(load_document(path, archived=archived))
            except SpecError as exc:
                problems.extend(exc.problems)

    corpus = Corpus(documents=documents)
    problems.extend(check_cites(corpus))

    if problems:
        raise SpecError(problems)

    return corpus


# ----------------------- #
# Numbers


def next_number(rfc_dir: Path) -> int:
    """The maximum over corpus and archive, plus one (D-53.10): a number
    retired into the archive is still a number that was cited."""

    taken = [int(n) for n in rfc_files(rfc_dir)] + [int(n) for n in archive_files(rfc_dir)]

    return max(taken, default=0) + 1


def next_amendment(files: dict[str, Path], archived: dict[str, Path] | None = None) -> str:
    """The next free global amendment number (D-A.5), derived over the
    corpus and the archive (A-152) — never chosen."""

    taken: list[int] = []

    for path in (*files.values(), *(archived or {}).values()):
        try:
            doc = load_document(path)
        except SpecError:
            continue

        taken += [int(a.id[2:]) for a in doc.amendments if re.fullmatch(r"A-\d+", a.id)]

    return f"A-{max(taken, default=0) + 1}"


def next_decision(files: dict[str, Path], number: str) -> str:
    """The next free identifier in one document's own dotted family, scanned
    corpus-wide (D-A.4); retired identifiers count as taken (D-16.1)."""

    family = str(int(number))
    pattern = re.compile(rf"^D-{re.escape(family)}\.(\d+)[a-z]?$")
    taken: list[int] = []

    for path in files.values():
        try:
            doc = load_document(path)
        except SpecError:
            continue

        for ident in (*(row.id for row in doc.decisions), *doc.retired):
            match = pattern.match(ident)

            if match:
                taken.append(int(match.group(1)))

    return f"D-{family}.{max(taken, default=0) + 1}"


# ----------------------- #
# Reading one identifier


def _name(doc: Document) -> str:
    return Path(doc.path).name if doc.path else doc.id


def _prose(doc: Document) -> str:
    """Every string a person wrote, fences stripped: what a citation, a
    link or a line number is looked for in."""

    parts = [s.md for s in doc.sections] + [a.md for a in doc.amendments]
    parts += [f"{r.text} {r.consequence} {r.rationale}" for r in doc.decisions]
    parts += [f"{a.option} {a.rejected_because}" for a in doc.alternatives]
    parts += [q.text for q in doc.questions] + [i.statement for i in doc.invariants]

    return FENCED_BLOCK.sub("", "\n".join(parts))


def _cites(ident: str) -> re.Pattern[str]:
    # As a word: not inside a longer identifier, not a dotted child (D-2
    # must not match inside D-2.10), not a digit-extended sibling.
    return re.compile(rf"(?<![\w.]){re.escape(ident)}(?![\w.])")


def lookup(rfc_dir: Path, identifier: str) -> dict[str, Any] | None:
    """One corpus identifier resolved from the same load `check` runs
    (D-7.28): a row as it stands, an invariant, a question, an amendment
    or a document; an archived one answers marked archived (D-53.9). None
    when nothing defines it."""

    try:
        corpus = load_corpus(rfc_dir)
    except SpecError:
        return None

    return lookup_in(corpus, identifier)


def lookup_in(corpus: Corpus, identifier: str) -> dict[str, Any] | None:
    number = identifier.strip().removesuffix(".yaml").removesuffix(".md")

    if re.fullmatch(r"\d{1,4}", number):
        doc = corpus.document(number.zfill(4))

        return None if doc is None else _document_payload(doc)

    cites = _cites(identifier)
    cited_by = [_name(d) for d in corpus.documents if cites.search(_prose(d))]

    for doc in corpus.documents:
        for amendment in doc.amendments:
            if amendment.id == identifier:
                rows = [
                    row.id
                    for other in corpus.documents
                    for row in other.decisions
                    if cites.search(row.text) or any(c.subject == row.id for c in amendment.changes)
                ]

                return {
                    "kind": "amendment",
                    "identifier": identifier,
                    "defined_in": _name(doc),
                    "heading": f"{amendment.id} — {amendment.at or ''} — {amendment.title}",
                    "title": amendment.title,
                    "at": str(amendment.at or ""),
                    "changes": [c.model_dump(mode="json") for c in amendment.changes],
                    "rows": sorted(set(rows)),
                    "archived": doc.archived,
                }

        row = doc.decision(identifier)

        if row is not None:
            return {
                "kind": "decision",
                "identifier": row.id,
                "defined_in": _name(doc),
                "cited_by": [name for name in cited_by if name != _name(doc)],
                "retired_in": None,
                "grade": row.grade,
                "text": row.text,
                "paths": list(row.paths),
                "consequence": row.consequence,
                "rationale": row.rationale,
                "cites": list(row.cites),
                "check": row.check,
                "check_state": row.check_state,
                "check_twin": row.check_twin,
                "fingerprint": row.fingerprint,
                "archived": doc.archived,
            }

        for invariant in doc.invariants:
            if invariant.id == identifier:
                return {
                    "kind": "invariant",
                    "identifier": invariant.id,
                    "statement": invariant.statement,
                    "paths": list(invariant.paths),
                    "check": invariant.check,
                    "defined_in": _name(doc),
                    "archived": doc.archived,
                }

        for question in doc.questions:
            if question.id == identifier:
                return {
                    "kind": "question",
                    "identifier": question.id,
                    "text": question.text,
                    "status": question.status,
                    "settled_by": question.settled_by,
                    "defined_in": _name(doc),
                    "archived": doc.archived,
                }

    for doc in corpus.documents:
        if identifier in doc.retired:
            return {
                "kind": "decision",
                "identifier": identifier,
                "defined_in": None,
                "cited_by": cited_by,
                "retired_in": _name(doc),
                "archived": doc.archived,
            }

    return None


def _document_payload(doc: Document) -> dict[str, Any]:
    return {
        "kind": "document",
        "identifier": doc.id,
        "file": _name(doc),
        "title": doc.title,
        "status": doc.status,
        "implementation": doc.implementation,
        "depends_on": list(doc.depends_on),
        "superseded_by": doc.superseded_by,
        "amended_by": list(doc.amended_by),
        "description": doc.description.strip(),
        "sections": [s.key for s in doc.sections],
        "phases": [
            {"phase": p.phase, "title": p.title, "depends_on": list(p.depends_on)}
            for p in doc.phasing
        ],
        "archived": doc.archived,
    }


# ----------------------- #
# Checks the model cannot express


def check_cites(corpus: Corpus) -> list[str]:
    """Every `cites` entry of every row and alternative resolves to an
    identifier the corpus or the archive defines (RFC 0053 §6)."""

    defined = corpus.defined_identifiers()
    problems: list[str] = []

    for doc in corpus.documents:
        where = _name(doc)
        citing = [(row.id, row.cites) for row in doc.decisions]
        citing += [(f"alternative {a.option[:40]!r}", a.cites) for a in doc.alternatives]

        for who, cites in citing:
            for cite in cites:
                if not is_citation(cite):
                    problems.append(f"{where}: {who} cites {cite!r}, which is not an identifier")
                elif cite not in defined:
                    problems.append(f"{where}: {who} cites {cite}, which nothing defines")

    return problems


def find_comments(text: str) -> list[int]:
    """Line numbers (1-based) of every comment but the schema header. A
    line is a comment when dropping it — or its ` #…` tail — leaves the
    loaded document unchanged: a `#` inside a block scalar changes what
    loads, a comment never does (D-56.4)."""

    lines = text.splitlines()

    try:
        baseline = yaml.safe_load(text)
    except yaml.YAMLError:
        return []

    found: list[int] = []

    for index, line in enumerate(lines):
        if index == 0 and line.startswith(SCHEMA_HEADER):
            continue

        stripped = line.lstrip()

        if stripped.startswith("#"):
            probe = [*lines[:index], *lines[index + 1 :]]
        elif " #" in line:
            probe = [*lines[:index], line[: line.index(" #")], *lines[index + 1 :]]
        else:
            continue

        try:
            if yaml.safe_load("\n".join(probe)) == baseline:
                found.append(index + 1)
        except yaml.YAMLError:
            continue

    return found


def _glob_matches(root: Path, pattern: str) -> bool:
    try:
        return next(root.glob(pattern), None) is not None
    except (ValueError, NotImplementedError):
        return False


def check_directory(rfc_dir: Path) -> list[str]:
    """Only documents live in the corpus path (D-A.17), one per number."""

    problems: list[str] = []
    numbers: dict[str, list[str]] = {}

    for entry in sorted(rfc_dir.iterdir()):
        if entry.name in ("schema",) and entry.is_dir():
            continue

        if entry.is_dir():
            problems.append(
                f"{entry.name}/: a subdirectory in the corpus path — documents are flat"
            )
            continue

        if entry.name == "INDEX.md":
            problems.append("INDEX.md: the index is a query now (`torve rfc list`) — delete it")
            continue

        if entry.suffix == ".md":
            problems.append(
                f"{entry.name}: a markdown document — convert it (RFC 0056 D-56.5); "
                "the corpus is YAML"
            )
            continue

        match = RFC_FILENAME.match(entry.name)

        if match is None:
            problems.append(f"{entry.name}: not NNNN-slug.yaml — a stray file in the corpus path")
            continue

        numbers.setdefault(match.group(1), []).append(entry.name)

    for number, names in sorted(numbers.items()):
        if len(names) > 1:
            problems.append(f"RFC {number} is claimed by {len(names)} files: {', '.join(names)}")

    return problems


def check_document(doc: Document, root: Path, rfc_dir: Path) -> tuple[list[str], list[str]]:
    """One document's own problems and warnings, given it loaded."""

    where = _name(doc)
    problems: list[str] = []
    warnings: list[str] = []
    match = RFC_FILENAME.match(where)

    if match is not None and match.group(1) != doc.id:
        problems.append(f"{where}: id {doc.id!r} disagrees with the filename")

    if match is not None:
        slug_words = {w for w in match.group(2).split("-") if len(w) >= 4}
        title_words = {w for w in slugify(doc.title).split("-") if len(w) >= 4}

        if slug_words and title_words and not slug_words & title_words:
            warnings.append(
                f"{where}: filename slug shares no word with title {doc.title!r} — "
                "a materially different title is usually a new document (D-A.20)"
            )

    if doc.status == "superseded" and not doc.superseded_by:
        problems.append(f"{where}: superseded, but superseded_by names nothing")

    if [a.id for a in doc.amendments] != list(doc.amended_by):
        problems.append(
            f"{where}: amended_by {list(doc.amended_by)} does not match the amendments "
            f"{[a.id for a in doc.amendments]}"
        )

    keys = [s.key for s in doc.sections]

    for key in sorted({k for k in keys if keys.count(k) > 1}):
        problems.append(f"{where}: two sections keyed {key!r} — one of them is misnamed")

    # D-32: for a document not yet built the globs name intended areas;
    # once implemented an unmatched LOCKED glob is rot.
    check_globs = doc.status == "accepted" and doc.implementation != "none" and not doc.archived
    unbuilt: list[str] = []

    for row in doc.decisions:
        if row.grade not in GRADES:
            problems.append(f"{where}: row {row.id!r} has grade {row.grade!r}")

        if row.grade != "LOCKED":
            continue

        if not row.paths:
            problems.append(
                f"{where}: LOCKED row {row.id!r} declares no paths — the silence check "
                "skips it and the lock protects nothing"
            )
            continue

        if not check_globs:
            continue

        for pattern in row.paths:
            if _glob_matches(root, pattern):
                continue

            if doc.implementation == "complete":
                problems.append(
                    f"{where}: LOCKED row {row.id!r} paths glob {pattern!r} matches nothing "
                    "in the repository (an implemented RFC cites real areas, D-32)"
                )
            else:
                unbuilt.append(f"{row.id} -> {pattern}")

    if unbuilt:
        warnings.append(
            f"{where}: {len(unbuilt)} LOCKED glob(s) name unbuilt areas — intended modules "
            "awaiting implementation (D-32): " + "; ".join(unbuilt)
        )

    for row in doc.decisions:
        if row.fingerprint and "/" not in row.fingerprint:
            problems.append(f"{where}: {row.id}'s fingerprint is not `<content>/<rule>`")

    prose = _prose(doc)
    base = root.resolve()

    for link in LOCAL_LINK.finditer(prose):
        target = link.group(1).split("#", 1)[0]

        if not target or URI_OR_PROTOCOL_RELATIVE.match(target):
            continue

        for start in (rfc_dir, root):
            try:
                resolved = (start / target).resolve()
            except OSError:
                continue

            if resolved.is_relative_to(base) and resolved.exists():
                break
        else:
            warnings.append(
                f"{where}: link target {target!r} does not resolve inside the repository"
            )

    for cite in LINE_CITE.finditer(prose):
        if (root / cite.group(1)).is_file():
            problems.append(
                f"{where}: cites {cite.group(1)}:{cite.group(2)} — line numbers rot at the "
                "first refactor above them (0007 §3a); cite the path alone"
            )

    return problems, warnings


def check_graph(documents: dict[str, Document]) -> tuple[list[str], list[str]]:
    """Cycles in `depends_on` are problems, and so is an accepted document
    depending on one that is not accepted (D-A.10)."""

    edges = {n: [d for d in doc.depends_on if d in documents] for n, doc in documents.items()}
    problems: list[str] = []
    seen_cycles: set[frozenset[str]] = set()
    state: dict[str, int] = {}

    def visit(number: str, trail: list[str]) -> None:
        state[number] = 1

        for target in edges.get(number, []):
            if state.get(target) == 1:
                cycle = [*trail[trail.index(target) :], target]

                if frozenset(cycle) not in seen_cycles:
                    seen_cycles.add(frozenset(cycle))
                    problems.append(
                        "depends_on cycle: " + " -> ".join(cycle) + " — the graph must be acyclic"
                    )
            elif state.get(target) != 2:
                visit(target, [*trail, target])

        state[number] = 2

    for number in sorted(edges):
        if state.get(number) != 2:
            visit(number, [number])

    for number, doc in sorted(documents.items()):
        if doc.status != "accepted":
            continue

        for target in edges[number]:
            status = documents[target].status

            if status != "accepted":
                problems.append(
                    f"{_name(doc)}: accepted but depends_on {target} which is {status} — "
                    "no inheritance from a non-accepted document (D-A.10)"
                )

    return problems, []


def check_corpus(rfc_dir: Path, root: Path) -> CheckReport:
    """The whole of `torve rfc check` over one corpus directory: the
    directory, every document's load, its own checks, the comments it
    must not carry, identifiers unique and never reused, citations that
    resolve into the corpus or the archive, and the graph."""

    report = CheckReport()
    report.problems += check_directory(rfc_dir)
    documents: dict[str, Document] = {}
    archived: dict[str, Document] = {}

    for source, is_archive, into in (
        (rfc_dir, False, documents),
        (archive_dir(rfc_dir), True, archived),
    ):
        if not source.is_dir():
            continue

        for number, path in sorted(rfc_files(source).items()):
            for line in find_comments(path.read_text(encoding="utf-8")):
                report.problems.append(
                    f"{path.name}:{line}: a comment — meaning outside the model; a row that "
                    "needs a note needs a rationale (D-56.4)"
                )

            try:
                into[number] = load_document(path, archived=is_archive)
            except SpecError as exc:
                report.problems += exc.problems

    report.count = len(documents)
    corpus = Corpus(documents=[*documents.values(), *archived.values()])
    report.problems += check_cites(corpus)
    resolvable = corpus.defined_identifiers() | {f"D-{n}" for n in archived}
    retired: dict[str, str] = {}
    seen: dict[str, str] = {}

    for doc in corpus.documents:
        for ident in doc.retired:
            retired.setdefault(ident, _name(doc))

    for doc in [documents[n] for n in sorted(documents)]:
        where = _name(doc)
        problems, warnings = check_document(doc, root, rfc_dir)
        report.problems += problems
        report.warnings += warnings

        for ident in sorted({row.id for row in doc.decisions} | {i.id for i in doc.invariants}):
            if ident in seen:
                report.problems.append(
                    f"{where}: identifier {ident!r} already used in {seen[ident]} — "
                    "identifiers are permanent and corpus-unique (D-A.4)"
                )
            elif ident in retired:
                report.problems.append(
                    f"{where}: defines {ident}, which {retired[ident]} retired — "
                    "identifiers are never reused (D-A.19, D-16.1)"
                )

            seen[ident] = where

        for fname in ("depends_on", "informed_by", "supersedes"):
            for ref in getattr(doc, fname):
                if ref in documents:
                    continue

                if ref in archived:
                    report.warnings.append(
                        f"{where}: {fname} names {ref}, which is archived "
                        f"({_name(archived[ref])}) — nothing inherits from it (D-53.8)"
                    )
                else:
                    report.problems.append(f"{where}: {fname} names {ref!r}, no such RFC")

        reported: set[str] = set()

        for cite in DECISION_CITE.finditer(_prose(doc)):
            cited = cite.group(0)

            if cited not in resolvable and cited not in reported:
                reported.add(cited)
                report.problems.append(
                    f"{where}: cites {cited}, which no document in the corpus defines "
                    "and no `retired` list records"
                )

    graph_problems, graph_warnings = check_graph(documents)
    report.problems += graph_problems
    report.warnings += graph_warnings

    return report
