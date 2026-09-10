"""The one writer of a document (S-0025/the-canonical-emitter, S-0025/D-1; S-0056 S-0056/D-4;
S-0057 S-0057/D-1): every verb that changes a document — `amend`, `fix`,
`retire`, `archive`, `add-decision`, `relocate-paths`, `new` — mutates the
loaded model and writes it through `dump_document`, one serializer that
splits the model into the directory's four files with the model's key
order, block scalars for anything holding a newline, folded scalars for
long lines and flow lists for short lists of identifiers. Comments are not
preserved: there is no second renderer to drop a field, and a row that
needs a note needs a `rationale`.

Every verb is a parse-mutate-dump-check transaction (S-0025/D-2): the
mutated corpus is checked whole in a scratch copy with the archive in
view, and only a clean check is copied back.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel

from torve.config.spec import (
    CheckReport,
    SpecError,
    archive_dir,
    check_corpus,
    landing_files_in,
    landing_header,
    load_document,
    schema_header,
)
from torve.domain.spec import (
    DECISIONS_FILE,
    DOCUMENT_FILE,
    FILE_FIELDS,
    LANDING_FILE,
    SCHEMA_VERSION,
    Amendment,
    Change,
    Decision,
    Document,
    Landing,
    document_id,
    heading_of,
    qualify,
)

# ----------------------- #
# The serializer

# A string longer than this is written folded (`>-`) so no line exceeds
# WIDTH; a shorter one stays plain, and stays on one line at any depth
# the model reaches, so a phrase is grep-able as written.
FOLD_AT = 48
WIDTH = 80

# The files the author's hand starts: written even when the lists are
# empty, so a new document shows where the rows go. The tool's and the
# landing's files appear with their first entry and never shrink to nothing.
ALWAYS_WRITTEN = (DOCUMENT_FILE, DECISIONS_FILE)


class _Dumper(yaml.SafeDumper):
    """PyYAML's safe dumper with the document's three conventions: a
    string holding a newline is a literal block, a long string is folded,
    and a short list of short scalars is a flow list. Indentation is two
    spaces at every level, list items indented under their key."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    if len(data) > FOLD_AT and " " in data and not data.startswith((" ", "\t")):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")

    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.SequenceNode:
    short = all(isinstance(item, str) and " " not in item and "\n" not in item for item in data)
    flow = bool(data) and short and len(data) <= 8 and sum(len(cast("str", i)) for i in data) < 70

    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow or None)


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(list, _represent_list)

# The header fields: written always, so the file reads as a document even
# when a value is the default.
HEADER_FIELDS = (
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
)


def _value(doc: Document, name: str) -> Any:
    """One field as its file carries it: inside every list entry only what
    differs from the model's default — so a row reads as its author wrote
    it; a contract example whole."""

    value = getattr(doc, name)

    if value is None or value == []:
        return None

    if name == "contract_example":
        return value.model_dump(mode="json", exclude_none=True)

    if isinstance(value, list):
        return [
            item.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
            if isinstance(item, BaseModel)
            else item
            for item in value
        ]

    return value


# The fields that carry identifiers (S-0058/D-1): written by the local half
# alone when the identifier is the document's own.
IDENTIFIER_FIELDS = {"id", "cites", "superseded_by", "settled_by", "subject", "retired", "decision"}


def _localize(doc: Document, value: Any, key: str | None = None) -> Any:
    """The payload with this document's own identifiers written local."""

    if isinstance(value, dict):
        return {k: _localize(doc, v, str(k)) for k, v in cast("dict[str, Any]", value).items()}

    if isinstance(value, list):
        return [_localize(doc, v, key) for v in value]

    if isinstance(value, str) and key in IDENTIFIER_FIELDS:
        return doc.local(value)

    return value


def _payload(doc: Document) -> dict[str, dict[str, Any]]:
    """The document split into its files (S-0057/D-1): the header fields
    always, every list only when non-empty, the document's own
    identifiers local (S-0058/D-1)."""

    head = doc.model_dump(mode="json", include=set(HEADER_FIELDS))
    files: dict[str, dict[str, Any]] = {}

    for file_name, fields in FILE_FIELDS.items():
        body: dict[str, Any] = {}

        for name in fields:
            if name in HEADER_FIELDS:
                body[name] = head[name]
                continue

            value = _localize(doc, _value(doc, name), name)

            if value is not None:
                body[name] = value
            elif name == "decisions":
                body[name] = []  # the author's file shows where the rows go

        files[file_name] = body

    return files


def _text(file_name: str, payload: dict[str, Any]) -> str:
    text = yaml.dump(
        payload,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=WIDTH,
        default_flow_style=False,
    )

    return f"{schema_header(file_name)}\n{text}"


def dump_document(doc: Document) -> dict[str, str]:
    """The document as texts by file name: the author's two files always,
    the others only with content. Each text opens with its schema line."""

    texts: dict[str, str] = {}

    for file_name, payload in _payload(doc).items():
        if payload or file_name in ALWAYS_WRITTEN:
            texts[file_name] = _text(file_name, payload)

    return texts


def write_document(directory: Path, doc: Document) -> list[str]:
    """The dump written into *directory*, created when missing; the file
    names written are returned. A file the dump does not carry is left as
    it is — nothing here deletes."""

    directory.mkdir(parents=True, exist_ok=True)
    texts = dump_document(doc)

    for file_name, text in texts.items():
        (directory / file_name).write_text(text, encoding="utf-8")

    return list(texts)


def write_landing(execution: Path, doc: Document | None, landing: Landing) -> tuple[Path, bool]:
    """One landing as its own file in an execution directory (S-0058/D-6):
    named by task, attempt and instant, written once. A file already there
    for the same task and attempt with the same entries is the same landing
    — returned, not rewritten; anything else is a new file. *doc* is the
    document whose directory this is, whose own identifiers are written
    local; None is the document-less directory (S-0059/D-11), where every
    identifier is already global. The path and whether it was written."""

    execution.mkdir(parents=True, exist_ok=True)
    dumped = landing.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    payload = _localize(doc, dumped) if doc is not None else dumped

    for existing in landing_files_in(execution):
        match = LANDING_FILE.match(existing.name)

        if (
            match is None
            or match.group(1) != landing.task
            or int(match.group(2)) != landing.attempt
        ):
            continue

        carried: Any = yaml.safe_load(existing.read_text(encoding="utf-8")) or {}

        if cast("dict[str, Any]", carried).get("entries", []) == payload.get("entries", []):
            return existing, False

    path = execution / landing.file_name()
    ordinal = 1

    while path.exists():  # two landings of one task and attempt within one second
        ordinal += 1
        path = execution / landing.file_name().replace(".yaml", f"-{ordinal}.yaml")

    text = yaml.dump(
        payload,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=WIDTH,
        default_flow_style=False,
    )
    levels = 3 if doc is not None else 1
    path.write_text(f"{landing_header(levels)}\n{text}", encoding="utf-8")

    return path, True


def canonical(directory: Path) -> dict[str, str]:
    """What the serializer would write for the document as loaded from
    *directory* — what `fmt --check` compares against."""

    return dump_document(load_document(directory))


# ----------------------- #
# The stamp


def stamp(row: Decision) -> str:
    return row.stamp()


# ----------------------- #
# The mutations (each returns a new Document; nothing here writes)


def _row(doc: Document, identifier: str) -> Decision:
    row = doc.decision(identifier)

    if row is None:
        raise ValueError(f"no decision {qualify(doc.id, identifier)!r} in this document")

    return row


def _with_rows(doc: Document, rows: list[Decision]) -> Document:
    return doc.model_copy(update={"decisions": rows})


def amend_row(
    doc: Document,
    identifier: str,
    *,
    grade: str | None = None,
    paths: list[str] | None = None,
    new_text: str | None = None,
) -> tuple[Document, list[dict[str, Any]]]:
    """One row changed by the tool (S-0053/D-4): grade, paths or text replaced,
    the row re-stamped, and the typed diff returned for the amendment that
    records it. Raises `ValueError` when the row is unknown or nothing was
    asked to change."""

    current = _row(doc, identifier)
    identifier = current.id
    changes: list[dict[str, Any]] = []
    update: dict[str, Any] = {}

    if grade is not None and grade != current.grade:
        changes.append(
            {"subject": identifier, "field": "grade", "before": current.grade, "after": grade}
        )
        update["grade"] = grade

    if paths is not None and paths != current.paths:
        changes.append(
            {"subject": identifier, "field": "paths", "before": list(current.paths), "after": paths}
        )
        update["paths"] = paths

    if new_text is not None and new_text.strip() != current.text.strip():
        changes.append(
            {
                "subject": identifier,
                "field": "text",
                "before": current.text,
                "after": new_text.strip(),
            }
        )
        update["text"] = new_text.strip()

    if not changes:
        raise ValueError(f"nothing to change on {identifier}")

    updated = Decision.model_validate({**current.model_dump(), **update})
    updated = updated.model_copy(update={"fingerprint": updated.stamp()})
    changes.append(
        {
            "subject": identifier,
            "field": "fingerprint",
            "before": current.fingerprint or None,
            "after": updated.fingerprint,
        }
    )
    rows = [updated if row.id == identifier else row for row in doc.decisions]

    return _with_rows(doc, rows), changes


def fix_row_text(
    doc: Document, identifier: str, new_text: str
) -> tuple[Document, list[dict[str, Any]]]:
    """The editorial lane (S-0053/D-4): the text replaced, the row re-stamped,
    the before and after recorded under `editorial` — never an amendment
    number. A rewording that changes the rule is an amendment."""

    current = _row(doc, identifier)
    identifier = current.id
    text = new_text.strip()

    # The text as it now reads, hand-edited and unstamped, is exactly what
    # this lane accepts: the refusal is for a row already stamped as it is.
    if text == current.text.strip() and current.fingerprint == current.stamp():
        raise ValueError(f"{identifier}'s text already reads that way, and it is stamped")

    updated = current.model_copy(update={"text": text})
    updated = updated.model_copy(update={"fingerprint": updated.stamp()})
    changes: list[dict[str, Any]] = [
        {"subject": identifier, "field": "text", "before": current.text, "after": text},
        {
            "subject": identifier,
            "field": "fingerprint",
            "before": current.fingerprint or None,
            "after": updated.fingerprint,
        },
    ]
    rows = [updated if row.id == identifier else row for row in doc.decisions]
    editorial = [*doc.editorial, *(Change.model_validate(c) for c in changes)]

    return doc.model_copy(update={"decisions": rows, "editorial": editorial}), changes


def append_amendment(
    doc: Document,
    amendment: str,
    title: str,
    today: str,
    changes: list[dict[str, Any]] | None = None,
) -> Document:
    """`spec amend` (S-0025/D-4): the next amendment appended with its typed
    diff (S-0053/D-4); the words are the author's to write."""

    amendment = qualify(doc.id, amendment)

    if any(a.id == amendment for a in doc.amendments):
        raise ValueError(f"{amendment} already exists on this document")

    entry = Amendment.model_validate(
        {"id": amendment, "at": today, "title": title, "changes": changes or []}
    )

    return doc.model_copy(update={"amendments": [*doc.amendments, entry]})


def append_decision(doc: Document, identifier: str) -> Document:
    """`spec add-decision` (S-0025/D-3): a row under the next free identifier,
    grade OPEN — the vocabulary's own "not yet decided" — and the text
    left for the author."""

    if doc.decision(identifier) is not None:
        raise ValueError(f"{qualify(doc.id, identifier)} already exists on this document")

    row = Decision(id=qualify(doc.id, identifier), grade="OPEN", text="<decision>")

    return _with_rows(doc, [*doc.decisions, row])


def retire_decision(doc: Document, identifier: str, today: str, reason: str = "") -> Document:
    """`spec retire` (S-0025/D-6, S-0016/D-1): the row removed and its identifier
    recorded in `retired`, never reused. The reason rides the amendment's
    diff that records the retirement."""

    gone = _row(doc, identifier).id
    rows = [row for row in doc.decisions if row.id != gone]

    return doc.model_copy(update={"decisions": rows, "retired": [*doc.retired, gone]})


def relocate_paths(doc: Document, old: str, new: str) -> tuple[Document, list[str]]:
    """`spec relocate-paths` (S-0025/D-7): every row carrying the exact glob
    *old* carries *new*; the rows touched are returned. Text is never
    touched."""

    touched: list[str] = []
    rows: list[Decision] = []

    for row in doc.decisions:
        if old in row.paths:
            touched.append(row.id)
            row = row.model_copy(update={"paths": [new if p == old else p for p in row.paths]})

        rows.append(row)

    return _with_rows(doc, rows), touched


def archive_document(doc: Document, superseded_by: str) -> Document:
    """What a retired document carries in the archive (S-0053/D-8): `status:
    superseded`, `superseded_by` naming the baseline. Every identifier
    stays and still resolves."""

    if doc.status == "superseded" and doc.superseded_by:
        raise ValueError(f"document {doc.id} is already superseded by {doc.superseded_by}")

    return doc.model_copy(
        update={
            "status": "superseded",
            "superseded_by": document_id(superseded_by),  # S-0058/D-1
            "archived": True,
        }
    )


def new_document(number: str, title: str, owner: str, kind: str = "design") -> Document:
    """`spec new`: the smallest document that checks. The number is written
    as the one grammar spells it (S-0058/D-1), however the caller spells it."""

    return Document.model_validate(
        {
            "id": document_id(number),
            "title": title,
            "kind": kind,
            "status": "draft",
            "owner": owner,
            "schema_version": SCHEMA_VERSION,
            "summary": "What this document decides.\n",
        }
    )


# ----------------------- #
# The transaction


def write_transaction(
    spec_dir: Path,
    root: Path,
    mutations: dict[str, Document],
    deletions: tuple[str, ...] = (),
    archived: dict[str, Document] | None = None,
) -> CheckReport:
    """One parse-mutate-dump-check cycle (S-0025/D-2): *mutations* (directory
    name -> document) is applied and *deletions* removed in a scratch copy
    of the corpus, *archived* is placed in a scratch copy of the archive
    beside it, and the scratch corpus is checked whole with the archive and
    the schemas in view (S-0053/A-1). Only a clean check is copied back; a red
    check leaves the tree untouched."""

    real_archive = archive_dir(spec_dir)

    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name) / spec_dir.parent.name / spec_dir.name
        scratch.parent.mkdir(parents=True)
        scratch_archive = archive_dir(scratch)

        for sibling in ("archive", "schemas"):
            if (spec_dir.parent / sibling).is_dir():
                shutil.copytree(spec_dir.parent / sibling, scratch.parent / sibling)

        for name, doc in (archived or {}).items():
            write_document(scratch_archive / name, doc)

        for path in spec_dir.iterdir():
            if path.is_dir() and path.name not in deletions:
                shutil.copytree(path, scratch / path.name)
            elif path.is_file():
                shutil.copy2(path, scratch / path.name)

        for name, doc in mutations.items():
            write_document(scratch / name, doc)

        report = check_corpus(scratch, root)

        if not report.ok:
            return report

        for name in mutations:
            shutil.copytree(scratch / name, spec_dir / name, dirs_exist_ok=True)

        for name in archived or {}:
            shutil.copytree(scratch_archive / name, real_archive / name, dirs_exist_ok=True)

        for name in deletions:
            shutil.rmtree(spec_dir / name, ignore_errors=True)

    return report


def load_or_fail(directory: Path) -> Document:
    """A verb's entry: the document, or the loader's refusal as `ValueError`
    for the verb to name."""

    try:
        return load_document(directory)
    except SpecError as exc:
        raise ValueError("; ".join(exc.problems)) from None


# ----------------------- #
# The human page (S-0056/D-7): the only markdown writer, never the source


def render_markdown(doc: Document) -> str:
    """One document as a page a person reads: the header facts, the prose
    in order with headings from the keys (S-0057/D-2), the rows as a table,
    then invariants, alternatives, questions, phasing and amendments.
    Generated; nothing parses it."""

    lines = [f"# {doc.id} — {doc.title}", ""]
    facts = [
        f"**{k}:** {v}"
        for k, v in (
            ("status", doc.status),
            ("implementation", doc.implementation),
            ("kind", doc.kind),
            ("owner", doc.owner),
            ("depends on", ", ".join(doc.depends_on) or "—"),
            ("informed by", ", ".join(doc.informed_by) or "—"),
            ("superseded by", doc.superseded_by or "—"),
        )
    ]
    lines += [" · ".join(facts), ""]

    for number, section in enumerate(doc.prose(), start=1):
        lines += [f"## {number}. {heading_of(section.key)}", "", section.md.rstrip(), ""]

    if doc.decisions:
        lines += ["## Decisions", "", "| # | Grade | Decision | Paths | Consequence |"]
        lines.append("| --- | --- | --- | --- | --- |")

        for row in doc.decisions:
            paths = " ".join(f"`{p}`" for p in row.paths) or "—"
            text = row.text.replace("|", "\\|")
            consequence = row.consequence.replace("|", "\\|") or "—"
            lines.append(f"| {row.id} | `{row.grade}` | {text} | {paths} | {consequence} |")

        lines.append("")

        for row in doc.decisions:
            details = [
                f"- {label}: {value}"
                for label, value in (
                    ("rationale", row.rationale),
                    ("cites", ", ".join(row.cites)),
                    ("check", f"`{row.check}` ({row.check_state})" if row.check else ""),
                    ("check twin", row.check_twin or ""),
                    ("superseded by", row.superseded_by or ""),
                )
                if value
            ]

            if details:
                lines += [f"**{row.id}**", "", *details, ""]

    if doc.retired:
        lines += ["Retired identifiers: " + ", ".join(doc.retired), ""]

    if doc.invariants:
        lines += ["## Invariants", ""]
        lines += [
            f"- **{i.id}** {i.statement} — paths {' '.join(f'`{p}`' for p in i.paths) or '—'}; "
            f"check `{i.check}`"
            for i in doc.invariants
        ]
        lines.append("")

    if doc.alternatives:
        lines += ["## Alternatives considered", ""]
        lines += [
            f"- **{a.option}** — rejected because {a.rejected_because}" for a in doc.alternatives
        ]
        lines.append("")

    if doc.questions:
        lines += ["## Questions", ""]
        lines += [
            f"- **{q.id}** ({q.status}{', settled by ' + q.settled_by if q.settled_by else ''}) {q.text}"
            for q in doc.questions
        ]
        lines.append("")

    if doc.phasing:
        lines += ["## Phasing", ""]

        for phase in doc.phasing:
            deps = (
                f" — after phase(s) {', '.join(str(d) for d in phase.depends_on)}"
                if phase.depends_on
                else ""
            )
            lines += [
                f"### Phase {phase.phase} — {phase.title}{deps}",
                "",
                phase.intent.strip(),
                "",
            ]
            lines += ["Scope: " + " ".join(f"`{p}`" for p in phase.scope), ""]

            if phase.acceptance:
                lines += ["Acceptance:", "", *(f"- `{a}`" for a in phase.acceptance), ""]

    if doc.amendments:
        lines += ["## Amendments", ""]

        for amendment in doc.amendments:
            lines += [
                f"### {amendment.id} — {amendment.at or ''} — {amendment.title}".rstrip(" —"),
                "",
            ]

            if amendment.changes:
                lines += [
                    f"- {c.subject} {c.field}: {c.before!r} → {c.after!r}"
                    for c in amendment.changes
                ]
                lines.append("")

            if amendment.md.strip():
                lines += [amendment.md.rstrip(), ""]

    return "\n".join(lines).rstrip() + "\n"
