"""`torve rfc` — corpus validation and authoring mechanics (RFC 0007 §3a).

The package owns the format (D-7.12); the skill teaches content. `check` is
the whole `rfc-valid` gate and needs no store (D-7.16). The corpus location is
`rfcs.path` from the runner's configuration — one path, never a list (D-13.7,
D-A.16) — defaulting to `rfcs/`.

`new` derives its number as the maximum plus one (D-A.17); there is no way to
create a document in a numbering hole (D-A.19) and no counter file to merge.
INDEX.md is generated output, like a lockfile (D-A.6).

`fmt` is the authoring surface's other half (RFC 0025 §5.2, D-25.1): the
canonical emitter in `torve.config.rfc_emit` renders a document's structural
surfaces back to text and `fmt` writes the result when it differs, refusing
a document its own check already reddens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

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

    from torve.config.rfc_parse import RFC_FILENAME

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
    root: RootOption = Path("."),
    config: ConfigOption = None,
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Validate the corpus: directory contents, frontmatter, decision tables,
    links, the dependency graph, and INDEX.md drift. A malformed corpus is a
    configuration error — exit 3."""

    from torve.config.rfc_parse import check_corpus

    rfc_dir = corpus_dir(root, config)
    report = check_corpus(rfc_dir, root)
    problems, warnings = report.problems, report.warnings

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


@rfc_app.command("fmt")
def fmt(
    number: Annotated[
        str | None,
        typer.Argument(
            help="One document's number (e.g. 0025); omitted formats the whole corpus."
        ),
    ] = None,
    check_only: Annotated[
        bool, typer.Option("--check", help="Report drift without writing.")
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Normalise a document's frontmatter, decision table, phasing fence and
    amendment headings to their canonical rendering; every word of prose is
    left alone. A document whose own check already reports a problem is
    refused rather than formatted, and `--check` reports without writing."""
    # The canonical rendering lives in `torve.config.rfc_emit.emit` (D-25.1);
    # refusing an already-broken document is what stops its breakage from
    # being laundered into a diff that looks deliberate (D-25.2).

    from torve.config.rfc_emit import emit
    from torve.config.rfc_parse import build_index, check_corpus, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)

    if number is None:
        targets = files
    else:
        key = number.strip().removesuffix(".md").zfill(4)

        if key not in files:
            raise fail(f"configuration error: no RFC {number!r} under {rfc_dir}", EXIT_CONFIG)

        targets = {key: files[key]}

    problems = check_corpus(rfc_dir, root).problems
    console = out()
    changed = written = refused = 0

    for key in sorted(targets):
        path = targets[key]
        own = _own_problems(problems, path.name)

        if own:
            refused += 1
            console.print(Text(f"REFUSE  {path.name}: {len(own)} check problem(s)", STYLE_FAIL))
            continue

        original = path.read_text(encoding="utf-8")

        try:
            canonical = emit(original)
        except ValueError as exc:
            refused += 1
            console.print(Text(f"REFUSE  {path.name}: {exc}", STYLE_FAIL))
            continue

        if canonical == original:
            continue

        changed += 1

        if check_only:
            console.print(Text(f"DRIFT   {path.name}", STYLE_WARN))
        else:
            path.write_text(canonical, encoding="utf-8")
            written += 1
            console.print(Text(f"WROTE   {path.name}", STYLE_PASS))

    if written and not check_only:
        # D-25.2: the transaction regenerates the index too — a no-op in
        # practice, since `emit` only reformats a value's YAML, never its
        # content, but the cycle stays whole rather than relying on that.
        index_path = rfc_dir / "INDEX.md"
        rendered_index = build_index(rfc_files(rfc_dir))

        if index_path.read_text(encoding="utf-8") != rendered_index:
            index_path.write_text(rendered_index, encoding="utf-8")

    ok = refused == 0 and (not check_only or changed == 0)
    tail = f"{changed} drifting" if check_only else f"{written} written"

    closing(
        console,
        f"{'OK   ' if ok else 'FAIL '} {len(targets)} checked, {tail}, {refused} refused",
        STYLE_PASS if ok else STYLE_FAIL,
    )

    raise typer.Exit(EXIT_OK if ok else EXIT_CONFIG)


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
            ("defined in", str(found.get("defined_in") or "")),
            ("cited by", joined("cited_by")),
            ("retired in", str(found.get("retired_in") or "")),
        ]
    elif kind == "amendment":
        rows = [
            ("defined in", str(found["defined_in"])),
            ("heading", str(found["heading"])),
            ("rows citing it", joined("rows")),
            ("next free", str(found["next_free"])),
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
            ("state", str(found["implementation_state"])),
            ("phases", ", ".join(f"{e['phase']}: {e['title']}" for e in phases)),
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
    """Resolve one corpus identifier from the same parse `check` runs:
    no cache, no store — an undefined identifier is a configuration
    error naming the nearest family."""
    # The one-parse rule is D-7.28; the docstring is `show`'s help text
    # and stays free of corpus coordinates.

    from torve.config.rfc_parse import lookup, next_amendment, rfc_files

    rfc_dir = corpus_dir(root, config)
    found = lookup(rfc_dir, identifier)

    if found is None:
        files = rfc_files(rfc_dir)
        family = (
            f"the next free amendment number is {next_amendment(files)}"
            if identifier.startswith("A-")
            else f"the next free document number is {int(max(files, default='0000')) + 1:04d}"
            if identifier.isdigit()
            else "decision identifiers are listed in each document's Decisions table"
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


@rfc_app.command("index")
def index(
    check_only: Annotated[
        bool, typer.Option("--check", help="Compare instead of writing; drift exits 3.")
    ] = False,
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Regenerate INDEX.md from frontmatter. The index is output,
    like a lockfile — with `--check`, drift is reported and nothing is
    written."""

    from torve.config.rfc_parse import build_index, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)
    index_path = rfc_dir / "INDEX.md"
    rendered = build_index(files)
    current = index_path.read_text(encoding="utf-8") if index_path.is_file() else None

    if check_only:
        if current == rendered:
            out().print(f"OK    INDEX.md matches {len(files)} RFC(s)")
            raise typer.Exit(EXIT_OK)

        raise fail(
            "INDEX.md differs from what `torve rfc index` writes — it is "
            "generated output; regenerate it instead of editing it",
            EXIT_CONFIG,
        )

    index_path.write_text(rendered, encoding="utf-8")
    out().print(f"generated {index_path} ({len(files)} RFC(s))")


# ....................... #


@rfc_app.command("new")
def new(
    title: Annotated[str, typer.Argument(help="Document title; the slug derives from it.")],
    kind: Annotated[str, typer.Option("--kind", help="design (default) or convention.")] = "design",
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Create the next document from the rfc-writer template: the number is
    derived as the maximum plus one — never chosen, never reused — and the
    index is regenerated."""

    from torve.application.skills import skills_root
    from torve.config.rfc_parse import build_index, next_number, rfc_files, slugify

    if kind not in KINDS:
        raise fail(
            f"configuration error: kind {kind!r} is not one of {', '.join(KINDS)}", EXIT_CONFIG
        )

    rfc_dir = corpus_dir(root, config)
    slug = slugify(title)

    if not slug:
        raise fail("configuration error: title produces an empty slug", EXIT_CONFIG)

    template_path = skills_root() / "rfc-writer" / "references" / "rfc-template.md"
    template_text = template_path.read_text(encoding="utf-8")
    block = template_text.split("```markdown\n", 1)

    if len(block) < 2 or TEMPLATE_TITLE not in block[1]:
        raise fail(f"configuration error: no usable skeleton in {template_path}", EXIT_CONFIG)

    body = block[1].split("\n```", 1)[0]

    allocated = next_number(rfc_dir)
    path = rfc_dir / f"{allocated:04d}-{slug}.md"

    body = (
        body.replace(TEMPLATE_TITLE, f"RFC {allocated:04d} — {title}")
        .replace('id: "NNNN"', f'id: "{allocated:04d}"')
        .replace("title: <Title>", f"title: {title}")
    )

    if kind == "convention":
        body = body.replace("status: draft", "kind: convention\nstatus: draft", 1)

    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")

    except FileExistsError:
        raise fail(
            f"configuration error: {path.name} was created by another "
            "process — re-run to take the next number",
            EXIT_CONFIG,
        ) from None

    (rfc_dir / "INDEX.md").write_text(build_index(rfc_files(rfc_dir)), encoding="utf-8")
    console = out()
    console.print(f"created {path}")
    console.print("next: fill the frontmatter description and the Scope paragraph")


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

    from torve.config.rfc_parse import check_graph, fm_list, parse_frontmatter, rfc_files

    rfc_dir = corpus_dir(root, config)
    files = rfc_files(rfc_dir)

    frontmatter = {
        number: parse_frontmatter(path.read_text(encoding="utf-8")) or {}
        for number, path in files.items()
    }

    depends = {number: fm_list(frontmatter[number], "depends_on") for number in frontmatter}

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

    problems, warnings = check_graph(files, frontmatter)

    if fmt is Format.JSON:
        emit_json({"schema_version": 1, "edges": edges, "problems": problems, "warnings": warnings})
        return

    console = out(fmt)
    header(console, "rfc graph", f"{len(files)} RFC(s), {len(edges)} edge(s)")
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
    not a verdict. No single corpus score is computed anywhere."""
    # The docstring is help text and carries no corpus coordinates; the
    # rules it states are D-22.2, D-22.1 and D-22.3 in that order.

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

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": report["schema_version"],
                "floor": report["floor"],
                "document": document,
                "caveat": QUASI_EXPERIMENT_CAVEAT,
                "populations": populations,
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
