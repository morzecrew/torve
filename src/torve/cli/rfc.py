"""`torve rfc` — corpus validation and authoring mechanics (RFC 0007 §3a).

The package owns the format (D-7.12); the skill teaches content. `check` is
the whole `rfc-valid` gate and needs no store (D-7.16). The corpus location is
`rfcs.path` from the runner's configuration — one path, never a list (D-13.7,
D-A.16) — defaulting to `rfcs/`.

`new` derives its number as the maximum plus one (D-A.17); there is no way to
create a document in a numbering hole (D-A.19) and no counter file to merge.
INDEX.md is generated output, like a lockfile (D-A.6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
    footer,
    header,
    id_list,
    out,
)
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.rfc import KINDS
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #

rfc_app = typer.Typer(no_args_is_help=True, help="Validate and author the RFC corpus.")

TEMPLATE_TITLE = "RFC NNNN — <Title>"

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
