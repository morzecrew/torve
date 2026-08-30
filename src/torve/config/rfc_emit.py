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
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from torve.config.rfc_parse import (
    AMENDMENTS_SECTION,
    FRONTMATTER,
    PHASING_HEADING,
    TABLE_HEADER,
    YAML_FENCE,
    DecisionRow,
    PhasingEntry,
    decision_table,
    parse_frontmatter,
    parse_phasing,
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
        lines.append(
            f"| {row.identifier} | `{row.grade}` | {row.text} | {paths} | {consequence} |"
        )

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
