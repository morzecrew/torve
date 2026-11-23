"""The acceptance compiler (S-0129): before a unit's first candidate, one
reviewer-seat call turns the unit's document into probes, each a file under
`probes/acceptance/` and a command pinned to one row (S-0129/D-1). The engine
keeps a probe only when it executes red on the tree the unit starts from
(S-0129/D-2) and stores the kept ones once per unit (S-0129/D-3); the critic's
decision re-runs them against every candidate (S-0129/D-4), and a probe that
fails every run is put to a dispute, whose kept counter-probe takes its place
(S-0129/D-9)."""

from __future__ import annotations

import hashlib
import posixpath
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application.critic import NOT_RUNNABLE, PROBE_CAP, Reproduce, _probe_names, scrub_copy
from torve.application.review import (
    close_review,
    last_document,
    mint_review_task,
    review_record,
    reviewer_for,
    run_seat,
    schema_refusal,
    seat_api_error,
)
from torve.config import layout
from torve.config.spec import document_dir
from torve.domain.task import InheritedDecision, Task
from torve.gates.evidence_package import CommandRun

if TYPE_CHECKING:
    from torve.application.dispatch import Dispatch
    from torve.application.runstate import RunState

# ....................... #

_PROMPTS = Path(__file__).with_name("prompts")
PROMPT_FILE = _PROMPTS / "compile-v2.md"
SCHEMA_FILE = _PROMPTS / "compile-v1.schema.json"
# v2 (S-0129/A-2): every row its probe first, then the rules a schema in the
# document states. The answer's shape is unchanged, so its schema is still
# `torve.compile/v1`.
PROMPT_VERSION = "compile/v2"

#: Where the compiler's inputs sit in its copy, as the frozen prompt names them.
COMPILE_DIR = ".torve/compile"

#: Where every compiled probe lives, in the seat's copy, in each candidate's
#: worktree and in every re-run's checkout.
ACCEPTANCE_DIR = "probes/acceptance"

#: The most probes a unit keeps, in the seat's order (S-0129/D-2).
ACCEPTANCE_CAP = 24

#: The unit's store, beside its contract.
STORE_FILE = "acceptance.yaml"

#: The history fact a compile leaves, and the one the first critic decision
#: leaves with how many probes blocked it (S-0129/D-5).
COMPILED_MARKER = "acceptance compiled "
FAILED_FIRST_MARKER = "acceptance failed first "

#: The id a probe's finding carries in the critic's decision: no critic id
#: has the colon, so a reader tells the two apart.
FINDING_PREFIX = "acceptance:"

#: The dispute's frozen prompt and the schema of its answer (S-0129/D-9).
DISPUTE_PROMPT_FILE = _PROMPTS / "dispute-v1.md"
DISPUTE_SCHEMA_FILE = _PROMPTS / "dispute-v1.schema.json"
DISPUTE_VERSION = "dispute/v1"

#: Where the dispute's inputs sit in its copy, as its frozen prompt names them.
DISPUTE_DIR = ".torve/dispute"

#: The history fact a dispute leaves.
DISPUTED_MARKER = "acceptance disputed "


def compile_prompt() -> str:
    """The frozen system prompt, byte for byte as shipped."""

    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_sha() -> str:
    """The sha256 over the frozen prompt and its schema, recorded on every
    compile as the critic's is (S-0122/D-19)."""

    return hashlib.sha256(PROMPT_FILE.read_bytes() + SCHEMA_FILE.read_bytes()).hexdigest()


def dispute_prompt() -> str:
    """The dispute's frozen system prompt, byte for byte as shipped."""

    return DISPUTE_PROMPT_FILE.read_text(encoding="utf-8")


def dispute_sha() -> str:
    """The sha256 over the dispute's frozen prompt and its schema, recorded on
    every dispute (S-0129/D-9)."""

    return hashlib.sha256(
        DISPUTE_PROMPT_FILE.read_bytes() + DISPUTE_SCHEMA_FILE.read_bytes()
    ).hexdigest()


# ----------------------- #
# torve.compile/v1

# The compiler reads these docstrings: they describe the schema staged beside
# its prompt, and change with it (S-0122/D-19).
_ANSWER = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class CompiledProbe(BaseModel):
    """One probe."""

    model_config = _ANSWER

    row: str
    """The row it pins, exactly as given, `S-NNNN/D-n`."""
    quote: str
    """The passage of the document that states the behaviour, copied verbatim."""
    claim: str
    """What the probe asserts, in one sentence."""
    file: str
    """The probe's file, a path under probes/acceptance/ that you wrote."""
    command: str
    """The command that runs it from the workspace root, exiting 0 when the behaviour holds."""


class CompileDocument(BaseModel):
    """torve.compile/v1: the one JSON document your answer ends with."""

    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True, populate_by_name=True)

    schema_name: Literal["torve.compile/v1"] = Field(default="torve.compile/v1", alias="schema")
    probes: list[CompiledProbe]
    """Every probe: one for each row first, then the rest; each group most likely to catch
    a wrong implementation first."""


def compile_schema() -> dict[str, Any]:
    """The JSON schema the compiler's answer is checked against, as staged."""

    return CompileDocument.model_json_schema(by_alias=True)


def read_compile(output: str) -> tuple[CompileDocument | None, str | None]:
    """The last `torve.compile/v1` document in the output, or None, beside
    the refusal when the model gave one the schema refuses."""

    last = last_document(output, key="probes")

    if last is None:
        return None, None

    try:
        return CompileDocument.model_validate(last), None

    except ValidationError as exc:
        return None, schema_refusal(exc, "compile document")


# ----------------------- #
# torve.dispute/v1


class DisputeDocument(BaseModel):
    """torve.dispute/v1: the one JSON document your answer ends with."""

    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True, populate_by_name=True)

    schema_name: Literal["torve.dispute/v1"] = Field(default="torve.dispute/v1", alias="schema")
    verdict: Literal["upheld", "disputed"]
    """`upheld` when the probe asserts what the document states, `disputed` when it does not."""
    reason: str
    """Why, in one sentence: what the probe asserts and what the document states."""
    quote: str = ""
    """For a dispute: the passage of the document the probe contradicts, copied verbatim."""
    file: str = ""
    """For a dispute: the counter-probe's file, a new path under probes/acceptance/."""
    command: str = ""
    """For a dispute: the command that runs it from the workspace root, exiting 0 when the
    behaviour the passage states holds."""


def dispute_schema() -> dict[str, Any]:
    """The JSON schema the dispute's answer is checked against, as staged."""

    return DisputeDocument.model_json_schema(by_alias=True)


def read_dispute(output: str) -> tuple[DisputeDocument | None, str | None]:
    """The last `torve.dispute/v1` document in the output, or None, beside the
    refusal when the model gave one the schema refuses."""

    last = last_document(output, key="verdict")

    if last is None:
        return None, None

    try:
        return DisputeDocument.model_validate(last), None

    except ValidationError as exc:
        return None, schema_refusal(exc, "dispute document")


# ----------------------- #
# What a unit keeps.


@dataclass(frozen=True)
class Probe:
    """One kept probe: what the compiler wrote, the text the engine holds of
    its file and what its command exited on the unit's start tree."""

    id: str
    row: str
    quote: str
    claim: str
    file: str
    command: str
    text: str
    base_exit: int


@dataclass
class Acceptance:
    """A unit's compile: the probes it keeps, the ones it dropped with why,
    and what the compile cost. Empty when no compile ran or it kept nothing,
    and then the unit runs as it would without one (S-0129/D-2)."""

    kept: list[Probe] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    review_id: str | None = None
    fact: str = ""
    spend_usd: float | None = None
    wall_s: float = 0.0
    #: The digest of the document files and contract rows it was compiled
    #: from (S-0129/D-7); None on a store written before it was recorded.
    source: str | None = None
    #: The tree the probes were compiled over, where a counter-probe must
    #: fail (S-0129/D-9); None on a store written before it was recorded.
    #: shortcut: a start tree with uncommitted work is a snapshot commit no
    #: ref holds, which git's gc may prune past its expiry (two weeks by
    #: default); pin it under a ref if a unit ever outlives that.
    start: str | None = None
    #: Every probe a kept dispute withdrew, its text left out, with the
    #: dispute's review, passage and reason and the counter-probe that took
    #: its place (S-0129/D-9).
    withdrawn: list[dict[str, Any]] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        return [probe.command for probe in self.kept]

    @property
    def texts(self) -> dict[str, str]:
        return {probe.file: probe.text for probe in self.kept}


#: What makes a command more than one simple command: a list, a pipe, a
#: redirection, a substitution or a second line.
_COMPOUND = re.compile(r"[;&|<>`\n]|\$\(")


def _collapsed(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _linked(tree: Path, name: str) -> bool:
    """Whether a link sits anywhere on *name*'s path under *tree*, which a
    write would follow out of it."""

    parts = PurePosixPath(name).parts

    return any(tree.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1))


def _own_file(name: str) -> str | None:
    """*name* as a path under `probes/acceptance/`, or None when it is not
    one: absolute, leaving it, or naming the directory itself."""

    path = posixpath.normpath(name.strip())

    if path.startswith("/") or ".." in path.split("/"):
        return None

    return path if path.startswith(f"{ACCEPTANCE_DIR}/") else None


def _written_reason(
    probe: CompiledProbe, carried: Iterable[str], documents: Iterable[str], text: str | None
) -> str | None:
    """Why *probe* is dropped before anything runs it, or None."""

    if probe.row not in set(carried):
        return f"row {probe.row} is not one the contract carries"

    quote = _collapsed(probe.quote)

    if not quote or not any(quote in _collapsed(one) for one in documents):
        return "its quote is not in the document"

    own = _own_file(probe.file)

    if own is None or text is None:
        return f"no file under {ACCEPTANCE_DIR}/ within {PROBE_CAP} bytes at {probe.file}"

    if _probe_names(probe.command, None) != [own]:
        return "its command does not run its own file alone"

    # A command another command can fail before the probe runs would fail on
    # every tree, a correct one included.
    if _COMPOUND.search(probe.command):
        return "its command is not one simple command"

    return None


#: Python's report of an import that found nothing on the path.
_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")
#: The header Python prints above an uncaught exception's frames.
_TRACEBACK = "Traceback (most recent call last):"
#: pytest's heading for a test file it could not import, which ends the session.
_COLLECTING = "ERROR collecting"
#: pytest's mark on the lines of the exception it reports.
_MARKED = re.compile(r"^E +(\S.*)$", re.MULTILINE)
#: What Python and pytest print between two exceptions of a chain.
_CHAINED = re.compile(
    r"^(?:During handling of the above exception, another exception occurred"
    r"|The above exception was the direct cause of the following exception):$",
    re.MULTILINE,
)


def _uncaught(tail: str) -> str | None:
    """The exception line of what ended the run in *tail*, whichever comes
    last: Python's last traceback, whose exception is the first line after
    its header that is not an indented frame (a chained traceback prints the
    one that ended the run last), or pytest's last collection error, whose
    exception is the first line marked `E` after its last link of a chain.
    None for neither."""

    traceback, collecting = tail.rfind(_TRACEBACK), tail.rfind(_COLLECTING)

    if collecting > traceback:
        links = [*_CHAINED.finditer(tail, collecting)]
        marked = _MARKED.search(tail, links[-1].end() if links else collecting)

        return marked[1] if marked else None

    if traceback < 0:
        return None

    frames = tail[traceback + len(_TRACEBACK) :].splitlines()[1:]

    return next((line for line in frames if line[:1].strip()), None)


#: The start tree as the files it holds, by path relative to its root: what
#: a dispute reads it as, the tree itself being a commit (S-0129/D-9).
Listing = frozenset[str]


def _holds_module(tree: Path | Listing, name: str) -> bool:
    """Whether *tree*, a directory or a listing of one, holds module *name* at
    its root or under `src/`: as a module, a package, or a namespace package,
    a folder holding modules at any depth with no `__init__`."""

    # shortcut: the root and `src/` only, so a monorepo's `packages/x/src`
    # keeps such a probe; read the project's own path config if one appears.
    for root in ("", "src/"):
        path = root + "/".join(name.split("."))

        if isinstance(tree, Path):
            found = tree / path

            if found.with_suffix(".py").is_file() or (found.is_dir() and any(found.rglob("*.py"))):
                return True

        elif f"{path}.py" in tree or any(
            one.startswith(f"{path}/") and one.endswith(".py") for one in tree
        ):
            return True

    return False


def _held_unimported(tail: str, tree: Path | Listing | None) -> str | None:
    """The module whose import ended the probe's run in *tail* although
    *tree*, the start tree or a listing of its files, holds it, or None.
    Running a file puts its own folder on the import path, not the
    repository's root, so a probe written as if it did cannot see a project
    that is not installed, on any tree. A message the probe printed or caught
    before failing otherwise is not the failure."""

    found = _NOT_FOUND.match(_uncaught(tail) or "")

    if tree is None or found is None:
        return None

    return found[1] if _holds_module(tree, found[1]) else None


def _base_reason(base: CommandRun | None, tree: Path | Listing | None = None) -> str | None:
    """Why a probe whose command ran as *base* on the start tree, *tree*, is
    dropped, or None when it executed and failed there."""

    if base is None or base.exit_code is None:
        return f"its run on the start tree measured nothing: {base.tail if base else 'not run'}"

    if base.exit_code in NOT_RUNNABLE:
        return f"it exits {base.exit_code} on the start tree, so it cannot run"

    if base.exit_code == 0:
        return "it passes on the start tree, so it pins nothing the unit adds"

    held = _held_unimported(base.tail, tree)

    if held is not None:
        return f"it cannot import {held}, which the start tree holds, so it fails on every tree"

    return None


def drop_reason(
    probe: CompiledProbe,
    carried: Iterable[str],
    documents: Iterable[str],
    text: str | None,
    base: CommandRun | None,
    tree: Path | None = None,
) -> str | None:
    """Why the engine drops *probe*, or None when it keeps it (S-0129/D-2):
    its row must be one the contract *carried*, its quote must occur in one
    of the document's files, *documents*, with whitespace collapsed, its
    *text* must have been kept from
    a file under `probes/acceptance/` that its command alone runs, and its
    command, run over the unit's start tree as *base*, must have executed and
    exited non-zero, and not for want of a module the start tree, *tree*,
    holds (S-0129/A-3). *base* is None when that run never happened."""

    return _written_reason(probe, carried, documents, text) or _base_reason(base, tree)


# ----------------------- #
# The store (S-0129/D-3).


def store_file(root: Path, task_id: str) -> Path:
    return layout.task_dir(root, task_id) / STORE_FILE


def load(root: Path, task_id: str) -> Acceptance | None:
    """The unit's stored compile, or None when it has none: a unit whose
    store was compiled from the document and rows it reads now never compiles
    again, whether it kept probes or not (S-0129/D-7)."""

    path = store_file(root, task_id)

    if not path.is_file():
        return None

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    return Acceptance(
        kept=[Probe(**probe) for probe in data.get("kept", [])],
        dropped=list(data.get("dropped", [])),
        review_id=data.get("review_id"),
        fact=data.get("fact", ""),
        spend_usd=data.get("spend_usd"),
        wall_s=data.get("wall_s", 0.0),
        source=data.get("source"),
        start=data.get("start"),
        withdrawn=list(data.get("withdrawn", [])),
    )


def save(root: Path, task_id: str, acceptance: Acceptance) -> None:
    path = store_file(root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(asdict(acceptance), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def plant(root: Path, task_id: str, worktree: Path) -> list[str]:
    """The unit's kept probes written into *worktree* at their paths, and the
    paths planted. The caller keeps `probes/acceptance/` out of what the
    judge commits; the copy written here is the executor's to read, and
    nothing it does to it reaches a re-run, which writes the stored text."""

    stored = load(root, task_id)

    if stored is None:
        return []

    held = {probe.file for probe in stored.kept}

    # A probe a dispute withdrew is no longer the executor's to pass
    # (S-0129/D-9), so a copy an earlier planting left goes.
    for one in stored.withdrawn:
        name = _own_file(str(one.get("file", "")))

        # Only a plain file goes: a link is never followed, and a folder the
        # executor put there is its own.
        if not name or name in held or _linked(worktree, name):
            continue

        if (worktree / name).is_file():
            (worktree / name).unlink()

    planted: list[str] = []

    for probe in stored.kept:
        parts = PurePosixPath(probe.file).parts

        # A tracked link on the path would carry the write outside the tree.
        if any(
            worktree.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1)
        ):
            continue

        path = worktree / probe.file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(probe.text, encoding="utf-8")
        planted.append(probe.file)

    return planted


# ----------------------- #
# The compile (S-0129/D-1).


def _document_files(spec_dir: Path, reference: str | None) -> dict[str, str]:
    """Every file of the unit's document, by name; empty when it has none."""

    found = document_dir(spec_dir, reference) if reference else None

    if found is None:
        return {}

    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(found.glob("*.yaml"))
        if path.is_file()
    }


def _rows_document(rows: Sequence[InheritedDecision]) -> list[dict[str, Any]]:
    return [
        {"id": row.id, "grade": row.grade, "text": row.text, "paths": list(row.paths)}
        for row in rows
    ]


def build_compile_prompt(target: Task) -> str:
    """The message beside the frozen prompt: the unit, its rows and where its
    inputs are. The rows ride inline because every probe names one."""

    listed = "\n".join(f"- {row.id} [{row.grade}]: {row.text}" for row in target.decisions)

    return f"""# Unit {target.id} of {target.spec}

## Intent

{target.intent or "(the contract states no intent)"}

## Rows

{listed or "- none"}

## What the engine does with your probes

It keeps a probe when its row is one above, its quote occurs in the document, its file is
under `{ACCEPTANCE_DIR}/` within {PROBE_CAP} bytes, its command names that file and no other
probe, and the command exits non-zero, but not 126 or 127, on this tree, and not because it
could not import a module this tree holds: running a file puts its own folder on the import
path, not the repository's root. It keeps at most
{ACCEPTANCE_CAP}, in your order. The re-run reads a fresh checkout with the probe written in:
no `.venv`, no build output.

## Inputs

- `{COMPILE_DIR}/document/` — the document's files
- `{COMPILE_DIR}/rows.yaml` — the rows above, with their paths
- `{COMPILE_DIR}/compile.schema.json` — the shape of your answer
"""


def _read_own(copy: Path, name: str) -> str | None:
    """The text of *name*, a path under `probes/acceptance/` in *copy*, or
    None for a link on its path, a path leaving the copy, no file or one over
    the cap."""

    root = copy.resolve()
    path = copy / name
    parts = PurePosixPath(name).parts

    try:
        if any(copy.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1)):
            return None

        if not path.resolve().is_relative_to(root):
            return None

        if path.is_file() and path.stat().st_size <= PROBE_CAP:
            return path.read_text(encoding="utf-8", errors="replace")

    except OSError:
        return None

    return None


def _keep_files(copy: Path, document: CompileDocument | None) -> dict[str, str]:
    """The probe files the answer names, read before the copy dies; a path
    outside `probes/acceptance/`, a link or a file over the cap is left."""

    if document is None:
        return {}

    kept: dict[str, str] = {}

    for probe in document.probes:
        name = _own_file(probe.file)

        if name is None or name in kept:
            continue

        text = _read_own(copy, name)

        if text is not None:
            kept[name] = text

    return kept


def _fact(state: RunState, fact: str) -> None:
    state.history.append(
        {"at": state.heartbeat, "from": str(state.state), "to": str(state.state), "fact": fact}
    )
    state.save()


def _inputs(run: Dispatch) -> tuple[dict[str, str], str, str]:
    """The document's files and the contract's rows as the seat is staged
    them, and the digest of both a store records (S-0129/D-7)."""

    files = _document_files(run.root / run.config.specs.path, run.task.spec)
    rows = yaml.safe_dump(_rows_document(run.task.decisions), sort_keys=False, allow_unicode=True)

    return files, rows, hashlib.sha256(yaml.safe_dump([files, rows]).encode("utf-8")).hexdigest()


def _current(run: Dispatch, source: str) -> Acceptance | None:
    """The unit's store when it was compiled from *source*; one from another
    document or other rows, an amendment or a refresh since, is removed, so
    neither a planting, the critic nor the recheck meets it (S-0129/D-7)."""

    stored = load(run.root, run.task.id)

    if stored is not None and stored.source == source:
        return stored

    store_file(run.root, run.task.id).unlink(missing_ok=True)

    return None


def discard_stale(run: Dispatch) -> None:
    """A compile the unit's cap or clock skipped leaves no store from another
    document or other rows behind it (S-0129/D-7)."""

    _current(run, _inputs(run)[2])


def compile_acceptance(
    run: Dispatch,
    state: RunState,
    reproduce: Reproduce,
    price: Callable[[Mapping[str, Any]], float | None],
    start: str | None = None,
) -> Acceptance:
    """The unit's probes (S-0129/D-1, S-0129/D-2): its store when that was
    compiled from the document and rows the unit reads now (S-0129/D-7), else
    one compile over the tree the unit starts from, kept and stored.

    *reproduce* runs the probes' commands over a judge checkout of that tree,
    *start*, which the store records for a dispute's counter-probe
    (S-0129/D-9); *price* turns the seat's agent block into the unit spend it
    records, and returns it. A seat that refuses, errs or answers nothing
    readable keeps nothing, and so does a document that is not there: the
    store then holds the empty compile, so the unit never compiles twice."""

    from torve.application.telemetry import config_hash

    files, rows, source = _inputs(run)
    # S-0129/D-7: a stale store goes before the seat runs, so a seat that
    # fails leaves the unit without probes rather than with stale ones.
    stored = _current(run, source)

    if stored is not None:
        return stored

    started = time.monotonic()

    if not files:
        acceptance = Acceptance(
            fact=f"no document to compile for {run.task.spec}", source=source, start=start
        )
        save(run.root, run.task.id, acceptance)
        _fact(state, f"{COMPILED_MARKER}{acceptance.fact}")

        return acceptance

    taken = list(run.deps.taken()) if run.deps.taken is not None else []
    review = mint_review_task(
        run.root,
        run.task,
        intent=f"Compile {run.task.spec} into acceptance probes for {run.task.id}.",
        taken=taken,
    )
    agent = reviewer_for(run)
    staged = {
        **{f"document/{name}": text for name, text in files.items()},
        "rows.yaml": rows,
        "compile.schema.json": SCHEMA_FILE.read_text(encoding="utf-8"),
    }

    def stage(copy: Path) -> None:
        scrub_copy(copy)

        for name, text in staged.items():
            path = copy / COMPILE_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    try:
        session = run_seat(
            run.root,
            run.worktree,
            run.task,
            review,
            run.config,
            run.deps.runtime,
            agent,
            build_compile_prompt(run.task),
            stage=stage,
            read=read_compile,
            harvest=_keep_files,
            system=compile_prompt(),
            broker=run.deps.broker,
            broker_handle=run.broker_handle,
        )

    except Exception as exc:
        # S-0129/D-2: a seat that could not run leaves the unit to run without
        # probes rather than not at all. Nothing is stored, so the next
        # attempt, from the same start tree, compiles again.
        failed = Acceptance(fact=f"the compile seat failed: {exc!r}")
        _fact(state, f"{COMPILED_MARKER}{failed.fact}")

        return failed

    document, refusal = session.verdict, session.refusal
    texts: dict[str, str] = session.harvested or {}
    carried = [row.id for row in run.task.decisions]
    documents = list(files.values())
    kept: list[Probe] = []
    dropped: list[dict[str, Any]] = []
    candidates: list[tuple[CompiledProbe, str]] = []
    taken_files: set[str] = set()

    for compiled in document.probes if document is not None else []:
        own = _own_file(compiled.file) or ""
        # Everything but the base run, which only a survivor of the rest costs.
        why = _written_reason(compiled, carried, documents, texts.get(own))

        if why is None and own in taken_files:
            why = "another probe took its file"

        if why is None and len(candidates) >= ACCEPTANCE_CAP:
            why = f"the cap of {ACCEPTANCE_CAP} was reached"

        if why is not None:
            dropped.append({**compiled.model_dump(), "why": why})
            continue

        taken_files.add(own)
        candidates.append((compiled, own))

    runs: list[CommandRun] = []

    # Each probe alone with its own file, as the decision and the recheck
    # re-run it: a probe that leans on a sibling's file measures the same
    # on every tree.
    for compiled, own in candidates:
        try:
            (base,) = reproduce([compiled.command], {own: texts[own]})

        except Exception as exc:
            # A checkout or sandbox that would not open measured nothing.
            base = CommandRun(compiled.command, None, f"not run: {exc!r}")

        runs.append(base)

    for (compiled, own), base in zip(candidates, runs, strict=True):
        why = _base_reason(base, run.worktree)

        if why is not None or base.exit_code is None:
            dropped.append({**compiled.model_dump(), "base_exit": base.exit_code, "why": why})
            continue

        kept.append(
            Probe(
                id=f"{FINDING_PREFIX}{len(kept) + 1}",
                row=compiled.row,
                quote=compiled.quote,
                claim=compiled.claim,
                file=own,
                command=compiled.command,
                text=texts[own],
                base_exit=base.exit_code,
            )
        )

    seat_error = seat_api_error(session.result.output) if document is None else None

    if refusal is not None:
        fact = f"compile output refused: {refusal}"
    elif seat_error is not None:
        fact = f"the compile seat gave no answer: {seat_error}"
    elif document is None:
        fact = "compile output unparseable — no probes kept"
    else:
        fact = f"kept {len(kept)} of {len(document.probes)} probe(s)"

    spend = price(
        {
            "cost_usd": session.result.cost_usd,
            "provider": session.tier.provider or None,
            "model": session.tier.model or None,
            **session.token_counts,
        }
    )
    acceptance = Acceptance(
        kept=kept,
        dropped=dropped,
        review_id=review.id,
        fact=fact,
        spend_usd=spend,
        wall_s=round(time.monotonic() - started, 3),
        source=source,
        start=start,
    )
    record = review_record(
        session,
        run.task,
        review,
        agent,
        config_hash(run.manifest_file, run.worktree, run.config, image_digest=run.image_digest),
        "compile",
        {
            "mode": "compile",
            "prompt": PROMPT_VERSION,
            "prompt_sha": prompt_sha(),
            "findings": [],
            "blocked": False,
            "kept": [
                {key: value for key, value in asdict(probe).items() if key != "text"}
                for probe in kept
            ],
            "dropped": dropped,
            "unparseable": document is None and refusal is None,
            "refusal": refusal,
        },
        run.deps.broker,
    )
    close_review(run.root, run.worktree, session, record, fact)

    # Only an answer is kept, so it is paid once. A seat that gave none, a
    # provider error or a harness that never ran, is asked again next attempt.
    if document is not None or refusal is not None:
        save(run.root, run.task.id, acceptance)

    _fact(state, f"{COMPILED_MARKER}{review.id}: {fact}")

    return acceptance


# ----------------------- #
# The dispute (S-0129/D-9).


@dataclass(frozen=True)
class Dispute:
    """What the dispute of one probe that failed every run came to: whether
    the engine kept it and why, the seat's verdict and reason, and, for a kept
    one, the passage it quotes, the counter-probe that takes the probe's place
    and that counter-probe's run on the candidate."""

    probe: str
    kept: bool
    why: str
    review_id: str | None = None
    verdict: str | None = None
    reason: str = ""
    quote: str = ""
    counter: Probe | None = None
    run: CommandRun | None = None


#: A dispute of a probe, given its failed runs, as `decide` calls it.
Disputer = Callable[[Probe, Sequence[CommandRun]], Dispute]

#: The same, given beside them the agent block of the critic call whose
#: decision asks, which the unit has spent but not yet recorded.
UnitDisputer = Callable[[Probe, Sequence[CommandRun], Mapping[str, Any]], Dispute]

#: The most disputes a unit makes (S-0129/D-9): a candidate failing many
#: probes is most likely unfinished, and each dispute is a seat call.
DISPUTE_CAP = 2


def disputes_made(state: RunState) -> int:
    """How many disputes the unit's history records."""

    return sum(str(entry.get("fact", "")).startswith(DISPUTED_MARKER) for entry in state.history)


def _start_files(root: Path, commit: str) -> Listing | None:
    """Every file *commit* holds, or None when git cannot read it."""

    import subprocess

    listed = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", commit],
        capture_output=True,
        text=True,
        check=False,
    )

    return frozenset(filter(None, listed.stdout.split("\0"))) if listed.returncode == 0 else None


def _next_id(stored: Acceptance) -> str:
    """A probe id no kept or withdrawn probe of the unit has held."""

    taken = [probe.id for probe in stored.kept] + [str(one["id"]) for one in stored.withdrawn]
    numbers = [
        int(one.removeprefix(FINDING_PREFIX))
        for one in taken
        if one.removeprefix(FINDING_PREFIX).isdigit()
    ]

    return f"{FINDING_PREFIX}{max(numbers, default=0) + 1}"


def _counter_reason(
    answer: DisputeDocument,
    probe: Probe,
    stored: Acceptance,
    documents: Sequence[str],
    text: str | None,
) -> str | None:
    """Why the engine refuses *answer*'s dispute before anything runs its
    counter-probe, or None: the seat must have disputed, and the counter-probe
    must pass every rule a compiled probe's text and command do (S-0129/D-2)
    on the probe's row, in a file no kept probe holds."""

    if answer.verdict != "disputed":
        return "the seat upheld the probe"

    compiled = CompiledProbe(
        row=probe.row,
        quote=answer.quote,
        claim=answer.reason,
        file=answer.file,
        command=answer.command,
    )
    why = _written_reason(compiled, [probe.row], documents, text)

    if why is not None:
        return f"its counter-probe is refused: {why}"

    if _own_file(answer.file) in {one.file for one in stored.kept}:
        return "its counter-probe's file is a kept probe's"

    return None


def dispute_reason(
    answer: DisputeDocument,
    probe: Probe,
    stored: Acceptance,
    documents: Sequence[str],
    text: str | None,
    candidate: CommandRun | None,
    start: CommandRun | None,
    tree: Path | Listing | None = None,
) -> str | None:
    """Why the engine refuses *answer*'s dispute of *probe*, or None when it
    keeps it (S-0129/D-9): the seat disputed; its passage occurs in one of
    the document's files, *documents*, with whitespace collapsed; its
    counter-probe's *text* and command pass a compiled probe's rules on the
    probe's row (S-0129/D-2) in a file no kept probe of *stored* holds; and
    the command, run alone with its file, exited 0 on the candidate as
    *candidate* and executed red on the unit's start tree as *start*, and not
    for want of a module the start tree, *tree*, holds: the tree's files as
    listed from its commit, never the candidate's. *candidate* and *start*
    are None when that run never happened."""

    why = _counter_reason(answer, probe, stored, documents, text)

    if why is not None:
        return why

    if candidate is None or candidate.exit_code != 0:
        exit_code = candidate.exit_code if candidate is not None else None
        return f"its counter-probe exits {exit_code} on the candidate"

    if start is None:
        return "the unit records no start tree to run its counter-probe on"

    return _base_reason(start, tree)


def _measured(reproduce: Reproduce, command: str, probes: Mapping[str, str]) -> CommandRun:
    try:
        (run,) = reproduce([command], probes)

    except Exception as exc:
        # A checkout or sandbox that would not open measured nothing.
        return CommandRun(command, None, f"not run: {exc!r}")

    return run


def dispute_probe(
    run: Dispatch,
    state: RunState,
    probe: Probe,
    runs: Sequence[CommandRun],
    reproduce: Reproduce,
    reproduce_start: Reproduce | None,
    price: Callable[[Mapping[str, Any]], float | None],
) -> Dispute:
    """One reviewer-tier seat call under the frozen `dispute/v1` over a copy
    of the candidate, asked whether *probe*, which failed each of *runs*, or
    the candidate is wrong (S-0129/D-9).

    The engine keeps a dispute only when the seat disputed, its passage occurs
    in one of the document's files with whitespace collapsed, its
    counter-probe passes the text and command rules of a compiled probe on the
    same row in a file no kept probe holds, and the counter-probe's command,
    run alone with its own file, exits 0 on the candidate through *reproduce*
    and executes red on the unit's start tree through *reproduce_start*, as a
    compiled probe must (S-0129/D-2). A kept dispute withdraws *probe* from
    the unit's store and keeps the counter-probe in its place, under a new id,
    so every later decision and recheck is judged by it. Anything else leaves
    the store as it was, and the caller blocks on the probe.

    *price* turns the seat's agent block into the unit spend it records. The
    call is a `review` with `mode: dispute`, its prompt sha, the probe and
    what came of it; a seat that could not run records nothing."""

    from torve.application.telemetry import config_hash

    stored = load(run.root, run.task.id)

    if stored is None or all(one.id != probe.id for one in stored.kept):
        return Dispute(probe=probe.id, kept=False, why="the probe is not in the unit's store")

    # A dispute no start tree can keep is not paid for.
    if stored.start is None or reproduce_start is None:
        return Dispute(
            probe=probe.id,
            kept=False,
            why="the unit records no start tree to run a counter-probe on",
        )

    files, _rows, _source = _inputs(run)

    if not files:
        return Dispute(probe=probe.id, kept=False, why=f"no document for {run.task.spec}")

    taken = list(run.deps.taken()) if run.deps.taken is not None else []
    review = mint_review_task(
        run.root,
        run.task,
        intent=f"Dispute {probe.id} of {run.task.id}, which failed every run.",
        taken=taken,
    )
    agent = reviewer_for(run)
    shown = {
        "id": probe.id,
        "row": probe.row,
        "quote": probe.quote,
        "claim": probe.claim,
        "file": probe.file,
        "command": probe.command,
        "runs": [{"exit": one.exit_code, "tail": one.tail} for one in runs],
    }
    staged = {
        **{f"document/{name}": text for name, text in files.items()},
        "probe.yaml": yaml.safe_dump(shown, sort_keys=False, allow_unicode=True),
        "dispute.schema.json": DISPUTE_SCHEMA_FILE.read_text(encoding="utf-8"),
    }

    def stage(copy: Path) -> None:
        scrub_copy(copy)
        written = [f"{DISPUTE_DIR}/{name}" for name in staged] + [probe.file]

        # The copy is the candidate's, links kept: a write through one the
        # executor left would land outside it, so the seat is refused instead.
        for name in written:
            if _linked(copy, name):
                raise ValueError(f"{name} passes through a link in the candidate")

        for name, text in staged.items():
            path = copy / DISPUTE_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        target = copy / probe.file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(probe.text, encoding="utf-8")

    def harvest(copy: Path, document: DisputeDocument | None) -> dict[str, str]:
        name = _own_file(document.file) if document is not None else None
        text = _read_own(copy, name) if name is not None else None

        return {name: text} if name is not None and text is not None else {}

    try:
        session = run_seat(
            run.root,
            run.worktree,
            run.task,
            review,
            run.config,
            run.deps.runtime,
            agent,
            build_dispute_prompt(run.task, probe),
            stage=stage,
            read=read_dispute,
            harvest=harvest,
            system=dispute_prompt(),
            broker=run.deps.broker,
            broker_handle=run.broker_handle,
        )

    except Exception as exc:
        failed = Dispute(probe=probe.id, kept=False, why=f"the dispute seat failed: {exc!r}")
        _fact(state, f"{DISPUTED_MARKER}{probe.id}: {failed.why}")

        return failed

    answer, refusal = session.verdict, session.refusal
    texts: dict[str, str] = session.harvested or {}
    own = _own_file(answer.file) if answer is not None else None
    counter_text = texts.get(own) if own is not None else None
    counter: Probe | None = None
    measured: CommandRun | None = None
    start_run: CommandRun | None = None

    if refusal is not None:
        why: str | None = f"dispute output refused: {refusal}"
    elif answer is None:
        error = seat_api_error(session.result.output)
        why = (
            f"the dispute seat gave no answer: {error}"
            if error is not None
            else "dispute output unparseable"
        )
    else:
        documents = list(files.values())
        written = _counter_reason(answer, probe, stored, documents, counter_text)

        # Only a counter-probe the written rules keep costs a run, and only
        # one that passes on the candidate a run on the start tree.
        if written is None and own is not None and counter_text is not None:
            measured = _measured(reproduce, answer.command, {own: counter_text})

            if measured.exit_code == 0:
                start_run = _measured(reproduce_start, answer.command, {own: counter_text})

        # The module check reads the start tree itself, never the candidate,
        # which holds every module the unit adds.
        why = dispute_reason(
            answer,
            probe,
            stored,
            documents,
            counter_text,
            measured,
            start_run,
            _start_files(run.root, stored.start),
        )

        # `dispute_reason` refuses a start run with no exit, so a kept one has it.
        if (
            why is None
            and own is not None
            and counter_text is not None
            and start_run is not None
            and start_run.exit_code is not None
        ):
            counter = Probe(
                id=_next_id(stored),
                row=probe.row,
                quote=answer.quote,
                claim=answer.reason,
                file=own,
                command=answer.command,
                text=counter_text,
                base_exit=start_run.exit_code,
            )

    kept = counter is not None
    fact = f"kept: {counter.id} replaces {probe.id}" if counter is not None else f"not kept: {why}"
    price(
        {
            "cost_usd": session.result.cost_usd,
            "provider": session.tier.provider or None,
            "model": session.tier.model or None,
            **session.token_counts,
        }
    )

    if counter is not None and answer is not None:
        withdrawn = {key: value for key, value in asdict(probe).items() if key != "text"}
        stored.kept = [counter if one.id == probe.id else one for one in stored.kept]
        stored.withdrawn.append(
            {
                **withdrawn,
                "review_id": review.id,
                "reason": answer.reason,
                "quote": answer.quote,
                "counter": counter.id,
            }
        )
        save(run.root, run.task.id, stored)
        # The candidate's copy follows the store, so a revision turn in the
        # same session meets the counter-probe and not the withdrawn one.
        plant(run.root, run.task.id, run.worktree)

    record = review_record(
        session,
        run.task,
        review,
        agent,
        config_hash(run.manifest_file, run.worktree, run.config, image_digest=run.image_digest),
        "dispute",
        {
            "mode": "dispute",
            "prompt": DISPUTE_VERSION,
            "prompt_sha": dispute_sha(),
            "findings": [],
            "blocked": False,
            "probe": probe.id,
            "row": probe.row,
            "verdict": answer.verdict if answer is not None else None,
            "reason": answer.reason if answer is not None else "",
            "kept": kept,
            "why": why or "",
            "counter": (
                {key: value for key, value in asdict(counter).items() if key != "text"}
                if counter is not None
                else None
            ),
            "counter_exit": measured.exit_code if measured is not None else None,
            "start_exit": start_run.exit_code if start_run is not None else None,
            "unparseable": answer is None and refusal is None,
            "refusal": refusal,
        },
        run.deps.broker,
    )
    close_review(run.root, run.worktree, session, record, fact)
    _fact(state, f"{DISPUTED_MARKER}{probe.id} {review.id}: {fact}")

    return Dispute(
        probe=probe.id,
        kept=kept,
        why=why or "the counter-probe passes on the candidate and fails on the start tree",
        review_id=review.id,
        verdict=answer.verdict if answer is not None else None,
        reason=answer.reason if answer is not None else "",
        quote=answer.quote if answer is not None else "",
        counter=counter,
        run=measured,
    )


def build_dispute_prompt(target: Task, probe: Probe) -> str:
    """The message beside the dispute's frozen prompt: the unit, the probe's
    row and where its inputs are."""

    row = next((one for one in target.decisions if one.id == probe.row), None)
    stated = f"- {row.id} [{row.grade}]: {row.text}" if row is not None else f"- {probe.row}"

    return f"""# Probe {probe.id} of unit {target.id} of {target.spec}

## Its row

{stated}

## What the engine does with your answer

`upheld` leaves the probe blocking the work. A dispute is kept when its `quote` occurs in
the document, its file is new under `{ACCEPTANCE_DIR}/` within {PROBE_CAP} bytes, its
command is one simple command that names that file and no other probe, and the command
exits 0 on this candidate and non-zero, but not 126 or 127 and not because it could not
import a module that tree holds, on the tree the work started from. A module the work
adds is not on that tree, so a counter-probe that imports it fails there as it should. The re-runs read a fresh checkout with
the file written in: no `.venv`, no build output. A kept counter-probe replaces the probe
for every later candidate of the unit.

## Inputs

- `{DISPUTE_DIR}/document/` — the document's files
- `{DISPUTE_DIR}/probe.yaml` — the probe and its runs; its file is at `{probe.file}`
- `{DISPUTE_DIR}/dispute.schema.json` — the shape of your answer
"""


# ----------------------- #
# The record (S-0129/D-5).


def failed_first(state: RunState) -> int | None:
    """How many probes blocked the unit's first critic decision, from the
    fact that decision left; None when no decision has left one."""

    for entry in state.history:
        fact = str(entry.get("fact", ""))

        if fact.startswith(FAILED_FIRST_MARKER):
            return int(fact.removeprefix(FAILED_FIRST_MARKER).split()[0])

    return None


def note_first_decision(state: RunState, blockers: Iterable[Any]) -> None:
    """Leave the first critic decision's count of probe blockers, once."""

    if failed_first(state) is not None:
        return

    count = sum(str(getattr(one, "id", "")).startswith(FINDING_PREFIX) for one in blockers)
    _fact(state, f"{FAILED_FIRST_MARKER}{count}")


def unit_block(root: Path, state: RunState, task_id: str) -> dict[str, Any] | None:
    """`unit.closed`'s `acceptance` block (S-0129/D-5), or None for a unit
    that compiled nothing."""

    stored = load(root, task_id)

    if stored is None:
        return None

    return {
        "kept": len(stored.kept),
        "dropped": len(stored.dropped),
        "failed_first": failed_first(state),
        "spend_usd": stored.spend_usd,
        "wall_s": stored.wall_s,
    }
