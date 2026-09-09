"""The context pack (RFC 0054 §5.6, D-54.10, D-54.11): `.torve/context/`
in the worktree, written host-side before the prompt, with the facts the
corpus cannot carry and the tree cannot grep — this task's rows with
their consequences and the amendments that changed them, the rows other
documents hold over the same paths, the battery and its axes, the tests
covering the scope, this task's own prior attempts and what convicted
them (D-54.12), the paths other work is contending for, and the schemas
the engine parses agent output with.

Every builder is a pure function of (record snapshot, tree, contract):
computed with no model, deterministic for a base sha and record state,
gitignored, never in an image, byte-identical for a shadow run. The pack
never carries another task's escalations, findings or attempts, and no
model output from a previous attempt beyond the divergence entries it
recorded (RFC 0007 §5, D-31, D-17.7).

`index.md` lists the files with one line each so an agent opens what it
needs: identifiers up front, bodies on demand.
"""

from __future__ import annotations

import json
import re
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

# ----------------------- #

PACK_DIR = Path(".torve") / "context"
OUTPUT_TAIL = 2000  # characters of a red gate's output the retry sees
FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.M)
GOVERNING = re.compile(r"\b(D-[A-Za-z0-9]+(?:\.\d+[a-z]?)?)\b")


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
    the standing set the document lane never saw (D-30.6, closed here)."""

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
                if c.subject == decision.id
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
    (D-54.12), read from the rows the runner already wrote."""

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
                "governing_decisions": sorted(set(GOVERNING.findall(output)))
                if result.get("name") in ("decisions-reported", "scope")
                else [],
                "failed_tests": sorted(set(FAILED_TEST.findall(output))),
            }
        )

    return reds


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
    output with (D-54.15) and the models a row and a document take."""

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
    put("gates.json", gates_file(root, task, manifest_path))
    put("tests.json", tests_file(root, task))

    if not replay:
        put("attempts.json", attempts_file(root, task))
        put("contended.json", contended_file(root))

    for name, schema in schemas().items():
        put(f"schema/{name}.json", schema)

    files["index.md"] = render_index(files, task)

    return files


# ....................... #


def render_index(files: dict[str, str], task: Task) -> str:
    lines = [
        f"# What the engine knows about {task.id}",
        "",
        "Written by the engine before this attempt, from the record and the tree,",
        "with no model. Nothing here outranks the contract. Open what you need:",
        "",
        "- `decisions.json` — the contract's rows with consequence, check, rationale and the",
        "  amendments that changed each; plus accepted rows from other documents over this scope",
        "- `gates.json` — the battery this attempt faces: name, axis, state, what convicts",
        "- `tests.json` — coverage of the files in scope from the last battery, and the tests that name them",
        "- `attempts.json` — this task's prior attempts, and each red gate's output, governing rows and failed tests",
        "- `contended.json` — paths other work is contending for right now",
        "- `schema/*.json` — the shapes the engine parses: a task, a document, a finding, a draft",
        "",
    ]

    if "attempts.json" in files:
        payload = cast("dict[str, Any]", json.loads(files["attempts.json"]))
        prior = cast("list[dict[str, Any]]", payload.get("attempts") or [])
        reds = cast("list[dict[str, Any]]", payload.get("last_red_gates") or [])

        if prior:
            lines.append(f"Prior attempts on this task: {len(prior)}.")

        if reds:
            names = ", ".join(str(r.get("gate")) for r in reds)
            lines.append(f"The last red pass convicted on: {names} — read `attempts.json` first.")

        if prior or reds:
            lines.append("")

    return "\n".join(lines)


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
    # ignore file never heard of it (D-54.10).
    target.mkdir(parents=True, exist_ok=True)
    (target / ".gitignore").write_text("*\n", encoding="utf-8")

    return target
