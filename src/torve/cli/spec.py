"""`torve spec` — the specification read from the worktree (RFC 0054 §5.4,
§5.5): `project` renders the managed sections beside the code and
`--check` names their drift; `show`, `paths`, `tests` and `why-not` are
the sandbox's read verb over the corpus and the archive already in the
worktree — progressive disclosure over files the agent could have opened,
never reach into the record, another task or an escalation. Parsing and
rendering only (D-15.6); the reading is `torve.application.colocation`
and the loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.text import Text

from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    STYLE_WARN,
    Format,
    closing,
    emit_json,
    fail,
    header,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

spec_app = typer.Typer(
    no_args_is_help=True,
    help="Read the specification from the worktree, and render it beside the code.",
)


# ....................... #


def _corpus_dir(root: Path, config_path: Path | None) -> Path:
    config = load_config(root, config_path)
    resolved = root / config.rfcs.path

    if not resolved.is_dir():
        raise fail(
            f"configuration error: no corpus directory at {resolved} "
            "(the rfcs.path configuration key)",
            EXIT_CONFIG,
        )

    return resolved


def _load(root: Path, config_path: Path | None) -> Any:
    from pydantic import ValidationError

    from torve.config.spec import SpecError, load_corpus

    try:
        return load_corpus(_corpus_dir(root, config_path))
    except SpecError as exc:
        raise fail("configuration error: " + "; ".join(exc.problems), EXIT_CONFIG) from None
    except ValidationError as exc:
        raise fail(f"configuration error: {exc.errors()[0]['msg']}", EXIT_CONFIG) from None


# ----------------------- #


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
        projection = project(root, _corpus_dir(root, config), check=check)
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


# ----------------------- #


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


@spec_app.command("show")
def show_cmd(
    identifier: Annotated[str, typer.Argument(help="A decision, invariant or question id.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """One row as it stands: text, paths, consequence, rationale, what it
    cites, its check and the check's state; an archived row answers and
    says so. Invariants and questions resolve too."""

    corpus = _load(root, config)
    found: dict[str, Any] | None = None
    hit = corpus.decision(identifier)

    if hit is not None:
        found = _row_payload(*hit)
    else:
        for doc in corpus.documents:
            for invariant in doc.invariants:
                if invariant.id == identifier:
                    found = {
                        "kind": "invariant",
                        "identifier": invariant.id,
                        "statement": invariant.statement,
                        "paths": list(invariant.paths),
                        "check": invariant.check,
                        "defined_in": Path(doc.path).name if doc.path else doc.id,
                        "archived": doc.archived,
                    }

            for question in doc.questions:
                if question.id == identifier:
                    found = {
                        "kind": "question",
                        "identifier": question.id,
                        "text": question.text,
                        "status": question.status,
                        "settled_by": question.settled_by,
                        "defined_in": Path(doc.path).name if doc.path else doc.id,
                        "archived": doc.archived,
                    }

    if found is None:
        raise fail(f"configuration error: nothing defines {identifier!r}", EXIT_CONFIG)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, **found})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "spec show", f"{identifier} · {found['kind']}")

    for label, value in found.items():
        if label in ("kind", "identifier") or value in ("", None, []):
            continue

        line = Text(f"  {label:>12}  ", STYLE_DIM)
        shown = (
            ", ".join(str(v) for v in cast("list[object]", value))
            if isinstance(value, list)
            else str(value)
        )
        line.append(shown, STYLE_ID if label == "defined_in" else "")
        console.print(line)

    raise typer.Exit(EXIT_OK)


# ....................... #


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
