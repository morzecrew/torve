"""The acceptance compiler (S-0129): before a unit's first candidate, one
reviewer-seat call turns the unit's document into probes, each a file under
`probes/acceptance/` and a command pinned to one row (S-0129/D-1). The engine
keeps a probe only when it executes red on the tree the unit starts from
(S-0129/D-2) and stores the kept ones once per unit (S-0129/D-3); the critic's
decision re-runs them against every candidate (S-0129/D-4)."""

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


def compile_prompt() -> str:
    """The frozen system prompt, byte for byte as shipped."""

    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_sha() -> str:
    """The sha256 over the frozen prompt and its schema, recorded on every
    compile as the critic's is (S-0122/D-19)."""

    return hashlib.sha256(PROMPT_FILE.read_bytes() + SCHEMA_FILE.read_bytes()).hexdigest()


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


def _held_unimported(tail: str, tree: Path | None) -> str | None:
    """A module *tail* says the probe could not import although *tree*, the
    start tree, holds it, or None. Running a file puts its own folder on the
    import path, not the repository's root, so a probe written as if it did
    cannot see a project that is not installed, on any tree."""

    if tree is None:
        return None

    names: list[str] = _NOT_FOUND.findall(tail)

    for name in names:
        for root in (tree, tree / "src"):
            path = root.joinpath(*name.split("."))

            # A namespace package is a folder of modules with no `__init__`.
            if path.with_suffix(".py").is_file() or (path.is_dir() and any(path.glob("*.py"))):
                return name

    return None


def _base_reason(base: CommandRun | None, tree: Path | None = None) -> str | None:
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
    """The unit's stored compile, or None when it has none: a unit with a
    store never compiles again, whether it kept probes or not."""

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


def _keep_files(copy: Path, document: CompileDocument | None) -> dict[str, str]:
    """The probe files the answer names, read before the copy dies; a path
    outside `probes/acceptance/`, a link or a file over the cap is left."""

    if document is None:
        return {}

    root = copy.resolve()
    kept: dict[str, str] = {}

    for probe in document.probes:
        name = _own_file(probe.file)

        if name is None or name in kept:
            continue

        path = copy / name

        parts = PurePosixPath(name).parts

        try:
            if any(
                copy.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1)
            ):
                continue

            if not path.resolve().is_relative_to(root):
                continue

            if path.is_file() and path.stat().st_size <= PROBE_CAP:
                kept[name] = path.read_text(encoding="utf-8", errors="replace")

        except OSError:
            continue

    return kept


def _fact(state: RunState, fact: str) -> None:
    state.history.append(
        {"at": state.heartbeat, "from": str(state.state), "to": str(state.state), "fact": fact}
    )
    state.save()


def compile_acceptance(
    run: Dispatch,
    state: RunState,
    reproduce: Reproduce,
    price: Callable[[Mapping[str, Any]], float | None],
) -> Acceptance:
    """The unit's probes (S-0129/D-1, S-0129/D-2): its store when it has one,
    else one compile over the tree the unit starts from, kept and stored.

    *reproduce* runs the probes' commands over a judge checkout of that tree;
    *price* turns the seat's agent block into the unit spend it records, and
    returns it. A seat that refuses, errs or answers nothing readable keeps
    nothing, and so does a document that is not there: the store then holds
    the empty compile, so the unit never compiles twice."""

    from torve.application.telemetry import config_hash

    stored = load(run.root, run.task.id)

    if stored is not None:
        return stored

    started = time.monotonic()
    files = _document_files(run.root / run.config.specs.path, run.task.spec)

    if not files:
        acceptance = Acceptance(fact=f"no document to compile for {run.task.spec}")
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
        "rows.yaml": yaml.safe_dump(
            _rows_document(run.task.decisions), sort_keys=False, allow_unicode=True
        ),
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
