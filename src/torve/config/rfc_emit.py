"""The canonical emitter beside `torve.config.spec` (RFC 0025 §5.1,
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

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import yaml

from torve.config.spec import (
    AMENDMENTS_SECTION,
    FRONTMATTER,
    PHASING_HEADING,
    TABLE_HEADER,
    YAML_FENCE,
    CheckReport,
    DecisionRow,
    PhasingEntry,
    archive_dir,
    build_index,
    check_corpus,
    decision_table,
    fm_list,
    parse_frontmatter,
    parse_phasing,
    rfc_files,
)
from torve.domain.spec import fingerprint

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

# `fingerprints:` in frontmatter (RFC 0053 D-53.4, D-53.5): identifier ->
# "<full>/<rule>", full over text, grade and paths, rule over grade and
# paths alone. Written by every mutating verb, read by `rfc check` to tell
# a hand-edited grade or paths (a problem) from a hand-edited text (a
# warning that `rfc fix` re-stamps).
FINGERPRINTS_KEY = "fingerprints"


def rule_fingerprint(grade: str, paths: list[str]) -> str:
    material = "\n".join([grade, " ".join(sorted(paths))])

    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def stamp(row: DecisionRow) -> str:
    """The value the tool records for a row it just changed."""

    return f"{fingerprint(row.text, row.grade, row.paths)}/{rule_fingerprint(row.grade, row.paths)}"


def _stamps(fm: dict[str, Any]) -> dict[str, str]:
    raw = fm.get(FINGERPRINTS_KEY)

    if not isinstance(raw, dict):
        return {}

    return {str(k): str(v) for k, v in cast("dict[object, object]", raw).items()}


def _stamped(fm: dict[str, Any], row: DecisionRow) -> dict[str, Any]:
    stamps = {**_stamps(fm), row.identifier: stamp(row)}

    return {**fm, FINGERPRINTS_KEY: stamps}


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

    if isinstance(value, dict):
        entries = "\n".join(
            f"  {_yaml_scalar(k)}: {_yaml_scalar(v)}"
            for k, v in sorted(cast("dict[str, Any]", value).items())
        )
        return f"{key}:\n{entries}" if entries else f"{key}: {{}}"

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


def render_changes(changes: list[dict[str, Any]]) -> str:
    """A `yaml changes` fence (RFC 0053 §5.2): one entry per typed edit,
    `before` read from the document as it stood."""

    lines = ["```yaml changes"]

    for change in changes:
        lines.append(f"- subject: {_one_line(change['subject'])}")
        lines.append(f"  field: {_one_line(change['field'])}")

        for key in ("before", "after"):
            value = change.get(key)

            if isinstance(value, list):
                lines.append(f"  {key}: {_dump_list(value)}")
            else:
                lines.append(f"  {key}: {_one_line(value)}")

    lines.append("```")
    return "\n".join(lines) + "\n"


# ....................... #


def _one_line(value: Any) -> str:
    """One scalar on one line, whatever its length: a JSON string is a
    valid YAML double-quoted scalar and never wraps, where PyYAML's own
    rendering folds a long plain scalar across lines — which inside a list
    item is not the YAML it came from (T-0293)."""

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)

    return _yaml_scalar(value)


# ....................... #


def append_amendment(
    text: str,
    amendment: str,
    title: str,
    today: str,
    changes: list[dict[str, Any]] | None = None,
) -> str:
    """`rfc amend` (D-25.4): appends the dated `### A-nn — date — title`
    skeleton to the end of the document's `## Amendments` container — the
    section runs to end of file by the same convention `check_amendments`
    already assumes — and records *amendment* in `amended_by`. The entry's
    own words are left for the author to write; the typed diff of any row
    the same verb changed rides beneath the heading as a `changes` fence
    (D-53.4), which is the only moment the prior value still exists."""

    fm, rest = _split(text)

    if AMENDMENTS_SECTION.search(rest) is None:
        raise ValueError("no '## Amendments' section to append to")

    fm = {**fm, "amended_by": [*fm_list(fm, "amended_by"), amendment]}
    rest = rest.rstrip("\n") + f"\n\n### {amendment} — {today} — {title}\n"

    if changes:
        rest += "\n" + render_changes(changes)

    return emit(render_frontmatter(fm) + rest)


# ....................... #


def amend_row(
    text: str,
    identifier: str,
    *,
    grade: str | None = None,
    paths: list[str] | None = None,
    new_text: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """One row changed by the tool (D-53.4): grade, paths or text replaced,
    the row re-stamped in `fingerprints:`, and the typed diff returned for
    the amendment heading that records it. Raises `ValueError` when the
    row is unknown or nothing was asked to change."""

    fm, rest = _split(text)
    rows = decision_table(rest)
    current = next((row for row in rows if row.identifier == identifier), None)

    if current is None:
        raise ValueError(f"no decision {identifier!r} in this document's table")

    changes: list[dict[str, Any]] = []
    updated = current

    if grade is not None and grade != current.grade:
        changes.append(
            {"subject": identifier, "field": "grade", "before": current.grade, "after": grade}
        )
        updated = replace(updated, grade=grade)

    if paths is not None and paths != current.paths:
        changes.append(
            {"subject": identifier, "field": "paths", "before": current.paths, "after": paths}
        )
        updated = replace(updated, paths=paths)

    if new_text is not None and new_text.strip() != current.text.strip():
        changes.append(
            {
                "subject": identifier,
                "field": "text",
                "before": current.text,
                "after": new_text.strip(),
            }
        )
        updated = replace(updated, text=new_text.strip())

    if not changes:
        raise ValueError(f"nothing to change on {identifier}")

    changes.append(
        {
            "subject": identifier,
            "field": "fingerprint",
            "before": _stamps(fm).get(identifier),
            "after": stamp(updated),
        }
    )
    rest = _replace_table(rest, [updated if row.identifier == identifier else row for row in rows])
    return emit(render_frontmatter(_stamped(fm, updated)) + rest), changes


# ....................... #


def fix_row_text(text: str, identifier: str, new_text: str) -> tuple[str, list[dict[str, Any]]]:
    """`rfc fix` (D-53.4's editorial lane): the row's text replaced and the
    row re-stamped, with the before and after recorded in a `changes`
    fence directly under the decision table — never an amendment number.
    A typo costs one command and loses nothing; a rewording that changes
    the rule's meaning is the author's judgement to raise to an amendment,
    and the recorded pair is what lets a reviewer say so."""

    fm, rest = _split(text)
    rows = decision_table(rest)
    current = next((row for row in rows if row.identifier == identifier), None)

    if current is None:
        raise ValueError(f"no decision {identifier!r} in this document's table")

    updated = replace(current, text=new_text.strip())
    already = _stamps(fm).get(identifier)

    if updated.text == current.text and already == stamp(updated):
        raise ValueError(f"{identifier}'s text already reads that way, and it is stamped")

    changes: list[dict[str, Any]] = []

    if updated.text != current.text:
        changes.append(
            {"subject": identifier, "field": "text", "before": current.text, "after": updated.text}
        )

    # The same text, re-stamped: the row was edited by hand and the author
    # is accepting the edit as editorial — the pair recorded is the stamped
    # fingerprint against the one the row now carries.
    changes.append(
        {"subject": identifier, "field": "fingerprint", "before": already, "after": stamp(updated)}
    )
    rest = _replace_table(rest, [updated if row.identifier == identifier else row for row in rows])
    rest = _append_editorial(rest, changes)
    return emit(render_frontmatter(_stamped(fm, updated)) + rest), changes


# ....................... #

_EDITORIAL_MARK = "<!-- editorial changes, recorded by `torve rfc fix` -->"


def _append_editorial(rest: str, changes: list[dict[str, Any]]) -> str:
    """The editorial fence lives right after the decision table, one fence
    per document, entries appended in order."""

    fence_lines = render_changes(changes).splitlines()[1:-1]  # entries only
    marker = rest.find(_EDITORIAL_MARK)

    if marker != -1:
        fence_start = rest.find("```yaml changes", marker)
        fence_end = rest.find("```", fence_start + len("```yaml changes"))

        if fence_start != -1 and fence_end != -1:
            body = rest[fence_start:fence_end].rstrip("\n")
            return (
                rest[:fence_start] + body + "\n" + "\n".join(fence_lines) + "\n" + rest[fence_end:]
            )

    start = rest.find(TABLE_HEADER)
    rows = decision_table(rest)
    pos = start

    for _ in range(2 + len(rows)):
        newline = rest.find("\n", pos)
        pos = newline + 1 if newline != -1 else len(rest)

    block = "\n" + _EDITORIAL_MARK + "\n" + render_changes(changes)
    return rest[:pos] + block + rest[pos:]


# ....................... #


def append_decision(text: str, identifier: str) -> str:
    """`rfc add-decision` (D-25.4): appends a row skeleton under *identifier*
    — the next free id in the document's own family, derived by
    `spec.next_decision` before this is called. The grade is written as
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


def retire_decision(text: str, identifier: str, today: str, reason: str = "") -> str:
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
    why = reason or "<why>"
    tombstone = f"\n{identifier} was retired {today}; {why}. The identifier is never reused.\n"
    rest = rest[:start] + _render_decision_table(remaining) + tombstone + rest[pos:]
    stamps = {k: v for k, v in _stamps(fm).items() if k != identifier}
    fm = {**fm, "retired": [*fm_list(fm, "retired"), identifier]}
    fm = (
        {**fm, FINGERPRINTS_KEY: stamps}
        if stamps
        else {k: v for k, v in fm.items() if k != FINGERPRINTS_KEY}
    )
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


def write_transaction(
    rfc_dir: Path,
    root: Path,
    mutations: dict[str, str],
    deletions: tuple[str, ...] = (),
    archived: dict[str, str] | None = None,
) -> CheckReport:
    """One parse-mutate-emit-check cycle (D-25.2): *mutations* (filename ->
    new text) is applied and *deletions* removed in a scratch copy of the
    corpus, *archived* (filename -> text) is placed in a scratch copy of
    the archive beside it, the index is regenerated there, and the scratch
    corpus is checked whole with the archive in view — what `check` sees
    is what the transaction sees (A-140). Only a clean check is copied
    back to *rfc_dir* and the real archive; a red check leaves both
    untouched. A deletion paired with an archived copy is what `rfc
    archive` does (D-53.8); nothing else deletes."""

    real_archive = archive_dir(rfc_dir)

    with tempfile.TemporaryDirectory() as scratch_name:
        scratch_root = Path(scratch_name)
        scratch = scratch_root / rfc_dir.name
        scratch.mkdir()
        scratch_archive = archive_dir(scratch)

        if real_archive.is_dir():
            shutil.copytree(real_archive, scratch_archive)

        for name, text in (archived or {}).items():
            scratch_archive.mkdir(parents=True, exist_ok=True)
            (scratch_archive / name).write_text(text, encoding="utf-8")

        for path in rfc_dir.glob("*.md"):
            if path.name not in deletions:
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

        for name, text in (archived or {}).items():
            real_archive.mkdir(parents=True, exist_ok=True)
            (real_archive / name).write_text(text, encoding="utf-8")

        for name in deletions:
            (rfc_dir / name).unlink(missing_ok=True)

    return report


# ....................... #


def archive_document(text: str, superseded_by: str, today: str) -> str:
    """The frontmatter a retired document carries in the archive (D-53.8):
    `status: superseded`, `superseded_by` naming the baseline. Every other
    byte, every identifier, stays — an archived identifier still resolves."""

    fm, rest = _split(text)

    if str(fm.get("status")) == "superseded" and fm.get("superseded_by"):
        raise ValueError(f"RFC {fm.get('id')} is already superseded by {fm.get('superseded_by')}")

    fm = {**fm, "status": "superseded", "superseded_by": superseded_by}
    marker = f"\n*Archived {today}: superseded by {superseded_by} (RFC 0053 D-53.8).*\n"
    return render_frontmatter(fm) + rest.rstrip("\n") + "\n" + marker
