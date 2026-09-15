"""The context pack (S-0054/the-context-pack, S-0054/D-10, S-0054/D-11): `.torve/context/`
in the worktree, written host-side before the prompt, with the facts the
corpus cannot carry and the tree cannot grep — this task's rows with
their consequences and the amendments that changed them, the rows other
documents hold over the same paths, the battery and its axes, the tests
covering the scope, this task's own prior attempts and what convicted
them (S-0054/D-12), the paths other work is contending for, and the schemas
the engine parses agent output with.

Every builder is a pure function of (record snapshot, tree, contract):
computed with no model, deterministic for a base sha and record state,
gitignored, never in an image, byte-identical for a shadow run. The pack
never carries another task's escalations, findings or attempts, and no
model output from a previous attempt beyond the divergence entries it
recorded (S-0007/mcp-as-the-read-surface, S-0001/D-29, S-0017/D-7).

The pack's small files travel in the attempt's first message (S-0076/D-1);
`index.md` names what stayed behind a read — `decisions.json`, large and
often unopened, and the schemas — so nothing points at bytes already in
context.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from pathspec import GitIgnoreSpec

from torve.application.colocation import contended_paths
from torve.application.planner import standing_decisions
from torve.application.projections import why_report
from torve.config.manifest import load_manifest
from torve.config.spec import SpecError, load_corpus
from torve.domain.spec import Corpus, Document
from torve.domain.task import Task
from torve.gates.context import GitError, git

# ----------------------- #

PACK_DIR = Path(".torve") / "context"
OUTPUT_TAIL = 2000  # characters of a red gate's output the retry sees
# Directories the symbol index does not walk: no source of the repository's own
# is under them, and a virtualenv's site-packages alone outweighs the tree.
UNWALKED = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.M)
GOVERNING = re.compile(r"(?<![\w/-])(S-\d{4}/D-\d+)(?![\w/-])")


# ....................... #


def _load(rfc_dir: Path) -> Corpus | None:
    try:
        return load_corpus(rfc_dir)
    except (SpecError, ValueError):
        return None


# ----------------------- #


def decisions_file(task: Task, corpus: Corpus | None, rfc_dir: Path) -> dict[str, Any]:
    """The contract's rows whole, the amendments that changed each, and the
    accepted rows from other documents whose paths intersect the scope —
    the standing set the document lane never saw (S-0030/D-6, closed here)."""

    by_id: dict[str, tuple[Document, Any]] = {}

    if corpus is not None:
        for doc in corpus.documents:
            for row in doc.decisions:
                by_id[row.id] = (doc, row)

    rows: list[dict[str, Any]] = []

    for decision in task.decisions:
        entry: dict[str, Any] = {
            "id": decision.id,
            "grade": decision.grade,
            "text": decision.text,
            "paths": list(decision.paths),
            "consequence": decision.consequence,
            "check": decision.check,
            "check_state": decision.check_state,
        }
        hit = by_id.get(decision.id)

        if hit is not None:
            doc, row = hit
            entry["rationale"] = row.rationale
            entry["cites"] = list(row.cites)
            entry["archived"] = doc.archived
            entry["amended_by"] = [
                {
                    "amendment": a.id,
                    "at": str(a.at) if a.at else None,
                    "field": c.field,
                    "before": c.before,
                    "after": c.after,
                }
                for a in doc.amendments
                for c in a.changes
                # Not the stamp the tool re-writes on every amendment: a
                # fingerprint is how `spec check` catches a hand edit, and to an
                # agent reading a row's history it is two hashes where a rule
                # should be. A third of the change entries this corpus holds are
                # these, and none of them says anything.
                if c.subject == decision.id and c.field != "fingerprint"
            ]

        rows.append(entry)

    inherited = {d.id for d in task.decisions}
    standing = (
        [
            {
                "id": row.id,
                "grade": row.grade,
                "text": row.text,
                "paths": list(row.paths),
                "consequence": row.consequence,
                "check": row.check,
            }
            for row in standing_decisions(rfc_dir, task.scope.allow)
            if row.id not in inherited
        ]
        if rfc_dir.is_dir()
        else []
    )

    return {
        "schema_version": 1,
        "task": task.id,
        "inherited": rows,
        "standing_over_scope": standing,
    }


# ....................... #


def gates_file(root: Path, task: Task, manifest_path: Path) -> dict[str, Any]:
    """The battery this attempt will face: every manifest entry with its
    axis, state and command, and the contract's decision gates."""

    entries: list[dict[str, Any]] = []

    if manifest_path.is_file():
        for gate in load_manifest(manifest_path).resolved_gates():
            entries.append(
                {
                    "name": gate.name,
                    "run": gate.run,
                    "axis": gate.axis,
                    "state": gate.state,
                    "origin": gate.origin,
                    "convicts_on": _convicts_on(gate.name),
                }
            )

    for row in task.decisions:
        if row.check:
            entries.append(
                {
                    "name": f"decision:{row.id}",
                    "run": row.check,
                    "axis": "compliance",
                    "state": row.check_state,
                    "origin": f"contract row {row.id}",
                    "convicts_on": f"the check exits non-zero; no log entry is owed for {row.id}",
                }
            )

    return {"schema_version": 1, "gates": entries}


def _convicts_on(name: str) -> str:
    return {
        "scope": "a changed path outside scope.allow or inside scope.deny",
        "secrets": "a secret pattern in an added line",
        "no-test-tampering": "an existing test edited without a licence",
        "decisions-reported": "a LOCKED row's paths touched with no log entry, or a malformed entry",
        "self-audit": "drift_count missing from the log",
        "acceptance": "an acceptance command exits non-zero",
        "user-facing-text": "a corpus coordinate in a user-facing string",
        "source-layout": "a module named for what it is not",
        "layering": "an import against the layer contracts",
        "spec-valid": "a corpus document that does not check",
        "coverage-delta": "changed lines below the coverage threshold",
        "spec-projection": "a managed AGENTS.md section that drifted from the corpus",
    }.get(name, "the command exits non-zero")


# ....................... #


def tests_file(root: Path, task: Task) -> dict[str, Any]:
    """What covers the scope: files under scope.allow with their measured
    line coverage from the last battery's coverage.xml, and the test
    modules that name them (the T-0113 rule). Per-test ids need coverage
    contexts the battery does not record yet; what it does record is here."""

    allow = GitIgnoreSpec.from_lines(task.scope.allow or ["**"])
    covered: list[dict[str, Any]] = []
    report = root / "coverage.xml"

    if report.is_file():
        try:
            tree = ElementTree.parse(report)
        except ElementTree.ParseError:
            tree = None

        if tree is not None:
            for cls in tree.iter("class"):
                filename = cls.get("filename") or ""
                path = filename if filename.startswith("src/") else f"src/{filename}"

                if not allow.match_file(path):
                    continue

                lines = list(cls.iter("line"))
                hit = sum(1 for line in lines if line.get("hits") not in (None, "0"))
                covered.append({"path": path, "lines": len(lines), "covered": hit})

    named: list[dict[str, str]] = []

    for glob in task.scope.allow:
        if "*" in glob or not glob.endswith(".py") or glob.startswith("tests/"):
            continue

        stem = Path(glob).stem
        candidate = root / "tests" / f"test_{stem}.py"

        if candidate.is_file():
            named.append({"module": glob, "test": f"tests/test_{stem}.py"})

    return {"schema_version": 1, "coverage": covered, "named_tests": named}


# ....................... #


def attempts_file(root: Path, task: Task) -> dict[str, Any]:
    """This task's prior attempts (own task only): verdict, tier, convictions
    and, for each red gate, the output tail, the governing decision ids it
    names and the failed test ids it lists — the red reaching the retry
    (S-0054/D-12), read from the rows the runner already wrote."""

    report = why_report(root, task.id)
    attempts: list[dict[str, Any]] = []

    for attempt in cast("list[dict[str, Any]]", report.get("attempts") or []):
        entry: dict[str, Any] = {
            "attempt": attempt.get("attempt"),
            "at": attempt.get("at"),
            "verdict": attempt.get("verdict"),
            "tier": attempt.get("tier"),
            "model": attempt.get("model"),
            "convictions": list(cast("list[str]", attempt.get("convictions") or [])),
            "escalation": attempt.get("escalation"),
        }
        attempts.append(entry)

    reds = _red_gates(root, task.id)

    return {"schema_version": 1, "task": task.id, "attempts": attempts, "last_red_gates": reds}


def _red_gates(root: Path, task_id: str) -> list[dict[str, Any]]:
    """The last gate-run row for this task that carried a red: each red
    gate's output tail, the decision ids and failed test ids in it."""

    from torve.application.specquality import telemetry_file

    stream = telemetry_file(root)

    if not stream.is_file():
        return []

    last: dict[str, Any] | None = None

    for line in stream.read_text(encoding="utf-8").splitlines():
        try:
            record = cast("dict[str, Any]", json.loads(line))
        except json.JSONDecodeError:
            continue

        if record.get("task_id") != task_id or not record.get("results"):
            continue

        results = cast("list[dict[str, Any]]", record["results"])

        if any(r.get("outcome") in ("fail", "error") for r in results):
            last = record

    if last is None:
        return []

    touched = _touched_paths(root, last)
    reds: list[dict[str, Any]] = []

    for result in cast("list[dict[str, Any]]", last["results"]):
        if result.get("outcome") not in ("fail", "error"):
            continue

        output = str(result.get("output") or "")
        reds.append(
            {
                "gate": result.get("name"),
                "state": result.get("state"),
                "output_tail": output[-OUTPUT_TAIL:],
                "truncated": len(output) > OUTPUT_TAIL or "truncated" in output,
                "touched_paths": touched,
                "governing_decisions": sorted(set(GOVERNING.findall(output)))
                if result.get("name") in ("decisions-reported", "scope")
                else [],
                "failed_tests": sorted(set(FAILED_TEST.findall(output))),
            }
        )

    return reds


def _touched_paths(root: Path, record: dict[str, Any]) -> list[str]:
    """The paths the convicted diff touched, from the shas the record itself
    names (S-0069/D-2): `merge_base` to `head` over the objects, so the answer
    is deterministic for the record and survives the worktree it was run in.
    Empty when a sha is missing or its objects are gone — the block then says
    nothing about a tree the record cannot point at."""

    base = str(record.get("merge_base") or "")
    head = str(record.get("head") or "")

    if not (base and head):
        return []

    try:
        out = git(root, "diff", "--name-only", base, head)
    except GitError:
        return []

    return sorted({line for line in out.splitlines() if line.strip()})


# ....................... #


def conviction_of(root: Path, task: Task) -> dict[str, Any] | None:
    """The conviction that ended the previous attempt, as one block's worth of
    facts (S-0069/D-1, D-2): the blocking gate, its output tail, the paths its
    diff touched and the inherited rows governing those paths — read from what
    the pack already writes. None when the last red pass convicted nothing
    blocking; a shadow red is a fact, not a conviction."""

    for red in _red_gates(root, task.id):
        if str(red.get("state") or "blocking") != "blocking":
            continue

        touched = cast("list[str]", red.get("touched_paths") or [])
        rows = [
            {"id": row.id, "grade": row.grade, "text": row.text}
            for row in task.decisions
            if row.paths
            and any(GitIgnoreSpec.from_lines(list(row.paths)).match_file(path) for path in touched)
        ]

        return {**red, "governing_rows": rows}

    return None


# ....................... #


def contended_file(root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contended": [
            {"path": path, "blocked_dispatches": count}
            for path, count in sorted(contended_paths(root).items(), key=lambda kv: -kv[1])
        ],
    }


# ....................... #


def schemas() -> dict[str, dict[str, Any]]:
    """The output contracts, as contracts: what the engine parses agent
    output with (S-0054/D-15) and the models a row and a document take."""

    from torve.application.intake import Draft
    from torve.domain.attempt import Finding
    from torve.domain.spec import Document

    return {
        "task": Task.model_json_schema(),
        "document": Document.model_json_schema(),
        "finding": Finding.model_json_schema(),
        "draft": Draft.model_json_schema(),
    }


# ----------------------- #


def source_file(root: Path, task: Task) -> dict[str, Any] | None:
    """The source the contract names, whole (S-0060/D-9) — what asked for
    this work, so the executor reads the audit rather than its slug. None
    when the contract names none, or names a document, which the corpus
    files already carry."""

    from torve.config.sources import load_sources
    from torve.config.spec import SpecError

    if not task.source:
        return None

    try:
        found = load_sources(root).get(task.source)
    except SpecError:
        return None

    return found.model_dump(mode="json") if found is not None else None


# ....................... #


def symbols_file(root: Path) -> str:
    """Every symbol the tree defines and where (S-0076/D-5): one line per
    class, function, method and module-level constant, `path:line` first so a
    grep's hit is already the coordinate. The whole tree, not the scope,
    because what a lookup wants is usually outside it — and a file, never
    prompt content: an attempt that never greps it pays nothing for it.

    Read with `ast`, so a name is a definition rather than whatever matched a
    regex. A file that will not parse contributes nothing and stops nothing."""

    lines: list[str] = []

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)

        if any(part in UNWALKED or part.startswith(".") for part in rel.parts[:-1]):
            continue

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue

        lines += [f"{rel.as_posix()}:{line} {what}" for line, what in _defines(tree, "")]

    return "".join(f"{line}\n" for line in lines)


def _defines(node: ast.AST, prefix: str) -> Iterator[tuple[int, str]]:
    """What this node defines, depth first, methods qualified by their class.
    Assignments only at module level: a local is not a symbol anyone greps."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            kind = "class" if isinstance(child, ast.ClassDef) else "def"
            name = f"{prefix}{child.name}"

            yield child.lineno, f"{kind} {name}"
            yield from _defines(child, f"{name}.")
        elif not prefix and isinstance(child, ast.Assign | ast.AnnAssign):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]

            for target in targets:
                if isinstance(target, ast.Name):
                    yield child.lineno, target.id


# ....................... #

# The size, in characters, under which the scope arrives as contents rather
# than as an outline (S-0076/D-2). A calibration knob rather than a truth:
# roughly a module and its test, past which the bodies cost more context than
# the reads they save. Deliberately not a key in the runner's configuration —
# every section of that file loads under a model that forbids an unknown key,
# and the model is outside this scope; the divergence log carries the reason.
SCOPE_BUDGET = 60_000

# The fence hint per suffix, for the ones this repository's scopes name. An
# unlisted suffix gets a bare fence, which renders the same minus colour.
LANGS = {
    ".py": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".toml": "toml",
    ".sh": "bash",
}


def scope_file(root: Path, task: Task, tests: dict[str, Any], budget: int = SCOPE_BUDGET) -> str:
    """The files `scope.allow` names and the tests `tests.json` names, as one
    document (S-0076/D-2): their contents when they fit under *budget*, an
    outline of the same files when they do not. Either way the sixteen reads a
    small attempt spends opening what the contract already named become one.

    Empty when the scope and the named tests reach no readable file, so a pack
    with nothing to say adds no file and no index line."""

    bodies: list[tuple[str, str]] = []

    for rel in _in_scope(root, task, tests):
        try:
            bodies.append((rel, (root / rel).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue

    if not bodies:
        return ""

    total = sum(len(text) for _, text in bodies)
    whole = total <= budget
    lines = [
        "# The files this scope names",
        "",
        f"The scope's own files and the tests that name them — {len(bodies)} files,"
        f" {total} characters,"
        + (
            f" under the {budget} the pack carries whole. Nothing here needs opening again."
            if whole
            else f" over the {budget} the pack carries whole, so what follows is an outline:"
            " what each file defines and where. Open a file for a body."
        ),
        "",
    ]

    for rel, text in bodies:
        lines += [f"## `{rel}` — {len(text.splitlines())} lines", ""]

        if whole:
            fence = _fence(text)
            lines += [fence + LANGS.get(Path(rel).suffix, ""), text.rstrip("\n"), fence, ""]
        else:
            lines += [f"- `{rel}:{line} {what}`" for line, what in _outline(rel, text)] or [
                "No definitions to outline."
            ]
            lines += [""]

    return "\n".join(lines)


def _in_scope(root: Path, task: Task, tests: dict[str, Any]) -> list[str]:
    """The paths under `scope.allow` and not under `scope.deny`, plus the tests
    `tests_file` named for them. An empty allow list means unconstrained, which
    is every file in the tree — so it contributes nothing here and the named
    tests stand alone: a pack is not the place to inline a repository."""

    allow = GitIgnoreSpec.from_lines(task.scope.allow) if task.scope.allow else None
    deny = GitIgnoreSpec.from_lines(task.scope.deny) if task.scope.deny else None
    named = {str(row["test"]) for row in cast("list[dict[str, str]]", tests["named_tests"])}
    found: set[str] = set()

    for parent, dirs, names in root.walk():
        dirs[:] = [name for name in dirs if name not in UNWALKED]

        for name in names:
            rel = (parent / name).relative_to(root).as_posix()

            if rel in named or (
                allow is not None
                and allow.match_file(rel)
                and not (deny is not None and deny.match_file(rel))
            ):
                found.add(rel)

    return sorted(found)


def _outline(rel: str, text: str) -> list[tuple[int, str]]:
    """What a file defines, for the over-budget form — the same reading
    `symbols.txt` is built from, over the scope rather than the tree. A file
    `ast` cannot parse, and any file that is not Python, outlines to nothing."""

    if not rel.endswith(".py"):
        return []

    try:
        return list(_defines(ast.parse(text), ""))
    except (SyntaxError, ValueError):
        return []


def _fence(text: str) -> str:
    """A fence longer than the longest backtick run the file holds, so a body
    that is itself markdown cannot close the block it sits in."""

    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)

    return "`" * max(3, longest + 1)


# ....................... #


def build(
    root: Path, rfc_dir: Path, task: Task, manifest_path: Path, *, replay: bool = False
) -> dict[str, str]:
    """Every file of the pack, path (relative to the pack directory) ->
    contents. Pure in its inputs; the caller writes. A *replay* (a shadow
    run) omits the two files that read the stream as it stands now —
    attempts and contention — so the pack is what the live attempt could
    have read, and the replay measures the harness, not the calendar."""

    corpus = _load(rfc_dir) if rfc_dir.is_dir() else None
    files: dict[str, str] = {}

    def put(name: str, payload: dict[str, Any]) -> None:
        files[name] = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    put("decisions.json", decisions_file(task, corpus, rfc_dir))

    asked = source_file(root, task)

    if asked is not None:
        put("source.json", asked)

    put("gates.json", gates_file(root, task, manifest_path))

    tests = tests_file(root, task)
    put("tests.json", tests)

    scope = scope_file(root, task, tests)

    if scope:
        files["scope.md"] = scope

    if not replay:
        put("attempts.json", attempts_file(root, task))
        put("contended.json", contended_file(root))

    files["symbols.txt"] = symbols_file(root)

    for name, schema in schemas().items():
        put(f"schema/{name}.json", schema)

    files["index.md"] = render_index(files, task)

    return files


# ....................... #


def render_index(files: dict[str, str], task: Task) -> str:
    """What the pack holds that is still behind a read (S-0076/D-1).

    The small files — the source, the battery, the tests over the scope, this
    task's prior attempts and the contended paths — arrive in the attempt's
    first message, so the index no longer tells anyone to open them: a
    pointer at bytes already in context is a round trip spent re-reading
    them. `files` stays in the signature because the caller hands the whole
    pack over and a later file may want naming here."""

    return "\n".join(
        [
            f"# What the engine knows about {task.id}",
            "",
            "Written by the engine before this attempt, from the record and the tree,",
            "with no model. Nothing here outranks the contract.",
            "",
            "The pack's small files arrived with the task itself, in the first message:",
            "what asked for this work, the battery this attempt faces, the coverage and",
            "tests over the scope, this task's prior attempts and what convicted them, and",
            "the paths other work is contending for. There is nothing to open for those.",
            "",
            "What is behind a read, because it is large or seldom wanted:",
            "",
            "- `decisions.json` — the contract's rows with consequence, check, rationale and the",
            "  amendments that changed each; plus accepted rows from other documents over this scope",
            "- `schema/*.json` — the shapes the engine parses: a task, a document, a finding, a draft",
            "- `symbols.txt` — every class, function, method and module constant the tree defines,",
            "  one per line as `path:line name`. Grep it for a name rather than searching the tree;",
            "  it is a file to grep, not a file to read.",
            *(
                [
                    "- `scope.md` — the files this scope names and the tests that name them,",
                    "  whole when they fit and as an outline when they do not. One read instead",
                    "  of one per file; it says at the top which form it took.",
                ]
                if "scope.md" in files
                else []
            ),
            "",
        ]
    )


# ....................... #


def materialize(worktree: Path, files: dict[str, str]) -> Path:
    """Write the pack into the worktree, replacing any previous one."""

    target = worktree / PACK_DIR

    if target.is_dir():
        for path in sorted(target.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()

    for name, contents in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    # The pack ignores itself: derived state must never reach a diff, a
    # scope verdict or a landing, in this repository or an adopter's whose
    # ignore file never heard of it (S-0054/D-10).
    target.mkdir(parents=True, exist_ok=True)
    (target / ".gitignore").write_text("*\n", encoding="utf-8")

    return target
