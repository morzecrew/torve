"""The specification's storage, owned by the package (S-0007/format-validation,
S-0007/D-12; S-0057 S-0057/D-1): a document is a directory of four YAML files
split by who writes each — `document.yaml` and `decisions.yaml` the
author's, `amendments.yaml` the tool's, `execution/` the landings' —
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

from torve.domain.spec import (
    AMENDMENTS_FILE,
    DECISIONS_FILE,
    DOCUMENT_FILE,
    EXECUTION_DIR,
    EXTRAS_CAP,
    FILE_FIELDS,
    FILES,
    LANDING_FILE,
    PROSE_FIELDS,
    REQUIRED_PROSE,
    SCHEMA_VERSION,
    SECTION_KEY,
    Corpus,
    Document,
    Landing,
    document_id,
    file_of,
    is_citation,
    number_of,
    prose_key,
    qualify,
)
from torve.domain.vocabulary import GRADES

# ----------------------- #

# A document's directory is its identifier and nothing else (S-0057/D-1): the
# title lives in `document.yaml`, and `spec list` shows it.
DOCUMENT_DIRNAME = re.compile(r"^S-(\d{4})$")

# The one comment a file carries: its first line, naming the schema an
# editor validates it against (S-0056/D-6, S-0057/D-5). Everything else is meaning
# outside the model and is refused (S-0056/D-4).
SCHEMA_HEADER = "# yaml-language-server: $schema="

URI_OR_PROTOCOL_RELATIVE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
LOCAL_LINK = re.compile(r"\[[^\]]*\]\((?!#)([^)\s]+)")
LINE_CITE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9_]+):(\d+)")
FENCED_BLOCK = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)

# A citation as prose, code and docs spell it (S-0058/D-1, S-0057/D-9): the global
# form — a document, or a document and one of its items or prose keys —
# and, in a document's own prose, the bare local half of its own items.
# Scanned over what git tracks under these roots and names; never tests
# or skills, whose fixtures invent identifiers by design.
GLOBAL_CITE = re.compile(
    r"(?<![\w/-])(S-\d{4}(?:/(?:[DIQAP]-\d+|[a-z0-9][a-z0-9-]*))?)(?![\w/-]|\.\w)"
)
LOCAL_CITE = re.compile(r"(?<![\w/.-])([DIQA]-\d+)(?![\w/-]|\.\d)")
# What stood before the one grammar (S-0058/D-2, S-0058/D-3): a dotted row or the
# charter's `D-A.n`, a dotted invariant or question, a global amendment
# number, and "RFC NNNN" with or without a section — each answered by the
# mapping the conversion wrote.
LEGACY_CITE = re.compile(
    r"(?<![\w/-])(D-[A-Za-z0-9]+\.\d+[a-z]?|I-\d+\.\d+|Q-\d+\.\d+|"
    r"RFC 0\d{3}(?: §[\d.]+[a-z]?)?)(?![\w/-]|\.\d)"
)
# A bare amendment number is a local inside a document's own prose and a
# legacy global counter anywhere else — code has no document to be local to.
TREE_LEGACY_CITE = re.compile(r"(?<![\w/-])(A-\d+)(?![\w/-]|\.\d)")
SCAN_ROOTS = ("src/", "pages/")
SCAN_NAMES = ("AGENTS.md", "CLAUDE.md", "README.md")
# Test data that invents identifiers by design, as the user-facing-text
# gate already exempts it.
SCAN_EXCLUDE = ("src/torve/gates/sabotage.py",)
MAPPING_FILE = "identifiers.yaml"

# What a section may not carry (S-0057/D-2): a typed list restated as the fence
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
    """The archive beside the corpus (S-0057/D-3): `.torve/archive/` for
    `.torve/specs/`."""

    return spec_dir.parent / "archive"


def schemas_dir(spec_dir: Path) -> Path:
    """Where `torve init` writes the schemas (S-0057/D-5): `.torve/schemas/`
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


def document_dir(spec_dir: Path, reference: str) -> Path | None:
    """The directory of one document by any spelling of its identifier, in
    the corpus or the archive; None when none."""

    try:
        number = number_of(reference)
    except ValueError:
        return None

    return document_dirs(spec_dir).get(number) or archive_dirs(spec_dir).get(number)


def load_mapping(spec_dir: Path) -> dict[str, str]:
    """The renumbering the conversion wrote (S-0058/D-2): every identifier that
    stood before the one grammar, and what it became. Empty when the corpus
    never had one."""

    path = archive_dir(spec_dir) / MAPPING_FILE

    if not path.is_file():
        return {}

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    return {str(k): str(v) for k, v in cast("dict[Any, Any]", raw).items()}


def legacy_hint(mapping: dict[str, str], legacy: str) -> str:
    """What a legacy citation should say now, from the mapping; a name for
    the mapping when it has nothing."""

    if legacy in mapping:
        return f"write {mapping[legacy]}"

    stem = legacy.split(" §", 1)[0]

    if stem.startswith("RFC "):
        return f"write {document_id(stem[4:])} with the section's key"

    return f"nothing in {MAPPING_FILE} maps it — it never stood"


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
    file carries (S-0057/D-1), shared definitions kept whole."""

    whole = Document.model_json_schema()
    fields = FILE_FIELDS[file_name]
    schema: dict[str, Any] = {
        "$defs": whole.get("$defs", {}),
        "additionalProperties": False,
        "description": f"{file_name} of a specification directory (S-0057).",
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


LANDING_SCHEMA = "landing"


def landing_schema_text() -> str:
    """One landing's JSON Schema (S-0058/D-6): the file under `execution/`."""

    return json.dumps(Landing.model_json_schema(), indent=2, sort_keys=True) + "\n"


def landing_header(levels: int = 3) -> str:
    """The first line of a landing file: its schema, *levels* directories up
    — three from a document's `execution/`, one from the document-less
    directory beside the corpus (S-0059/D-11)."""

    return f"{SCHEMA_HEADER}{'../' * levels}schemas/{LANDING_SCHEMA}.json"


def check_schema(spec_dir: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings): a schema file that lags the model is a
    problem — an editor would validate against a shape the engine no
    longer reads; a corpus that has not written them yet is told to."""

    problems: list[str] = []
    warnings: list[str] = []

    expected = {schema_file(spec_dir, name): schema_text(name) for name in FILES}
    expected[schemas_dir(spec_dir) / f"{LANDING_SCHEMA}.json"] = landing_schema_text()

    for path, text in expected.items():
        where = f"schemas/{path.name}"

        if not path.is_file():
            warnings.append(f"{where}: not written yet — `torve init` writes it (S-0057/D-5)")
        elif path.read_text(encoding="utf-8") != text:
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


def landing_files(directory: Path) -> list[Path]:
    """The landing files of one document, sorted by instant, task and
    attempt — the order `landings` reads in (S-0058/D-6)."""

    return landing_files_in(directory / EXECUTION_DIR)


def landing_files_in(execution: Path) -> list[Path]:
    """The landing files of one execution directory — a document's, or the
    document-less one beside the corpus (S-0059/D-11)."""

    if not execution.is_dir():
        return []

    named = [(m, p) for p in execution.iterdir() if (m := LANDING_FILE.match(p.name))]

    return [
        p
        for _, p in sorted(
            named, key=lambda one: (one[0].group(3), one[0].group(1), int(one[0].group(2)))
        )
    ]


def _read_landings(directory: Path) -> list[dict[str, Any]]:
    landings: list[dict[str, Any]] = []
    problems: list[str] = []

    for path in landing_files(directory):
        where = f"{directory.name}/{EXECUTION_DIR}/{path.name}"

        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{where}: not YAML — {str(exc).splitlines()[0]}")
            continue

        if not isinstance(raw, dict):
            problems.append(f"{where}: not a mapping")
            continue

        landings.append(cast("dict[str, Any]", raw))

    if problems:
        raise SpecError(problems)

    return landings


def load_document(directory: Path, *, archived: bool = False) -> Document:
    """One document from its directory: the four files joined and validated
    as the model, every refusal named by directory, file, entry and field
    (S-0053/D-3). A document of another schema version is refused with the
    conversion named, never half-read."""

    where = directory.name

    if not (directory / DOCUMENT_FILE).is_file():
        raise SpecError([f"{where}: no {DOCUMENT_FILE} — a document is a directory (S-0057/D-1)"])

    data: dict[str, Any] = {}

    for file_name in FILES:
        if (directory / file_name).is_file():
            data.update(_read_file(directory, file_name))

    data["landings"] = _read_landings(directory)
    version = data.get("schema_version")

    if version != SCHEMA_VERSION:
        raise SpecError(
            [
                (
                    f"{where}/{DOCUMENT_FILE}: schema_version {version!r} — this loader reads "
                    f"{SCHEMA_VERSION}; a flat section list converts once through S-0058 phase 2"
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

            if loc and loc[0] == "landings":
                owner = EXECUTION_DIR

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
    """The maximum over corpus and archive, plus one (S-0053/D-10): a number
    retired into the archive is still a number that was cited."""

    taken = [int(n) for n in document_dirs(spec_dir)] + [int(n) for n in archive_dirs(spec_dir)]

    return max(taken, default=0) + 1


def _next_local(doc: Document, family: str, taken_from: list[str]) -> str:
    """The next free local identifier of one family in one document (S-0058/D-1);
    retired identifiers count as taken (S-0016/D-1)."""

    pattern = re.compile(rf"^{re.escape(doc.id)}/{family}-(\d+)$")
    taken = [int(m.group(1)) for ident in taken_from if (m := pattern.match(ident))]

    return f"{family}-{max(taken, default=0) + 1}"


def next_amendment(doc: Document) -> str:
    """The next free amendment number of one document, local — `A-n` —
    derived, never chosen."""

    return _next_local(doc, "A", [a.id for a in doc.amendments])


def next_decision(doc: Document) -> str:
    """The next free decision number of one document, local — `D-n`."""

    return _next_local(doc, "D", [*(row.id for row in doc.decisions), *doc.retired])


# ----------------------- #
# Reading one identifier


def _name(doc: Document) -> str:
    return Path(doc.path).name if doc.path else doc.id


def _prose(doc: Document) -> str:
    """Every string a person wrote, fences stripped: what a citation, a
    link or a line number is looked for in."""

    parts = [s.md for s in doc.prose()] + [a.md for a in doc.amendments]
    parts += [f"{r.text} {r.consequence} {r.rationale}" for r in doc.decisions]
    parts += [f"{a.option} {a.rejected_because}" for a in doc.alternatives]
    parts += [q.text for q in doc.questions] + [i.statement for i in doc.invariants]

    return FENCED_BLOCK.sub("", "\n".join(parts))


def _cites(ident: str) -> re.Pattern[str]:
    # As a word: not inside a longer identifier, not a dotted child (S-0001/D-10
    # must not match inside S-0002/D-10), not a digit-extended sibling.
    return re.compile(rf"(?<![\w.]){re.escape(ident)}(?![\w.])")


def lookup(spec_dir: Path, identifier: str) -> dict[str, Any] | None:
    """One corpus identifier resolved from the same load `check` runs
    (S-0007/D-28): a row as it stands, an invariant, a question, an amendment
    or a document; an archived one answers marked archived (S-0053/D-9); a
    legacy identifier answers through the mapping and says what it was
    (S-0058/D-3). None when nothing defines it."""

    try:
        corpus = load_corpus(spec_dir)
    except SpecError:
        return None

    return lookup_in(corpus, identifier, load_mapping(spec_dir))


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


def lookup_in(
    corpus: Corpus, identifier: str, mapping: dict[str, str] | None = None
) -> dict[str, Any] | None:
    identifier = identifier.strip()

    if mapping and identifier in mapping:
        found = lookup_in(corpus, mapping[identifier])

        if found is not None:
            found["was"] = identifier

        return found

    if "/" not in identifier:
        doc = corpus.document(identifier)

        if doc is not None:
            return _document_payload(doc)

        if not identifier.startswith("S-"):
            return None

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
        "number": number_of(doc.id),
        "file": _name(doc),
        "title": doc.title,
        "status": doc.status,
        "implementation": doc.implementation,
        "depends_on": list(doc.depends_on),
        "superseded_by": doc.superseded_by,
        "amended_by": doc.amended_by(),
        "description": doc.routing(),
        "sections": [s.key for s in doc.prose()],
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
    identifier the corpus or the archive defines (S-0053/tests)."""

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
    loads, a comment never does (S-0056/D-4)."""

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
    """Only documents live in the corpus path and the archive (S-0016/D-24,
    S-0057/I-1): one directory per number, holding only the four file names."""

    problems: list[str] = []

    for base in (spec_dir, archive_dir(spec_dir)):
        if not base.is_dir():
            continue

        numbers: dict[str, list[str]] = {}
        label = "" if base == spec_dir else "archive/"

        for entry in sorted(base.iterdir()):
            name = f"{label}{entry.name}"

            if entry.is_file() and entry.name == MAPPING_FILE and base != spec_dir:
                continue  # the renumbering (S-0058/D-2) lives beside the archive

            if entry.is_dir() and entry.name == "schema":
                problems.append(
                    f"{name}/: schemas live in `.torve/schemas/` (S-0057/D-5) — delete it"
                )
                continue

            if entry.is_file() and entry.suffix in (".yaml", ".md"):
                problems.append(
                    f"{name}: a one-file document — a document is a directory of four files "
                    "(S-0057 S-0057/D-1); convert it"
                )
                continue

            match = DOCUMENT_DIRNAME.match(entry.name)

            if match is None or not entry.is_dir():
                problems.append(f"{name}: not S-NNNN/ — a stray entry in the corpus path")
                continue

            numbers.setdefault(match.group(1), []).append(entry.name)

            for inner in sorted(entry.iterdir()):
                if inner.is_dir() and inner.name == EXECUTION_DIR:
                    for landing in sorted(inner.iterdir()):
                        if not LANDING_FILE.match(landing.name):
                            problems.append(
                                f"{name}/{EXECUTION_DIR}/{landing.name}: not "
                                "<task>-<attempt>-<instant>.yaml — a landing is named by what "
                                "landed and when (S-0058/D-6)"
                            )

                    continue

                if inner.name == "execution.yaml":
                    problems.append(
                        f"{name}/execution.yaml: execution is a directory of landings "
                        "(S-0058/D-6); convert it"
                    )
                    continue

                if inner.name not in FILES:
                    problems.append(
                        f"{name}/{inner.name}: not one of {', '.join(FILES)} or "
                        f"{EXECUTION_DIR}/ — a document directory holds nothing else (S-0057/I-1)"
                    )

        for number, names in sorted(numbers.items()):
            if len(names) > 1:
                problems.append(
                    f"document {number} is claimed by {len(names)} directories: {', '.join(names)}"
                )

    return problems


def check_sections(doc: Document) -> list[str]:
    """A section carries prose and nothing a typed list holds (S-0057/D-2): an
    empty body, a typed-kind fence, the decisions table or an amendment's
    identifier as its key is refused with the key named."""

    where = f"{_name(doc)}/{DOCUMENT_FILE}"
    problems: list[str] = []

    for section in doc.prose():
        key = section.key

        if not SECTION_KEY.match(key):
            problems.append(
                f"{where}: section key {key!r} — a key is lower-case words and dashes, never "
                "a family shape (S-0058/D-1)"
            )

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


def prose_citations(doc: Document, resolvable: set[str], mapping: dict[str, str]) -> list[str]:
    """Every citation the document's prose makes that nothing defines
    (S-0058/D-1, S-0058/D-3): a global identifier no document holds, a bare local
    the document itself does not define, a legacy shape answered by the
    mapping. One problem per identifier."""

    prose = _prose(doc)
    where = _name(doc)
    problems: list[str] = []
    reported: set[str] = set()

    for match in GLOBAL_CITE.finditer(prose):
        cited = match.group(1)

        if cited not in resolvable and cited not in reported:
            reported.add(cited)
            problems.append(f"{where}: cites {cited}, which no document in the corpus defines")

    for match in LOCAL_CITE.finditer(prose):
        cited = qualify(doc.id, match.group(1))

        if cited not in resolvable and cited not in reported:
            reported.add(cited)
            problems.append(
                f"{where}: cites {match.group(1)}, which this document does not define ({cited})"
            )

    for match in LEGACY_CITE.finditer(prose):
        cited = match.group(1)

        if cited not in reported:
            reported.add(cited)
            problems.append(
                f"{where}: cites {cited} in the old grammar — {legacy_hint(mapping, cited)} (S-0058/D-3)"
            )

    return problems


def check_anatomy(doc: Document) -> tuple[list[str], list[str]]:
    """S-0058/D-4: an accepted document says its summary, motivation,
    current state, goals, non-goals, tests and risks and designs at least
    one thing; a key is unique document-wide and never a typed name; the
    extras stop at the cap; the routing line is one sentence a list can
    show."""

    where = f"{_name(doc)}/{DOCUMENT_FILE}"
    problems: list[str] = []
    warnings: list[str] = []
    typed = {prose_key(name) for name in PROSE_FIELDS}

    # A convention (the standing baseline) is its rows: it says its summary
    # and nothing else is owed; a design says the anatomy whole.
    required = REQUIRED_PROSE if doc.kind == "design" else ("summary",)

    if doc.status == "accepted" and not doc.archived:
        for name in required:
            if not str(getattr(doc, name)).strip():
                problems.append(f"{where}: {name} is empty — an accepted {doc.kind} says it")

        if doc.kind == "design" and not doc.design:
            problems.append(f"{where}: design is empty — an accepted design designs something")

    if len(doc.sections) > EXTRAS_CAP:
        problems.append(
            f"{where}: {len(doc.sections)} extra sections — the anatomy stops at {EXTRAS_CAP}; "
            "a document that needs more has a design list and a second document"
        )

    keys = [s.key for s in doc.design] + [s.key for s in doc.sections]

    for key in sorted({k for k in keys if keys.count(k) > 1}):
        problems.append(f"{where}: two sections keyed {key!r} — one of them is misnamed")

    for key in sorted(set(keys) & typed):
        problems.append(f"{where}: section {key!r} is a typed key — write it as the field")

    if len(doc.routing()) > 300:
        warnings.append(
            f"{where}: the summary's first sentence runs past 300 characters — it is the "
            "line `spec list` shows"
        )

    return problems, warnings


def check_document(doc: Document, root: Path, spec_dir: Path) -> tuple[list[str], list[str]]:
    """One document's own problems and warnings, given it loaded."""

    where = _name(doc)
    problems: list[str] = []
    warnings: list[str] = []
    if DOCUMENT_DIRNAME.match(where) and where != doc.id:
        problems.append(f"{where}: id {doc.id!r} disagrees with the directory name")

    if doc.status == "superseded" and not doc.superseded_by:
        problems.append(f"{where}: superseded, but superseded_by names nothing")

    anatomy_problems, anatomy_warnings = check_anatomy(doc)
    problems += anatomy_problems
    warnings += anatomy_warnings

    # S-0016/D-15: a local written twice by hand is two rows under one name.
    own = [row.id for row in doc.decisions] + [i.id for i in doc.invariants]
    own += [q.id for q in doc.questions] + [a.id for a in doc.amendments]

    for ident in sorted({i for i in own if own.count(i) > 1}):
        problems.append(f"{where}: {doc.local(ident)} is defined twice — identifiers are unique")

    problems += check_sections(doc)
    warnings += check_landings(doc)

    # S-0001/D-32: for a document not yet built the globs name intended areas;
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
                    "in the repository (an implemented document cites real areas, S-0001/D-32)"
                )
            else:
                unbuilt.append(f"{row.id} -> {pattern}")

    if unbuilt:
        warnings.append(
            f"{where}: {len(unbuilt)} LOCKED glob(s) name unbuilt areas — intended modules "
            "awaiting implementation (S-0001/D-32): " + "; ".join(unbuilt)
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
                "first refactor above them (S-0007/format-validation); cite the path alone"
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
        if name
        and name not in SCAN_EXCLUDE
        and (name.startswith(SCAN_ROOTS) or Path(name).name in SCAN_NAMES)
    ]


def tree_citations(root: Path) -> list[tuple[str, int, str, bool]]:
    """Every citation in the scanned files, as (file, line, identifier,
    legacy), in file order: the global grammar, and what stood before it."""

    found: list[tuple[str, int, str, bool]] = []

    for name in tracked_files(root):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            found += [(name, number, m.group(1), False) for m in GLOBAL_CITE.finditer(line)]
            found += [(name, number, m.group(1), True) for m in LEGACY_CITE.finditer(line)]
            found += [(name, number, m.group(1), True) for m in TREE_LEGACY_CITE.finditer(line)]

    return found


def check_backlinks(
    root: Path, corpus: Corpus, citations: list[tuple[str, int, str, bool]]
) -> list[str]:
    """S-0058/D-8: a LOCKED row of a standing document whose declared paths
    match scanned files, none of which cites it, is a warning — the comment
    an agent deleted is heard; a row over files the scan never reads is
    silent, having nowhere to be cited from."""

    from fnmatch import fnmatch

    scanned = tracked_files(root)
    citing: dict[str, set[str]] = {}

    for name, _line, ident, legacy in citations:
        if not legacy:
            citing.setdefault(ident, set()).add(name)

    warnings: list[str] = []

    for doc in corpus.standing():
        if doc.superseded_by:
            continue

        for row in doc.decisions:
            if row.grade != "LOCKED":
                continue

            governed = {
                name
                for name in scanned
                if any(
                    fnmatch(name, glob) or name.startswith(glob.rstrip("*")) for glob in row.paths
                )
            }

            if governed and not (governed & citing.get(row.id, set())):
                warnings.append(
                    f"{_name(doc)}: {row.id} (LOCKED) governs {len(governed)} scanned file(s) and "
                    "none cites it — the backlink is silent (S-0058/D-8)"
                )

    return warnings


def check_tree(
    root: Path,
    corpus: Corpus,
    spec_dir: Path | None = None,
    citations: list[tuple[str, int, str, bool]] | None = None,
) -> tuple[list[str], list[str]]:
    """S-0057 S-0057/D-9: every identifier the code and the docs cite resolves
    over the corpus and the archive — an identifier nothing defines is a
    problem naming its line, a retired one a warning, an archived one
    clean, history being what a comment may cite. One finding per file and
    identifier, at its first line."""

    defined = corpus.defined_identifiers()
    retired = {ident for doc in corpus.documents for ident in doc.retired}
    mapping = load_mapping(spec_dir) if spec_dir is not None else {}
    problems: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()

    for name, line, ident, legacy in citations if citations is not None else tree_citations(root):
        if (name, ident) in seen or (ident in defined and ident not in retired):
            continue

        seen.add((name, ident))

        if legacy:
            problems.append(
                f"{name}:{line}: cites {ident} in the old grammar — "
                f"{legacy_hint(mapping, ident)} (S-0058/D-3)"
            )
        elif ident in retired:
            warnings.append(f"{name}:{line}: cites {ident}, which is retired (S-0057/D-9)")
        else:
            problems.append(
                f"{name}:{line}: cites {ident}, which no document in the corpus or the "
                "archive defines (S-0057/D-9)"
            )

    return problems, warnings


def check_landings(doc: Document) -> list[str]:
    """S-0057 S-0057/D-11: the status field and the execution file agree —
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
                f"{where}: implementation complete, but phase(s) {missing} have no landing under "
                f"{EXECUTION_DIR}/ (S-0057/D-11)"
            )
        ]

    if phases <= landed and doc.implementation != "complete":
        return [
            (
                f"{where}: every phase has landed and implementation is {doc.implementation!r} — "
                "mark it complete, or say in a section why not (S-0057/D-11)"
            )
        ]

    return []


def check_graph(documents: dict[str, Document]) -> tuple[list[str], list[str]]:
    """Cycles in `depends_on` are problems, and so is an accepted document
    depending on one that is not accepted (S-0016/D-20)."""

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
                    "no inheritance from a non-accepted document (S-0016/D-20)"
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
            number = document_id(number)

            carried = [path / f for f in FILES if (path / f).is_file()] + landing_files(path)

            for file in carried:
                spot = str(file.relative_to(path.parent))

                for line in find_comments(file.read_text(encoding="utf-8")):
                    report.problems.append(
                        f"{spot}:{line}: a comment — meaning outside the "
                        "model; a row that needs a note needs a rationale (S-0056/D-4)"
                    )

            try:
                into[number] = load_document(path, archived=is_archive)
            except SpecError as exc:
                report.problems += exc.problems

    report.count = len(documents)
    corpus = Corpus(documents=[*documents.values(), *archived.values()])
    report.problems += check_cites(corpus)
    resolvable = corpus.defined_identifiers()
    mapping = load_mapping(spec_dir)
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
                    "identifiers are permanent and corpus-unique (S-0016/D-15)"
                )
            elif ident in retired:
                report.problems.append(
                    f"{where}: defines {ident}, which {retired[ident]} retired — "
                    "identifiers are never reused (S-0016/D-26, S-0016/D-1)"
                )

            seen[ident] = where

        for fname in ("depends_on", "informed_by", "supersedes"):
            for ref in getattr(doc, fname):
                if ref in documents:
                    continue

                if ref in archived:
                    report.warnings.append(
                        f"{where}: {fname} names {ref}, which is archived "
                        f"({_name(archived[ref])}) — nothing inherits from it (S-0053/D-8)"
                    )
                else:
                    report.problems.append(f"{where}: {fname} names {ref!r}, no such document")

        report.problems += prose_citations(doc, resolvable, mapping)

    graph_problems, graph_warnings = check_graph(documents)
    report.problems += graph_problems
    report.warnings += graph_warnings
    citations = tree_citations(root)
    tree_problems, tree_warnings = check_tree(root, corpus, spec_dir, citations)
    report.problems += tree_problems
    report.warnings += tree_warnings
    report.warnings += check_backlinks(root, corpus, citations)
    schema_problems, schema_warnings = check_schema(spec_dir)
    report.problems += schema_problems
    report.warnings += schema_warnings

    return report
