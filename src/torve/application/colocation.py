"""The projections beside the code (S-0054/the-projections-beside-the-code, S-0054/D-6, S-0054/D-7): for
every directory a standing row's paths or an accepted document's phase
scope names, a managed section in that directory's `AGENTS.md` carrying
the rows with their grade, consequence, check and state, the invariants
holding over it, and the paths other work is contending for; plus a root
index of governed directories, because a harness that loads nested files
lazily should know a rule is waiting before it opens the subtree.

Rendered, never edited: text outside the markers is the operator's and is
never touched; a hand edit inside them is drift, which `--check` names by
file. Rows, invariants, checks, tests and warnings only — never an
overview (S-0054/D-8): the one controlled study of context files found
generated overviews cost and did not help, and rules did.

Application code (S-0015/D-1): reads the model through the loader and the
telemetry stream for contention, writes files under the repository root.
Nothing here reaches the record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from torve.application.specquality import telemetry_file
from torve.config.spec import archive_dir, load_corpus
from torve.domain.spec import Corpus, Decision, Document, Invariant

# ----------------------- #

AGENTS_FILE = "AGENTS.md"
MARK_OPEN = "<!-- torve:managed {where} — rendered from the corpus; do not edit by hand -->"
MARK_CLOSE = "<!-- /torve:managed -->"
CONTENTION_WINDOW = 500  # telemetry rows, the drafter's window (S-0020 phase 3)


# ....................... #


@dataclass
class Projection:
    """What `project` computed: the managed section per directory (the
    repository root is `"."`), and — after a write or a check — which files
    changed, which drift, and which were removed."""

    sections: dict[str, str] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.drifted


# ----------------------- #


def directory_of(glob: str) -> str:
    """The directory a path glob reaches: its literal prefix up to the first
    wildcard, or the parent of a literal file. A root-level file is the
    root's, spelled `"."`."""

    literal = glob.split("*", 1)[0]

    if literal.endswith("/"):
        head = literal.rstrip("/")
    elif "/" in literal:
        head = literal.rsplit("/", 1)[0]
    else:
        head = ""

    return head or "."


# ....................... #


def _projectable(where: str, root: Path, rfc_dir: Path) -> bool:
    """Where a section may go: an existing directory that is not hidden
    (the engine's own state under `.torve/`, `.github/`, `.agents/`), not
    the corpus path (S-0016/D-25 admits nothing else there) and not the archive
    beside it. The root is always projectable."""

    if where == ".":
        return True

    if any(part.startswith(".") for part in where.split("/")):
        return False

    if not (root / where).is_dir():
        return False

    corpus_rel = rfc_dir.resolve().relative_to(root.resolve()).as_posix()
    archive_rel = archive_dir(rfc_dir).resolve().relative_to(root.resolve()).as_posix()
    excluded = (corpus_rel, archive_rel)

    return not any(
        where == one or where.startswith(one + "/") or one.startswith(where + "/")
        for one in excluded
    )


def governed_directories(
    corpus: Corpus, root: Path, rfc_dir: Path
) -> dict[str, list[tuple[Document, Decision]]]:
    """Directory -> the standing rows whose paths reach it (phase scopes
    count toward the directory set but carry no rows), projectable
    directories only."""

    found: dict[str, list[tuple[Document, Decision]]] = {}

    for doc in corpus.standing():
        if doc.superseded_by:
            continue

        for row in doc.decisions:
            # A row its own document replaced, not a document another one did
            # (S-0054/D-6). Both belong in the section only if the reader is
            # meant to reconcile them, and a rendered page has no way to say
            # which of two contradicting rows is live — so the replacement
            # stands alone, the way an amended row's current text does.
            if row.superseded_by:
                continue

            for glob in row.paths:
                where = directory_of(glob)

                if not _projectable(where, root, rfc_dir):
                    continue

                rows = found.setdefault(where, [])

                if all(existing.id != row.id for _, existing in rows):
                    rows.append((doc, row))

        for entry in doc.phasing:
            for glob in entry.scope:
                where = directory_of(glob)

                if _projectable(where, root, rfc_dir):
                    found.setdefault(where, [])

    return found


# ....................... #


def invariants_over(corpus: Corpus, where: str) -> list[tuple[Document, Invariant]]:
    return [
        (doc, invariant)
        for doc in corpus.standing()
        if not doc.superseded_by
        for invariant in doc.invariants
        if any(directory_of(glob) == where for glob in invariant.paths)
    ]


def superseded_rows(corpus: Corpus) -> list[str]:
    """Every standing document's rows that a later row replaced — what the
    projection now withholds, so a reader can see the filter did something."""

    return sorted(
        row.id
        for doc in corpus.standing()
        if not doc.superseded_by
        for row in doc.decisions
        if row.superseded_by
    )


# ....................... #


def contended_paths(root: Path) -> dict[str, int]:
    """Paths blocked dispatches collided on in the telemetry stream's last
    rows — the same read the drafter's execution facts make."""

    stream = telemetry_file(root)

    if not stream.is_file():
        return {}

    contended: dict[str, int] = {}

    for line in stream.read_text(encoding="utf-8").splitlines()[-CONTENTION_WINDOW:]:
        try:
            record = cast("dict[str, Any]", json.loads(line))
        except json.JSONDecodeError:
            continue

        if record.get("event") == "blocked_dispatch":
            path = str(record.get("path", ""))

            if path:
                contended[path] = contended.get(path, 0) + 1

    return contended


# ----------------------- #


def render_section(
    where: str,
    rows: list[tuple[Document, Decision]],
    invariants: list[tuple[Document, Invariant]],
    contended: dict[str, int],
) -> str:
    """One directory's managed section. Rows, invariants, checks and
    warnings; no prose beyond the row's own text and consequence."""

    label = "the repository root" if where == "." else f"`{where}/`"
    lines = [MARK_OPEN.format(where=where if where != "." else "root"), ""]

    if rows:
        lines += [f"## Decisions governing {label}", ""]

        for doc, row in rows:
            lines.append(f"### {row.id} — `{row.grade}` ({doc.title})")
            lines += ["", row.text, ""]
            lines.append("- Paths: " + " ".join(f"`{p}`" for p in row.paths))

            if row.consequence:
                lines.append(f"- Consequence: {row.consequence}")

            if row.check:
                lines.append(
                    f"- Check: `{row.check}` ({row.check_state}; runs as `decision:{row.id}`, "
                    "no log entry owed)"
                )
            elif row.grade == "LOCKED":
                lines.append(
                    "- Touching these paths owes a divergence entry: "
                    "`torve log owed <task> --touched <files>` before you finish"
                )

            lines.append("")

    if invariants:
        lines += [f"## Invariants holding over {label}", ""]

        for _doc, invariant in invariants:
            lines.append(f"- **{invariant.id}**: {invariant.statement}")
            lines.append("  - Paths: " + " ".join(f"`{p}`" for p in invariant.paths))
            lines.append(f"  - Check: `{invariant.check}`")

        lines.append("")

    here = {
        path: count
        for path, count in contended.items()
        if directory_of(path) == where or (where != "." and path.startswith(where + "/"))
    }

    if here:
        lines += ["## Contended now", ""]
        lines += [
            f"- `{path}` — {count} blocked dispatch(es) in the last {CONTENTION_WINDOW} attempts"
            for path, count in sorted(here.items(), key=lambda kv: -kv[1])
        ]
        lines.append("")

    lines.append(MARK_CLOSE)
    return "\n".join(lines) + "\n"


# ....................... #


def render_index(directories: dict[str, int]) -> str:
    """The root's managed index: which directories carry a section, so a
    harness that loads nested files lazily knows a rule is waiting."""

    lines = [
        MARK_OPEN.format(where="root index"),
        "",
        "## Governed directories",
        "",
        "Each of these carries a managed `AGENTS.md` section listing the decisions",
        "and invariants that govern it. `torve spec show S-NNNN/D-n`, `torve spec paths`",
        "`<file>` and `torve spec tests S-NNNN/D-n` read the same corpus from the worktree.",
        "",
    ]

    for where, count in sorted(directories.items()):
        if where == ".":
            continue

        lines.append(f"- `{where}/` — {count} decision(s)")

    lines += ["", MARK_CLOSE]
    return "\n".join(lines) + "\n"


# ----------------------- #


def splice(existing: str, section: str, where: str) -> str:
    """*section* replaces the managed block in *existing* if one is there,
    else is appended; everything outside the markers survives byte for
    byte."""

    opening = MARK_OPEN.format(where=where)
    start = existing.find(opening)

    if start == -1:
        joint = (
            ""
            if not existing or existing.endswith("\n\n")
            else ("\n" if existing.endswith("\n") else "\n\n")
        )
        return existing + joint + section

    end = existing.find(MARK_CLOSE, start)

    if end == -1:
        return existing[:start] + section

    end += len(MARK_CLOSE)

    if existing[end : end + 1] == "\n":
        end += 1

    return existing[:start] + section + existing[end:]


# ....................... #


def strip_section(existing: str, where: str) -> str:
    """The file without its managed block; the operator's text stays."""

    opening = MARK_OPEN.format(where=where)
    start = existing.find(opening)

    if start == -1:
        return existing

    end = existing.find(MARK_CLOSE, start)
    end = len(existing) if end == -1 else end + len(MARK_CLOSE)

    if existing[end : end + 1] == "\n":
        end += 1

    remaining = existing[:start] + existing[end:]

    return remaining.rstrip("\n") + ("\n" if remaining.strip() else "")


# ----------------------- #


def compute(root: Path, rfc_dir: Path) -> Projection:
    """Every managed section the corpus implies, keyed by directory."""

    corpus = load_corpus(rfc_dir)
    governed = governed_directories(corpus, root, rfc_dir)
    contended = contended_paths(root)
    projection = Projection()

    for where, rows in governed.items():
        projection.sections[where] = render_section(
            where, rows, invariants_over(corpus, where), contended
        )

    counts = {where: len(rows) for where, rows in governed.items()}
    root_section = projection.sections.get(".", "")
    projection.sections["."] = (root_section + ("\n" if root_section else "")) + render_index(
        counts
    )

    return projection


# ....................... #


def _managed_files(root: Path) -> list[Path]:
    """Every AGENTS.md under the root carrying a managed block, so a
    directory that stopped being governed loses its section."""

    found: list[Path] = []

    for path in root.rglob(AGENTS_FILE):
        parts = path.relative_to(root).parts[:-1]

        if any(part.startswith(".") or part == "node_modules" for part in parts):
            continue

        if "torve:managed" in path.read_text(encoding="utf-8"):
            found.append(path)

    return found


def _where_of(root: Path, path: Path) -> str:
    rel = path.parent.relative_to(root).as_posix()

    return "." if rel in ("", ".") else rel


# ....................... #


def project(root: Path, rfc_dir: Path, *, check: bool = False) -> Projection:
    """Write every section into place (or, with *check*, compare and name
    the drift). A directory with no section left loses its block, and an
    otherwise-empty file is removed."""

    projection = compute(root, rfc_dir)
    wanted: dict[str, str] = {}

    for where, section in projection.sections.items():
        if where == ".":
            body_section = projection.sections["."]
            # The root file carries both its own rows (if any) and the index,
            # spliced as one block under the "root index" marker after any
            # "root" block: simplest is one block, so render it whole.
            wanted["."] = body_section
        else:
            wanted[where] = section

    for where, section in wanted.items():
        path = root / (AGENTS_FILE if where == "." else f"{where}/{AGENTS_FILE}")
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        rendered = (
            _splice_root(existing, section) if where == "." else splice(existing, section, where)
        )

        if rendered == existing:
            continue

        rel = path.relative_to(root).as_posix()

        if check:
            projection.drifted.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            projection.written.append(rel)

    for path in _managed_files(root):
        where = _where_of(root, path)

        if where in wanted:
            continue

        rel = path.relative_to(root).as_posix()
        stripped = strip_section(path.read_text(encoding="utf-8"), where)

        if check:
            projection.drifted.append(rel)
        elif stripped.strip():
            path.write_text(stripped, encoding="utf-8")
            projection.removed.append(rel)
        else:
            path.unlink()
            projection.removed.append(rel)

    return projection


# ....................... #


def _splice_root(existing: str, section: str) -> str:
    """The root file carries up to two blocks — its own rows and the index —
    rendered as one section string; both markers are replaced together."""

    for where in ("root", "root index"):
        existing = strip_section(existing, where)

    return splice(existing, section, "__never__")
