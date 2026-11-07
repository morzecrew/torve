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
from pydantic import TypeAdapter, ValidationError

from torve.config import layout
from torve.config.spec import SpecError, document_dir, load_document
from torve.domain.attempt import GateResult
from torve.domain.spec import FILE_FIELDS, PHASING_FILE, Commit, Corpus, Document, Phase
from torve.domain.task import InheritedDecision, Task

# ----------------------- #

# The log kinds a reviewer must see before the diff: work that diverged
# from its contract, not work that went to plan.
DIVERGENT_KINDS = ("contradicted", "departed", "blocked")

# S-0087/D-2: one gitmoji per Conventional Commits type, as the operator's
# mapping reads it; a breaking change wears 💥 in place of its type's own.
GITMOJI = {
    "feat": "✨",
    "fix": "🐛",
    "refactor": "♻️",
    "perf": "⚡️",
    "docs": "📝",
    "test": "✅",
    "build": "📦️",
    "ci": "👷",
    "chore": "🔧",
}

# A pull request title is a commit subject: one line, bounded.
TITLE_LIMIT = 72

#: GitHub refuses a pull request body past this ("Body is too long (maximum
#: is 65536 characters)"), and a document that has carried a dozen landings
#: composes past it (bloomery S-0002: eleven tasks' records, refused three
#: passes running). The body is bounded here with a pointer to the records it
#: was composed from, which the branch carries in full.
BODY_LIMIT = 65536


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


def _description(title: str) -> str:
    """A title as a conventional description (S-0087/D-2): the tail of a
    sentence, so its first letter is lowered — unless it opens on an
    identifier, an acronym or a backticked name, which carries its own case.
    A one-letter word is an article, not an acronym (bloomery #233: "A
    declared key is checked" kept its capital)."""

    head = title.split(" ", 1)[0]

    if not title or not title[0].isalpha() or not head.isalpha():
        return title

    if len(head) > 1 and head.upper() == head:
        return title

    return title[0].lower() + title[1:]


def _typed_lead(change: Commit) -> tuple[str, int]:
    """`<gitmoji> <type>(<scope>)[!]: ` and the room it leaves the description
    within the limit — the one sum the title and `torve spec check` both read
    (#184)."""

    scope = f"({change.scope})" if change.scope else ""
    gitmoji = "💥" if change.breaking else GITMOJI[change.type]
    lead = f"{gitmoji} {change.type}{scope}{'!' if change.breaking else ''}: "

    return lead, TITLE_LIMIT - len(lead)


def _typed_title(change: Commit, description: str, suffix: str = "") -> str:
    """`<gitmoji> <type>(<scope>)[!]: <description>` with the suffix the
    caller owns (S-0087/D-2). An overflowing title is cut at the
    description: the type a release reads is never the part that goes. The
    suffix gives way first, so a description that fits the room is never cut
    to make room for a phase count (#184)."""

    lead, room = _typed_lead(change)
    text = _description(description)

    if len(text) + len(suffix) > room:
        suffix = ""

    if len(text) > room:
        text = text[: max(room - 1, 0)].rstrip() + "…"

    return f"{lead}{text}{suffix}"


def title_warnings(corpus: Corpus) -> list[str]:
    """A document still to land whose title, as the typed description, is
    longer than the room its `change` leaves (#184): the pull request's title,
    and so the squash subject, would end in `…`. `torve spec check` says so
    while the author can still shorten it."""

    warnings: list[str] = []

    for doc in corpus.documents:
        landed = doc.archived or doc.status == "superseded" or doc.implementation == "complete"

        if doc.change is None or landed:
            continue

        lead, room = _typed_lead(doc.change)
        length = len(_description(doc.title))

        if length > room:
            warnings.append(
                f"{Path(doc.path).name if doc.path else doc.id}: the title runs {length} "
                f"characters and `{lead.strip()}` leaves it {room} — the pull request's "
                "title and its squash subject would end in …"
            )

    return warnings


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

    # S-0087/D-3: one rule for both units — the task wears its document's
    # change with the phase's title as the description; a task naming no
    # document, or one the corpus cannot answer for, is titled as today.
    change = _phasing(worktree, task.spec)[2] if task.spec else None

    title = (
        _typed_title(change, task.title.strip() or summary)
        if change is not None
        else f"{task.id}: {summary}"
    )

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


class PhasingUnreadable(ValueError):
    """A phasing that is present but cannot be parsed or validated
    (S-0124/D-10): the document it belongs to is not complete."""


def _boolean_phase(entry: object) -> bool:
    """Whether *entry* names its phase with a bool, which is no phase number:
    coerced it would read as phase 1 and clear the draft flag."""

    return isinstance(entry, dict) and isinstance(cast("dict[str, Any]", entry).get("phase"), bool)


def phase_numbers(raw: object) -> list[int]:
    """The phases a parsed `phasing.yaml` names, read the way `load_document`
    reads the file (S-0124/D-10): no file names no phases, a key outside the
    fields `phasing.yaml` carries is refused, and every present field is
    validated against the `Document` model — so `phasing: null`, a bad `unit`
    or a malformed `after` is unreadable. Each phase entry is validated as the
    `Phase` model; an entry that fails makes the whole phasing unreadable,
    since dropping it would let the document read complete.

    A boolean `phase` is dropped before validation, as the intent licenses."""

    # An empty or comment-only file names no phases, as `load_document` reads it.
    if raw is None:
        return []

    if not isinstance(raw, dict):
        raise PhasingUnreadable("phasing: not a mapping")

    data = cast("dict[str, Any]", raw)
    carried = FILE_FIELDS[PHASING_FILE]
    # The boolean entry is dropped before the model sees it; everything else
    # is validated as the loader would validate the joined document.
    checked = dict(data)

    if isinstance(data.get("phasing"), list):
        checked["phasing"] = [
            entry for entry in cast("list[object]", data["phasing"]) if not _boolean_phase(entry)
        ]

    for key, value in checked.items():
        if key not in carried:
            raise PhasingUnreadable(f"phasing: {key} is not carried by {PHASING_FILE}")

        annotation = Document.model_fields[key].annotation

        # Every carried field is typed on the model; an untyped one would be
        # no field the loader could validate, so refuse it rather than guess.
        if annotation is None:
            raise PhasingUnreadable(f"phasing: {key} names no type")

        try:
            TypeAdapter(annotation).validate_python(value)

        except ValidationError as exc:
            raise PhasingUnreadable(
                f"phasing: {key} does not validate — {str(exc).splitlines()[0]}"
            ) from exc

    entries = checked.get("phasing")

    if entries is None:
        return []

    # The list validated above, its boolean entry already dropped.
    return [Phase.model_validate(entry).phase for entry in cast("list[object]", entries)]


def root_phases(root: Path, document: str) -> list[int] | None:
    """The phases the root's copy of *document* names, under the same rule
    `lane.document_phases` reads a ref by (S-0124/D-10): None when the
    document or its phasing is absent, and `PhasingUnreadable` when the file
    is there but cannot be parsed or validated. The caller still counts the
    phases a branch was cut before."""

    directory = document_dir(root / layout.SPECS_DIR, document)

    if directory is None:
        return None

    path = directory / PHASING_FILE

    if not path.is_file():
        return None

    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))

    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PhasingUnreadable(f"{document}/{PHASING_FILE}: {exc}") from exc

    return phase_numbers(raw)


def _phasing(
    root: Path, document: str
) -> tuple[str, list[tuple[int, str]], Commit | None, list[int | str]]:
    """The document's title, its phasing as (phase, title), what its
    landing is to a release and the issues it fixes, read from the corpus in
    one load (S-0083/D-17, S-0087/D-4): the phases still to come are named
    from the record the document itself carries, never counted from an
    estimate. A document that is absent or does not load names none and is
    typed as today."""

    directory = document_dir(root / layout.SPECS_DIR, document)

    if directory is None:
        return "", [], None, []

    try:
        doc = load_document(directory)

    except SpecError:
        return "", [], None, []

    phasing = [(phase.phase, phase.title) for phase in doc.phasing]

    return doc.title, phasing, doc.change, doc.fixes


def _gates_line(results: list[GateResult]) -> str:
    red = [r for r in results if r.outcome not in ("pass", "bypassed")]

    if red:
        return "gates: " + ", ".join(f"{r.name} {r.outcome}" for r in red)

    return f"gates: all {len(results)} pass"


def document_complete(
    document: str,
    landings: list[DocumentLanding],
    root: Path,
    phases: list[int] | None = None,
) -> bool:
    """Whether every phase the document's phasing names has a landing on the
    branch — the pull request's draft flag is this, read from the same
    records the body is composed from.

    *phases* are the phase numbers to check (S-0124/D-10): the union of the
    root's phasing and the phasing at the ref being landed, as the lane reads
    them. When they are not given, the root's phasing is read under the same
    rule as `lane.document_phases`: absent names none, and one that is
    present but cannot be read or validated leaves the document incomplete."""

    if phases is None:
        try:
            phases = root_phases(root, document)

        except PhasingUnreadable:
            return False

    numbers = set(phases or [])
    landed = {number for landing in landings for number in landing.task.covered}
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

    doc_title, phasing, change, fixes = _phasing(root, document)
    landed = {number for landing in landings for number in landing.task.covered}
    # A phase may hold several entries, so the count is over phase numbers —
    # what a reviewer counts merges of, not contracts.
    numbers = {number for number, _ in phasing}
    to_come = sorted(numbers - landed)
    remaining = [(number, title) for number, title in phasing if number in set(to_come)]

    count = f" · {len(numbers) - len(to_come)}/{len(numbers)} phases" if numbers else ""

    if change is not None:
        # S-0087/D-2: the count rides only while phases are still to come, so
        # the title the last landing leaves is the subject the squash merge
        # takes, in the repository's own format and with nothing to edit.
        title = _typed_title(change, doc_title, count if to_come else "")
    else:
        title = f"{document}: {doc_title or f'{len(landings)} landed'}{count}"

        if len(title) > TITLE_LIMIT:
            title = title[: TITLE_LIMIT - 1].rstrip() + "…"

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
    # The issues the document fixes, closed by the merge: a number is this
    # repository's, `owner/repo#N` another's.
    closes = [f"Closes #{ref}" if isinstance(ref, int) else f"Closes {ref}" for ref in fixes]
    tail = "\n\n" + "\n".join(closes) if closes else ""

    return title, _bounded(document, "\n".join(lines), tail)


def _bounded(document: str, body: str, tail: str) -> str:
    """The body under :data:`BODY_LIMIT`, cut on a line and saying so; *tail*
    follows whole, so the `Closes` lines are never what the cut drops."""

    if len(body) + len(tail) <= BODY_LIMIT:
        return body + tail

    note = (
        f"\n\n… cut here: the composed body passed the forge's {BODY_LIMIT}-character "
        f"limit. The whole record is on the branch, under "
        f"`.torve/specs/{document}/execution/`."
    )
    kept = body[: BODY_LIMIT - len(note) - len(tail)]
    return kept[: kept.rfind("\n")] + note + tail
