"""`torve spec` — the specification corpus: validation, authoring
mechanics and the read verbs (S-0007/format-validation; S-0054/the-projections-beside-the-code, §5.5; S-0057
S-0057/D-4).

The package owns the format (S-0007/D-12); the skill teaches content. `check`
is the whole `spec-valid` gate and needs no store (S-0007/D-16). The corpus
location is `specs.path` from the runner's configuration — one path,
never a list (S-0013/D-7, S-0016/D-23) — defaulting to `.torve/specs/`, with the
archive and the schemas beside it.

A document is a directory of four YAML files in the model's own shape
(S-0057 S-0057/D-1). `new` derives its number as the maximum over the corpus
and the archive plus one (S-0016/D-24, S-0053/D-10); there is no way to create a
document in a numbering hole and no counter file to merge. `list` is the
index as a query (S-0056/D-7).

`amend`, `fix`, `archive`, `add-decision`, `retire` and `relocate-paths`
are the transactional verbs (S-0025/the-transactional-verbs, S-0025/D-2): each mutates one
document's model through `torve.config.spec_emit`, writes it through the
one serializer (S-0056/D-4), and only when the mutated corpus checks clean —
a red check leaves the tree untouched. `fmt` reports what differs from
the serializer's output and writes nothing.

`project` renders the managed sections beside the code and `--check`
names their drift; `show`, `paths`, `tests` and `why-not` are the
sandbox's read verbs over the corpus and the archive already in the
worktree — progressive disclosure over files the agent could have opened,
never reach into the record, another task or an escalation. Parsing and
rendering only (S-0015/D-6); the reading is `torve.application.colocation`
and the loader.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    add_rows_truncated,
    closing,
    emit_json,
    fail,
    footer,
    header,
    id_list,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.rfc import KINDS
from torve.domain.states import EXIT_CONFIG, EXIT_OK

if TYPE_CHECKING:
    from torve.config.spec import CheckReport

# ----------------------- #

spec_app = typer.Typer(
    no_args_is_help=True,
    help="Validate, author and read the specification corpus.",
)

# S-0004/measurement-defects-to-fix-before-trusting-a-number, printed with `health`'s output verbatim, never paraphrased
# (S-0022/D-7 LOCKED): the first attractive number otherwise becomes a promise to
# someone before anyone wrote down its limits. The printed text carries the
# caveat's substance without the corpus coordinate — the reader of a report
# has no corpus to resolve it.
QUASI_EXPERIMENT_CAVEAT = (
    "Baseline is a quasi-experiment, not an A/B: tasks before "
    "and after are different tasks, done under different conditions. This "
    'supports direction ("iterations fell") and not magnitude ("40% faster").'
)

# Colour supplements the status word, never replaces it (S-0018/D-4); an unknown
# status ("?": a dangling depends_on target) reads as a failure.
_STATUS_STYLES: dict[str, str] = {
    "accepted": STYLE_PASS,
    "draft": STYLE_WARN,
    "superseded": STYLE_DIM,
}

PathsArgument = Annotated[
    list[Path] | None,
    typer.Argument(
        help="Report only findings for these documents; corpus-wide findings always show."
    ),
]


# ....................... #


def corpus_dir(root: Path, config_path: Path | None) -> Path:
    config = load_config(root, config_path)
    resolved = root / config.specs.path

    if not resolved.is_dir():
        raise fail(
            f"configuration error: no corpus directory at {resolved} "
            "(the specs.path configuration key)",
            EXIT_CONFIG,
        )

    return resolved


def _load(root: Path, config_path: Path | None) -> Any:
    from pydantic import ValidationError

    from torve.config.spec import SpecError, load_corpus

    try:
        return load_corpus(corpus_dir(root, config_path))
    except SpecError as exc:
        raise fail("configuration error: " + "; ".join(exc.problems), EXIT_CONFIG) from None
    except ValidationError as exc:
        raise fail(f"configuration error: {exc.errors()[0]['msg']}", EXIT_CONFIG) from None


def _key(number: str) -> str:
    """The four digits of any spelling of a document identifier, or the
    verb's refusal."""

    from torve.domain.spec import number_of

    try:
        return number_of(number)
    except ValueError:
        raise fail(f"configuration error: {number!r} names no document", EXIT_CONFIG) from None


def _document(spec_dir: Path, number: str) -> Path:
    """The directory of one document, or the verb's refusal."""

    from torve.config.spec import document_dirs

    dirs = document_dirs(spec_dir)
    key = _key(number)

    if key not in dirs:
        raise fail(f"configuration error: no document {number!r} under {spec_dir}", EXIT_CONFIG)

    return dirs[key]


# ....................... #


def _selected(lines: list[str], names: set[str]) -> list[str]:
    """Document-scoped findings filtered to *names*; corpus-scoped ones kept."""

    from torve.config.spec import DOCUMENT_DIRNAME

    kept: list[str] = []

    for line in lines:
        head = line.split(":", 1)[0].split("/", 1)[0]

        if DOCUMENT_DIRNAME.match(head) and head not in names:
            continue

        kept.append(line)

    return kept


# ....................... #


@spec_app.command("check")
def check(
    paths: PathsArgument = None,
    fix_rot: Annotated[
        bool,
        typer.Option(
            "--fix-rot",
            help="Retire every path-rotted row through an amendment on its document, "
            "one transaction per document; nothing else changes.",
        ),
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Validate the corpus: every document loads as the model, identifiers
    are unique and never reused, citations resolve into the corpus or the
    archive, the dependency graph is acyclic, no file carries a comment, no
    section carries what a typed list holds, a row changed by hand since
    the tool last stamped it, and rows whose declared paths match nothing
    in the tree. A malformed corpus is a configuration error — exit 3."""

    from torve.config.spec import check_corpus

    spec_dir = corpus_dir(root, config)
    report = check_corpus(spec_dir, root)
    problems, warnings = list(report.problems), list(report.warnings)
    model_problems, model_warnings, rotted = _model_findings(spec_dir, root)
    problems += model_problems
    warnings += model_warnings

    if fix_rot and rotted and not problems:
        warnings = [w for w in warnings if "and nothing in the tree matches" not in w]
        warnings += _retire_rotted(spec_dir, root, rotted)

    if paths:
        names = {p.name for p in paths}
        problems = _selected(problems, names)
        warnings = _selected(warnings, names)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "ok": not problems,
                "count": report.count,
                "problems": problems,
                "warnings": warnings,
            }
        )
    else:
        console = out(fmt)

        for problem in problems:
            console.print(Text(f"PROBLEM {problem}", STYLE_FAIL))

        for warning in warnings:
            console.print(Text(f"WARN    {warning}", STYLE_WARN))

        verdict = "FAIL " if problems else "OK   "
        tail = f", {len(warnings)} warning(s)" if warnings else ""

        closing(
            console,
            f"{verdict} {report.count} document(s), {len(problems)} problem(s){tail}",
            STYLE_FAIL if problems else STYLE_PASS,
        )

    raise typer.Exit(EXIT_OK if not problems else EXIT_CONFIG)


# ....................... #


def _model_findings(spec_dir: Path, root: Path) -> tuple[list[str], list[str], list[Any]]:
    """What the application layer adds to the check: fingerprint drift by
    field (a hand-edited grade or paths is a problem, a hand-edited text a
    warning), and path rot (a warning naming the retiring verb)."""

    from torve.application.decisions import fingerprint_drift, path_rot
    from torve.config.spec import SpecError, load_corpus

    try:
        corpus = load_corpus(spec_dir)
    except SpecError:
        return [], [], []  # the check already reported the load

    problems, warnings = fingerprint_drift(corpus)
    rotted = path_rot(corpus, root)
    warnings += [one.line() for one in rotted]
    return problems, warnings, rotted


# ....................... #


def _retire_rotted(spec_dir: Path, root: Path, rotted: list[Any]) -> list[str]:
    """`check --fix-rot`: one amendment per document, retiring every rotted
    row it carries with the reason recorded (S-0053/D-7). Each document is its
    own transaction; a red check on one leaves that document untouched and
    is reported, never silently skipped."""

    from torve.config.spec import next_amendment
    from torve.config.spec_emit import (
        append_amendment,
        load_or_fail,
        retire_decision,
        write_transaction,
    )

    lines: list[str] = []
    by_document: dict[str, list[Any]] = {}

    for one in rotted:
        by_document.setdefault(one.document, []).append(one)

    for name, rows in sorted(by_document.items()):
        today = date.today().isoformat()
        changes: list[dict[str, Any]] = []

        try:
            doc = load_or_fail(spec_dir / name)

            for one in rows:
                doc = retire_decision(doc, one.identifier, today, reason="path rot")
                changes.append(
                    {
                        "subject": one.identifier,
                        "field": "retired",
                        "before": " ".join(one.paths),
                        "after": "path rot: every declared glob matches nothing in the tree",
                    }
                )

            doc = append_amendment(
                doc,
                next_amendment(doc),
                f"{len(rows)} path-rotted row(s) retired by `torve spec check --fix-rot`",
                today,
                changes,
            )
        except ValueError as exc:
            lines.append(f"{name}: fix-rot refused — {exc}")
            continue

        report = write_transaction(spec_dir, root, {name: doc})

        if report.ok:
            lines.append(f"{name}: retired {', '.join(r.identifier for r in rows)} (path rot)")
        else:
            lines.append(f"{name}: fix-rot aborted — {'; '.join(report.problems)}")

    return lines


# ....................... #


@spec_app.command("fmt")
def fmt(
    number: Annotated[
        str | None,
        typer.Argument(help="One document's number (e.g. 0025); omitted checks the whole corpus."),
    ] = None,
    check_only: Annotated[
        bool, typer.Option("--check", help="Report drift (the default and only mode).")
    ] = True,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Report every document whose files differ from what the serializer
    would write for it. Nothing is written: a hand-authored document is
    legal as it stands, and every verb that changes one writes the
    canonical form."""
    # The one-serializer rule is S-0056/D-4; the docstring is help text.

    from torve.config.spec import document_dirs
    from torve.config.spec_emit import canonical

    spec_dir = corpus_dir(root, config)
    dirs = document_dirs(spec_dir)
    targets = dirs if number is None else {_key(number): _document(spec_dir, number)}
    console = out()
    drifting = refused = 0

    for key in sorted(targets):
        directory = targets[key]

        try:
            expected = canonical(directory)
        except Exception as exc:  # the loader's refusal, named
            refused += 1
            console.print(Text(f"REFUSE  {directory.name}: {exc}", STYLE_FAIL))
            continue

        for file_name, text in expected.items():
            path = directory / file_name

            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                drifting += 1
                console.print(Text(f"DRIFT   {directory.name}/{file_name}", STYLE_WARN))

    ok = refused == 0

    closing(
        console,
        f"{'OK   ' if ok else 'FAIL '} {len(targets)} checked, {drifting} drifting, "
        f"{refused} refused",
        STYLE_PASS if ok else STYLE_FAIL,
    )

    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


# ....................... #


def _finish_transaction(report: CheckReport, success: str) -> None:
    """The shared tail of every transactional verb (S-0025/D-2): print the
    check's refusals and exit 3, or the success line and exit 0."""

    console = out()

    if not report.ok:
        for problem in report.problems:
            console.print(Text(f"PROBLEM {problem}", STYLE_FAIL))

        raise fail(
            f"configuration error: the mutated corpus does not check clean "
            f"({len(report.problems)} problem(s)) — nothing written",
            EXIT_CONFIG,
        )

    console.print(success)
    raise typer.Exit(EXIT_OK)


def _defining(spec_dir: Path, identifier: str) -> Path | None:
    """The corpus document defining a decision identifier, or None. A
    local half alone names a row in every document that has one, so the
    verbs that take a row take the global form."""

    from torve.config.spec import SpecError, document_dirs, load_document
    from torve.domain.spec import LOCAL_ID

    if LOCAL_ID.match(identifier):
        raise fail(
            f"configuration error: {identifier!r} is a local half — name the row as "
            f"S-NNNN/{identifier}",
            EXIT_CONFIG,
        )

    for path in document_dirs(spec_dir).values():
        try:
            if load_document(path).decision(identifier) is not None:
                return path
        except SpecError:
            continue

    return None


# ....................... #


@spec_app.command("amend")
def amend(
    number: Annotated[str, typer.Argument(help="The document being amended, e.g. 0016.")],
    title: Annotated[str, typer.Option("--title", help="The new amendment's title.")],
    row: Annotated[
        str | None, typer.Option("--row", help="The decision row this amendment changes.")
    ] = None,
    grade: Annotated[str | None, typer.Option("--grade", help="The row's new grade.")] = None,
    paths: Annotated[
        list[str] | None, typer.Option("--path", help="The row's new paths (repeat per glob).")
    ] = None,
    text: Annotated[str | None, typer.Option("--text", help="The row's new decision text.")] = None,
    retire: Annotated[
        bool, typer.Option("--retire", help="Retire the row instead of changing it.")
    ] = False,
    reason: Annotated[
        str, typer.Option("--reason", help="Why the row is retired (with --retire).")
    ] = "",
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Append the next amendment to NUMBER's amendments file — one
    load-mutate-dump-check transaction; a red check leaves the tree
    untouched. With --row, the same transaction changes that row's grade,
    paths or text (or retires it) and records the typed diff with the
    prior value on the amendment; the row is re-stamped so a later hand
    edit is caught. The entry's own words are the author's to write."""
    # S-0025/D-4: the number is derived via next_amendment, never chosen. S-0053/D-4:
    # a row's grade or paths change only here, and the diff is written now.

    from torve.config.spec import next_amendment
    from torve.config.spec_emit import (
        amend_row,
        append_amendment,
        load_or_fail,
        retire_decision,
        write_transaction,
    )

    spec_dir = corpus_dir(root, config)
    directory = _document(spec_dir, number)

    if row is None and (grade or paths or text or retire):
        raise fail(
            "configuration error: --grade, --path, --text and --retire need --row", EXIT_CONFIG
        )

    today = date.today().isoformat()
    changes: list[dict[str, Any]] = []

    try:
        doc = load_or_fail(directory)
        amendment = f"{doc.id}/{next_amendment(doc)}"

        if row is not None and retire:
            before = doc.decision(row)
            doc = retire_decision(doc, row, today, reason=reason)
            changes.append(
                {
                    "subject": row,
                    "field": "retired",
                    "before": " ".join(before.paths) if before else None,
                    "after": reason or "retired",
                }
            )
        elif row is not None:
            doc, changes = amend_row(doc, row, grade=grade, paths=paths, new_text=text)

        doc = append_amendment(doc, amendment, title, today, changes)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(spec_dir, root, {directory.name: doc})
    what = f" ({row} {'retired' if retire else 'changed'})" if row else ""
    _finish_transaction(
        report, f"appended {amendment} to {directory.name}{what} — write the entry's own words"
    )


# ....................... #


@spec_app.command("fix")
def fix(
    identifier: Annotated[str, typer.Argument(help="The decision row whose text is fixed.")],
    text: Annotated[str, typer.Argument(help="The row's corrected text.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Editorial: replace IDENTIFIER's text and re-stamp the row, recording
    the before and after under the document's editorial list — never an
    amendment number. For a typo; a rewording that changes the rule's
    meaning is an amendment."""
    # S-0053/D-4's editorial lane.

    from torve.config.spec_emit import fix_row_text, load_or_fail, write_transaction

    spec_dir = corpus_dir(root, config)
    defining = _defining(spec_dir, identifier)

    if defining is None:
        raise fail(
            f"configuration error: no decision {identifier!r} defined in the corpus", EXIT_CONFIG
        )

    try:
        doc, _ = fix_row_text(load_or_fail(defining), identifier, text)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(spec_dir, root, {defining.name: doc})
    _finish_transaction(report, f"fixed {identifier}'s text in {defining.name} (editorial)")


# ....................... #


@spec_app.command("archive")
def archive(
    number: Annotated[str, typer.Argument(help="The document to retire into the archive.")],
    superseded_by: Annotated[
        str, typer.Option("--superseded-by", help="The document that now stands for it.")
    ],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Move NUMBER's directory into the archive beside the corpus path,
    keeping its name and every identifier, with status superseded — one
    transaction: the corpus without it must check clean, or nothing moves.
    Archived identifiers still resolve through show and the record."""
    # S-0053/D-8. Deletion from the corpus path is the one thing this verb does
    # that no other verb may.

    from torve.config.spec import archive_dir
    from torve.config.spec_emit import archive_document, load_or_fail, write_transaction

    spec_dir = corpus_dir(root, config)
    directory = _document(spec_dir, number)
    target = archive_dir(spec_dir) / directory.name

    if target.exists():
        raise fail(f"configuration error: {target} already exists", EXIT_CONFIG)

    try:
        archived = archive_document(load_or_fail(directory), superseded_by)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    # The check that guards the move sees the document in the archive and
    # the archive itself, so a document others cite can leave (S-0053/A-1).
    report = write_transaction(
        spec_dir, root, {}, deletions=(directory.name,), archived={directory.name: archived}
    )
    _finish_transaction(
        report, f"archived {directory.name} → {target} (superseded by {superseded_by})"
    )


# ....................... #


@spec_app.command("add-decision")
def add_decision(
    number: Annotated[str, typer.Argument(help="The document gaining a decision, e.g. 0025.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Append a decision row under the next free identifier in NUMBER's own
    family, and print that identifier. The grade, paths and text are left
    for the author — one transaction; a red check leaves the tree
    untouched."""
    # S-0025/D-3 LOCKED: the row's grade is written as OPEN, the vocabulary's own
    # "not yet decided" value — never a chosen judgement.

    from torve.config.spec import next_decision
    from torve.config.spec_emit import append_decision, load_or_fail, write_transaction

    spec_dir = corpus_dir(root, config)
    directory = _document(spec_dir, number)

    try:
        doc = load_or_fail(directory)
        identifier = f"{doc.id}/{next_decision(doc)}"
        doc = append_decision(doc, identifier)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(spec_dir, root, {directory.name: doc})
    _finish_transaction(
        report, f"added {identifier} to {directory.name} — write its grade, paths and text"
    )


# ....................... #


@spec_app.command("retire")
def retire(
    identifier: Annotated[str, typer.Argument(help="The decision identifier to retire.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Retire IDENTIFIER: remove its row and record it in the document's
    retired list, never reused — one transaction; a red check leaves the
    tree untouched. Prefer `amend --row X --retire --reason …`, which
    records why."""
    # S-0025/D-6: executes S-0016/D-1 whole. Whether every remaining citation still
    # resolves is the transaction's own check, not a separate pre-check.

    from torve.config.spec_emit import load_or_fail, retire_decision, write_transaction

    spec_dir = corpus_dir(root, config)
    defining = _defining(spec_dir, identifier)

    if defining is None:
        raise fail(
            f"configuration error: no decision {identifier!r} defined in the corpus", EXIT_CONFIG
        )

    try:
        doc = retire_decision(load_or_fail(defining), identifier, date.today().isoformat())
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(spec_dir, root, {defining.name: doc})
    _finish_transaction(report, f"retired {identifier} in {defining.name}")


# ....................... #


@spec_app.command("relocate-paths")
def relocate_paths(
    old: Annotated[str, typer.Argument(help="The exact paths glob being relocated.")],
    new: Annotated[str, typer.Argument(help="Its replacement.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Sweep every document in the corpus, replacing OLD with NEW wherever
    a row's paths carry it exactly, and print the touched rows — one
    transaction; a red check leaves the tree untouched. Decision text is
    never touched."""
    # S-0025/D-7: the paths are mechanical; text naming the old location stays
    # hand-written where the text itself names it.

    from torve.config.spec import document_dirs
    from torve.config.spec_emit import load_or_fail, write_transaction
    from torve.config.spec_emit import relocate_paths as relocate
    from torve.domain.spec import Document

    spec_dir = corpus_dir(root, config)
    mutations: dict[str, Document] = {}
    touched: dict[str, list[str]] = {}

    for directory in document_dirs(spec_dir).values():
        try:
            doc, rows = relocate(load_or_fail(directory), old, new)
        except ValueError:
            continue

        if rows:
            mutations[directory.name], touched[directory.name] = doc, rows

    if not mutations:
        raise fail(f"configuration error: no row's paths carry {old!r}", EXIT_CONFIG)

    report = write_transaction(spec_dir, root, mutations)

    if report.ok:
        console = out()

        for name, ids in sorted(touched.items()):
            console.print(f"{name}: {', '.join(sorted(ids))}")

    count = sum(len(ids) for ids in touched.values())
    _finish_transaction(
        report,
        f"relocated {old!r} to {new!r} in {count} row(s) across {len(mutations)} document(s)",
    )


# ....................... #


def _show_lines(found: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, value) rows for the text rendering, empties dropped."""

    def joined(key: str) -> str:
        items: list[str] = found.get(key) or []
        return ", ".join(items)

    kind = found["kind"]

    if kind == "decision":
        rows = [
            ("grade", str(found.get("grade") or "")),
            ("decision", str(found.get("text") or "")),
            ("paths", joined("paths")),
            ("consequence", str(found.get("consequence") or "")),
            ("rationale", str(found.get("rationale") or "")),
            ("cites", joined("cites")),
            ("check", str(found.get("check") or "")),
            ("defined in", str(found.get("defined_in") or "")),
            ("cited by", joined("cited_by")),
            ("retired in", str(found.get("retired_in") or "")),
            ("archived", "yes" if found.get("archived") else ""),
        ]
    elif kind == "invariant":
        rows = [
            ("statement", str(found.get("statement") or "")),
            ("paths", joined("paths")),
            ("check", str(found.get("check") or "")),
            ("defined in", str(found.get("defined_in") or "")),
            ("archived", "yes" if found.get("archived") else ""),
        ]
    elif kind == "question":
        rows = [
            ("question", str(found.get("text") or "")),
            ("status", str(found.get("status") or "")),
            ("settled by", str(found.get("settled_by") or "")),
            ("defined in", str(found.get("defined_in") or "")),
            ("archived", "yes" if found.get("archived") else ""),
        ]
    elif kind == "amendment":
        rows = [
            ("defined in", str(found["defined_in"])),
            ("heading", str(found["heading"])),
            ("rows citing it", joined("rows")),
            ("archived", "yes" if found.get("archived") else ""),
        ]
    else:
        phases: list[dict[str, Any]] = found.get("phases") or []
        rows = [
            ("title", str(found["title"])),
            ("status", str(found["status"])),
            ("implementation", str(found["implementation"])),
            ("depends on", joined("depends_on")),
            ("amended by", joined("amended_by")),
            ("description", str(found["description"])),
            ("sections", joined("sections")),
            ("phases", ", ".join(f"{e['phase']}: {e['title']}" for e in phases)),
            ("archived", "yes" if found.get("archived") else ""),
        ]

    return [(label, value) for label, value in rows if value]


# ....................... #


@spec_app.command("show")
def show(
    identifier: Annotated[
        str,
        typer.Argument(
            help="A corpus identifier: a decision, invariant, question, amendment or document "
            "number."
        ),
    ],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Resolve one corpus identifier from the same load `check` runs: a row
    as it stands with its paths, consequence, rationale, citations, check
    and what cites it; an invariant, a question, an amendment or a
    document. No cache, no store — an undefined identifier is a
    configuration error naming the nearest family. An archived identifier
    answers, marked."""
    # The one-parse rule is S-0007/D-28; the docstring is `show`'s help text
    # and stays free of corpus coordinates.

    from torve.config.spec import lookup, next_number

    spec_dir = corpus_dir(root, config)
    found = lookup(spec_dir, identifier)

    if found is None:
        family = (
            f"the next free document number is {next_number(spec_dir):04d}"
            if identifier.isdigit() or (identifier.startswith("S-") and "/" not in identifier)
            else "an item is `S-NNNN/<local>`; `torve spec show S-NNNN` lists what a document defines"
        )

        raise fail(f"configuration error: nothing defines {identifier!r} — {family}", EXIT_CONFIG)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, **found})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "spec show", f"{identifier} · {found['kind']}")

    for label, value in _show_lines(found):
        line = Text(f"  {label:>14}  ", STYLE_DIM)
        line.append(value, STYLE_ID if label in ("defined in", "next free") else "")
        console.print(line)

    raise typer.Exit(EXIT_OK)


# ....................... #


@spec_app.command("render")
def render(
    number: Annotated[str, typer.Argument(help="The document to render, e.g. 0056.")],
    output: Annotated[
        Path | None, typer.Option("--out", help="Write the page here instead of printing it.")
    ] = None,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """The document as a markdown page for a person: header facts, prose
    with headings from the section keys, the rows as a table, invariants,
    alternatives, questions, phasing and amendments. The one markdown
    writer, and never the source of anything."""
    # S-0056/D-7, S-0057/D-2.

    from torve.config.spec import archive_dirs, document_dirs
    from torve.config.spec_emit import load_or_fail, render_markdown

    spec_dir = corpus_dir(root, config)
    key = _key(number)
    dirs = {**archive_dirs(spec_dir), **document_dirs(spec_dir)}

    if key not in dirs:
        raise fail(f"configuration error: no document {number!r} under {spec_dir}", EXIT_CONFIG)

    try:
        page = render_markdown(load_or_fail(dirs[key]))
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    if output is None:
        out().print(page, end="", markup=False, highlight=False)
        raise typer.Exit(EXIT_OK)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    out().print(f"wrote {output}")
    raise typer.Exit(EXIT_OK)


# ....................... #


@spec_app.command("list")
def list_cmd(
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every document in the corpus path with its status, implementation
    and dependencies — the index as a query, never a file."""
    # S-0056/D-7: what INDEX.md was, answered instead of generated.

    from torve.config.spec import archive_dirs, document_dirs, next_number
    from torve.domain.spec import number_of

    spec_dir = corpus_dir(root, config)
    corpus = _load(root, config)
    rows = [
        {
            "id": doc.id,
            "number": number_of(doc.id),
            "title": doc.title,
            "kind": doc.kind,
            "status": doc.status,
            "implementation": doc.implementation,
            "depends_on": list(doc.depends_on),
            "amended_by": doc.amended_by(),
            "description": doc.routing(),
            "file": Path(doc.path).name,
        }
        for doc in corpus.documents
        if not doc.archived
    ]
    allocated = next_number(spec_dir)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "documents": rows,
                "archived": len(archive_dirs(spec_dir)),
                "next_number": f"{allocated:04d}",
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(
        console,
        "spec list",
        f"{len(rows)} document(s), {len(document_dirs(spec_dir))} directory(ies)",
    )

    for one in rows:
        status, implementation = str(one["status"]), str(one["implementation"])
        line = Text(f"  {one['id']}  ", STYLE_ID)
        line.append(f"{status:<10}", _STATUS_STYLES.get(status, STYLE_FAIL))
        line.append(f"{implementation:<10}", STYLE_DIM)
        line.append(str(one["title"]))
        depends = cast("list[str]", one["depends_on"])

        if depends:
            line.append(f"  ← {', '.join(depends)}", STYLE_DIM)

        console.print(line)

    closing(
        console,
        f"{len(archive_dirs(spec_dir))} archived; the next number is {allocated:04d}",
        STYLE_DIM,
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@spec_app.command("new")
def new(
    title: Annotated[str, typer.Argument(help="Document title.")],
    kind: Annotated[str, typer.Option("--kind", help="design (default) or convention.")] = "design",
    owner: Annotated[str, typer.Option("--owner", help="The document's owner.")] = "",
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Create the next document: the number is derived as the maximum over
    the corpus and the archive plus one — never chosen, never reused — and
    the directory holds the smallest document that checks, written by the
    one serializer with each file's schema header line."""

    from torve.config.spec import dirname_of, next_number
    from torve.config.spec_emit import new_document, write_document

    if kind not in KINDS:
        raise fail(
            f"configuration error: kind {kind!r} is not one of {', '.join(KINDS)}", EXIT_CONFIG
        )

    spec_dir = corpus_dir(root, config)
    allocated = next_number(spec_dir)
    number = f"{allocated:04d}"
    directory = spec_dir / dirname_of(number)
    doc = new_document(number, title, owner or _git_user(root) or "owner", kind)

    try:
        directory.mkdir()
    except FileExistsError:
        raise fail(
            f"configuration error: {directory.name} was created by another "
            "process — re-run to take the next number",
            EXIT_CONFIG,
        ) from None

    write_document(directory, doc)
    console = out()
    console.print(f"created {directory}")
    console.print("next: write the description, the summary section and the first rows")


def _git_user(root: Path) -> str:
    import subprocess

    try:
        done = subprocess.run(
            ["git", "-C", str(root), "config", "user.name"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""

    return done.stdout.strip() if done.returncode == 0 else ""


# ....................... #


@spec_app.command("graph")
def graph(
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The dependency graph as a tree — dependents nested under what they
    build on, roots first, standalone documents as bare roots, each node
    carrying its status and implementation state. Documents both accepted
    and complete are finished business: omitted from the tree (their
    dependents attach where they stood) and counted in a dim line. Also
    shows the inheritance hazards the corpus check would flag."""

    from rich.tree import Tree

    from torve.config.spec import check_graph

    corpus = _load(root, config)
    documents = {doc.id: doc for doc in corpus.documents if not doc.archived}
    frontmatter = {
        number: {"status": doc.status, "implementation": doc.implementation}
        for number, doc in documents.items()
    }
    depends = {number: list(doc.depends_on) for number, doc in documents.items()}

    edges = [
        {
            "from": number,
            "from_status": str(frontmatter[number].get("status", "?")),
            "to": target,
            "to_status": str(frontmatter.get(target, {}).get("status", "?")),
        }
        for number in sorted(depends)
        for target in depends[number]
    ]

    problems, warnings = check_graph(documents)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "edges": edges, "problems": problems, "warnings": warnings})
        return

    console = out(fmt)
    header(console, "spec graph", f"{len(documents)} document(s), {len(edges)} edge(s)")
    console.print()
    dependents: dict[str, list[str]] = {}

    for number, targets in depends.items():
        for target in targets:
            dependents.setdefault(target, []).append(number)

    seen: set[str] = set()
    omitted: list[str] = []

    def done(number: str) -> bool:
        front = frontmatter.get(number, {})

        return (
            str(front.get("status")) == "accepted"
            and str(front.get("implementation")) == "complete"
        )

    def grow(branch: Tree, number: str) -> None:
        front = frontmatter.get(number, {})
        status = str(front.get("status", "?"))

        if number in seen:
            if not done(number):
                # A node expands under its first parent only; here it is a
                # back-reference, dimmed whole so the repeat never reads as
                # a second document.
                branch.add(Text(f"{number} {status} ↑", STYLE_DIM))

            return

        seen.add(number)

        if done(number):
            # Finished business: the node itself is omitted and counted;
            # its dependents attach where it stood.
            omitted.append(number)

            for child in sorted(dependents.get(number, [])):
                grow(branch, child)

            return

        label = Text(number, STYLE_ID)
        label.append(" ")
        label.append(status, style=_STATUS_STYLES.get(status, STYLE_FAIL))
        implementation = str(front.get("implementation", ""))

        if implementation and implementation != "none":
            label.append(f" {implementation}", style=STYLE_DIM)

        node = branch.add(label)

        for child in sorted(dependents.get(number, [])):
            grow(node, child)

    tree = Tree("", hide_root=True, guide_style=STYLE_DIM)

    for number in sorted(depends):
        if not depends[number]:
            grow(tree, number)

    # Anything unreachable from a root — a dependency cycle, or a document
    # whose only dependency dangles — still renders rather than vanishing.
    for number in sorted(depends):
        if number not in seen:
            grow(tree, number)

    console.print(tree)

    if omitted:
        console.print()

        footer(
            console, f"… {len(omitted)} accepted and complete, omitted: {id_list(sorted(omitted))}"
        )

    for problem in problems:
        console.print(Text(f"PROBLEM {problem}", STYLE_FAIL))

    for warning in warnings:
        console.print(Text(f"WARN    {warning}", STYLE_WARN))


# ....................... #


@spec_app.command("health")
def health(
    document: Annotated[
        str | None,
        typer.Argument(
            help="Report only this document's decisions (e.g. 0022); omitted reports the "
            "whole corpus."
        ),
    ] = None,
    floor: Annotated[
        int,
        typer.Option(
            "--floor",
            min=1,
            help="Observations a reading needs before it is asserted; counts and "
            "denominators print below it regardless.",
        ),
    ] = 5,  # torve.application.specquality.DEFAULT_FLOOR, repeated: kept lazily imported below
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Per-decision populations over every task that inherited the row: how
    many touched its declared paths, how many cited it in their log, and —
    only once there are enough observations to say anything — the one
    reading that shape of evidence supports. The grade compared is always
    the one copied onto the contract at mint time, never the row as the
    corpus stands today. Never edits a decision table, proposes no text
    and calls no model: this is evidence for a human writing an amendment,
    not a verdict. No single corpus score is computed anywhere. The
    corpus-wide view (no document given) also prints landed changes beside
    the operator attention already on record for them — feedback minutes
    and escalations triaged."""
    # The docstring is help text and carries no corpus coordinates; the
    # rules it states are S-0022/D-2, S-0022/D-1, S-0022/D-3 and S-0022/D-12 in that order.

    from torve.application import specquality

    spec_dir = corpus_dir(root, config)
    report = specquality.decision_report(root.resolve(), spec_dir, floor=floor)
    populations = report["populations"]

    if document is not None:
        number = _key(document)
        wanted = specquality.identifiers_for_document(spec_dir, number)

        if wanted is None:
            raise fail(
                f"configuration error: no document {document!r} under {spec_dir}", EXIT_CONFIG
            )

        populations = [p for p in populations if p["identifier"] in wanted]

    # S-0022/D-12: the operator-attention line is a corpus-wide fact — a
    # single-document filter is a decision-level view and has no bearing
    # on it, so it prints only when the whole corpus is in view.
    attention = (
        specquality.operator_attention(root.resolve(), floor=floor) if document is None else None
    )

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": report["schema_version"],
                "floor": report["floor"],
                "document": document,
                "caveat": QUASI_EXPERIMENT_CAVEAT,
                "populations": populations,
                "operator_attention": attention,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    subject = f"document {document}" if document else f"{len(populations)} decision(s), corpus-wide"
    header(console, "spec health", subject)
    console.print()
    console.print(Text(QUASI_EXPERIMENT_CAVEAT, STYLE_DIM))
    console.print(
        Text(
            f"readings suppressed below {floor} observation(s) — counts and denominators "
            "print regardless; no single corpus score is computed",
            STYLE_DIM,
        )
    )

    if attention is not None:
        console.print(Text(specquality.render_operator_attention(attention), STYLE_DIM))

    console.print()

    if not populations:
        closing(console, "no decisions inherited by any task yet", STYLE_DIM)
        raise typer.Exit(EXIT_OK)

    table = make_table(
        "decision", "grade", "inherited", "touched", "cited", "reading", title="Decision health"
    )

    rows: list[tuple[Text | str, ...]] = [
        (
            Text(str(pop["identifier"]), STYLE_ID),
            str(pop["grade"] or "mixed"),
            f"{pop['inherited']} ({pop['inherited_landed']} landed)",
            str(pop["touched"]),
            str(pop["cited"]),
            Text(str(pop["reading"] or "—"), STYLE_WARN if pop["reading"] else STYLE_DIM),
        )
        for pop in populations
    ]

    withheld = add_rows_truncated(table, rows, limit=50)
    console.print(table)

    if withheld:
        footer(console, f"… {withheld} more decision(s) (see JSON)")

    readings = [p for p in populations if p["reading"]]

    if readings:
        console.print()

        for pop in readings:
            console.print(Text(f"  {pop['identifier']}: {pop['reading_detail']}", STYLE_WARN))

    with_claims = [p for p in populations if p["decided_claims"]]

    if with_claims:
        console.print()
        console.print(Text("Decided claims, for a human to read for agreement:", STYLE_DIM))

        for pop in with_claims:
            for claim in pop["decided_claims"]:
                console.print(
                    Text(f"  {pop['identifier']} · {claim['task']}: {claim['claim']}", STYLE_DIM)
                )

    console.print()

    closing(
        console,
        f"{len(populations)} decision(s) reported, {len(readings)} with a reading",
        STYLE_WARN if readings else STYLE_PASS,
    )

    raise typer.Exit(EXIT_OK)


# ....................... #


def _commits_citing(root: Path, identifier: str) -> list[dict[str, str]]:
    """The commits whose `Torve-Decisions` trailer grades this row — the
    identifier followed by `=`, so a longer number never matches a shorter
    one's prefix."""

    import subprocess

    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--fixed-strings",
                f"--grep={identifier}=",
                "--format=%h%x09%ad%x09%s",
                "--date=short",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []

    if done.returncode != 0:
        return []

    return [
        {"sha": sha, "at": at, "subject": subject}
        for sha, at, subject in (
            line.split("\t", 2) for line in done.stdout.splitlines() if line.count("\t") == 2
        )
    ]


@spec_app.command("cites")
def cites_cmd(
    identifier: Annotated[
        str, typer.Argument(help="A decision, invariant, question or amendment id.")
    ],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Who cites an identifier — the row's side of the link: the code and
    docs lines that mention it, the landings whose entries cite it, the
    amendments that changed it, the documents whose rows or prose cite
    it, and the commits whose trailers grade it."""
    # S-0057 S-0057/D-10; the trailers are S-0057/D-13, decided in phase 4.

    from torve.config.spec import cited_in, tree_citations

    corpus = _load(root, config)
    code = [
        {"file": name, "line": line}
        for name, line, ident, _legacy in tree_citations(root)
        if ident == identifier
    ]
    landings = [
        {
            "task": landing.task,
            "attempt": landing.attempt,
            "commit": landing.commit,
            "document": Path(doc.path).name,
        }
        for doc in corpus.documents
        for landing in doc.landings
        if any(entry.decision == identifier for entry in landing.entries)
    ]
    amendments = [
        {"id": amendment.id, "document": Path(doc.path).name}
        for doc in corpus.documents
        for amendment in doc.amendments
        if any(change.subject == identifier for change in amendment.changes)
    ]
    documents = cited_in(corpus, identifier)
    commits = _commits_citing(root, identifier)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "identifier": identifier,
                "code": code,
                "landings": landings,
                "amendments": amendments,
                "documents": documents,
                "commits": commits,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    total = len(code) + len(landings) + len(amendments) + len(documents) + len(commits)
    header(console, "spec cites", f"{identifier} · {total} citation(s)")

    for one in code:
        console.print(Text(f"  code       {one['file']}:{one['line']}", ""))

    for one in landings:
        commit = f" @ {str(one['commit'])[:10]}" if one["commit"] else ""
        console.print(
            Text(
                f"  landing    {one['task']} attempt {one['attempt']}{commit} ({one['document']})",
                "",
            )
        )

    for one in amendments:
        console.print(Text(f"  amendment  {one['id']} ({one['document']})", ""))

    for name in documents:
        console.print(Text(f"  document   {name}", ""))

    for named in commits:
        console.print(
            Text(f"  commit     {named['sha']} {named['at']} {named['subject']}", STYLE_DIM)
        )

    if not total:
        closing(console, "nothing cites it yet", STYLE_DIM)

    raise typer.Exit(EXIT_OK)


# ----------------------- #
# The read verbs beside the code (S-0054)


@spec_app.command("project")
def project_cmd(
    check: Annotated[
        bool, typer.Option("--check", help="Compare instead of writing; drift exits 3.")
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Render, into every directory a standing row or an accepted phase
    names, a managed section of that directory's AGENTS.md listing the
    decisions and invariants governing it, and a root index of governed
    directories. Text outside the markers is never touched. With --check,
    a section that differs from what would be rendered is drift, named by
    file, and the exit code is 3."""

    from torve.application.colocation import project

    try:
        projection = project(root, corpus_dir(root, config), check=check)
    except Exception as exc:  # the loader's refusals, named
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "check": check,
                "ok": projection.ok,
                "directories": sorted(projection.sections),
                "written": projection.written,
                "drifted": projection.drifted,
                "removed": projection.removed,
            }
        )
        raise typer.Exit(EXIT_OK if projection.ok else EXIT_CONFIG)

    console = out(fmt)

    for rel in projection.written:
        console.print(Text(f"WROTE   {rel}", STYLE_PASS))

    for rel in projection.removed:
        console.print(Text(f"REMOVED {rel}", STYLE_WARN))

    for rel in projection.drifted:
        console.print(Text(f"DRIFT   {rel}", STYLE_FAIL))

    count = len(projection.sections)

    if check:
        verdict = "OK   " if projection.ok else "FAIL "
        closing(
            console,
            f"{verdict} {count} governed director{'y' if count == 1 else 'ies'}, "
            f"{len(projection.drifted)} drifting",
            STYLE_PASS if projection.ok else STYLE_FAIL,
        )
        raise typer.Exit(EXIT_OK if projection.ok else EXIT_CONFIG)

    closing(
        console,
        f"{count} governed director{'y' if count == 1 else 'ies'}, "
        f"{len(projection.written)} written, {len(projection.removed)} removed",
        STYLE_PASS,
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


def _row_payload(doc: Any, row: Any) -> dict[str, Any]:
    return {
        "kind": "decision",
        "identifier": row.id,
        "grade": row.grade,
        "text": row.text,
        "paths": list(row.paths),
        "consequence": row.consequence,
        "rationale": row.rationale,
        "cites": list(row.cites),
        "check": row.check,
        "check_state": row.check_state,
        "check_twin": row.check_twin,
        "defined_in": Path(doc.path).name if doc.path else doc.id,
        "archived": doc.archived,
    }


@spec_app.command("paths")
def paths_cmd(
    path: Annotated[str, typer.Argument(help="A file or directory path, as the tree spells it.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The rows and invariants governing a path, and its coverage state:
    governed, ungoverned (the frontier, never a finding) or retired (only
    archived rows ever named it)."""

    from torve.application.colocation import directory_of
    from torve.application.decisions import coverage

    corpus = _load(root, config)
    state = coverage(corpus, path)
    rows = [
        _row_payload(doc, row)
        for doc in corpus.standing()
        if not doc.superseded_by
        for row in doc.decisions
        if any(
            directory_of(glob) in (path, directory_of(path))
            or path.startswith(directory_of(glob).rstrip("/") + "/")
            for glob in row.paths
        )
    ]
    invariants = [
        {"identifier": inv.id, "statement": inv.statement, "check": inv.check, "defined_in": doc.id}
        for doc in corpus.standing()
        if not doc.superseded_by
        for inv in doc.invariants
        if any(path.startswith(directory_of(glob).rstrip("/")) for glob in inv.paths)
    ]

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "path": path,
                "coverage": state,
                "decisions": rows,
                "invariants": invariants,
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "spec paths", path)
    console.print(Text(f"  coverage: {state}", STYLE_DIM if state == "governed" else STYLE_WARN))

    for row in rows:
        console.print(Text(f"  {row['identifier']} ({row['grade']}) — {row['text']}", ""))

    for inv in invariants:
        console.print(
            Text(f"  {inv['identifier']} — {inv['statement']} — `{inv['check']}`", STYLE_DIM)
        )

    raise typer.Exit(EXIT_OK)


# ....................... #


@spec_app.command("tests")
def tests_cmd(
    identifier: Annotated[str, typer.Argument(help="A decision or invariant id.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """What proves a row: its check, the twin that proves the check can fail,
    and the invariants whose paths overlap the row's."""

    from torve.application.colocation import directory_of

    corpus = _load(root, config)
    hit = corpus.decision(identifier)
    checks: list[dict[str, Any]] = []

    if hit is not None:
        doc, row = hit

        if row.check:
            checks.append({"kind": "check", "command": row.check, "state": row.check_state})

        if row.check_twin:
            checks.append({"kind": "twin", "path": row.check_twin})

        dirs = {directory_of(glob) for glob in row.paths}

        for other in corpus.standing():
            for inv in other.invariants:
                if any(directory_of(glob) in dirs for glob in inv.paths):
                    checks.append({"kind": "invariant", "identifier": inv.id, "command": inv.check})
    else:
        for doc in corpus.documents:
            for inv in doc.invariants:
                if inv.id == identifier:
                    checks.append({"kind": "check", "command": inv.check, "state": "invariant"})

    if hit is None and not checks:
        raise fail(f"configuration error: nothing defines {identifier!r}", EXIT_CONFIG)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "identifier": identifier, "proofs": checks})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "spec tests", identifier)

    if not checks:
        closing(console, "nothing proves this row yet — no check, no twin, no invariant", STYLE_DIM)
        raise typer.Exit(EXIT_OK)

    for one in checks:
        console.print(Text("  " + " · ".join(f"{k}={v}" for k, v in one.items()), ""))

    raise typer.Exit(EXIT_OK)


# ....................... #


@spec_app.command("why-not")
def why_not_cmd(
    text: Annotated[str, typer.Argument(help="Words from the option you are about to propose.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """The alternatives the corpus already rejected whose option or reason
    matches these words, with why each lost — the negative space every
    executor re-proposes when nobody wrote that the option was closed."""

    corpus = _load(root, config)
    needle = text.lower()
    matches = [
        {
            "option": alternative.option,
            "rejected_because": alternative.rejected_because,
            "defined_in": Path(doc.path).name if doc.path else doc.id,
            "archived": doc.archived,
        }
        for doc in corpus.documents
        for alternative in doc.alternatives
        if needle in alternative.option.lower() or needle in alternative.rejected_because.lower()
    ]

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "query": text, "alternatives": matches})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "spec why-not", text)

    if not matches:
        closing(
            console,
            "no rejected alternative matches — nothing in the corpus closed this",
            STYLE_DIM,
        )
        raise typer.Exit(EXIT_OK)

    for one in matches:
        where = f"{one['defined_in']}{' (archived)' if one['archived'] else ''}"
        console.print(Text(f"  {one['option']}", ""))
        console.print(Text(f"    rejected because: {one['rejected_because']} — {where}", STYLE_DIM))

    raise typer.Exit(EXIT_OK)
