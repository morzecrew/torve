"""`torve rfc` — corpus validation and authoring mechanics (RFC 0007 §3a).

The package owns the format (D-7.12); the skill teaches content. `check` is
the whole `rfc-valid` gate and needs no store (D-7.16). The corpus location is
`rfcs.path` from the runner's configuration — one path, never a list (D-13.7,
D-A.16) — defaulting to `rfcs/`.

A document is one YAML file in the model's own shape (RFC 0056 D-56.1).
`new` derives its number as the maximum over the corpus and the archive
plus one (D-A.17, D-53.10); there is no way to create a document in a
numbering hole and no counter file to merge. `list` is the index as a
query (D-56.7).

`amend`, `fix`, `archive`, `add-decision`, `retire` and `relocate-paths`
are the transactional verbs (RFC 0025 §5.3, D-25.2): each mutates one
document's model through `torve.config.rfc_emit`, writes it through the
one serializer (D-56.4), and only when the mutated corpus checks clean —
a red check leaves the tree untouched. `fmt` reports what differs from
the serializer's output and writes nothing.
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

rfc_app = typer.Typer(no_args_is_help=True, help="Validate and author the RFC corpus.")

TEMPLATE_TITLE = "RFC NNNN — <Title>"

# RFC 0004 §6a, printed with `health`'s output verbatim, never paraphrased
# (D-22.7 LOCKED): the first attractive number otherwise becomes a promise to
# someone before anyone wrote down its limits. The printed text carries the
# caveat's substance without the corpus coordinate — the reader of a report
# has no corpus to resolve it.
QUASI_EXPERIMENT_CAVEAT = (
    "Baseline is a quasi-experiment, not an A/B: tasks before "
    "and after are different tasks, done under different conditions. This "
    'supports direction ("iterations fell") and not magnitude ("40% faster").'
)

# Colour supplements the status word, never replaces it (D-18.4); an unknown
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
    resolved = root / config.rfcs.path

    if not resolved.is_dir():
        raise fail(
            f"configuration error: no corpus directory at {resolved} "
            "(the rfcs.path configuration key)",
            EXIT_CONFIG,
        )

    return resolved


# ....................... #


def _selected(lines: list[str], names: set[str]) -> list[str]:
    """Document-scoped findings filtered to *names*; corpus-scoped ones kept."""

    from torve.config.spec import RFC_FILENAME

    kept: list[str] = []

    for line in lines:
        head = line.split(":", 1)[0]

        if RFC_FILENAME.match(head) and head not in names:
            continue

        kept.append(line)

    return kept


# ....................... #


def _own_problems(problems: list[str], name: str) -> list[str]:
    """This one document's own check problems — never the corpus-scoped
    ones (INDEX drift, a cycle), which do not open with a filename and so
    never block formatting a document that is itself clean."""

    return [p for p in problems if p.split(":", 1)[0] == name]


# ....................... #


@rfc_app.command("check")
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
    archive, the dependency graph is acyclic, no document carries a
    comment, a row changed by hand since the tool last stamped it, and
    rows whose declared paths match nothing in the tree. A malformed
    corpus is a configuration error — exit 3."""

    from torve.config.spec import check_corpus

    rfc_dir = corpus_dir(root, config)
    report = check_corpus(rfc_dir, root)
    problems, warnings = list(report.problems), list(report.warnings)
    model_problems, model_warnings, rotted = _model_findings(rfc_dir, root)
    problems += model_problems
    warnings += model_warnings

    if fix_rot and rotted and not problems:
        warnings = [w for w in warnings if "and nothing in the tree matches" not in w]
        warnings += _retire_rotted(rfc_dir, root, rotted)

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
            f"{verdict} {report.count} RFC(s), {len(problems)} problem(s){tail}",
            STYLE_FAIL if problems else STYLE_PASS,
        )

    raise typer.Exit(EXIT_OK if not problems else EXIT_CONFIG)


# ....................... #


def _model_findings(rfc_dir: Path, root: Path) -> tuple[list[str], list[str], list[Any]]:
    """What the application layer adds to the check: fingerprint drift by
    field (a hand-edited grade or paths is a problem, a hand-edited text a
    warning), and path rot (a warning naming the retiring verb)."""

    from torve.application.decisions import fingerprint_drift, path_rot
    from torve.config.spec import SpecError, load_corpus

    try:
        corpus = load_corpus(rfc_dir)
    except SpecError:
        return [], [], []  # the check already reported the load

    problems, warnings = fingerprint_drift(corpus)
    rotted = path_rot(corpus, root)
    warnings += [one.line() for one in rotted]
    return problems, warnings, rotted


# ....................... #


def _retire_rotted(rfc_dir: Path, root: Path, rotted: list[Any]) -> list[str]:
    """`check --fix-rot`: one amendment per document, retiring every rotted
    row it carries with the reason recorded (D-53.7). Each document is its
    own transaction; a red check on one leaves that document untouched and
    is reported, never silently skipped."""

    from torve.config.rfc_emit import (
        append_amendment,
        load_or_fail,
        retire_decision,
        write_transaction,
    )
    from torve.config.spec import archive_files, next_amendment, rfc_files

    lines: list[str] = []
    by_document: dict[str, list[Any]] = {}

    for one in rotted:
        by_document.setdefault(one.document, []).append(one)

    for name, rows in sorted(by_document.items()):
        files = rfc_files(rfc_dir)
        today = date.today().isoformat()
        changes: list[dict[str, Any]] = []

        try:
            doc = load_or_fail(rfc_dir / name)

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
                next_amendment(files, archive_files(rfc_dir)),
                f"{len(rows)} path-rotted row(s) retired by `torve rfc check --fix-rot`",
                today,
                changes,
            )
        except ValueError as exc:
            lines.append(f"{name}: fix-rot refused — {exc}")
            continue

        report = write_transaction(rfc_dir, root, {name: doc})

        if report.ok:
            lines.append(f"{name}: retired {', '.join(r.identifier for r in rows)} (path rot)")
        else:
            lines.append(f"{name}: fix-rot aborted — {'; '.join(report.problems)}")

    return lines


# ....................... #


@rfc_app.command("fmt")
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
    """Report every document whose text differs from what the serializer
    would write for it. Nothing is written: a hand-authored document is
    legal as it stands, and every verb that changes one writes the
    canonical form."""
    # The one-serializer rule is D-56.4; the docstring is help text.

    from torve.config.rfc_emit import canonical
    from torve.config.spec import rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)

    if number is None:
        targets = files
    else:
        key = _key(number)

        if key not in files:
            raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

        targets = {key: files[key]}

    console = out()
    drifting = refused = 0

    for key in sorted(targets):
        path = targets[key]
        original = path.read_text(encoding="utf-8")

        try:
            if canonical(original, path) != original:
                drifting += 1
                console.print(Text(f"DRIFT   {path.name}", STYLE_WARN))
        except Exception as exc:  # the loader's refusal, named
            refused += 1
            console.print(Text(f"REFUSE  {path.name}: {exc}", STYLE_FAIL))

    ok = refused == 0

    closing(
        console,
        f"{'OK   ' if ok else 'FAIL '} {len(targets)} checked, {drifting} drifting, "
        f"{refused} refused",
        STYLE_PASS if ok else STYLE_FAIL,
    )

    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


# ....................... #


def _key(number: str) -> str:
    return number.strip().removesuffix(".yaml").removesuffix(".md").zfill(4)


def _finish_transaction(report: CheckReport, success: str) -> None:
    """The shared tail of every transactional verb (D-25.2): print the
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


def _defining(rfc_dir: Path, identifier: str) -> Path | None:
    """The corpus document defining a decision identifier, or None."""

    from torve.config.spec import SpecError, load_document, rfc_files

    for path in rfc_files(rfc_dir).values():
        try:
            if load_document(path).decision(identifier) is not None:
                return path
        except SpecError:
            continue

    return None


# ....................... #


@rfc_app.command("amend")
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
    """Append the next amendment to NUMBER and record it in amended_by — one
    load-mutate-dump-check transaction; a red check leaves the tree
    untouched. With --row, the same transaction changes that row's grade,
    paths or text (or retires it) and records the typed diff with the
    prior value on the amendment; the row is re-stamped so a later hand
    edit is caught. The entry's own words are the author's to write."""
    # D-25.4: the number is derived via next_amendment, never chosen. D-53.4:
    # a row's grade or paths change only here, and the diff is written now.

    from torve.config.rfc_emit import (
        amend_row,
        append_amendment,
        load_or_fail,
        retire_decision,
        write_transaction,
    )
    from torve.config.spec import archive_files, next_amendment, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)
    key = _key(number)

    if key not in files:
        raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

    if row is None and (grade or paths or text or retire):
        raise fail(
            "configuration error: --grade, --path, --text and --retire need --row", EXIT_CONFIG
        )

    path = files[key]
    amendment = next_amendment(files, archive_files(rfc_dir))
    today = date.today().isoformat()
    changes: list[dict[str, Any]] = []

    try:
        doc = load_or_fail(path)

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

    report = write_transaction(rfc_dir, root, {path.name: doc})
    what = f" ({row} {'retired' if retire else 'changed'})" if row else ""
    _finish_transaction(
        report, f"appended {amendment} to {path.name}{what} — write the entry's own words"
    )


# ....................... #


@rfc_app.command("fix")
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
    # D-53.4's editorial lane.

    from torve.config.rfc_emit import fix_row_text, load_or_fail, write_transaction

    rfc_dir = corpus_dir(root, config)
    defining = _defining(rfc_dir, identifier)

    if defining is None:
        raise fail(
            f"configuration error: no decision {identifier!r} defined in the corpus", EXIT_CONFIG
        )

    try:
        doc, _ = fix_row_text(load_or_fail(defining), identifier, text)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(rfc_dir, root, {defining.name: doc})
    _finish_transaction(report, f"fixed {identifier}'s text in {defining.name} (editorial)")


# ....................... #


@rfc_app.command("archive")
def archive(
    number: Annotated[str, typer.Argument(help="The document to retire into the archive.")],
    superseded_by: Annotated[
        str, typer.Option("--superseded-by", help="The document that now stands for it.")
    ],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Move NUMBER into the archive beside the corpus path, keeping its
    filename and every identifier, with status superseded — one
    transaction: the corpus without it must check clean, or nothing moves.
    Archived identifiers still resolve through show and the record."""
    # D-53.8. Deletion from the corpus path is the one thing this verb does
    # that no other verb may.

    from torve.config.rfc_emit import archive_document, load_or_fail, write_transaction
    from torve.config.spec import archive_dir, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)
    key = _key(number)

    if key not in files:
        raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

    path = files[key]
    target = archive_dir(rfc_dir) / path.name

    if target.exists():
        raise fail(f"configuration error: {target} already exists", EXIT_CONFIG)

    try:
        archived = archive_document(load_or_fail(path), superseded_by)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    # The check that guards the move sees the document in the archive and
    # the archive itself, so a document others cite can leave (A-140).
    report = write_transaction(
        rfc_dir, root, {}, deletions=(path.name,), archived={path.name: archived}
    )
    _finish_transaction(report, f"archived {path.name} → {target} (superseded by {superseded_by})")


# ....................... #


@rfc_app.command("add-decision")
def add_decision(
    number: Annotated[str, typer.Argument(help="The document gaining a decision, e.g. 0025.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Append a decision row under the next free identifier in NUMBER's own
    family, and print that identifier. The grade, paths and text are left
    for the author — one transaction; a red check leaves the tree
    untouched."""
    # D-25.3 LOCKED: the row's grade is written as OPEN, the vocabulary's own
    # "not yet decided" value — never a chosen judgement.

    from torve.config.rfc_emit import append_decision, load_or_fail, write_transaction
    from torve.config.spec import next_decision, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)
    key = _key(number)

    if key not in files:
        raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

    path = files[key]
    identifier = next_decision(files, key)

    try:
        doc = append_decision(load_or_fail(path), identifier)
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(rfc_dir, root, {path.name: doc})
    _finish_transaction(
        report, f"added {identifier} to {path.name} — write its grade, paths and text"
    )


# ....................... #


@rfc_app.command("retire")
def retire(
    identifier: Annotated[str, typer.Argument(help="The decision identifier to retire.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Retire IDENTIFIER: remove its row and record it in the document's
    retired list, never reused — one transaction; a red check leaves the
    tree untouched. Prefer `amend --row X --retire --reason …`, which
    records why."""
    # D-25.6: executes D-16.1 whole. Whether every remaining citation still
    # resolves is the transaction's own check, not a separate pre-check.

    from torve.config.rfc_emit import load_or_fail, retire_decision, write_transaction

    rfc_dir = corpus_dir(root, config)
    defining = _defining(rfc_dir, identifier)

    if defining is None:
        raise fail(
            f"configuration error: no decision {identifier!r} defined in the corpus", EXIT_CONFIG
        )

    try:
        doc = retire_decision(load_or_fail(defining), identifier, date.today().isoformat())
    except ValueError as exc:
        raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    report = write_transaction(rfc_dir, root, {defining.name: doc})
    _finish_transaction(report, f"retired {identifier} in {defining.name}")


# ....................... #


@rfc_app.command("relocate-paths")
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
    # D-25.7: the paths are mechanical; text naming the old location stays
    # hand-written where the text itself names it.

    from torve.config.rfc_emit import load_or_fail, write_transaction
    from torve.config.rfc_emit import relocate_paths as relocate
    from torve.config.spec import rfc_files
    from torve.domain.spec import Document

    rfc_dir = corpus_dir(root, config)
    mutations: dict[str, Document | str] = {}
    touched: dict[str, list[str]] = {}

    for path in rfc_files(rfc_dir).values():
        try:
            doc, rows = relocate(load_or_fail(path), old, new)
        except ValueError:
            continue

        if rows:
            mutations[path.name], touched[path.name] = doc, rows

    if not mutations:
        raise fail(f"configuration error: no row's paths carry {old!r}", EXIT_CONFIG)

    report = write_transaction(rfc_dir, root, mutations)

    if report.ok:
        console = out()

        for name, ids in sorted(touched.items()):
            console.print(f"{name}: {', '.join(sorted(ids))}")

    count = sum(len(ids) for ids in touched.values())
    _finish_transaction(
        report, f"relocated {old!r} to {new!r} in {count} row(s) across {len(mutations)} file(s)"
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


@rfc_app.command("show")
def show(
    identifier: Annotated[
        str,
        typer.Argument(help="A corpus identifier: a decision, an amendment or a document number."),
    ],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Resolve one corpus identifier from the same load `check` runs: no
    cache, no store — an undefined identifier is a configuration error
    naming the nearest family. An archived identifier answers, marked."""
    # The one-parse rule is D-7.28; the docstring is `show`'s help text
    # and stays free of corpus coordinates.

    from torve.config.spec import archive_files, lookup, next_amendment, rfc_files

    rfc_dir = corpus_dir(root, config)
    found = lookup(rfc_dir, identifier)

    if found is None:
        files = rfc_files(rfc_dir)
        family = (
            f"the next free amendment number is {next_amendment(files, archive_files(rfc_dir))}"
            if identifier.startswith("A-")
            else f"the next free document number is {int(max(files, default='0000')) + 1:04d}"
            if identifier.isdigit()
            else "decision identifiers are listed in each document's decisions"
        )

        raise fail(f"configuration error: nothing defines {identifier!r} — {family}", EXIT_CONFIG)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, **found})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "rfc show", f"{identifier} · {found['kind']}")

    for label, value in _show_lines(found):
        line = Text(f"  {label:>14}  ", STYLE_DIM)
        line.append(value, STYLE_ID if label in ("defined in", "next free") else "")
        console.print(line)

    raise typer.Exit(EXIT_OK)


# ....................... #


@rfc_app.command("schema")
def schema(
    check_only: Annotated[
        bool, typer.Option("--check", help="Compare instead of writing; drift exits 3.")
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Write the model's JSON Schema beside the corpus, where every
    document's first line points an editor at it. Generated output, like
    a lockfile: `check` reddens when it lags the model, and `--check`
    reports without writing."""
    # D-56.6: the authoring contract, as a file an editor validates against.

    from torve.config.spec import SCHEMA_RELATIVE, schema_file, schema_text

    rfc_dir = corpus_dir(root, config)
    path = schema_file(rfc_dir)
    rendered = schema_text()
    current = path.read_text(encoding="utf-8") if path.is_file() else None

    if check_only:
        if current == rendered:
            out().print(f"OK    {SCHEMA_RELATIVE} matches the model")
            raise typer.Exit(EXIT_OK)

        raise fail(
            f"{SCHEMA_RELATIVE} {'is missing' if current is None else 'lags the model'} — "
            "it is generated output; `torve rfc schema` writes it",
            EXIT_CONFIG,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    out().print(f"wrote {path}")
    raise typer.Exit(EXIT_OK)


# ....................... #


@rfc_app.command("render")
def render(
    number: Annotated[str, typer.Argument(help="The document to render, e.g. 0056.")],
    output: Annotated[
        Path | None, typer.Option("--out", help="Write the page here instead of printing it.")
    ] = None,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """The document as a markdown page for a person: header facts, prose,
    the rows as a table, invariants, alternatives, questions, phasing and
    amendments. The one markdown writer, and never the source of anything."""
    # D-56.7.

    from torve.config.rfc_emit import load_or_fail, render_markdown
    from torve.config.spec import archive_files, rfc_files

    rfc_dir = corpus_dir(root, config)
    key = _key(number)
    files = {**archive_files(rfc_dir), **rfc_files(rfc_dir)}

    if key not in files:
        raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

    try:
        page = render_markdown(load_or_fail(files[key]))
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


@rfc_app.command("list")
def list_cmd(
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every document in the corpus path with its status, implementation
    and dependencies — the index as a query, never a file."""
    # D-56.7: what INDEX.md was, answered instead of generated.

    from torve.config.spec import SpecError, archive_files, load_corpus, next_number, rfc_files

    rfc_dir = corpus_dir(root, config)

    try:
        corpus = load_corpus(rfc_dir)
    except SpecError as exc:
        raise fail("configuration error: " + "; ".join(exc.problems), EXIT_CONFIG) from None

    rows = [
        {
            "number": doc.id,
            "title": doc.title,
            "kind": doc.kind,
            "status": doc.status,
            "implementation": doc.implementation,
            "depends_on": list(doc.depends_on),
            "amended_by": list(doc.amended_by),
            "description": doc.description.strip(),
            "file": Path(doc.path).name,
        }
        for doc in corpus.documents
        if not doc.archived
    ]
    allocated = next_number(rfc_dir)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "documents": rows,
                "archived": len(archive_files(rfc_dir)),
                "next_number": f"{allocated:04d}",
            }
        )
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "rfc list", f"{len(rows)} document(s), {len(rfc_files(rfc_dir))} file(s)")

    for one in rows:
        status, implementation = str(one["status"]), str(one["implementation"])
        line = Text(f"  {one['number']}  ", STYLE_ID)
        line.append(f"{status:<10}", _STATUS_STYLES.get(status, STYLE_FAIL))
        line.append(f"{implementation:<10}", STYLE_DIM)
        line.append(str(one["title"]))
        depends = cast("list[str]", one["depends_on"])

        if depends:
            line.append(f"  ← {', '.join(depends)}", STYLE_DIM)

        console.print(line)

    closing(
        console,
        f"{len(archive_files(rfc_dir))} archived; the next number is {allocated:04d}",
        STYLE_DIM,
    )
    raise typer.Exit(EXIT_OK)


# ....................... #


@rfc_app.command("new")
def new(
    title: Annotated[str, typer.Argument(help="Document title; the slug derives from it.")],
    kind: Annotated[str, typer.Option("--kind", help="design (default) or convention.")] = "design",
    owner: Annotated[str, typer.Option("--owner", help="The document's owner.")] = "",
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Create the next document: the number is derived as the maximum over
    the corpus and the archive plus one — never chosen, never reused — and
    the file is the smallest document that checks, written by the one
    serializer with the schema header line."""

    from torve.config.rfc_emit import dump_document, new_document
    from torve.config.spec import next_number, slugify

    if kind not in KINDS:
        raise fail(
            f"configuration error: kind {kind!r} is not one of {', '.join(KINDS)}", EXIT_CONFIG
        )

    rfc_dir = corpus_dir(root, config)
    slug = slugify(title)

    if not slug:
        raise fail("configuration error: title produces an empty slug", EXIT_CONFIG)

    allocated = next_number(rfc_dir)
    number = f"{allocated:04d}"
    path = rfc_dir / f"{number}-{slug}.yaml"
    doc = new_document(number, title, owner or _git_user(root) or "owner", kind)

    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(dump_document(doc))

    except FileExistsError:
        raise fail(
            f"configuration error: {path.name} was created by another "
            "process — re-run to take the next number",
            EXIT_CONFIG,
        ) from None

    console = out()
    console.print(f"created {path}")
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


@rfc_app.command("graph")
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

    from torve.config.spec import SpecError, check_graph, load_corpus

    rfc_dir = corpus_dir(root, config)

    try:
        corpus = load_corpus(rfc_dir)
    except SpecError as exc:
        raise fail("configuration error: " + "; ".join(exc.problems), EXIT_CONFIG) from None

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
    header(console, "rfc graph", f"{len(documents)} RFC(s), {len(edges)} edge(s)")
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


@rfc_app.command("health")
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
    # rules it states are D-22.2, D-22.1, D-22.3 and D-22.12 in that order.

    from torve.application import specquality

    rfc_dir = corpus_dir(root, config)
    report = specquality.decision_report(root.resolve(), rfc_dir, floor=floor)
    populations = report["populations"]

    if document is not None:
        number = document.strip().removesuffix(".md")
        wanted = specquality.identifiers_for_document(rfc_dir, number)

        if wanted is None:
            raise fail(f"configuration error: no RFC {document!r} under {rfc_dir}", EXIT_CONFIG)

        populations = [p for p in populations if p["identifier"] in wanted]

    # D-22.12: the operator-attention line is a corpus-wide fact — a
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
    subject = f"RFC {document}" if document else f"{len(populations)} decision(s), corpus-wide"
    header(console, "rfc health", subject)
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
