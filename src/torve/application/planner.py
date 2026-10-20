"""`torve plan` — the deterministic minter (S-0007/torve-plan). One accepted,
committed specification in; implement-task contracts out. No model call at
any point, for any reason (S-0007/D-1): the planner is a projection of decisions
someone already made, and the absence of that capability — not policy — is
what keeps it from growing into an autonomous orchestrator (§2).

Admission (§3.1) refuses by name with a configuration error: a draft has no
settled decisions to inherit, an unsettled dependency breaks the
copy-grade-at-write-time guarantee, a superseded document's decisions no
longer stand, and a cycle means the readiness order is fiction. Exactly one
document per invocation (§3.2, S-0007/D-8) — batch planning inherits from
documents still being amended, which is the drift this system removes.

The minted contract copies the document's decision table verbatim — grade
and declared paths at write time — and takes intent, scope and acceptance
from the Phasing entry. Dry-run is the default (D-11's convention): minting
writes `.torve/tasks/T-nnnn/contract.yaml`, ids derived max+1 and never
reused, the same discipline as RFC numbering (S-0016/D-24 by analogy).

Beside `inherit_decisions` sits the document-less lane's mechanism,
`standing_decisions` (S-0030): rows of every accepted document whose
declared paths intersect a contract's scope, copied at write time the same
way. `torve plan` itself is unchanged — a document's own table is inherited
whole (S-0007/D-22); standing inheritance is what adoption and the contract lint
read.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from torve.application import neighbours, sizing
from torve.base.globs import overlap
from torve.config import layout, spec
from torve.config.manifest import read_layout
from torve.domain.attempt import SizeVerdict
from torve.domain.spec import ChangeType, Corpus, Document, Phase, document_id, number_of
from torve.domain.task import InheritedDecision, Scope, Task
from torve.domain.vocabulary import GRADES

if TYPE_CHECKING:
    from collections.abc import Iterable

    from torve.application.eventlog import EventLog
    from torve.application.manager import Board
    from torve.config.runconfig import VerifyConfig

# ----------------------- #

TASK_DIR_NAME = re.compile(r"^T-(\d{4,})$")


# ....................... #


class PlanError(ValueError):
    """A refusal at admission or minting — a configuration error (exit 3),
    naming the offending document, edge or entry."""


# ....................... #


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


# ....................... #


def _require_committed(root: Path, doc: Path) -> None:
    """Only a committed, reviewed document is admissible (S-0007/D-2): the commit
    is the human signature in the loop, and planning uncommitted text plans
    something nobody reviewed."""

    rel = doc.resolve().relative_to(root.resolve())
    tracked = _git(root, "ls-files", "--error-unmatch", str(rel))

    if tracked.returncode != 0:
        raise PlanError(f"{doc.name} is not tracked by git — commit the reviewed document first")

    dirty = _git(root, "status", "--porcelain", "--", str(rel))

    if dirty.returncode != 0:
        raise PlanError(f"cannot verify {doc.name} against git: {dirty.stderr.strip()}")

    if dirty.stdout.strip():
        raise PlanError(
            f"{doc.name} has uncommitted changes — `torve plan` accepts only the "
            "committed, reviewed text (S-0007/D-2)"
        )


# ....................... #


def _admit(corpus: Corpus, number: str) -> None:
    doc = corpus.document(number)

    if doc is None or doc.archived:
        raise PlanError(f"{number} is not in the corpus path")

    if doc.status != "accepted":
        raise PlanError(
            f"{number} is {doc.status} — a {doc.status} document "
            "has no settled decisions to inherit (§3.1)"
        )

    if doc.superseded_by:
        raise PlanError(
            f"{number} is superseded by {doc.superseded_by} — its decisions no longer stand"
        )

    for dep in doc.depends_on:
        target = corpus.document(dep)

        if target is None:
            raise PlanError(f"{number} depends on {dep}, which does not exist")

        if target.status != "accepted":
            raise PlanError(
                f"{number} depends on {dep}, which is {target.status} — "
                "inheriting a grade from an unsettled document breaks the "
                "copy-at-write-time guarantee (S-0007/D-7)"
            )

    # A cycle reachable from this document (§3.1) — DFS over depends_on.
    state: dict[str, int] = {}

    def visit(num: str, trail: list[str]) -> None:
        state[num] = 1

        for dep in _depends(corpus, num):
            if state.get(dep) == 1:
                cycle = " -> ".join([*trail, num, dep])
                raise PlanError(f"depends_on cycle reachable from {number}: {cycle}")

            if state.get(dep) != 2 and corpus.document(dep) is not None:
                visit(dep, [*trail, num])

        state[num] = 2

    visit(number, [])


# ....................... #


def _depends(corpus: Corpus, number: str) -> list[str]:
    doc = corpus.document(number)

    return list(doc.depends_on) if doc is not None else []


# ....................... #


@dataclass(frozen=True)
class PlannedTask:
    task: Task
    title: str
    size: SizeVerdict


# ....................... #


@dataclass(frozen=True)
class PlanReport:
    number: str
    document: str  # the identifier the contract's `spec` names (S-0059/D-1)
    tasks: list[PlannedTask]


# ....................... #


def scopes_clash(left: list[str], right: list[str]) -> bool:
    """Whether two tasks may not run at the same time (S-0019/A-6, S-0019/D-14).

    An empty allow-set is unconstrained (S-0002/scope-in-detail), and a task that may
    touch anything can prove itself disjoint from nothing — so it clashes
    with every other task, including another unconstrained one. Everything
    else is `globs.overlap`'s exact reading (S-0105/D-2).

    One rule with two callers: the standing loop asks it of the run-state
    files it can see, the manager asks it of the board it folds, and the
    two answering differently is how two agents end up editing one file.
    """

    if not left or not right:
        return True

    return overlap(left, right)


# ....................... #


def next_task_number(root: Path, taken: Iterable[str] = ()) -> int:
    """Max over the task directories and *taken* (the board's ids, when a
    store holds the tasks — S-0056/D-9), plus one; never reused."""

    tasks_dir = root / layout.TORVE_DIR / "tasks"
    numbers = [0]

    for task_id in taken:
        found = TASK_DIR_NAME.match(task_id)

        if found:
            numbers.append(int(found.group(1)))

    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            found = TASK_DIR_NAME.match(entry.name)

            if found:
                numbers.append(int(found.group(1)))

    return max(numbers) + 1


# ....................... #


def document_of(reference: str) -> str:
    """The document a contract names, as `S-NNNN` — from `spec`, or from
    the path a contract minted before S-0059 carried, whatever its shape
    was — so a recorded mint is recognised across the conversions
    (S-0059/D-3); the empty string when the reference names none."""

    try:
        return document_id(reference)
    except ValueError:
        return ""


# ....................... #


def change_type_of(spec_dir: Path, task: Task | None) -> ChangeType | None:
    """The change type of the document a contract names (S-0106/D-6): the one
    fact `red-on-base` re-aims its verdict on, read here because a gate never
    parses a specification document (S-0015/A-1).

    None when the contract names no document, the corpus does not hold it, or
    it declares no change — the feature reading is the default. A corpus the
    loader refuses is `spec check`'s to report, never the battery's to die
    on, so every refusal is the same answer."""

    if task is None or not task.spec:
        return None

    directory = spec.document_dir(spec_dir, task.spec)

    if directory is None:
        return None

    try:
        document = spec.load_document(directory)

    except spec.SpecError:
        return None

    return document.change.type if document.change else None


# S-0116/D-4: the fixed preamble every verify task's intent opens with, before
# the phase's own intent — the verifier writes the tests from the rows and this
# intent alone, so the contract it reads says so before it reads the intent.
VERIFY_PREAMBLE = (
    "Write this phase's tests from its decisions and this intent alone, before "
    "any implementation exists. Give every row the phase settles at least one "
    "test whose docstring cites it, and import the code under test inside each "
    "test body so the file collects before the code exists.\n\n"
)


def _verify_terms(root: Path) -> VerifyConfig:
    """The verify pilot's terms (S-0116/D-2, D-4): off by default. A
    configuration the loader refuses reads as off here — `torve plan` has
    already refused it by name, and the planner is not the place that reports
    a broken configuration (the same reading `change_type_of` takes of a corpus
    `spec check` owns)."""

    from torve.config.runconfig import VerifyConfig, load_runner_config

    try:
        return load_runner_config(root).verify

    except ValueError:
        return VerifyConfig()


def _test_run(root: Path) -> str:
    """The manifest's `tests.run` launcher (S-0106/D-7) a verify task appends
    its file to; the engine default when the tree carries no manifest, or one
    the loader refuses."""

    from torve.config.manifest import DEFAULT_TEST_RUN, load_manifest

    path = layout.gates_file(root)

    if not path.is_file():
        return DEFAULT_TEST_RUN

    try:
        return load_manifest(path).tests.run

    except ValueError:
        return DEFAULT_TEST_RUN


def _verify_file(tests_dir: str, number: str, phase: int, k: int) -> str:
    """The one file a verifier may write (S-0116/D-4): `<tests_dir>/test_sNNNN_pN.py`,
    or `..._pN_<k>.py` for the k-th entry of several sharing phase N."""

    base = f"{tests_dir.rstrip('/')}/test_s{number_of(number)}_p{phase}"

    return f"{base}.py" if k == 1 else f"{base}_{k}.py"


def _minted_without_a_verifier(task: Task) -> Task:
    """An implement derivation with its verifier stripped (S-0116/D-5): the
    verifier's file is the whole of `deny` and its run the last of
    `acceptance` on the derivation a fresh mint writes, and both leave it.
    A refresh mints nothing, so a phase that holds no verify contract is
    diffed against the derivation it can actually be dispatched under."""

    if not task.scope.deny:
        return task

    return task.model_copy(
        update={
            "scope": task.scope.model_copy(update={"deny": []}),
            "acceptance": task.acceptance[:-1],
        }
    )


def _verifier_runs(task: Task) -> list[str]:
    """The acceptance lines that are the verifier's: those naming a file it
    denies. Every other line is the phase's own, and a refresh takes it from
    the derivation rather than the minted contract (S-0116/D-5)."""

    return [line for line in task.acceptance if any(file in line for file in task.scope.deny)]


def _verify_derivation(
    current: Task,
    implements: list[Task],
    implement_derived: dict[str, PlannedTask],
    deriveds: list[PlannedTask],
) -> PlannedTask | None:
    """The derivation a minted verify contract is refreshed against: the
    verify derivation of the entry whose implement contract depends on it
    (S-0116/D-5). None when no implement contract names it, or when that
    entry's derivation earns no verifier now."""

    owner = next((one for one in implements if current.id in one.depends_on), None)

    if owner is None or owner.id not in implement_derived:
        return None

    wanted = set(implement_derived[owner.id].task.depends_on)

    return next((one for one in deriveds if one.task.id in wanted), None)


def _minted_with_a_verifier(derived: Task, current: Task) -> Task:
    """An implement derivation wearing the verifier its minted contract carries
    (S-0116/D-5): the three wirings are fixed at mint, so a refresh whose
    derivation no longer earns a verifier — the pilot is off, or the document's
    change type left `verify.types` — still denies the verifier's file and runs
    it. The file and its run come from the contract, never re-derived."""

    # The verifier's run is the one acceptance line naming its file; every
    # other line is the phase's own, and the derivation supplies it, so a
    # command edited or dropped in the document leaves with it (S-0088/D-1).
    runs = _verifier_runs(current)
    kept = [line for line in derived.acceptance if line not in _verifier_runs(derived)]

    return derived.model_copy(
        update={
            "scope": derived.scope.model_copy(update={"deny": list(current.scope.deny)}),
            "acceptance": kept + runs,
        }
    )


def _already_minted(
    root: Path, document: str, phases: set[int] | None, board: Board | None = None
) -> list[str]:
    """Task ids whose contracts already cite this document and one of these
    phases — minting twice mints duplicate work, and what to do with the
    first batch is a human decision. The board's contracts count when a
    store holds the tasks (S-0056/D-9); the files count either way.

    *phases* None is every phase: what `after` names is a whole document, so
    the edge names every task minted from it (S-0085/D-2, S-0085/D-5)."""

    clashes: list[str] = []
    wanted = document_of(document)

    for view in board.tasks.values() if board is not None else []:
        contract = view.contract

        if contract is None or not contract.spec:
            continue

        if phases is not None and contract.phase not in phases:
            continue

        if document_of(contract.spec) == wanted:
            clashes.append(view.task_id)

    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir():
        return clashes

    for path in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))

        except yaml.YAMLError:
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)

        minted = document_of(str(record.get("spec") or ""))

        if minted == wanted and (phases is None or record.get("phase") in phases):
            clashes.append(str(record.get("id", path.parent.name)))

    return sorted(set(clashes))


# ....................... #


def inherit_decisions(doc: Document) -> list[InheritedDecision]:
    """The document's rows as a contract inherits them (§3.1): grade and
    paths copied at write time, so the executor sees what stood when the
    task was minted. One implementation — `torve plan` and adoption mint
    the same rows or the two drift (S-0007/A-3)."""

    name = Path(doc.path).name if doc.path else doc.id
    decisions: list[InheritedDecision] = []

    for row in doc.decisions:
        if row.grade not in GRADES:
            raise PlanError(
                f"{name}: decision {row.id} has grade {row.grade!r} — "
                "not mintable (run `torve spec check`)"
            )

        # S-0054/D-4: a row whose check would block must name the test that
        # proves the check can fail — the manifest's twin rule, one level up.
        if row.check_state == "blocking" and not row.check_twin:
            raise PlanError(
                f"{name}: decision {row.id} has a blocking check and no check_twin — "
                "not mintable (S-0054/D-4)"
            )

        decisions.append(
            InheritedDecision(
                id=row.id,
                grade=row.grade,
                text=row.text.strip(),
                paths=list(row.paths),
                consequence=row.consequence.strip(),
                check=row.check,
                check_state=row.check_state,
                check_twin=row.check_twin,
            )
        )

    return decisions


# ....................... #


def load_corpus(rfc_dir: Path) -> Corpus:
    """The corpus as the planner reads it: the loader's refusals as
    `PlanError`, so nothing mints from a document `spec check` refuses."""

    try:
        return spec.load_corpus(rfc_dir)
    except spec.SpecError as exc:
        raise PlanError("; ".join(exc.problems)) from exc


# ....................... #


def standing_decisions(rfc_dir: Path, scope_allow: list[str]) -> list[InheritedDecision]:
    """The document-less lane's inheritance (S-0030/standing-inheritance): every accepted
    document's rows are read through the same one reader `inherit_decisions`
    is (S-0007/A-3), and a row is inherited when any of its declared paths
    intersects `scope_allow` (`globs.overlap`, exact — a false
    inclusion costs a few contract lines, a false exclusion costs the
    silence check). Rows without declared paths are never standing — they
    govern their own document's work only (S-0030/D-1). Draft, superseded and
    archived documents are never read: their decisions do not stand.
    Deterministic: corpus order, then document order."""

    standing: list[InheritedDecision] = []

    for doc in load_corpus(rfc_dir).standing():
        if doc.superseded_by:
            continue

        for row in inherit_decisions(doc):
            if row.paths and overlap(row.paths, scope_allow):
                standing.append(row)

    return standing


# ....................... #


def plan_document(
    root: Path,
    rfc_dir: Path,
    identifier: str,
    *,
    board: Board | None = None,
    refresh: bool = False,
    phases: set[int] | None = None,
    committed: bool = False,
) -> PlanReport:
    """Admission plus minting, dry: nothing is written. Raises PlanError on
    any refusal (§3.1) — each names the offending document or entry. With
    a *board* (S-0056/D-9), task numbers and prior mints are read from the
    record as well as from the task directories.

    *refresh* derives for `--refresh` (S-0088/D-1): the same admission and the
    same derivation, without the two refusals that are about minting — a
    phase already minted is what a refresh is *for*, and the `after` edges
    are the minted contracts' own, which a refresh keeps.

    *committed* says the document was read from a commit rather than the
    checkout, so the checkout's status says nothing about it."""

    files = spec.document_dirs(rfc_dir)

    try:
        number = document_id(identifier)
    except ValueError:
        raise PlanError(f"{identifier!r} names no document") from None

    if number_of(number) not in files:
        raise PlanError(f"no document {number} under {rfc_dir}")

    doc_path = files[number_of(number)]

    if not committed:
        _require_committed(root, doc_path)

    corpus = load_corpus(rfc_dir)
    _admit(corpus, number)
    doc = corpus.document(number)
    assert doc is not None
    entries = doc.phasing

    if not entries:
        raise PlanError(
            f"{doc_path.name} has no phasing — a `phasing` list is what `torve plan` "
            "consumes (spec-writer rule 2)"
        )

    known = {e.phase for e in entries}

    for entry in entries:
        for dep in entry.depends_on:
            if dep not in known:
                raise PlanError(
                    f"{doc_path.name}: phase {entry.phase} depends_on {dep}, which no entry defines"
                )

    # Same-phase scopes must not intersect (§3): overlapping tasks cannot run
    # in parallel and the plan silently serialises.
    by_phase: dict[int, list[Phase]] = {}

    for entry in entries:
        by_phase.setdefault(entry.phase, []).append(entry)

    for phase, siblings in sorted(by_phase.items()):
        for i, one in enumerate(siblings):
            for other in siblings[i + 1 :]:
                if overlap(one.scope, other.scope):
                    raise PlanError(
                        f"phase {phase}: scopes of {one.title!r} and {other.title!r} "
                        "intersect — same-phase tasks must be disjoint (§3)"
                    )

    decisions = inherit_decisions(doc)

    document = doc.id

    # `--phase`: a phase an amendment added to a document whose earlier
    # phases are minted and landed (bloomery S-0002/A-4). Only the named
    # phases are minted; the others are what their `depends_on` edges point
    # at, through the contracts those phases already have.
    if phases is not None:
        known = {e.phase for e in entries}
        missing = sorted(phases - known)

        if missing:
            raise PlanError(
                f"{document}: phase(s) {', '.join(map(str, missing))} not in its phasing"
            )

        entries = [e for e in entries if e.phase in phases]

    clashes = [] if refresh else _already_minted(root, document, {e.phase for e in entries}, board)

    if clashes:
        raise PlanError(
            f"phase(s) already minted from {document}: {', '.join(clashes)} — "
            "what to do with the existing tasks is a human decision"
        )

    # S-0085/D-2: `after` is one list of document ids and becomes contract
    # `depends_on`, so nothing downstream learns a new word. A named document
    # whose implementation is complete has already landed and adds no edge;
    # one with no minted tasks names nothing an edge could point at.
    after_tasks: list[str] = []

    for reference in [] if refresh else doc.after:
        target = corpus.document(reference)

        if target is None:
            raise PlanError(f"{document}: after names {reference!r}, no such document")

        if target.implementation == "complete":
            continue

        minted = _already_minted(root, target.id, None, board)

        if not minted:
            raise PlanError(
                f"{document}: after names {target.id}, which has no minted tasks — "
                "plan it first, or an edge to it names nothing"
            )

        after_tasks += minted

    after_tasks = sorted(set(after_tasks))
    ordered = sorted(entries, key=lambda e: e.phase)  # stable: document order within a phase
    next_number = next_task_number(root, board.tasks if board is not None else ())

    # S-0116/D-4: with the pilot on, a behavioural entry mints a verifier ahead
    # of it — the one file it may write, and the collect-only run that proves it.
    pilot = _verify_terms(root)
    source_prefix = read_layout(root).source_prefix()
    test_run = _test_run(root)

    def earns_verifier(entry: Phase) -> bool:
        if not pilot.enabled or doc.change is None or doc.change.type not in pilot.types:
            return False

        return any(one.startswith(source_prefix) for one in entry.scope)

    seen: dict[int, int] = {}
    verify_files: dict[int, str] = {}

    for entry in ordered:
        if not earns_verifier(entry):
            continue

        seen[entry.phase] = seen.get(entry.phase, 0) + 1
        verify_files[id(entry)] = _verify_file(
            pilot.tests_dir, number, entry.phase, seen[entry.phase]
        )

    # The verify task precedes its implement task; ids run in phasing order.
    emissions: list[tuple[str, Phase, str | None]] = []

    for entry in ordered:
        file = verify_files.get(id(entry))

        if file is not None:
            emissions.append(("verify", entry, file))

        emissions.append(("implement", entry, None))

    ids_by_phase: dict[int, list[str]] = {}
    verify_id_of: dict[int, str] = {}
    emitted: list[tuple[str, str, Phase, str | None]] = []

    for offset, (role, entry, file) in enumerate(emissions):
        task_id = f"T-{next_number + offset:04d}"
        emitted.append((task_id, role, entry, file))

        if role == "verify":
            verify_id_of[id(entry)] = task_id
        else:
            ids_by_phase.setdefault(entry.phase, []).append(task_id)

    if phases is not None:
        for entry in ordered:
            for predecessor in entry.depends_on:
                if predecessor not in ids_by_phase:
                    ids_by_phase[predecessor] = _already_minted(
                        root, document, {predecessor}, board
                    )

    # S-0106/D-7: the manifest's `pair` templates drive the neighbour pairing.
    from torve.application.intake import test_pairs

    pairs = test_pairs(root)
    planned: list[PlannedTask] = []

    for task_id, role, entry, file in emitted:
        # A task with an in-document predecessor waits through it; one with
        # none is where the other document's landing has to be waited on.
        within = [tid for p in entry.depends_on for tid in ids_by_phase.get(p, [])]
        depends = within or list(after_tasks)

        if role == "verify":
            assert file is not None
            task = Task(
                id=task_id,
                spec=document,
                phase=entry.phase,
                role="verify",
                intent=VERIFY_PREAMBLE + entry.intent.strip(),
                depends_on=depends,
                scope=Scope(allow=[file]),
                acceptance=[f"{test_run} --collect-only {file}"],
                decisions=decisions,
                tier_variant=pilot.variant or None,
            )
        else:
            verifier = verify_files.get(id(entry))
            # S-0116/D-5: the implementer depends on the verifier, runs its
            # tests, and may never edit the file they live in.
            task = Task(
                id=task_id,
                spec=document,
                phase=entry.phase,
                role="implement",  # review tasks are minted by the runner at `gated` (§3)
                intent=entry.intent.strip(),
                depends_on=([verify_id_of[id(entry)]] if verifier else []) + depends,
                scope=Scope(
                    allow=list(entry.scope),
                    deny=[verifier] if verifier else [],
                    contingent=neighbours.contingent_paths(
                        root, entry.scope, document=doc, phase=entry.phase, pair=pairs
                    ),
                ),
                acceptance=list(entry.acceptance)
                + ([f"{test_run} {verifier}"] if verifier else []),
                decisions=decisions,
                tier_variant=entry.tier_variant or None,
                character=entry.character or None,
            )

        planned.append(
            PlannedTask(
                task=task,
                title=entry.title,
                size=sizing.estimate(task, read_layout(root).carried),
            )
        )

    return PlanReport(number=number, document=document, tasks=planned)


# ....................... #


class _ContractDumper(yaml.SafeDumper):
    """Multiline strings as literal blocks (S-0007/A-1): the default single-quoted
    style writes every newline as a blank-line escape, and a contract's
    intent read like a double-spaced telegram."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)  # pyright: ignore[reportUnknownMemberType]


_ContractDumper.add_representer(str, _str_representer)


def _dump_contract(document: dict[str, object]) -> str:
    return yaml.dump(
        document, Dumper=_ContractDumper, sort_keys=False, allow_unicode=True, width=88
    )


def write_contract(root: Path, task: Task, title: str = "", *, minted_by: str = "") -> Path:
    """One contract as a file under the task directory: the header names
    what wrote it, the body is the contract."""

    path = layout.task_file(root, task.id)

    if path.exists():
        raise PlanError(f"{path} already exists — task ids are never reused")

    path.parent.mkdir(parents=True, exist_ok=True)
    # S-0057/D-5: the first line names the schema `torve init` writes, two
    # levels up from the task directory in the default layout.
    header = f"{spec.SCHEMA_HEADER}../../schemas/contract.json\n"
    header += (
        f"# Minted by `torve plan {minted_by}` — phase {task.phase}: {title}\n"
        if minted_by
        else "# Projected from the record for this attempt (S-0056 S-0056/D-9): the board "
        "holds the task; this file is what the gates and the log verbs read.\n"
    )
    document = task.model_dump()
    document["title"] = document.get("title") or title.replace("-", " ")
    path.write_text(header + _dump_contract(document), encoding="utf-8")

    return path


def write_contracts(root: Path, report: PlanReport) -> list[Path]:
    """The file mode (S-0056/D-9): one directory per task under the root."""

    return [
        write_contract(root, planned.task, planned.title, minted_by=report.number)
        for planned in report.tasks
    ]


def project_contract(worktree: Path, task: Task) -> Path | None:
    """S-0056/D-9: the contract the board holds, written into the worktree for
    the attempt that reads it — the gates, `torve log owed`, the log
    beside it. A worktree that already carries the file (the file mode,
    where the contract is tracked) is left alone; None says so."""

    if layout.task_file(worktree, task.id).is_file():
        return None

    return write_contract(worktree, task, task.title or "")


async def mint_contracts(
    log: EventLog, report: PlanReport, *, partition: str, actor_id: str = "plan"
) -> list[str]:
    """S-0056/D-9: mint into the record and write no file — the same mint the
    manager's importer records, so a task planned here and a task scanned
    from a file are the same row on the board."""

    from torve.application.residency import mint

    return await mint(
        log,
        {planned.task.id: planned.task for planned in report.tasks},
        partition=partition,
        actor_id=actor_id,
    )


# ----------------------- #
# The refresh (S-0088/the-verb): an amendment reaching the contracts
# its document already minted, through the one path that knows how a
# document becomes a contract.

# What the document owns, and therefore what a refresh rewrites (S-0088/D-1):
# the derived `Task`'s fields minus identity (`id`, `spec`, `phase`, `role`,
# `title`) and edges (`depends_on`), which the mint decided and a refresh
# keeps.
REFRESHED_FIELDS = ("intent", "scope", "acceptance", "decisions", "tier_variant", "character")


@dataclass(frozen=True)
class RefreshedTask:
    """One already-minted phase as the refresh leaves it: the fields that
    differ from the derivation, the reason it was left alone if it was, and
    the rewritten contract when there is one to write."""

    task_id: str
    phase: int
    title: str
    changed: list[str]
    held: str = ""  # why it was left alone; empty means it may be rewritten
    task: Task | None = None  # the rewritten contract — None when held or unchanged


@dataclass(frozen=True)
class RefreshReport:
    document: str
    tasks: list[RefreshedTask]

    @property
    def rewritten(self) -> list[tuple[RefreshedTask, Task]]:
        """Each entry with a contract to write, paired with it."""

        return [(one, one.task) for one in self.tasks if one.task is not None]


# ....................... #


def minted_contracts(root: Path, document: str, board: Board | None = None) -> dict[str, Task]:
    """The contracts already minted from *document*, by task id — the board's
    when a store holds the tasks (S-0056/D-9), the files otherwise. An
    unreadable contract is skipped, as it is everywhere else: one malformed
    file is not a reason to refuse the rest."""

    from torve.gates.context import load_task

    wanted = document_of(document)
    found: dict[str, Task] = {}

    for view in board.tasks.values() if board is not None else []:
        contract = view.contract

        if contract is not None and document_of(contract.spec or "") == wanted:
            found[view.task_id] = contract

    for path in sorted((root / layout.TORVE_DIR / "tasks").glob("T-*/contract.yaml")):
        try:
            task = load_task(path)

        except ValueError:
            continue

        if document_of(task.spec or "") == wanted:
            found.setdefault(task.id, task)

    return found


# ....................... #


def _beyond_refresh(root: Path, rfc_dir: Path, document: str) -> dict[str, str]:
    """Task id -> why a refresh leaves it alone, for everything a landing
    proves (S-0088/D-2): a landing file under the document's `execution/` or the
    document-less one, a task the base already carries, or one the document
    branch carries whose landing the base does not yet hold (S-0101/D-7). A
    landed phase's terms are the ones its landing was judged by.

    The two git carriers — `landed()` and the document branch's landing files —
    are read rather than a stream row, so a refresh on any host leaves the same
    tasks alone, whatever host minted their lane rows."""

    from torve.application.lane import document_tasks
    from torve.application.projections import landed
    from torve.base import naming
    from torve.domain.spec import LANDING_FILE

    directory = spec.document_dir(rfc_dir, document)
    files = spec.landing_files_in(layout.execution_dir(root))
    files += spec.landing_files(directory) if directory is not None else []
    held = {found.group(1): "landed" for path in files if (found := LANDING_FILE.match(path.name))}

    branch = naming.document_branch(document)
    held.update(dict.fromkeys(landed(root), "landed"))
    # The branch's own landings override the base read: a task held on a
    # document branch names the branch it is waiting on, which is what the
    # refresh says (S-0088/D-2's two reasons).
    held.update(dict.fromkeys(document_tasks(root, branch), f"carried by the branch {branch}"))

    return held


def _in_flight(root: Path, task_id: str, board: Board | None) -> str:
    """Why a refresh leaves a task alone for its state, or the empty string:
    an attempt in flight reads the contract it was dispatched under
    (S-0088/D-2), while an escalated or reaped task starts its next attempt from
    base and reads the contract afresh."""

    from torve.application.manager import IN_FLIGHT
    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TaskState

    view = board.tasks.get(task_id) if board is not None else None

    if view is not None:
        if view.state in IN_FLIGHT:
            return f"running ({view.state})"

        if view.state is TaskState.READY or view.landed_sha:
            return "landed"

    path = naming.state_file(root, task_id)

    if not path.exists():
        return ""

    state = RunState.load(path).state

    if state in IN_FLIGHT:
        return f"running ({state})"

    return "landed" if state is TaskState.READY else ""


# ....................... #


def _remote_document(root: Path, rfc_dir: Path, identifier: str, branch: str, into: Path) -> Path:
    """A corpus under *into* that is the checkout's but for one document, read
    from the remote's *branch* as the last fetch left it (S-0094/D-1): a phase
    widened there reaches the refresh without a hand edit. *rfc_dir* when the
    remote has no such branch, or the branch no such document."""

    try:
        directory = spec.document_dir(rfc_dir, identifier)
        relative = directory.resolve().relative_to(root.resolve()) if directory else None
    except ValueError:
        relative = None

    if directory is None or relative is None:
        return rfc_dir

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "archive",
            f"refs/remotes/origin/{branch}",
            "--",
            relative.as_posix(),
        ],
        capture_output=True,
        check=False,
    )

    if proc.returncode != 0 or not proc.stdout:
        return rfc_dir

    corpus = into / rfc_dir.name
    shutil.copytree(rfc_dir, corpus)
    archive = spec.archive_dir(rfc_dir)

    if archive.is_dir():
        (into / archive.name).symlink_to(archive.resolve(), target_is_directory=True)

    shutil.rmtree(corpus / directory.name)

    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(into / "tree", filter="data")

    shutil.move(into / "tree" / relative, corpus / directory.name)

    return corpus


def refresh_document(
    root: Path,
    rfc_dir: Path,
    identifier: str,
    *,
    board: Board | None = None,
    remote_branch: str | None = None,
) -> RefreshReport:
    """The document derived as for a mint, and each already-minted phase's
    contract compared with the derivation field by field (S-0088/D-1). Dry:
    nothing is written and nothing is recorded.

    A phase's contracts are paired, role by role, with the derivations of
    their own role in id order, which is mint order — the planner numbers a
    phase's entries in document order, so the nth contract of a role is the
    nth derivation of it, and a phase minted under the verify pilot holds two
    contracts per entry. A contract whose entry the phasing no longer carries
    is left alone and named; a phase never minted is not minted here (that is
    `plan`'s job, refused while any phase is minted, unchanged). A refresh
    mints no verifier, so an implement contract of a phase that holds no
    verify contract is diffed against the derivation without one.

    With *remote_branch*, the document is read from the remote's copy of that
    branch after a fetch the caller made, and the rest of the corpus from the
    checkout; from the checkout alone when the remote has no such branch."""

    if remote_branch is None:
        return _refresh(root, rfc_dir, identifier, board, committed=False)

    with tempfile.TemporaryDirectory(prefix="torve-refresh-") as scratch:
        corpus = _remote_document(root, rfc_dir, identifier, remote_branch, Path(scratch))

        return _refresh(root, corpus, identifier, board, committed=corpus != rfc_dir)


def _refresh(
    root: Path, rfc_dir: Path, identifier: str, board: Board | None, *, committed: bool
) -> RefreshReport:
    report = plan_document(
        root, rfc_dir, identifier, board=board, refresh=True, committed=committed
    )
    landed = _beyond_refresh(root, rfc_dir, report.document)
    existing: dict[int, list[Task]] = {}

    for task in sorted(minted_contracts(root, report.document, board).values(), key=lambda t: t.id):
        existing.setdefault(task.phase, []).append(task)

    found: list[RefreshedTask] = []

    for phase, contracts in sorted(existing.items()):
        entries = [planned for planned in report.tasks if planned.task.phase == phase]
        # S-0116/D-4: a phase minted under the pilot holds a verify task beside
        # its implement ones, and the two roles are paired with the derivation
        # of their own role, in id order — the nth contract of a role with the
        # nth derivation of it, never positionally across both.
        by_role: dict[str, list[PlannedTask]] = {}

        for planned in entries:
            by_role.setdefault(planned.task.role, []).append(planned)

        # S-0116/D-5: the verifier's wiring is fixed at mint, so the derivation
        # is aligned with the contract before the diff: an implement contract
        # minted with a verifier keeps its file and run whichever way the
        # derivation moves, and one minted without stays without — a file
        # name a later derivation gives its entry may be another entry's.
        seen_role: dict[str, int] = {}
        # A verify contract is paired through the implement contract that
        # depends on it (S-0116/D-5), never by offset: the entries that earn a
        # verifier can change after mint, and the nth verifier would then take
        # another entry's intent.
        implements = [one for one in contracts if one.role == "implement"]
        implement_derived = dict(
            zip([one.id for one in implements], by_role.get("implement", []), strict=False)
        )

        for current in contracts:
            offset = seen_role.get(current.role, 0)
            seen_role[current.role] = offset + 1
            deriveds = by_role.get(current.role, [])

            if current.role == "verify":
                pairing = _verify_derivation(current, implements, implement_derived, deriveds)
            else:
                pairing = deriveds[offset] if offset < len(deriveds) else None

            if pairing is None:
                found.append(
                    RefreshedTask(current.id, phase, current.title, [], "no phasing entry")
                )

                continue

            derived = pairing.task

            if current.role == "verify":
                # The verifier's file, and the run that collects it, are fixed
                # at mint like the implementer's wiring to them (S-0116/D-5):
                # only what the entry says follows the derivation.
                derived = derived.model_copy(
                    update={"scope": current.scope, "acceptance": current.acceptance}
                )

            if current.role == "implement":
                if current.scope.deny:
                    derived = _minted_with_a_verifier(derived, current)
                else:
                    derived = _minted_without_a_verifier(derived)

            changed = [
                field
                for field in REFRESHED_FIELDS
                if getattr(current, field) != getattr(derived, field)
            ]

            held = landed.get(current.id) or _in_flight(root, current.id, board)

            rewritten = (
                current.model_copy(update={field: getattr(derived, field) for field in changed})
                if changed and not held
                else None
            )

            found.append(RefreshedTask(current.id, phase, pairing.title, changed, held, rewritten))

    return RefreshReport(document=report.document, tasks=found)


# ....................... #


def record_refresh(root: Path, document: str, one: RefreshedTask) -> None:
    """One rewrite on the engine's stream (S-0088/D-3): a contract edited with no
    record is what the refresh exists to end."""

    from torve.application.telemetry import engine_event

    engine_event(
        root,
        "contract_refreshed",
        {"task": one.task_id, "spec": document, "phase": one.phase, "fields": one.changed},
    )


def refresh_contracts(root: Path, report: RefreshReport) -> list[Path]:
    """The file mode: each rewritten contract written back over its own file,
    keeping the header that names what minted it and stamping the refresh
    beside it (S-0088/D-3)."""

    from torve.base.clock import stamp

    written: list[Path] = []
    at = stamp()

    for one, task in report.rewritten:
        path = layout.task_file(root, task.id)
        header: list[str] = []

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#"):
                break

            if not line.startswith("# Refreshed by"):
                header.append(line)

        header.append(f"# Refreshed by `torve plan {report.document} --refresh` at {at}")
        path.write_text(
            "\n".join(header) + "\n" + _dump_contract(task.model_dump()), encoding="utf-8"
        )

        record_refresh(root, report.document, one)
        written.append(path)

    return written


async def refresh_into_record(
    root: Path, log: EventLog, report: RefreshReport, *, partition: str, actor_id: str = "plan"
) -> list[str]:
    """The partition mode (S-0088/D-3): the rewritten contracts through the
    residency path the mint uses, so the board's row is the rewritten one on
    the next pass — a re-mint transitions nothing and never happens under an
    attempt in flight (S-0049/D-3, S-0049/D-4)."""

    from torve.application.residency import mint

    for one, _task in report.rewritten:
        record_refresh(root, report.document, one)

    return await mint(
        log,
        {task.id: task for _one, task in report.rewritten},
        partition=partition,
        actor_id=actor_id,
    )


# ....................... #


@dataclass(frozen=True)
class StaleTask:
    """One non-terminal task whose source document became superseded (§3.3,
    charter S-0001/A-8)."""

    task_id: str
    document: str
    superseded_by: str | None
    state: str
    action: str  # escalated | would escalate | skipped (terminal) | already escalated (...)


# ....................... #


def reconcile(root: Path, rfc_dir: Path, dry_run: bool = True) -> list[StaleTask]:
    """Mark every non-terminal task minted from a superseded document,
    escalating each as `stale_inheritance` (S-0007/D-10, charter S-0001/A-8). Nothing is
    deleted or rewritten — what to do with in-flight work is a human
    decision, and this verb records a fact about a task's inheritance rather
    than touching a running aggregate (§2). A task that never ran gains a
    state file through the claimed -> escalated edge the reaper minted; a
    task already escalated for another reason is reported and left — one
    escalation, one human decision at a time."""

    from torve.application.runstate import RunState
    from torve.base import naming
    from torve.domain.states import TERMINAL, EscalationReason, TaskState

    superseded: dict[str, str | None] = {}

    for doc in load_corpus(rfc_dir).documents:
        if doc.archived:
            continue

        if doc.status == "superseded" or doc.superseded_by:
            superseded[doc.id] = doc.superseded_by or None

    found: list[StaleTask] = []
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    if not tasks_dir.is_dir() or not superseded:
        return found

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        try:
            raw: Any = yaml.safe_load(contract.read_text(encoding="utf-8"))

        except yaml.YAMLError:
            continue

        if not isinstance(raw, dict):
            continue

        record = cast("dict[str, Any]", raw)
        document = document_of(str(record.get("spec") or ""))

        if document not in superseded:
            continue

        task_id = str(record.get("id", contract.parent.name))
        by = superseded[document]

        detail = (
            f"minted from {document}, superseded by {by or 'an unset successor'} "
            "(charter S-0001/A-8): its inherited decisions no longer stand"
        )

        state_path = naming.state_file(root, task_id)

        if state_path.exists():
            state = RunState.load(state_path)

            if state.state in TERMINAL:
                found.append(
                    StaleTask(task_id, document, by, str(state.state), "skipped (terminal)")
                )

                continue

            if state.state is TaskState.ESCALATED:
                reason = state.escalation.reason if state.escalation else "unknown"

                action = (
                    "already escalated (stale_inheritance)"
                    if reason == "stale_inheritance"
                    else f"already escalated ({reason}) — left for triage"
                )

                found.append(StaleTask(task_id, document, by, str(state.state), action))
                continue

            if not dry_run:
                state.escalate(EscalationReason.STALE_INHERITANCE, detail)

            found.append(
                StaleTask(
                    task_id,
                    document,
                    by,
                    str(state.state),
                    "escalated" if not dry_run else "would escalate",
                )
            )
        else:
            if not dry_run:
                state = RunState(task_id=task_id, path=state_path)

                state.transition(
                    TaskState.CLAIMED, "torve plan --reconcile: claiming to record the fact"
                )

                state.escalate(EscalationReason.STALE_INHERITANCE, detail)

            found.append(
                StaleTask(
                    task_id,
                    document,
                    by,
                    "unstarted",
                    "escalated" if not dry_run else "would escalate",
                )
            )

    return found
