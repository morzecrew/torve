"""`torve source` — mint and read the sources (S-0060/D-6).

A document is a source without being filed as one, so `new` refuses its kind
and only `.torve/sources/` is written here. Reading is the other way round:
one grammar has one resolver, so `show` takes any source identifier a
contract may carry and answers for a document too, deferring to the corpus
for the rows themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from torve.base.clock import stamp
from torve.cli.console import (
    STYLE_DIM,
    STYLE_FAIL,
    STYLE_ID,
    STYLE_PASS,
    Format,
    closing,
    emit_json,
    fail,
    header,
    make_table,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

source_app = typer.Typer(
    no_args_is_help=True,
    help="Mint and read the sources that are not documents.",
)


@source_app.command("new")
def new_cmd(
    kind: Annotated[str, typer.Argument(help="incident, audit, review or operator.")],
    slug: Annotated[str, typer.Argument(help="The slug: lowercase, digits, . and -.")],
    title: Annotated[str, typer.Option("--title", help="What it is, in a line.")] = "",
    ref: Annotated[
        str, typer.Option("--ref", help="Where it lives — a URL, an issue, a commit, a person.")
    ] = "",
    root: RootOption = Path("."),
) -> None:
    """Write one source file: its identifier is the path it sits at, its
    first line names the schema, and it carries no decisions — rows that
    stand are the corpus's, and a source that settled some names the
    document holding them."""

    from torve.config.sources import schema_header, source_file
    from torve.domain.source import FILED_KINDS, SLUG, Source

    if kind not in FILED_KINDS:
        raise fail(
            f"configuration error: {kind!r} is not a filed source kind "
            f"({', '.join(FILED_KINDS)}) — a document is a source without being filed as one",
            EXIT_CONFIG,
        )

    if not SLUG.match(slug):
        raise fail(
            f"configuration error: {slug!r} is not a slug — lowercase, digits, . and -",
            EXIT_CONFIG,
        )

    path = source_file(root, f"{kind}/{slug}")

    if path.exists():
        raise fail(f"configuration error: {kind}/{slug} already exists at {path}", EXIT_CONFIG)

    source = Source(id=f"{kind}/{slug}", kind=kind, title=title, ref=ref, at=stamp())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{schema_header()}\n{_dump(source)}", encoding="utf-8")

    console = out()
    console.print(f"created {path}")
    console.print("next: write the summary — what it said, in prose")


def _dump(source) -> str:  # type: ignore[no-untyped-def]
    import yaml

    return yaml.safe_dump(
        source.model_dump(mode="json", exclude_defaults=True),
        sort_keys=False,
        allow_unicode=True,
        width=88,
    )


# ....................... #


@source_app.command("list")
def list_cmd(
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Also resolve every source a local contract names.",
        ),
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Every filed source, with what it is and where it lives. A file that
    does not load is a refusal, so this verb is what proves the directory."""

    from torve.config.sources import check_sources, cited_sources, load_sources
    from torve.config.spec import SpecError, document_dir
    from torve.domain.source import FILED_ID

    problems, warnings = check_sources(root)

    try:
        found = load_sources(root)
    except SpecError:
        found = {}

    unresolved: list[str] = []

    if check:
        spec_dir = root / load_config(root, config).specs.path

        for task_id, named in sorted(cited_sources(root).items()):
            known = named in found or (
                not FILED_ID.match(named) and document_dir(spec_dir, named) is not None
            )

            if not known:
                unresolved.append(f"{task_id} names {named}, which nothing in this tree holds")

    problems += unresolved

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "sources": [one.model_dump(mode="json") for one in found.values()],
                "problems": problems,
                "warnings": warnings,
            }
        )
        raise typer.Exit(EXIT_CONFIG if problems else EXIT_OK)

    console = out(fmt)
    header(console, "source list", f"{len(found)} source(s)")

    if found:
        table = make_table("id", "title", "ref")

        for one in found.values():
            table.add_row(Text(one.id, STYLE_ID), one.title or "—", Text(one.ref or "—", STYLE_DIM))

        console.print(table)

    for problem in problems:
        console.print(Text(f"PROBLEM {problem}", STYLE_FAIL))

    for warning in warnings:
        console.print(Text(f"WARN    {warning}", STYLE_DIM))

    closing(
        console,
        f"{len(found)} source(s), {len(problems)} problem(s), {len(warnings)} warning(s)",
        STYLE_FAIL if problems else STYLE_PASS,
    )

    raise typer.Exit(EXIT_CONFIG if problems else EXIT_OK)


# ....................... #


@source_app.command("show")
def show_cmd(
    identifier: Annotated[str, typer.Argument(help="A source id: `S-NNNN` or `<kind>/<slug>`.")],
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """One source whole, with the tasks whose contracts name it. An `S-NNNN`
    identifier resolves to the document, which is a source that is not filed
    as one — the grammar admits both, so this verb does too; `torve spec show`
    is where its decisions are."""

    from torve.application.decisions import PlanError, corpus_sources
    from torve.config.sources import cited_sources, load_sources
    from torve.config.spec import SpecError
    from torve.domain.source import FILED_ID

    try:
        found = load_sources(root) if FILED_ID.match(identifier) else {}
    except SpecError as exc:
        raise fail(f"configuration error: {'; '.join(exc.problems)}", EXIT_CONFIG) from None

    if not FILED_ID.match(identifier):
        try:
            found = corpus_sources(root / load_config(root, config).specs.path)
        except PlanError as exc:
            raise fail(f"configuration error: {exc}", EXIT_CONFIG) from None

    source = found.get(identifier)

    if source is None:
        raise fail(
            f"configuration error: nothing in this tree is the source {identifier!r} — "
            f"filed sources live under {root / '.torve' / 'sources'}, "
            "and a document is one when the corpus holds it",
            EXIT_CONFIG,
        )

    cited = sorted(task for task, named in cited_sources(root).items() if named == identifier)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, **source.model_dump(mode="json"), "cited_by": cited})
        raise typer.Exit(EXIT_OK)

    console = out(fmt)
    header(console, "source show", f"{source.id} · {source.kind}")

    for label, value in (
        ("title", source.title),
        ("ref", source.ref),
        ("at", source.at),
        ("settled by", ", ".join(source.settled_by)),
        ("cited by", ", ".join(cited)),
    ):
        if value:
            console.print(Text(f"  {label:12} {value}", ""))

    if source.summary.strip():
        console.print()
        console.print(source.summary.strip())

    raise typer.Exit(EXIT_OK)
