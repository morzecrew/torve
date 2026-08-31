"""The canonical emitter beside `torve.config.rfc_parse` (RFC 0025 §5.1,
D-25.1): renders a parsed document's model back to text. Frontmatter,
the decision table, the phasing fence and dated amendment headings are the
structures the parser models, so those are the only ones this module
touches — every other byte, every word of prose, passes through untouched.
That is what makes `emit` a structure-preserving rewrite rather than a
renderer, and what makes idempotence a property worth pinning by test:
formatting an already-canonical document must write nothing.

`emit` raises `ValueError` on anything the parser itself would reject —
the same failure `torve rfc fmt` uses to refuse a document rather than
laundering its breakage into a diff that looks deliberate.

The transactional verbs (RFC 0025 §5.3, D-25.2) live here too: each mutate
function takes a document's text and an identifier already derived by
`rfc_parse`, and returns the emitted result of one structural edit —
`append_amendment`, `append_decision`, `retire_decision`,
`relocate_paths_text`. None of them write to disk; `write_transaction` is
the one function that does, and only after the whole mutated corpus checks
clean in a scratch copy (D-25.2's "abort the whole write on any problem,
leaving the tree untouched").
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from torve.config.rfc_parse import (
    AMENDMENTS_SECTION,
    FRONTMATTER,
    PHASING_HEADING,
    TABLE_HEADER,
    YAML_FENCE,
    CheckReport,
    DecisionRow,
    PhasingEntry,
    build_index,
    check_corpus,
    decision_table,
    fm_list,
    parse_frontmatter,
    parse_phasing,
    rfc_files,
)

# ----------------------- #

# The order every emitted document's frontmatter keys land in (D-25.1). A
# key this tuple does not name keeps whatever position it already had,
# appended after the known ones — the corpus has never needed one, but a
# formatter that drops a field it does not recognise is a data-loss bug.
FRONTMATTER_ORDER: tuple[str, ...] = (
    "id",
    "title",
    "kind",
    "status",
    "implementation",
    "depends_on",
    "informed_by",
    "supersedes",
    "superseded_by",
    "amended_by",
    "retired",
    "owner",
    "description",
    "schema_version",
)

# `### A-n — YYYY-MM-DD — title` (D-A.5's dated form): the separator is
# matched loosely (a hand-typed "-" is the exact trap this normalises) and
# rewritten with the corpus's own em dash either side.
_DATED_HEADING = re.compile(
    r"^### (A-\d+)\s*(?:-{1,2}|—)\s*(\d{4}-\d{2}-\d{2})\s*(?:-{1,2}|—)\s*(.+?)\s*$", re.M
)

# ....................... #


def _yaml_scalar(value: Any) -> str:
    """One value, quoted exactly when YAML would otherwise misread it — a
    bare `on`, a leading-zero id parsed as octal, a colon or a ` #` inside a
    plain scalar. PyYAML's own analysis decides *whether* to quote, since it
    resolves scalars with the same rules `parse_frontmatter`'s loader does;
    only the quote character is ours, to match the corpus's double quotes
    rather than PyYAML's single quotes."""

    dumped = yaml.safe_dump(
        {"v": value}, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    body = dumped[len("v: ") :]

    if len(body) >= 2 and body[0] == "'" and body[-1] == "'":
        inner = body[1:-1].replace("''", "'").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{inner}"'

    return body


# ....................... #


def _dump_list(items: Any) -> str:
    if not items:
        return "[]"

    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


# ....................... #


def _render_field(key: str, value: Any) -> str:
    if key == "description" and isinstance(value, str):
        # Folded, one line: `parse_frontmatter` already collapsed the
        # source's newlines to spaces (YAML folding), so this is the
        # fixed point re-emitting reaches regardless of how the source
        # happened to be wrapped.
        return f"description: >-\n  {' '.join(value.split())}"

    if isinstance(value, list):
        return f"{key}: {_dump_list(value)}"

    return f"{key}: {_yaml_scalar(value)}"


# ....................... #


def render_frontmatter(fm: dict[str, Any]) -> str:
    ordered = {key: fm[key] for key in FRONTMATTER_ORDER if key in fm}

    for key, value in fm.items():
        ordered.setdefault(key, value)

    lines = ["---", *(_render_field(key, value) for key, value in ordered.items()), "---"]
    return "\n".join(lines) + "\n"


# ....................... #


def _render_decision_table(rows: list[DecisionRow]) -> str:
    lines = [TABLE_HEADER, "| --- | --- | --- | --- | --- |"]

    for row in rows:
        paths = " ".join(f"`{p}`" for p in row.paths) if row.paths else "—"
        consequence = row.consequence or "—"
        lines.append(f"| {row.identifier} | `{row.grade}` | {row.text} | {paths} | {consequence} |")

    return "\n".join(lines) + "\n"


# ....................... #


def _rewrite_table(rest: str) -> str:
    rows = decision_table(rest)

    if not rows:
        return rest

    start = rest.find(TABLE_HEADER)

    if start == -1:
        return rest

    pos = start

    for _ in range(2 + len(rows)):  # the header line, the separator, one line per row
        newline = rest.find("\n", pos)
        pos = newline + 1 if newline != -1 else len(rest)

    return rest[:start] + _render_decision_table(rows) + rest[pos:]


# ....................... #


def _render_phasing(entries: list[PhasingEntry]) -> str:
    lines = ["```yaml"]

    for entry in entries:
        lines.append(f"- phase: {entry.phase}")
        lines.append(f"  title: {_yaml_scalar(entry.title)}")
        lines.append("  intent: >-")
        lines.append(f"    {' '.join(entry.intent.split())}")
        lines.append("  scope:")
        lines += [f"    - {json.dumps(item)}" for item in entry.scope]

        if entry.acceptance:
            lines.append("  acceptance:")
            lines += [f"    - {json.dumps(item)}" for item in entry.acceptance]
        else:
            lines.append("  acceptance: []")

        if entry.tier_variant:
            lines.append(f"  tier_variant: {_yaml_scalar(entry.tier_variant)}")

        lines.append(f"  depends_on: [{', '.join(str(d) for d in entry.depends_on)}]")

    lines.append("```")
    # No trailing newline: `YAML_FENCE`'s closing `$` is zero-width, so the
    # splice site in `_rewrite_phasing` already keeps the source's own
    # newline after the fence — adding one here would double it.
    return "\n".join(lines)


# ....................... #


def _rewrite_phasing(rest: str) -> str:
    entries = parse_phasing(rest)  # raises ValueError on a fence that does not mint

    if not entries:
        return rest

    heading = PHASING_HEADING.search(rest)

    if heading is None:  # pragma: no cover - parse_phasing found entries, so a heading exists
        return rest

    section_start = heading.end()
    following = re.search(r"^##\s", rest[section_start:], re.M)
    section_end = section_start + following.start() if following else len(rest)
    fence = YAML_FENCE.search(rest[section_start:section_end])

    if fence is None:  # pragma: no cover - parse_phasing found entries, so a fence exists
        return rest

    start, end = section_start + fence.start(), section_start + fence.end()
    return rest[:start] + _render_phasing(entries) + rest[end:]


# ....................... #


def _rewrite_amendment_headings(rest: str) -> str:
    section = AMENDMENTS_SECTION.search(rest)

    if section is None:
        return rest

    head, tail = rest[: section.end()], rest[section.end() :]
    tail = _DATED_HEADING.sub(lambda m: f"### {m[1]} — {m[2]} — {m[3]}", tail)
    return head + tail


# ....................... #


def emit(text: str) -> str:
    """The parsed model, rendered back to text (D-25.1): frontmatter, the
    decision table, the phasing fence and dated amendment headings
    normalised; everything else passed through byte-for-byte. Raises
    `ValueError` on anything the parser itself would reject."""

    fm = parse_frontmatter(text)

    if fm is None:
        raise ValueError("no parseable YAML frontmatter")

    match = FRONTMATTER.match(text)

    if match is None:  # pragma: no cover - parse_frontmatter already matched this
        raise ValueError("no parseable YAML frontmatter")

    rest = text[match.end() :]
    rest = _rewrite_table(rest)
    rest = _rewrite_phasing(rest)
    rest = _rewrite_amendment_headings(rest)
    return render_frontmatter(fm) + rest


# ....................... #


def _split(text: str) -> tuple[dict[str, Any], str]:
    """One document's frontmatter and everything after it — the split every
    mutate function starts from, matching what `emit` itself parses."""

    match = FRONTMATTER.match(text)
    fm = parse_frontmatter(text)

    if match is None or fm is None:
        raise ValueError("no parseable YAML frontmatter")

    return fm, text[match.end() :]


# ....................... #


def _replace_table(rest: str, new_rows: list[DecisionRow]) -> str:
    """*rest*'s decision table, replaced whole by *new_rows* rendered
    canonically — the span-finding half of `_rewrite_table`, generalised to
    a caller-supplied row list so a mutate function can add, drop or edit a
    row before the table is re-rendered."""

    original = decision_table(rest)
    start = rest.find(TABLE_HEADER)

    if start == -1:
        raise ValueError("no Decisions table found")

    pos = start

    for _ in range(2 + len(original)):  # the header line, the separator, one line per row
        newline = rest.find("\n", pos)
        pos = newline + 1 if newline != -1 else len(rest)

    return rest[:start] + _render_decision_table(new_rows) + rest[pos:]


# ....................... #


def append_amendment(text: str, amendment: str, title: str, today: str) -> str:
    """`rfc amend` (D-25.4): appends the dated `### A-nn — date — title`
    skeleton to the end of the document's `## Amendments` container — the
    section runs to end of file by the same convention `check_amendments`
    already assumes — and records *amendment* in `amended_by`. The entry's
    own words are left for the author to write."""

    fm, rest = _split(text)

    if AMENDMENTS_SECTION.search(rest) is None:
        raise ValueError("no '## Amendments' section to append to")

    fm = {**fm, "amended_by": [*fm_list(fm, "amended_by"), amendment]}
    rest = rest.rstrip("\n") + f"\n\n### {amendment} — {today} — {title}\n"
    return emit(render_frontmatter(fm) + rest)


# ....................... #


def append_decision(text: str, identifier: str) -> str:
    """`rfc add-decision` (D-25.4): appends a row skeleton under *identifier*
    — the next free id in the document's own family, derived by
    `rfc_parse.next_decision` before this is called. The grade is written as
    `OPEN`, the vocabulary's own "not yet decided" value (D-25.3 LOCKED: no
    verb chooses a grade) — Paths and the decision text are left blank for
    the author."""

    fm, rest = _split(text)
    rows = decision_table(rest)

    if not rows:
        raise ValueError("no Decisions table to append to")

    new_row = DecisionRow(identifier=identifier, grade="OPEN", text="<decision>", paths=[])
    rest = _replace_table(rest, [*rows, new_row])
    return emit(render_frontmatter(fm) + rest)


# ....................... #


def retire_decision(text: str, identifier: str, today: str) -> str:
    """`rfc retire` (D-25.6): executes D-16.1 whole — removes *identifier*'s
    row, records it in `retired:`, and leaves a tombstone stub immediately
    after the table for the author to complete. Whether the result still
    checks clean — in particular, whether every remaining citation of
    *identifier* still resolves — is left to the transaction's check
    (D-25.2); this function only rewrites the text."""

    fm, rest = _split(text)
    rows = decision_table(rest)
    remaining = [row for row in rows if row.identifier != identifier]

    if len(remaining) == len(rows):
        raise ValueError(f"no decision {identifier!r} in this document's table")

    start = rest.find(TABLE_HEADER)

    if start == -1:
        raise ValueError("no Decisions table found")

    pos = start

    for _ in range(2 + len(rows)):
        newline = rest.find("\n", pos)
        pos = newline + 1 if newline != -1 else len(rest)

    # No citation of the "never reused" rule itself: which decision states it
    # (D-A.4 in this repository's own corpus) is a fact about one corpus, not
    # something this generic verb may assume of the corpus it is run against.
    tombstone = f"\n{identifier} was retired {today}; <why>. The identifier is never reused.\n"
    rest = rest[:start] + _render_decision_table(remaining) + tombstone + rest[pos:]
    fm = {**fm, "retired": [*fm_list(fm, "retired"), identifier]}
    return emit(render_frontmatter(fm) + rest)


# ....................... #


def relocate_paths_text(text: str, old: str, new: str) -> tuple[str, list[str]] | None:
    """`rfc relocate-paths` (D-25.7): rewrites every Paths cell carrying the
    exact glob *old* to *new* — an exact token match, never a substring,
    since a partial match risks rewriting an unrelated glob that merely
    shares a path segment. Returns `None` when nothing in this document
    matched. Decision text is never touched, per D-25.7."""

    fm, rest = _split(text)
    rows = decision_table(rest)
    touched: list[str] = []
    updated: list[DecisionRow] = []

    for row in rows:
        if old in row.paths:
            touched.append(row.identifier)
            row = replace(row, paths=[new if p == old else p for p in row.paths])

        updated.append(row)

    if not touched:
        return None

    rest = _replace_table(rest, updated)
    return emit(render_frontmatter(fm) + rest), touched


# ....................... #


def write_transaction(rfc_dir: Path, root: Path, mutations: dict[str, str]) -> CheckReport:
    """One parse-mutate-emit-check cycle (D-25.2): *mutations* (filename ->
    new text) is applied to a scratch copy of the corpus, the index is
    regenerated there, and the scratch corpus is checked whole. Only a clean
    check is copied back to *rfc_dir* — a red check leaves the real tree
    untouched."""

    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)

        for path in rfc_dir.glob("*.md"):
            shutil.copy2(path, scratch / path.name)

        for name, mutated in mutations.items():
            (scratch / name).write_text(mutated, encoding="utf-8")

        (scratch / "INDEX.md").write_text(build_index(rfc_files(scratch)), encoding="utf-8")
        report = check_corpus(scratch, root)

        if not report.ok:
            return report

        for name in (*mutations, "INDEX.md"):
            (rfc_dir / name).write_text(
                (scratch / name).read_text(encoding="utf-8"), encoding="utf-8"
            )

    return report
