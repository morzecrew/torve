"""The specification's storage, owned by the package (RFC 0007 §3a,
D-7.12; RFC 0057 D-57.1): a document is a directory of four YAML files
split by who writes each — `document.yaml` and `decisions.yaml` the
author's, `amendments.yaml` the tool's, `execution.yaml` the landing's —
joined here into the `Document` model by the model's validator and checked
for what the model cannot say: identifier continuity, the dependency
graph, citations into the archive, path rot, fingerprint drift, a section
that carries what a typed list holds, and the comment a file must not
carry.

Nothing in this module parses anything but YAML. `config/spec_emit.py` is
the one writer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from torve.domain.rfc import GRADES
from torve.domain.spec import (
    AMENDMENTS_FILE,
    DECISIONS_FILE,
    DOCUMENT_FILE,
    EXECUTION_FILE,
    FILE_FIELDS,
    FILES,
    SCHEMA_VERSION,
    Corpus,
    Document,
    file_of,
    is_citation,
)

# ----------------------- #

# A document's directory is its identifier and nothing else (D-57.1): the
# title lives in `document.yaml`, and `spec list` shows it.
DOCUMENT_DIRNAME = re.compile(r"^S-(\d{4})$")
NUMBER_ONLY = re.compile(r"^\d{4}(\.yaml|\.md)?$")

# The one comment a file carries: its first line, naming the schema an
# editor validates it against (D-56.6, D-57.5). Everything else is meaning
# outside the model and is refused (D-56.4).
SCHEMA_HEADER = "# yaml-language-server: $schema="

URI_OR_PROTOCOL_RELATIVE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
LOCAL_LINK = re.compile(r"\[[^\]]*\]\((?!#)([^)\s]+)")
LINE_CITE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9_]+):(\d+)")
FENCED_BLOCK = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)
DECISION_CITE = re.compile(r"\bD-[A-Za-z0-9]+\.\d+[a-z]?\b")

# A citation as code and docs spell it (D-57.9): a dotted row (or the
# charter's D-A.n), an invariant, a question, an amendment — never the bare
# `D-n`, which prose uses for other things. Scanned over what git tracks
# under these roots and names; never tests or skills, whose fixtures
# invent identifiers by design.
TREE_CITE = re.compile(
    r"(?<![\w.])(D-[A-Za-z0-9]+\.\d+[a-z]?|I-\d+\.\d+|Q-\d+\.\d+|A-\d+)(?![\w.])"
)
SCAN_ROOTS = ("src/", "pages/")
SCAN_NAMES = ("AGENTS.md", "CLAUDE.md", "README.md")

# What a section may not carry (D-57.2): a typed list restated as the fence
# or table it was lifted from, or an amendment's words under a section key.
TYPED_KINDS = (
    "alternatives",
    "questions",
    "decision-details",
    "invariants",
    "changes",
    "contract-example",
)
TYPED_FENCE = re.compile(r"^```\s*ya?ml\s+(" + "|".join(TYPED_KINDS) + r")\b", re.M)
TABLE_HEADER = re.compile(r"^\|\s*#\s*\|\s*Grade\s*\|", re.M)
AMENDMENT_KEY = re.compile(r"^a-\d+(?:-|$)")


# ----------------------- #


class SpecError(ValueError):
    """A document that does not load: each message names the directory, the
    file, the list entry and the field — the shape the corpus check
    reports."""

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
# Directories


def archive_dir(spec_dir: Path) -> Path:
    """The archive beside the corpus (D-57.3): `.torve/archive/` for
    `.torve/specs/`."""

    return spec_dir.parent / "archive"


def schemas_dir(spec_dir: Path) -> Path:
    """Where `torve init` writes the schemas (D-57.5): `.torve/schemas/`
    for `.torve/specs/`."""

    return spec_dir.parent / "schemas"


def document_dirs(spec_dir: Path) -> dict[str, Path]:
    """Zero-padded id -> directory. Duplicate numbers are reported by check."""

    found: dict[str, Path] = {}

    if not spec_dir.is_dir():
        return found

    for path in sorted(spec_dir.iterdir()):
        match = DOCUMENT_DIRNAME.match(path.name)

        if match and path.is_dir():
            found.setdefault(match.group(1), path)

    return found


def archive_dirs(spec_dir: Path) -> dict[str, Path]:
    """Zero-padded id -> directory, over the archive; empty when there is
    none."""

    return document_dirs(archive_dir(spec_dir))


def dirname_of(number: str) -> str:
    return f"S-{number}"


# ----------------------- #
# Schemas


def schema_name(file_name: str) -> str:
    return file_name.removesuffix(".yaml")


def schema_file(spec_dir: Path, file_name: str) -> Path:
    return schemas_dir(spec_dir) / f"{schema_name(file_name)}.json"


def schema_text(file_name: str) -> str:
    """One file's JSON Schema: the model's schema cut to the fields that
    file carries (D-57.1), shared definitions kept whole."""

    whole = Document.model_json_schema()
    fields = FILE_FIELDS[file_name]
    schema: dict[str, Any] = {
        "$defs": whole.get("$defs", {}),
        "additionalProperties": False,
        "description": f"{file_name} of a specification directory (RFC 0057).",
        "properties": {k: v for k, v in whole["properties"].items() if k in fields},
        "required": [k for k in whole.get("required", []) if k in fields],
        "title": schema_name(file_name),
        "type": "object",
    }

    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def schema_header(file_name: str) -> str:
    """The first line of one file: its schema, relative to the directory —
    the same two levels up from the corpus and from the archive."""

    return f"{SCHEMA_HEADER}../../schemas/{schema_name(file_name)}.json"


def check_schema(spec_dir: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings): a schema file that lags the model is a
    problem — an editor would validate against a shape the engine no
    longer reads; a corpus that has not written them yet is told to."""

    problems: list[str] = []
    warnings: list[str] = []

    for file_name in FILES:
        path = schema_file(spec_dir, file_name)
        where = f"schemas/{path.name}"

        if not path.is_file():
            warnings.append(f"{where}: not written yet — `torve init` writes it (D-57.5)")
        elif path.read_text(encoding="utf-8") != schema_text(file_name):
            problems.append(
                f"{where}: lags the model — it is generated output; `torve init` rewrites it"
            )

    return problems, warnings


# ----------------------- #
# Loading


def _read_file(directory: Path, file_name: str) -> dict[str, Any]:
    """One of the four files as a mapping, refused when it is not one or
    carries a key another file owns."""

    where = f"{directory.name}/{file_name}"
    path = directory / file_name

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError([f"{where}: not YAML — {str(exc).splitlines()[0]}"]) from None

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise SpecError([f"{where}: not a mapping"])

    data = cast("dict[str, Any]", raw)
    problems: list[str] = []

    for key in data:
        owner = file_of(str(key))

        if owner is None:
            problems.append(f"{where}: {key}: no file carries it")
        elif owner != file_name:
            problems.append(f"{where}: {key} belongs in {owner}")

    if problems:
        raise SpecError(problems)

    return data


def load_document(directory: Path, *, archived: bool = False) -> Document:
    """One document from its directory: the four files joined and validated
    as the model, every refusal named by directory, file, entry and field
    (D-53.3). A document of another schema version is refused with the
    conversion named, never half-read."""

    where = directory.name

    if not (directory / DOCUMENT_FILE).is_file():
        raise SpecError([f"{where}: no {DOCUMENT_FILE} — a document is a directory (D-57.1)"])

    data: dict[str, Any] = {}

    for file_name in FILES:
        if (directory / file_name).is_file():
            data.update(_read_file(directory, file_name))

    version = data.get("schema_version")

    if version != SCHEMA_VERSION:
        raise SpecError(
            [
                (
                    f"{where}/{DOCUMENT_FILE}: schema_version {version!r} — this loader reads "
                    f"{SCHEMA_VERSION}; a one-file document converts once through RFC 0057 phase 1"
                )
            ]
        )

    try:
        return Document.model_validate({**data, "path": str(directory), "archived": archived})
    except ValidationError as exc:
        problems: list[str] = []

        for error in exc.errors():
            loc = [str(p) for p in error["loc"]]
            owner = file_of(loc[0]) if loc else None
            spot = f"{where}/{owner}" if owner else where
            problems.append(f"{spot}: {'.'.join(loc) or 'document'}: {error['msg']}")

        raise SpecError(problems) from None


def load_corpus(spec_dir: Path) -> Corpus:
    """The corpus path and the archive beside it as one `Corpus`. Every
    document's problems are collected before anything is raised, and an
    unresolvable citation anywhere is a problem for the whole load."""

    problems: list[str] = []
    documents: list[Document] = []

    for source, archived in ((spec_dir, False), (archive_dir(spec_dir), True)):
        for _, path in sorted(document_dirs(source).items()):
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


def next_number(spec_dir: Path) -> int:
    """The maximum over corpus and archive, plus one (D-53.10): a number
    retired into the archive is still a number that was cited."""

    taken = [int(n) for n in document_dirs(spec_dir)] + [int(n) for n in archive_dirs(spec_dir)]

    return max(taken, default=0) + 1


def next_amendment(dirs: dict[str, Path], archived: dict[str, Path] | None = None) -> str:
    """The next free global amendment number (D-A.5), derived over the
    corpus and the archive (A-152) — never chosen."""

    taken: list[int] = []

    for path in (*dirs.values(), *(archived or {}).values()):
        try:
            doc = load_document(path)
        except SpecError:
            continue

        taken += [int(a.id[2:]) for a in doc.amendments if re.fullmatch(r"A-\d+", a.id)]

    return f"A-{max(taken, default=0) + 1}"


def next_decision(dirs: dict[str, Path], number: str) -> str:
    """The next free identifier in one document's own dotted family, scanned
    corpus-wide (D-A.4); retired identifiers count as taken (D-16.1)."""

    family = str(int(number))
    pattern = re.compile(rf"^D-{re.escape(family)}\.(\d+)[a-z]?$")
    taken: list[int] = []

    for path in dirs.values():
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


def lookup(spec_dir: Path, identifier: str) -> dict[str, Any] | None:
    """One corpus identifier resolved from the same load `check` runs
    (D-7.28): a row as it stands, an invariant, a question, an amendment
    or a document; an archived one answers marked archived (D-53.9). None
    when nothing defines it."""

    try:
        corpus = load_corpus(spec_dir)
    except SpecError:
        return None

    return lookup_in(corpus, identifier)


def cited_in(corpus: Corpus, identifier: str) -> list[str]:
    """The documents whose rows cite an identifier or whose prose mentions
    it, by directory name, the defining document excluded."""

    cites = _cites(identifier)
    defining = {_name(doc) for doc in corpus.documents if identifier in doc.defined_identifiers()}

    return [
        _name(doc)
        for doc in corpus.documents
        if _name(doc) not in defining
        and (
            any(identifier in row.cites for row in doc.decisions)
            or any(identifier in a.cites for a in doc.alternatives)
            or cites.search(_prose(doc))
        )
    ]


def lookup_in(corpus: Corpus, identifier: str) -> dict[str, Any] | None:
    number = identifier.strip().removeprefix("S-").removesuffix(".yaml").removesuffix(".md")

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
        "amended_by": doc.amended_by(),
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


def check_directory(spec_dir: Path) -> list[str]:
    """Only documents live in the corpus path and the archive (D-A.17,
    I-57.1): one directory per number, holding only the four file names."""

    problems: list[str] = []

    for base in (spec_dir, archive_dir(spec_dir)):
        if not base.is_dir():
            continue

        numbers: dict[str, list[str]] = {}
        label = "" if base == spec_dir else "archive/"

        for entry in sorted(base.iterdir()):
            name = f"{label}{entry.name}"

            if entry.is_dir() and entry.name == "schema":
                problems.append(f"{name}/: schemas live in `.torve/schemas/` (D-57.5) — delete it")
                continue

            if entry.is_file() and entry.suffix in (".yaml", ".md"):
                problems.append(
                    f"{name}: a one-file document — a document is a directory of four files "
                    "(RFC 0057 D-57.1); convert it"
                )
                continue

            match = DOCUMENT_DIRNAME.match(entry.name)

            if match is None or not entry.is_dir():
                problems.append(f"{name}: not S-NNNN/ — a stray entry in the corpus path")
                continue

            numbers.setdefault(match.group(1), []).append(entry.name)

            for inner in sorted(entry.iterdir()):
                if inner.name not in FILES:
                    problems.append(
                        f"{name}/{inner.name}: not one of {', '.join(FILES)} — a document "
                        "directory holds nothing else (I-57.1)"
                    )

        for number, names in sorted(numbers.items()):
            if len(names) > 1:
                problems.append(
                    f"document {number} is claimed by {len(names)} directories: {', '.join(names)}"
                )

    return problems


def check_sections(doc: Document) -> list[str]:
    """A section carries prose and nothing a typed list holds (D-57.2): an
    empty body, a typed-kind fence, the decisions table or an amendment's
    identifier as its key is refused with the key named."""

    where = f"{_name(doc)}/{DOCUMENT_FILE}"
    problems: list[str] = []

    for section in doc.sections:
        key = section.key

        if AMENDMENT_KEY.match(key):
            problems.append(
                f"{where}: section {key!r} is an amendment — its words belong in "
                f"{AMENDMENTS_FILE} under the entry's `md`"
            )
        elif not section.md.strip():
            problems.append(f"{where}: section {key!r} has no body — a heading is not a section")

        fence = TYPED_FENCE.search(section.md)

        if fence is not None:
            problems.append(
                f"{where}: section {key!r} carries a `{fence.group(1)}` fence — that list is "
                "typed; write it as the list, not as prose"
            )

        if TABLE_HEADER.search(section.md):
            problems.append(
                f"{where}: section {key!r} carries the decisions table — the rows live in "
                f"{DECISIONS_FILE}"
            )

    return problems


def check_document(doc: Document, root: Path, spec_dir: Path) -> tuple[list[str], list[str]]:
    """One document's own problems and warnings, given it loaded."""

    where = _name(doc)
    problems: list[str] = []
    warnings: list[str] = []
    match = DOCUMENT_DIRNAME.match(where)

    if match is not None and match.group(1) != doc.id:
        problems.append(f"{where}: id {doc.id!r} disagrees with the directory name")

    if doc.status == "superseded" and not doc.superseded_by:
        problems.append(f"{where}: superseded, but superseded_by names nothing")

    keys = [s.key for s in doc.sections]

    for key in sorted({k for k in keys if keys.count(k) > 1}):
        problems.append(f"{where}: two sections keyed {key!r} — one of them is misnamed")

    problems += check_sections(doc)
    warnings += check_landings(doc)

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
                    "in the repository (an implemented document cites real areas, D-32)"
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

        for start in (spec_dir, root):
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


def tracked_files(root: Path) -> list[str]:
    """What git tracks under the scanned roots and names, relative to
    *root*; nothing when the root is no repository."""

    import subprocess

    try:
        done = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []

    if done.returncode != 0:
        return []

    return [
        name
        for name in done.stdout.split("\0")
        if name and (name.startswith(SCAN_ROOTS) or Path(name).name in SCAN_NAMES)
    ]


def tree_citations(root: Path) -> list[tuple[str, int, str]]:
    """Every citation-shaped identifier in the scanned files, as
    (file, line, identifier), in file order."""

    found: list[tuple[str, int, str]] = []

    for name in tracked_files(root):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            found += [(name, number, match.group(1)) for match in TREE_CITE.finditer(line)]

    return found


def check_tree(root: Path, corpus: Corpus) -> tuple[list[str], list[str]]:
    """RFC 0057 D-57.9: every identifier the code and the docs cite resolves
    over the corpus and the archive — an identifier nothing defines is a
    problem naming its line, a retired one a warning, an archived one
    clean, history being what a comment may cite. One finding per file and
    identifier, at its first line."""

    defined = corpus.defined_identifiers()
    retired = {ident for doc in corpus.documents for ident in doc.retired}
    problems: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()

    for name, line, ident in tree_citations(root):
        if (name, ident) in seen or (ident in defined and ident not in retired):
            continue

        seen.add((name, ident))

        if ident in retired:
            warnings.append(f"{name}:{line}: cites {ident}, which is retired (D-57.9)")
        else:
            problems.append(
                f"{name}:{line}: cites {ident}, which no document in the corpus or the "
                "archive defines (D-57.9)"
            )

    return problems, warnings


def check_landings(doc: Document) -> list[str]:
    """RFC 0057 D-57.11: the status field and the execution file agree —
    complete with a phase no landing covers, or every phase landed and not
    complete, is a warning."""

    phases = {phase.phase for phase in doc.phasing}
    landed = {one.phase for one in doc.landings}
    where = _name(doc)

    if not phases:
        return []

    if doc.implementation == "complete" and phases - landed:
        missing = ", ".join(str(p) for p in sorted(phases - landed))

        return [
            (
                f"{where}: implementation complete, but phase(s) {missing} have no landing in "
                f"{EXECUTION_FILE} (D-57.11)"
            )
        ]

    if phases <= landed and doc.implementation != "complete":
        return [
            (
                f"{where}: every phase has landed and implementation is {doc.implementation!r} — "
                "mark it complete, or say in a section why not (D-57.11)"
            )
        ]

    return []


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


def check_corpus(spec_dir: Path, root: Path) -> CheckReport:
    """The whole of `torve spec check` over one corpus directory: the
    directory and the archive, every document's load, its own checks, the
    comments its files must not carry, identifiers unique and never
    reused, citations that resolve into the corpus or the archive, the
    graph and the schemas."""

    report = CheckReport()
    report.problems += check_directory(spec_dir)
    documents: dict[str, Document] = {}
    archived: dict[str, Document] = {}

    for source, is_archive, into in (
        (spec_dir, False, documents),
        (archive_dir(spec_dir), True, archived),
    ):
        for number, path in sorted(document_dirs(source).items()):
            for file_name in FILES:
                if not (path / file_name).is_file():
                    continue

                for line in find_comments((path / file_name).read_text(encoding="utf-8")):
                    report.problems.append(
                        f"{path.name}/{file_name}:{line}: a comment — meaning outside the "
                        "model; a row that needs a note needs a rationale (D-56.4)"
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
        problems, warnings = check_document(doc, root, spec_dir)
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
                    report.problems.append(f"{where}: {fname} names {ref!r}, no such document")

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
    tree_problems, tree_warnings = check_tree(root, corpus)
    report.problems += tree_problems
    report.warnings += tree_warnings
    schema_problems, schema_warnings = check_schema(spec_dir)
    report.problems += schema_problems
    report.warnings += schema_warnings

    return report
