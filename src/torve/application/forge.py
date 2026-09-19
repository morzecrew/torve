"""Pull-request composition (S-0010/pull-request-composition, S-0010/D-6): the body is built from
data — the contract, the gate outcomes, the inherited decisions, the
execution log's divergences, cost and trace — never from the agent's prose.
A self-report is not evidence; the pull request reads as a claim with proof
attached, checkable without opening a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from torve.config import layout
from torve.config.spec import SpecError, document_dir, load_document
from torve.domain.attempt import GateResult
from torve.domain.task import InheritedDecision, Task

# ----------------------- #

# The log kinds a reviewer must see before the diff: work that diverged
# from its contract, not work that went to plan.
DIVERGENT_KINDS = ("contradicted", "departed", "blocked")


# ....................... #


def _divergences(worktree: Path, task_id: str) -> list[str]:
    log_path = layout.log_file(worktree, task_id)

    if not log_path.is_file():
        return []

    try:
        loaded: object = yaml.safe_load(log_path.read_text(encoding="utf-8"))

    except yaml.YAMLError:
        return ["execution log unparseable — read it before merging"]

    if not isinstance(loaded, dict):
        return []

    entries = cast("dict[str, Any]", loaded).get("entries")

    if not isinstance(entries, list):
        return []

    found: list[str] = []

    for raw in cast("list[object]", entries):
        if not isinstance(raw, dict):
            continue

        entry = cast("dict[str, Any]", raw)

        if str(entry.get("kind")) in DIVERGENT_KINDS:
            found.append(
                f"{entry.get('decision', '?')} {entry.get('kind')}: "
                f"{str(entry.get('claim', '')).strip()}"
            )

    return found


# ....................... #


def _decision_table(decisions: list[InheritedDecision]) -> list[str]:
    """The rows a contract carried, as a table: a reviewer scans a grade
    column; a bullet per row hides it inside the prose."""

    def cell(text: str) -> str:
        return " ".join(text.split()).replace("|", "\\|")

    return [
        "| Decision | Grade | Text |",
        "| --- | --- | --- |",
        *(f"| {d.id} | `{d.grade}` | {cell(d.text)} |" for d in decisions),
    ]


def compose_pr(
    task: Task,
    attempts: int,
    digest: str,
    meta: dict[str, Any],
    results: list[GateResult],
    worktree: Path,
    changed: list[str] | None = None,
    landing: str = "local",
) -> tuple[str, str]:
    """(title, body), composed entirely from records — the task, the document
    it was minted from, the rows it carried with their grades, the gates'
    verdicts and the divergence entries (S-0080/D-4). The agent's output
    appears nowhere: if it had something to say beyond code, it belongs in
    an execution-log entry with evidence. The body leads with what a
    reader decides from — the contract, what changed, whether the gates held,
    where the control surface is. The contract is the paragraph a reviewer
    reads first, so it is in the open and not behind a details block."""

    summary = task.intent.strip().splitlines()[0] if task.intent.strip() else "task"

    if len(summary) > 72:  # a folded intent is one long line; titles are not
        summary = summary[:71].rstrip() + "…"

    title = f"{task.id}: {summary}"

    document = f" · {task.spec}" if task.spec else ""

    lines = [
        f"**{task.id}{document} · attempt {attempts} · config `{digest}`**",
        "",
    ]

    if landing == "local":
        # The sentence is true of a reading surface and false of a pull
        # request that is itself the landing route (S-0080/D-5).
        lines += [
            (
                "Reading surface: this pull request lands by fast-forward and "
                "the merge button is never used. Approval and revision live on "
                "the task's issue — `/torve approve` · `/torve revise`."
            ),
            "",
        ]

    if attempts > 1:
        lines += [
            (
                f"Attempt {attempts} supersedes the previous candidate on "
                "this branch; its review threads were captured into the "
                "revision record."
            ),
            "",
        ]

    if task.intent.strip():
        lines += ["## Contract", "", task.intent.strip(), ""]

        if task.acceptance:
            lines += ["**Acceptance**", *(f"- `{command}`" for command in task.acceptance), ""]

    if changed:
        lines += ["## Changed", *(f"- `{path}`" for path in changed), ""]

    if results:
        red = [r for r in results if r.outcome not in ("pass", "bypassed")]

        if red:
            lines += [
                "## Gates",
                *(f"- {r.name}: {r.outcome} ({r.duration_s:.1f}s)" for r in results),
                "",
            ]
        else:
            slowest = max(results, key=lambda r: r.duration_s)

            lines += [
                (
                    f"**Gates** — all {len(results)} pass "
                    f"(slowest: {slowest.name} {slowest.duration_s:.1f}s)."
                ),
                "",
            ]

    divergences = _divergences(worktree, task.id)

    if divergences:
        lines += ["## Divergences", *(f"- {d}" for d in divergences), ""]

    if task.decisions:
        lines += ["## Inherited decisions", "", *_decision_table(task.decisions), ""]

    cost = meta.get("cost_usd")
    trace = meta.get("trace_ref")
    model = meta.get("model")
    agent = f"{meta.get('adapter')}/{model}" if model else str(meta.get("adapter"))
    footer = [f"agent: {agent}"]

    if cost is not None:
        footer.append(f"cost: ${cost:.4f}")

    if trace:
        # A host-absolute path says nothing on the forge — its basename
        # names the artefact; a URI reference stays whole.
        trace_text = str(trace)

        if "://" not in trace_text:
            trace_text = Path(trace_text).name

        footer.append(f"trace: {trace_text}")

    lines.append(" · ".join(footer))

    return title, "\n".join(lines)


# ....................... #
# The document composer (S-0083/D-8): one pull request for a document branch,
# composed from the landings the branch carries so far. Takes no configuration
# and no lane — the records it reads are the tasks, their rows, their gates and
# their logs, plus the document's own phasing.


@dataclass(frozen=True)
class DocumentLanding:
    """One phase as the document branch carries it: the contract that landed,
    the sha the landing produced and the battery that judged it. There is no
    field an agent's prose can occupy."""

    task: Task
    sha: str = ""
    results: list[GateResult] = field(default_factory=list)


def _phasing(root: Path, document: str) -> tuple[str, list[tuple[int, str]]]:
    """The document's title and its phasing as (phase, title), read from the
    corpus (S-0083/D-17): the phases still to come are named from the record
    the document itself carries, never counted from an estimate. A document
    that is absent or does not load names none."""

    directory = document_dir(root / layout.SPECS_DIR, document)

    if directory is None:
        return "", []

    try:
        doc = load_document(directory)

    except SpecError:
        return "", []

    return doc.title, [(phase.phase, phase.title) for phase in doc.phasing]


def _gates_line(results: list[GateResult]) -> str:
    red = [r for r in results if r.outcome not in ("pass", "bypassed")]

    if red:
        return "gates: " + ", ".join(f"{r.name} {r.outcome}" for r in red)

    return f"gates: all {len(results)} pass"


def document_complete(document: str, landings: list[DocumentLanding], root: Path) -> bool:
    """Whether every phase the document's phasing names has a landing on the
    branch — the pull request's draft flag is this, read from the same
    records the body is composed from. A document with no readable phasing
    is complete when anything landed: nothing says otherwise."""
    _, phasing = _phasing(root, document)
    numbers = {number for number, _ in phasing}
    landed = {landing.task.phase for landing in landings if landing.task.phase}
    return not (numbers - landed)


def compose_document_pr(
    document: str,
    landings: list[DocumentLanding],
    root: Path,
) -> tuple[str, str]:
    """(title, body) for a document branch's one pull request, composed
    entirely from records: every task the branch carries, the rows each
    contract carried with their grades, the gates' verdicts and the
    divergence entries, and the phases the document has still to come
    (S-0083/D-8). The contract's intent is the author's paragraph and is in
    the open; nothing an agent wrote as prose reaches it."""

    doc_title, phasing = _phasing(root, document)
    landed = {landing.task.phase for landing in landings if landing.task.phase}
    # A phase may hold several entries, so the count is over phase numbers —
    # what a reviewer counts merges of, not contracts.
    numbers = {number for number, _ in phasing}
    to_come = sorted(numbers - landed)
    remaining = [(number, title) for number, title in phasing if number in set(to_come)]

    subject = doc_title or f"{len(landings)} landed"

    if numbers:
        subject = f"{subject} · {len(numbers) - len(to_come)}/{len(numbers)} phases"

    title = f"{document}: {subject}"

    if len(title) > 72:
        title = title[:71].rstrip() + "…"

    lines = [
        f"**{document} · {len(landings)} landing(s) on this branch**",
        "",
    ]

    if to_come:
        lines += [
            (
                f"Part of a design: {len(to_come)} of this document's "
                f"{len(numbers)} phases are still to come, so what is below is "
                "not the whole of it."
            ),
            "",
        ]
    elif numbers:
        lines += [f"Every phase of this document is on this branch ({len(numbers)}).", ""]

    # The rows once, for the document: every phase inherits the same table
    # from the same document, and a body that repeated it per task made a
    # three-phase pull request three tables long (bloomery #155).
    rows: dict[str, InheritedDecision] = {}

    for landing in landings:
        for decision in landing.task.decisions:
            rows.setdefault(decision.id, decision)

    if rows:
        lines += ["## Decisions", "", *_decision_table(list(rows.values())), ""]

    for landing in landings:
        task = landing.task
        phase = f" · phase {task.phase}" if task.phase else ""
        sha = f" · `{landing.sha[:12]}`" if landing.sha else ""

        lines += [f"## {task.id}{phase}{sha}", ""]

        if task.title.strip():
            lines += [f"**{task.title.strip()}**", ""]

        if task.intent.strip():
            lines += [task.intent.strip(), ""]

        if landing.results:
            lines.append(f"- {_gates_line(landing.results)}")

        for divergence in _divergences(root, task.id):
            lines.append(f"- divergence: {divergence}")

        lines.append("")

    if remaining:
        lines += [
            "## Still to come",
            *(f"- phase {number} — {title}" for number, title in remaining),
            "",
        ]

    lines.append("Composed from the landing records; no agent's prose reaches this body.")

    return title, "\n".join(lines)
