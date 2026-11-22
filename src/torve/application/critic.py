"""The critic (S-0122/D-1, S-0122/D-2, S-0122/D-6, S-0122/D-19): one candidate
judged per call, in a fresh context, from evidence the engine measured.

The critic is the classic reviewer's seat with a different input and a
different output. Its input is the diff, the evidence package
(`.torve/review/evidence.yaml` and its logs), the base tree under
`.torve/review/base/`, the rows the unit owns with the LOCKED rows its paths
touch, and the intent. Nothing else the author produced reaches it: the
copy is scrubbed of the engine's per-task files — the log, the pack, the
prompts, the traces, earlier feedback — and the copy carries no `.git`, so no
commit message either (S-0121/D-28, S-0121/D-40). Earlier reviews' findings
are never among its inputs (S-0122/D-6).

Its prompt is frozen: `critic/v1` lives beside this module as a file, goes
in system position, and its sha rides every record (S-0122/D-19). Its output
is `torve.critic/v1` — a verdict per row and findings with a kind, a
severity, evidence and a repro — and the engine, not the critic, turns that
into block or pass (`decide`, S-0122/D-1, S-0127/D-1): a finding that survives
the location filter at or above `review.blocks_at`, and a row `violated` with
evidence that locates, block only when the engine re-runs their repro
against the candidate and it fails; a `gaming` finding and a LOCKED row's
breach block on located evidence alone, a `weak_test` finding only through
a probe its repro names, and `unknown` never does. Whatever does not block
is recorded advisory.

A probe the critic wrote survives only when a surviving finding names it as
its repro; everything else dies with the copy. The engine writes a kept
probe into the re-run's checkout, after the critic's session has ended.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application import threads
from torve.application.dispatch import Dispatch
from torve.application.ports import Agent, Broker, BrokerHandle, Runtime, SandboxSpec
from torve.application.review import (
    STOPS_AT,
    JudgedFinding,
    ReviewOutcome,
    Revision,
    SchemaRefusal,
    _changed_lines,
    _citation_locates,
    close_review,
    elide_diff_bulk,
    elided_prefixes,
    last_document,
    located_spans,
    mapped_blocker_anchors,
    mint_review_task,
    repeats_blocker,
    review_record,
    reviewer_for,
    revision_for,
    run_seat,
    schema_refusal,
    seat_api_error,
    tree_snapshot,
)
from torve.base import naming
from torve.config import layout
from torve.config.manifest import DEFAULT_TEST_RUN, Manifest, TestsConfig, load_manifest
from torve.config.runconfig import RunnerConfig, image_for, tier_for
from torve.domain.attempt import Finding
from torve.domain.task import InheritedDecision, Task
from torve.domain.vocabulary import FindingSeverity
from torve.gates.context import NO_HOOKS, build_context
from torve.gates.evidence import CITATION, citations, filter_findings, locate
from torve.gates.evidence_package import (
    CommandRun,
    EvidencePackage,
    build_evidence,
    package_rows,
    render_evidence,
)
from torve.gates.suite import archive_tree

if TYPE_CHECKING:
    from torve.application.acceptance import Probe
    from torve.application.runstate import RunState
    from torve.application.threads import TriageDocument

# ----------------------- #

PROMPT_VERSION = "critic/v1"
SCHEMA = "torve.critic/v1"

_PROMPTS = Path(__file__).with_name("prompts")
PROMPT_FILE = _PROMPTS / "critic-v1.md"
SCHEMA_FILE = _PROMPTS / "critic-v1.schema.json"

#: Where the critic's inputs sit in its copy, as the frozen prompt names them.
REVIEW_DIR = ".torve/review"

#: The engine's own per-task files under `.torve/` — what `torve init` lists
#: as torve's alone to write, plus the feedback a revision is handed. Every
#: one is either the author's voice (the log, the feedback, the prompt) or
#: a trace of its session, and none reaches the critic (S-0122/D-2).
SCRUBBED = (
    "tasks",
    "context",
    "regimes",
    "traces",
    "skills",
    "tmp",
    "review",
    "telemetry.jsonl",
    "feedback.jsonl",
    "feedback.md",
)

#: The largest probe a finding may keep, in bytes.
PROBE_CAP = 64_000

#: The most files the critic's copy keeps under `probes/` beside the ones its
#: findings name: the modules those probes import (S-0127/D-10).
HELPER_CAP = 8

#: How long the host may spend extracting the base tree into the copy.
BASE_TIMEOUT = 120.0


# ....................... #


def critic_prompt() -> str:
    """The frozen system prompt, byte for byte as shipped."""

    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_sha() -> str:
    """The sha256 over the frozen prompt and the schema it names, recorded on
    every critic review so a verdict is always read beside the words that
    asked for it (S-0122/D-19)."""

    return hashlib.sha256(PROMPT_FILE.read_bytes() + SCHEMA_FILE.read_bytes()).hexdigest()


# ----------------------- #
# torve.critic/v1


# The critic reads these docstrings: they are the descriptions of the schema
# staged beside its prompt, and change with it (S-0122/D-19).
_ANSWER = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class RowVerdict(BaseModel):
    """Your verdict on one row you were given."""

    model_config = _ANSWER

    row: str
    """The row's id exactly as given, `S-NNNN/D-n`."""
    verdict: Literal["holds", "violated", "unknown"]
    """holds, violated, or unknown when you could not establish either."""
    evidence: str = ""
    """`path:line — one sentence`, or a backticked command followed by what it printed."""
    note: str = ""
    """For unknown, what you could not establish and why."""


class Repro(BaseModel):
    """How to see the finding for yourself."""

    model_config = _ANSWER

    command: str
    """The command that shows it, run from the workspace root."""
    file: str | None = None
    """The probe it runs, as a path under probes/; kept only when named here."""


class CriticFinding(BaseModel):
    """One finding."""

    model_config = _ANSWER

    id: str
    """Short and unique within this answer, e.g. F1."""
    severity: FindingSeverity
    kind: Literal["bug", "regression", "weak_test", "contract", "scope", "gaming", "style"]
    row: str | None = None
    """The row this finding is about, when it is about one."""
    claim: str
    """What is wrong, in one or two sentences."""
    evidence: str
    """`path:line — one sentence` against the tree as it arrived, or a backticked
    command followed by what it printed. Evidence that does not locate is discarded."""
    verified: Literal["executed", "read"]
    """executed when a command you ran showed it; read when you only read the code."""
    confidence: Literal["high", "medium", "low"]
    repro: Repro | None = None
    fix_hint: str = ""
    """Where a fix belongs, in a sentence; never a patch."""


class Contradiction(BaseModel):
    """Your judgment of the contradiction the author flagged, if any."""

    model_config = _ANSWER

    verdict: Literal["valid", "invalid"]
    evidence: str
    note: str = ""


class CriticDocument(BaseModel):
    """torve.critic/v1: the one JSON document your answer ends with."""

    model_config = ConfigDict(extra="ignore", use_attribute_docstrings=True, populate_by_name=True)

    schema_name: Literal["torve.critic/v1"] = Field(default="torve.critic/v1", alias="schema")
    rows: list[RowVerdict]
    """One verdict per owned row."""
    findings: list[CriticFinding]
    """Empty is a valid, complete review."""
    contradiction: Contradiction | None = None
    """Set only when the evidence carries an author_claim."""
    unknowns: list[str] = Field(default_factory=list)
    """What you could not establish about the change as a whole, and why."""


def critic_schema() -> dict[str, Any]:
    """The JSON schema the critic's answer is checked against, as staged."""

    return CriticDocument.model_json_schema(by_alias=True)


def parse_critic(output: str) -> CriticDocument | None:
    """The last `torve.critic/v1` document in the output (the last object
    carrying `findings`), validated; None when there is none. A document the
    model refuses raises `SchemaRefusal` naming the field (S-0054/D-15)."""

    last = last_document(output)

    if last is None:
        return None

    try:
        return CriticDocument.model_validate(last)

    except ValidationError as exc:
        raise SchemaRefusal(schema_refusal(exc, "critic document")) from None


def read_critic(output: str) -> tuple[CriticDocument | None, str | None]:
    try:
        return parse_critic(output), None

    except SchemaRefusal as exc:
        return None, str(exc)


# ----------------------- #
# The engine decides (S-0122/D-1, S-0127/D-1).

#: How the engine re-runs a repro against the candidate (S-0127/D-1): its
#: commands, and each probe path the repro names mapped to the text the
#: critic's copy kept of it or None, to one `CommandRun` per command —
#: `runner.reproduce` over a run, or the corpus replay's own sandbox.
Reproduce = Callable[[Sequence[str], Mapping[str, str | None]], list[CommandRun]]

#: A repro's exits that measured nothing of the candidate: the shell could
#: not run the command (S-0127/D-1).
NOT_RUNNABLE = frozenset({126, 127})

#: What pytest prints, by its exit, when a selector finds nothing in the tree
#: it runs on (S-0128/D-3, S-0128/A-5): a test id or file the revision renamed
#: or moved, as a fix that adds lines renames a case keyed by its line span,
#: exits 4 beside its `not found` line, and a selection left empty exits 5
#: beside its whole summary line. pytest has then run no test of the candidate.
_SELECTED_NOTHING = {
    4: re.compile(r"^ERROR: (?:file or directory )?not found: ", re.M),
    5: re.compile(r"^[= ]*(?:no tests ran|\d+ deselected)(?: in [\d.]+s)?[= ]*$", re.M),
}

#: The one exit a `weak_test` probe blocks with (S-0127/D-2): the changed test
#: passed the wrong implementation the probe built. A crashed probe exits 1,
#: and no interpreter, test runner or shell gives this code on its own.
WRONG_PASSES = 42

#: A path under the probe directory a command names, which the re-run must
#: be handed from the critic's copy or refuse (S-0127/D-10). It ends at a
#: colon, so a pytest selector's `::test_bug` is not read into the path.
_NAMED_PROBE = re.compile(r"(?<![\w./-])(?:\./)?(probes/[^\s'\"`;|&<>():]+)")


@dataclass(frozen=True)
class Decision:
    """What the engine made of a critic document: the findings that survived
    the location filter, the ones that block, the rows that block, and every
    row's verdict as recorded — a row the critic did not answer is `unknown`.

    On a revision attempt (S-0125/D-1) `carried` is the kept findings outside
    the revision's own changed lines and its earlier blockers, and `reraised`
    the blockers and rows that repeat an earlier round's (S-0127/D-4). On a
    review round's attempt (S-0126/D-1) `rejected` is the carried findings
    whose evidence disputes a claim the attempt rejected; they are marked in
    the record.

    `advisory` is every kept `weak_test` finding that does not block and every
    kept finding whose re-run did not fail, and `advisory_rows` every located `violated` row
    other than a LOCKED one, which has no repro to re-run (S-0127/D-1,
    S-0127/A-5, S-0127/D-2); `reruns` is each re-run the engine made, beside
    the finding it measured. `acceptance` is each compiled probe's re-run
    (S-0129/D-4): its id, row, file and exit, and whether it `failed`,
    `passed` or was `unmeasured`; a failed one is among the blockers."""

    blocked: bool
    blockers: list[CriticFinding]
    violated: list[RowVerdict]
    kept: list[CriticFinding]
    discarded: list[str]
    rows: list[dict[str, Any]]
    carried: list[CriticFinding] = field(default_factory=list)
    reraised: list[CriticFinding | RowVerdict] = field(default_factory=list)
    rejected: list[CriticFinding] = field(default_factory=list)
    advisory: list[CriticFinding] = field(default_factory=list)
    advisory_rows: list[RowVerdict] = field(default_factory=list)
    reruns: list[tuple[CriticFinding, CommandRun]] = field(default_factory=list)
    acceptance: list[dict[str, Any]] = field(default_factory=list)

    def rerun(self, one: CriticFinding | RowVerdict) -> CommandRun | None:
        """The engine's re-run of *one*, or None when it made none."""

        return next((run for judged, run in self.reruns if judged is one), None)


def _finding_anchor(evidence: str, root: Path) -> tuple[str, int] | None:
    """The `path` and line a finding is anchored at (S-0127/D-4): the first
    citation in its *evidence* that resolves under *root*, wherever it sits,
    or None for evidence that cites none that does — a backticked command
    with its output.

    The path is normalised the way `locate` resolves it, so `./src/app.py:12`
    and `src/app.py:12` name the same file when the revision's diff is read
    (S-0125/D-1)."""

    spans = located_spans(evidence, root)

    return (spans[0][0], spans[0][1]) if spans else None


def _anchor_changed(anchor: tuple[str, int], changed: Mapping[str, set[int]]) -> bool:
    """Whether *anchor* sits within `threads.WINDOW` lines of a line the
    revision changed in its file."""

    path, line = anchor
    lines = changed.get(path)

    return lines is not None and any(abs(number - line) <= threads.WINDOW for number in lines)


def _anchor_near(anchor: tuple[str, int], previous: list[dict[str, Any]]) -> bool:
    """Whether *anchor* sits near an earlier blocker's (S-0125/D-1): the same
    file within `threads.WINDOW` lines, or the same file when the recorded
    anchor carries no line. Near is what lets a revision's finding count;
    whether it repeats that blocker is `repeats_blocker`'s to say
    (S-0127/D-4). *previous* is already in the current tree's coordinates:
    `revision_for` maps each round's anchors from the head that round
    judged."""

    path, line = anchor

    for one in previous:
        recorded_path = one.get("path")

        if not isinstance(recorded_path, str) or posixpath.normpath(recorded_path) != path:
            continue

        recorded = one.get("line")

        if recorded is not None:
            try:
                recorded = int(recorded)

            except (TypeError, ValueError):
                continue

        if recorded is None or abs(recorded - line) <= threads.WINDOW:
            return True

    return False


def _repro_command(finding: CriticFinding) -> str:
    """The command a finding's re-run runs (S-0127/D-1, S-0127/A-5): its
    `repro.command` alone. A command its evidence shows is never run: a
    backticked span may as well be code, which a shell refuses with an exit
    that would block."""

    return finding.repro.command.strip() if finding.repro is not None else ""


def _probe_names(command: str, named: str | None) -> list[str]:
    """The probes a re-run of *command* names (S-0127/D-10): the one its
    repro names, and each path under `probes/` the command itself names; a
    critic finding's re-run gets `_helpers` beside them. A probe the critic's
    copy did not keep is handed as None, which refuses the re-run, so a
    command never fails for want of a file the critic wrote."""

    names = [named] if named else []

    for found in _NAMED_PROBE.finditer(command):
        path = posixpath.normpath(found.group(1))

        if all(posixpath.normpath(name) != path for name in names):
            names.append(path)

    return names


def _helpers(probes: Mapping[str, str], findings: Iterable[CriticFinding]) -> dict[str, str]:
    """The files among *probes* that none of *findings* names: the modules
    the critic's probes import, written beside every critic finding's re-run
    so a probe meets what it ran with in the critic's copy (S-0127/D-10). A
    compiled probe is never handed them; it runs alone (S-0129/D-2)."""

    named = {
        name
        for finding in findings
        if finding.repro is not None
        for name in _probe_names(finding.repro.command, finding.repro.file)
    }

    return {name: text for name, text in probes.items() if name not in named}


def _rerun_once(
    reproduce: Reproduce | None, command: str, probes: Mapping[str, str | None]
) -> CommandRun:
    """One re-run of *command* (S-0127/D-1). An engine that could not re-run
    it — none given, or one whose checkout or sandbox would not open —
    measured nothing of the candidate, so it reads as not run and never
    blocks; the review goes on."""

    if reproduce is None:
        return CommandRun(command=command, exit_code=None, tail="not run: no engine re-runs here")

    try:
        (run,) = reproduce([command], probes)

    except Exception as exc:
        return CommandRun(command=command, exit_code=None, tail=f"not run: {exc!r}")

    return run


def _failed(run: CommandRun) -> bool:
    """Whether a re-run measured the candidate failing (S-0127/D-1): any exit
    but 0, a timeout, a refused or unrun repro, or a command the shell could
    not run."""

    return run.exit_code is not None and run.exit_code != 0 and run.exit_code not in NOT_RUNNABLE


def _near_rejected(evidence: str, rejected: Iterable[tuple[str, int, int]], root: Path) -> bool:
    """Whether *evidence* disputes a rejected claim (S-0126/D-1): it cites at
    least one `path:line`, every citation locates in *root*, and every one
    lies within `threads.WINDOW` lines of a rejected span in the same file.

    A citation that does not locate keeps the finding under the ordinary
    rules, because it may name a separate defect, and evidence with no
    citation is not carried this way either, because nothing ties it to the
    claim."""

    cited = citations(evidence, root)

    if not cited:
        return False

    spots = [(posixpath.normpath(path), start, end) for path, start, end in rejected]

    for path, start, end in cited:
        if not _citation_locates(path, start, end, root):
            return False

        normalised = posixpath.normpath(path)

        if not any(
            normalised == spot_path
            and spot_start - threads.WINDOW <= start
            and end <= spot_end + threads.WINDOW
            for spot_path, spot_start, spot_end in spots
        ):
            return False

    return True


def rejected_spots(task: Task, worktree: Path, attempt: int) -> list[tuple[str, int, int]]:
    """The spans a review round's *attempt* rejected (S-0126/D-2): none unless
    the contract carries a `round`, otherwise each rejection the worktree's
    task log holds for *attempt* contributes the span of every round finding
    its claim names, the round's one finding when the claim names none, and
    every citation in its evidence that locates under *worktree*.

    The engine reads these; they are never staged for the critic, whose input
    stays byte-for-byte what it was (S-0121/D-28, S-0122/D-2)."""

    round_ = task.round

    if round_ is None:
        return []

    from torve.application.divergence import open_log

    entries = open_log(worktree, task.id).get("entries") or []
    findings = round_.findings
    spans: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()

    def add(span: tuple[str, int, int]) -> None:
        if span not in seen:
            seen.add(span)
            spans.append(span)

    for rejection in threads.rejections(list(entries), attempt):
        claim = str(rejection.get("claim", ""))
        named = [
            finding
            for finding in findings
            if threads.names_spot(claim, (finding.path, finding.line))
        ]

        if not named and len(findings) == 1:
            named = list(findings)

        for finding in named:
            if finding.line is not None:
                add(
                    (
                        posixpath.normpath(finding.path),
                        finding.line,
                        finding.end_line or finding.line,
                    )
                )

        for path, start, end in citations(str(rejection.get("evidence", "")), worktree):
            if _citation_locates(path, start, end, worktree):
                add((posixpath.normpath(path), start, end))

    return spans


def decide(
    document: CriticDocument,
    rows: Mapping[str, bool],
    root: Path,
    blocks_at: Literal["blocker", "major"],
    revision: Revision | None = None,
    rejected: Iterable[tuple[str, int, int]] = (),
    *,
    locked: Iterable[str] = (),
    reproduce: Reproduce | None = None,
    probes: Mapping[str, str] | None = None,
    earlier: Iterable[Mapping[str, Any]] | None = None,
    acceptance: Sequence[Probe] = (),
    revisions_left: bool = True,
) -> Decision:
    """Block or pass, from what ran and never from the critic's words
    (S-0122/D-1, S-0127/D-1, both LOCKED).

    *rows* maps each row the critic was given to whether the unit owns it; a
    row not owned is a LOCKED row the diff touches, given as a constraint,
    and binds as one, and *locked* names the owned rows graded LOCKED.
    `unknown` never blocks, and neither does a verdict on a row nobody gave
    it.

    The carries are read first, and a carried finding is never re-run.
    *rejected* is the spans a review round's attempt rejected (S-0126/D-1): a
    kept finding or a `violated` row whose every citation locates within
    `threads.WINDOW` lines of one of them is carried non-blocking, because
    the critic cannot see the rejection and only the engine can tell that the
    finding disputes it. On a revision attempt (S-0125/D-1) a kept finding
    counts only when its anchor sits near a line the revision changed or an
    earlier blocker's; every other kept finding, anchorless ones included, is
    carried.

    What is left blocks by what ran (S-0127/D-1, S-0127/D-2). A `gaming`
    finding at or above *blocks_at*, and a LOCKED row `violated` with evidence
    that locates, block on that evidence alone. A `weak_test` finding whose
    repro names no probe is never re-run: only a probe can hold the wrong
    implementation the changed test still passes. One that names a probe
    blocks only when the re-run exits `WRONG_PASSES` and *revisions_left*
    says a revision is left to send it to (S-0127/A-14): with none it is
    recorded advisory, so a weak test spends revisions but never escalates a
    unit, and it is never a re-raise. Any other finding at or
    above *blocks_at*
    blocks only when *reproduce* re-runs its `repro.command` against the
    candidate, with the probes *probes* kept for it, and the command exits
    non-zero. An exit of 0, 126 or 127, a timeout, a refused repro, a re-run
    that could not happen and no `repro` are recorded advisory; with no
    *reproduce*, nothing is re-run and all of them are. Any other `violated`
    row whose evidence locates is recorded advisory: a row carries no repro,
    so its breach blocks through a finding that names it (S-0127/A-5).

    Each of *acceptance*, the unit's compiled probes (S-0129/D-4), is re-run
    with the text the unit stored, whatever the document says: one whose
    re-run fails blocks as a `contract` finding of severity `blocker` on its
    row, at any *blocks_at* and on a revision attempt alike, and is never
    carried, since it is the engine's measurement and not the critic's claim.

    A blocker or a blocking row is re-raised when it repeats an earlier
    round's blocker (`repeats_blocker`, S-0127/D-4): one of *earlier*, every
    round's blockers in the current tree's coordinates, else of the
    revision's own."""

    kept, discarded = filter_findings(document.findings, root)
    stopping = STOPS_AT[blocks_at]
    spots = list(rejected)
    # The stored text wins over any copy of a probe the critic kept.
    held = {**(probes or {}), **{probe.file: probe.text for probe in acceptance}}
    lock = set(locked)
    carried: list[CriticFinding] = []
    rejected_findings: list[CriticFinding] = []
    reruns: list[tuple[CriticFinding, CommandRun]] = []
    # One review re-runs one command over the same probes once.
    measured: dict[tuple[str, frozenset[tuple[str, str | None]]], CommandRun] = {}
    helpers = _helpers(probes or {}, document.findings)

    def measure(command: str, named: str | None, alone: bool = False) -> CommandRun:
        """*command* re-run with the probes it names, and the critic's
        helpers unless it runs *alone*, once per review."""

        if not command:
            return CommandRun(command="", exit_code=None, tail="not run: no command to re-run")

        given = {name: held.get(name) for name in _probe_names(command, named)}
        given = given if alone else {**helpers, **given}
        key = (command, frozenset(given.items()))

        if key not in measured:
            measured[key] = _rerun_once(reproduce, command, given)

        return measured[key]

    def rerun(one: CriticFinding, command: str, named: str | None) -> CommandRun:
        """*one*'s *command* re-run with the probes it names, and recorded."""

        run = measure(command, named)
        reruns.append((one, run))

        return run

    if spots:
        for finding in kept:
            if _near_rejected(finding.evidence, spots, root):
                carried.append(finding)
                rejected_findings.append(finding)

    disputed = {id(finding) for finding in rejected_findings}
    counted: list[CriticFinding] = []

    for finding in kept:
        if id(finding) in disputed:
            continue

        if revision is None:
            if finding.severity in stopping:
                counted.append(finding)

            continue

        anchor = _finding_anchor(finding.evidence, root)

        if finding.severity not in stopping or anchor is None:
            carried.append(finding)
        elif _anchor_near(anchor, revision.blockers) or _anchor_changed(anchor, revision.changed):
            counted.append(finding)
        else:
            carried.append(finding)

    blockers: list[CriticFinding] = []
    unreproduced: set[int] = set()

    for finding in counted:
        if finding.kind == "gaming":
            # S-0127/D-1: integrity rarely reproduces by a command, so what
            # locates blocks, as it always did.
            blockers.append(finding)
        elif finding.kind == "weak_test":
            # S-0127/D-2: a test that proves too little blocks only when a
            # probe says it passed a wrong implementation; one only read is
            # advisory unrun. S-0127/A-14: it blocks to buy a revision, so
            # with none left it lands advisory over code no defect was shown in.
            named = finding.repro.file if finding.repro else None

            if not _probe_names(_repro_command(finding), named):
                continue

            exit_code = rerun(finding, _repro_command(finding), named).exit_code

            if exit_code == WRONG_PASSES and revisions_left:
                blockers.append(finding)
            else:
                unreproduced.add(id(finding))
        elif _failed(
            rerun(finding, _repro_command(finding), finding.repro.file if finding.repro else None)
        ):
            blockers.append(finding)
        else:
            unreproduced.add(id(finding))

    blocked_ids = {id(finding) for finding in blockers}
    advisory = [
        finding
        for finding in kept
        if id(finding) in unreproduced
        or (finding.kind == "weak_test" and id(finding) not in blocked_ids)
    ]
    probe_runs: list[dict[str, Any]] = []

    for probe in acceptance:
        run = measure(probe.command, probe.file, alone=True)
        outcome = "failed" if _failed(run) else "passed" if run.exit_code == 0 else "unmeasured"
        probe_runs.append(
            {
                "id": probe.id,
                "row": probe.row,
                "file": probe.file,
                "exit": run.exit_code,
                "outcome": outcome,
            }
        )

        if outcome != "failed":
            continue

        compiled = _probe_finding(probe, run)
        reruns.append((compiled, run))
        blockers.append(compiled)
        kept.append(compiled)

    def weight(verdict: RowVerdict) -> tuple[bool, bool, bool]:
        located = bool(verdict.evidence) and locate(verdict.evidence, root) is None
        # S-0126/D-1: a verdict disputing a rejected claim must not outrank an
        # independent one for the same row, or the reduction hides the defect
        # that still blocks.
        rejected = bool(spots) and located and _near_rejected(verdict.evidence, spots, root)
        return verdict.verdict == "violated", located, not rejected

    # A row answered twice keeps its gravest verdict: a later `holds` never
    # erases a located `violated`, and a rejected `violated` never erases an
    # independent one.
    answered = {verdict.row: verdict for verdict in sorted(document.rows, key=weight)}
    violated: list[RowVerdict] = []
    advisory_rows: list[RowVerdict] = []
    recorded: list[dict[str, Any]] = []

    for row, owned in rows.items():
        verdict = answered.get(row)

        if verdict is None:
            recorded.append({"row": row, "owned": owned, "verdict": "unknown", "answered": False})
            continue

        located = bool(verdict.evidence) and locate(verdict.evidence, root) is None
        # S-0126/D-1: a violated row on a rejected claim is recorded
        # non-blocking, never counted as a violation.
        rejected_row = (
            verdict.verdict == "violated"
            and located
            and bool(spots)
            and _near_rejected(verdict.evidence, spots, root)
        )
        breach = verdict.verdict == "violated" and located and not rejected_row
        # S-0127/D-1 (S-0127/A-5): a LOCKED row's breach blocks on located
        # evidence alone, the owner's rules rarely reproducing by a command;
        # any other row carries no repro to re-run, so its breach is advisory
        # and blocks only through a finding that names the row and reproduces.
        blocks = breach and not (owned and row not in lock)

        if blocks:
            violated.append(verdict)

        elif breach:
            advisory_rows.append(verdict)

        elif verdict.verdict == "violated" and not rejected_row:
            discarded.append(f"row {row}: violated without evidence that locates")

        entry = {
            "row": row,
            "owned": owned,
            "verdict": verdict.verdict,
            "located": located,
            "blocks": blocks,
        }

        if rejected_row:
            entry["rejected"] = True

        if breach and not blocks:
            entry["advisory"] = True

        recorded.append(entry)

    reraised: list[CriticFinding | RowVerdict] = []
    recorded_blockers: list[Mapping[str, Any]] = []

    if earlier is not None:
        recorded_blockers = list(earlier)
    elif revision is not None:
        recorded_blockers = list(revision.blockers)

    if recorded_blockers:
        # S-0127/A-14: a weak test repeated spends another revision, never
        # the escalation a re-raise is.
        blocking: list[CriticFinding | RowVerdict] = [
            *(one for one in blockers if one.kind != "weak_test"),
            *violated,
        ]

        for one in blocking:
            kind, named, claim = (
                ("contract", one.row, _row_claim(one))
                if isinstance(one, RowVerdict)
                else (one.kind, one.row, one.claim)
            )

            if repeats_blocker(
                kind, named, located_spans(one.evidence, root), claim, recorded_blockers
            ):
                reraised.append(one)

    return Decision(
        blocked=bool(blockers or violated),
        blockers=blockers,
        violated=violated,
        kept=kept,
        discarded=discarded,
        rows=recorded,
        carried=carried,
        reraised=reraised,
        rejected=rejected_findings,
        advisory=advisory,
        advisory_rows=advisory_rows,
        reruns=reruns,
        acceptance=probe_runs,
    )


def _probe_finding(probe: Probe, run: CommandRun) -> CriticFinding:
    """A compiled probe whose re-run failed, as the blocker it is (S-0129/D-4):
    the engine measured it, so it is `executed` at `high` confidence, and its
    repro is the probe the recheck re-runs (S-0128/D-3)."""

    return CriticFinding(
        id=probe.id,
        severity="blocker",
        kind="contract",
        row=probe.row,
        claim=f"{probe.claim} The document: “{probe.quote}”",
        evidence=f"`{probe.command}` exited {run.exit_code}: {run.tail}",
        verified="executed",
        confidence="high",
        repro=Repro(command=probe.command, file=probe.file),
        fix_hint="The probe was compiled from the document before the work began.",
    )


# ----------------------- #
# What the critic's verdict carries back.


@dataclass
class CriticVerdict(ReviewOutcome):
    """A review outcome read from a critic document: `blockers` and `kept`
    are the classic shape the run loop already acts on — a violated row
    becomes a blocking finding there — and the rest is what the record and
    the revision need."""

    decision: Decision | None = None
    document: CriticDocument | None = None
    prompt_sha: str = ""
    # A kept probe's text by its path in the copy, for the revision to read.
    probes: dict[str, str] = field(default_factory=dict)
    # What the call cost the unit (S-0123/D-1): the seat's reported cost, the
    # counts it reported, and the model they were spent on — the facts the
    # notional-spend cap prices.
    cost_usd: float | None = None
    token_counts: dict[str, int] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None


def _measured(one: CriticFinding | RowVerdict, decision: Decision, root: Path) -> dict[str, Any]:
    """What the engine read of *one* (S-0127/D-4, S-0127/D-14): its anchor in
    *root* and the command and exit code of its re-run, if it had one."""

    anchor = _finding_anchor(one.evidence, root)
    run = decision.rerun(one)

    return {
        "path": anchor[0] if anchor else "",
        "line": anchor[1] if anchor else None,
        "rerun_command": run.command if run is not None else "",
        "rerun_exit": run.exit_code if run is not None else None,
    }


def _as_finding(finding: CriticFinding, decision: Decision, root: Path) -> JudgedFinding:
    return JudgedFinding(
        severity=finding.severity,
        claim=finding.claim,
        evidence=finding.evidence,
        kind=finding.kind,
        row=finding.row,
        **_measured(finding, decision, root),
    )


def _recorded_findings(decision: Decision) -> list[dict[str, Any]]:
    """The kept findings as the review record carries them, each one a
    revision carried (S-0125/D-1) or a review round's rejected claim
    (S-0126/D-1) marked non-blocking, each one the engine did not block
    marked advisory, and each re-run beside the finding it measured
    (S-0127/D-1)."""

    carried = {id(finding) for finding in decision.carried}
    rejected = {id(finding) for finding in decision.rejected}
    advisory = {id(finding) for finding in decision.advisory}
    recorded: list[dict[str, Any]] = []

    for finding in decision.kept:
        one = finding.model_dump(exclude_none=True)
        run = decision.rerun(finding)

        if id(finding) in carried:
            one["carried"] = True

        if id(finding) in rejected:
            one["rejected"] = True

        if id(finding) in advisory:
            one["advisory"] = True

        if run is not None:
            one["rerun"] = asdict(run)

        recorded.append(one)

    return recorded


def verdict_findings(
    decision: Decision, grades: Mapping[str, str], root: Path
) -> tuple[list[Finding], list[Finding]]:
    """A decision's blockers and kept findings in the classic shape the run
    loop acts on, in the decision's own order: its findings first, then its
    rows, a violated row a finding of kind `contract`, blocking or advisory as
    the decision said (S-0127/D-1, S-0127/D-14). `recheck` reads a verdict's
    findings back in this order."""

    violated: list[Finding] = [
        _row_finding(verdict, grades, decision, root) for verdict in decision.violated
    ]
    advisory_rows: list[Finding] = [
        _row_finding(verdict, grades, decision, root) for verdict in decision.advisory_rows
    ]
    blockers: list[Finding] = [_as_finding(one, decision, root) for one in decision.blockers]
    kept: list[Finding] = [_as_finding(one, decision, root) for one in decision.kept]

    return blockers + violated, kept + violated + advisory_rows


def _row_claim(verdict: RowVerdict) -> str:
    return f"{verdict.row} is violated" + (f": {verdict.note}" if verdict.note else "")


def _row_finding(
    verdict: RowVerdict, grades: Mapping[str, str], decision: Decision, root: Path
) -> JudgedFinding:
    """A violated row in the classic shape: a LOCKED row's breach is a
    blocker, any other row's a major — the critic's own calibration — and its
    kind `contract` (S-0127/D-14)."""

    severity: FindingSeverity = "blocker" if grades.get(verdict.row) == "LOCKED" else "major"

    return JudgedFinding(
        severity=severity,
        claim=_row_claim(verdict),
        evidence=verdict.evidence,
        kind="contract",
        row=verdict.row,
        **_measured(verdict, decision, root),
    )


def _rerun_lines(run: CommandRun | None) -> list[str]:
    """What a blocker's thread says of the engine's re-run (S-0127/D-1): the
    command, its exit code and its output, so the next attempt reads the
    failure rather than the critic's opinion of it."""

    if run is None:
        return []

    return [
        "",
        f"the engine re-ran `{run.command}` on this candidate: exit {run.exit_code}",
        "```",
        run.tail.rstrip(),
        "```",
    ]


def revision_threads(verdict: CriticVerdict, root: Path) -> list[dict[str, Any]]:
    """The blocking findings and rows as the feedback record's threads
    (S-0122/D-8): each claim with its evidence, its repro and the probe that
    shows it, the engine's re-run of it (S-0127/D-1), anchored at its first
    citation that resolves in *root* (S-0127/D-4). Never the critic's trace,
    which no revision reads."""

    decision = verdict.decision
    threads: list[dict[str, Any]] = []

    if decision is None:
        return threads

    helpers = _helpers(verdict.probes, decision.kept)
    compiled = {entry["id"] for entry in decision.acceptance}

    for finding in decision.blockers:
        lines = [finding.claim, "", finding.evidence, ""]
        lines.append(
            f"kind: {finding.kind} · severity: {finding.severity} · verified: {finding.verified}"
        )

        if finding.repro is not None:
            lines.append(f"repro: `{finding.repro.command}`")

            # Every probe the re-run was handed, the ones the command alone
            # names and the critic's helpers included: a weak test's revision
            # reads the wrong implementation its test passed (S-0127/D-2).
            for name in _probe_names(finding.repro.command, finding.repro.file):
                probe = verdict.probes.get(name)

                if probe is not None:
                    lines += ["", f"{name}:", "```", probe.rstrip(), "```"]

            if finding.id not in compiled:
                for name, helper in sorted(helpers.items()):
                    lines += ["", f"{name}:", "```", helper.rstrip(), "```"]

        lines += _rerun_lines(decision.rerun(finding))

        if finding.fix_hint:
            lines += ["", f"fix: {finding.fix_hint}"]

        threads.append(_anchored(finding.evidence, verdict.review_id, "\n".join(lines), root))

    for row in decision.violated:
        lines = [_row_claim(row), "", row.evidence]
        threads.append(_anchored(row.evidence, verdict.review_id, "\n".join(lines), root))

    return threads


def _anchored(evidence: str, author: str, body: str, root: Path) -> dict[str, Any]:
    thread: dict[str, Any] = {"comments": [{"author": author, "body": body}]}
    anchor = _finding_anchor(evidence, root)

    if anchor is not None:
        thread["path"], thread["line"] = anchor

    return thread


# ----------------------- #
# A session turn judged by its blockers' re-runs (S-0128/D-3).


def _cleared(finding: CriticFinding, run: CommandRun) -> bool | None:
    """What *run* measured of *finding* after a revision: True when it ran
    and shows the finding fixed — exit 0, or anything but `WRONG_PASSES` for a
    `weak_test` (S-0127/D-2) — False when it ran and still fails, and None
    when it measured nothing: no exit, 126 or 127 (S-0127/D-1), a pytest
    whose selector found no test in the revised tree, or a `weak_test`
    probe's `WRONG_PASSES`, since the probe runs the tests the review named
    and a revision may have added the one that catches it."""

    if run.exit_code is None or run.exit_code in NOT_RUNNABLE:
        return None

    selected_nothing = _SELECTED_NOTHING.get(run.exit_code)

    if selected_nothing is not None and selected_nothing.search(run.tail):
        return None

    if finding.kind == "weak_test":
        return None if run.exit_code == WRONG_PASSES else True

    return run.exit_code == 0


def recheck(
    verdict: CriticVerdict,
    reproduce: Reproduce | None,
    root: Path,
    acceptance: Sequence[Probe] = (),
) -> CriticVerdict:
    """The verdict a session revision turn is judged by (S-0128/D-3):
    *verdict*, the one the turn was handed, with each finding that blocked it
    re-run against the revised tree at *root* — its `repro.command`, with the
    probes *verdict* stored — and the critic asked nothing.

    A blocker the re-run shows fixed is cleared; one it shows still failing
    is re-raised (S-0127/D-4); one it measured nothing of — no exit, 126 or
    127, a pytest that selected no test, a probe the review did not store, a
    `weak_test` probe's `WRONG_PASSES` — stays a blocker. A `gaming`
    finding and a violated row blocked on located evidence alone
    (S-0127/D-1), so no re-run measures them and they stay too. The result's
    `blockers` are every blocker not cleared: none is a clean turn, a
    re-raise a blocked one, and any other a turn the critic must read.

    Each re-run blocker, in `blockers` and `kept` alike, carries the new exit
    as `rerun_exit`, and a cleared one the exit that made it block as
    `blocked_exit` (S-0128/D-4). *verdict*'s findings are read in the order
    `verdict_findings` built them, which is how each one here finds its own.

    Every one of *acceptance*, the unit's compiled probes, that was not a
    blocker is re-run too, with its stored text (S-0129/D-4): the turn may
    break one that passed. One that now fails is a measured failure, so it
    stands among the re-raised and the turn is blocked."""

    decision = verdict.decision

    if decision is None:
        return verdict

    measured: dict[tuple[str, frozenset[tuple[str, str | None]]], CommandRun] = {}
    reruns: list[tuple[CriticFinding, CommandRun]] = []
    reraised: list[CriticFinding | RowVerdict] = []
    standing: list[CriticFinding] = []
    judged = dict(zip(map(id, decision.blockers), verdict.blockers, strict=False))
    rechecked: dict[int, JudgedFinding] = {}
    helpers = _helpers(verdict.probes, decision.kept)
    compiled = {entry["id"] for entry in decision.acceptance}

    for finding in decision.blockers:
        if finding.kind == "gaming":
            standing.append(finding)
            continue

        command = _repro_command(finding)
        given = {
            name: verdict.probes.get(name)
            for name in _probe_names(command, finding.repro.file if finding.repro else None)
        }
        given = given if finding.id in compiled else {**helpers, **given}
        key = (command, frozenset(given.items()))

        if key not in measured:
            measured[key] = (
                _rerun_once(reproduce, command, given)
                if command
                else CommandRun(command="", exit_code=None, tail="not run: no command to re-run")
            )

        run = measured[key]
        before = decision.rerun(finding)
        reruns.append((finding, run))
        cleared = _cleared(finding, run)
        # Only a cleared blocker carries `blocked_exit`: its readers list it
        # as fixed and raise no thread for it (S-0128/D-4, D-5).
        rechecked[id(finding)] = _as_finding(finding, decision, root).model_copy(
            update={
                "rerun_command": run.command,
                "rerun_exit": run.exit_code,
                "blocked_exit": (
                    before.exit_code if cleared is True and before is not None else None
                ),
            }
        )

        if cleared is not True:
            standing.append(finding)

        if cleared is False:
            reraised.append(finding)

    handed = {finding.id for finding in decision.blockers}
    broken: list[CriticFinding] = []
    probes = dict(verdict.probes)

    for probe in acceptance:
        if probe.id in handed:
            continue

        given = {probe.file: probe.text}
        key = (probe.command, frozenset(given.items()))

        if key not in measured:
            measured[key] = _rerun_once(reproduce, probe.command, given)

        run = measured[key]

        if not _failed(run):
            continue

        finding = _probe_finding(probe, run)
        reruns.append((finding, run))
        standing.append(finding)
        reraised.append(finding)
        broken.append(finding)
        probes[probe.file] = probe.text
        rechecked[id(finding)] = JudgedFinding(
            severity=finding.severity,
            claim=finding.claim,
            evidence=finding.evidence,
            kind=finding.kind,
            row=finding.row,
            rerun_command=run.command,
            rerun_exit=run.exit_code,
        )

    rows = verdict.blockers[len(decision.blockers) :]
    kept = [
        rechecked.get(id(one), shown)
        for one, shown in zip(decision.kept, verdict.kept, strict=False)
    ]
    kept += verdict.kept[len(decision.kept) :]
    kept += [rechecked[id(one)] for one in broken]
    blockers = [
        rechecked[id(one)] if id(one) in rechecked else judged[id(one)] for one in standing
    ] + rows
    cleared_count = len(decision.blockers) + len(broken) - len(standing)
    fact = f"re-run cleared {cleared_count} of {len(decision.blockers) + len(rows)} blocker(s)"
    outcome = replace(
        verdict,
        fact=fact + (f"; the turn broke {len(broken)} probe(s)" if broken else ""),
        blockers=blockers,
        kept=kept,
        probes=probes,
        decision=replace(
            decision,
            blocked=bool(standing or decision.violated),
            blockers=standing,
            kept=[*decision.kept, *broken],
            reraised=reraised,
            reruns=[*reruns, *decision.reruns],
        ),
        head=tree_snapshot(root),
        cost_usd=None,
        token_counts={},
    )
    outcome.threads = revision_threads(outcome, root)

    return outcome


# ----------------------- #
# The copy, scrubbed and staged.


def scrub_copy(copy: Path) -> None:
    """Remove from the critic's copy every engine-written file the author's
    session could have shaped (S-0122/D-2). A `.torve` the candidate replaced
    with a link is cut, so nothing staged below is written through it."""

    torve = copy / layout.TORVE_DIR

    if torve.is_symlink() or (torve.exists() and not torve.is_dir()):
        torve.unlink()

    for name in SCRUBBED:
        path = torve / name

        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _rows_document(rows: list[tuple[InheritedDecision, bool]]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "grade": row.grade,
            "owned": owned,
            "text": row.text,
            "paths": list(row.paths),
            **({"check": row.check} if row.check else {}),
        }
        for row, owned in rows
    ]


def build_critic_prompt(
    target: Task,
    candidate: str,
    rows: list[tuple[InheritedDecision, bool]],
    based: bool,
    revision: bool = False,
) -> str:
    """The message beside the frozen prompt: the candidate's intent, its rows,
    where its inputs are and what can block. The rows ride inline because
    they are short and the critic must answer each; everything else is a file
    it reads. A revision's prompt also names what it changed and what it was
    asked to fix (S-0125/D-2). What blocks is said here, never in the frozen
    prompt: only a repro that exits non-zero when the engine re-runs it on the
    candidate (S-0127/D-1), and for a weak test only a probe's `WRONG_PASSES`
    (S-0127/D-2)."""

    listed = (
        "\n".join(
            f"- {row.id} [{row.grade}] {'owned' if owned else 'constraint'}: {row.text}"
            for row, owned in rows
        )
        or "- none: no row governs these paths; judge the change on its own terms"
    )
    # S-0127/D-15: the rows the document's other units deliver, unjudged. A
    # unit judged against its own rows alone reads work a later unit orders
    # as a regression of an earlier promise, and blocks the document's own end.
    given = {row.id for row, _owned in rows}
    others = [
        row
        for row in target.decisions
        if target.spec and row.id.startswith(f"{target.spec}/") and row.id not in given
    ]
    document = (
        "\n\n## The document's other rows\n\n"
        "Other units of this document deliver these. Give them no verdict. Each is part of\n"
        "the end state the document orders, so behaviour one of them orders is never a\n"
        "regression or a defect here, even where a row above reads otherwise; doing that\n"
        "work in this unit is at most `scope`. A LOCKED row above is the exception: it\n"
        "still binds, so judge it as usual.\n\n"
        + "\n".join(f"- {row.id} [{row.grade}]: {row.text}" for row in others)
        if others
        else ""
    )
    base = (
        "- `.torve/review/base/` — the tree the candidate started from"
        if based
        else "- no base tree could be built for this candidate; the evidence says why"
    )
    revised = (
        "- `.torve/review/revision.diff` — what this revision changed since the last review\n"
        "- `.torve/review/blockers.yaml` — the findings earlier rounds asked it to fix"
        if revision
        else ""
    )

    return f"""# Candidate {candidate}

## Intent

{target.intent or "(the contract states no intent)"}

## Rows

{listed}{document}

## What blocks

Only a repro that exits non-zero on the candidate can block. After you answer, the engine
re-runs each finding's `repro.command`, with the probes it names; a command shown only in
evidence is never run. A violated row other than a LOCKED one blocks only through a finding
that names the row and carries a repro. A `gaming` finding and a LOCKED row's breach block
on located evidence alone.

A `weak_test` finding blocks only through a probe its repro names: the probe builds a
plausible wrong implementation, runs the changed test against it, and exits {WRONG_PASSES} when
that test still passes, with any other code otherwise. A crash exits 1, so only {WRONG_PASSES}
blocks. Build the wrong implementation in a copy of the tree or by patching it at import:
the re-run mounts the checkout read-only. A `weak_test` finding whose repro names no probe
never blocks.

The re-run reads a fresh checkout of the candidate's tracked files and the probes: no
`.venv`, no build output. Start the project's tools the way its acceptance commands do,
such as `uv run python probes/x.py` where the checkout holds `uv.lock`, never by a path
into `.venv/`. A command the checkout cannot start exits 127, and never blocks.

## Inputs

- `.torve/review/diff.patch` — the change under judgment
{base}
- `.torve/review/evidence.yaml` and `.torve/review/logs/` — the engine's measurements
- `.torve/review/rows.yaml` — the rows above, with their paths and checks
- `.torve/review/critic.schema.json` — the shape of your answer
{revised}
"""


def _keep_probes(copy: Path, document: CriticDocument | None, worktree: Path) -> dict[str, str]:
    """The probe files the critic's findings name as repros, by `repro.file`
    or in the command, read before the copy dies, so a re-run is handed every
    probe `_probe_names` asks for; a path that leaves the copy, a link or a
    file over the cap is left there.

    Beside them, up to `HELPER_CAP` other text files under `probes/` that
    the critic wrote — at a path *worktree*, the candidate the copy was taken
    from, does not hold — and that a kept probe reads (`_read_by`): the
    modules its probes import, which `_helpers` hands every critic re-run
    (S-0127/D-10)."""

    if document is None:
        return {}

    root = copy.resolve()
    kept: dict[str, str] = {}
    names = [
        name
        for finding in document.findings
        if finding.repro is not None
        for name in _probe_names(finding.repro.command, finding.repro.file)
    ]

    for name in names:
        if name in kept:
            continue

        path = copy / name

        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue

            if path.is_file() and path.stat().st_size <= PROBE_CAP:
                kept[name] = path.read_text(encoding="utf-8", errors="replace")

        except OSError:
            continue

    written = copy / "probes"

    if written.is_symlink() or not written.is_dir():
        return kept

    taken = {posixpath.normpath(name) for name in names}
    # A path the candidate holds is the candidate's, a compiled probe among
    # them, never a file the critic wrote.
    others = [
        name
        for name in (path.relative_to(copy).as_posix() for path in sorted(written.rglob("*")))
        if name not in taken and not (worktree / name).exists(follow_symlinks=False)
    ]
    helpers = 0
    grew = True

    # Until a pass adds none, since a helper may import another.
    while grew and helpers < HELPER_CAP:
        grew = False

        for name in others:
            if helpers == HELPER_CAP:
                break

            if name in kept or not _read_by(name, kept):
                continue

            path = copy / name

            try:
                # `rglob` never enters a linked directory, so a link is this
                # file itself; `is_file` leaves a fifo unread.
                if path.is_symlink() or not path.is_file() or path.stat().st_size > PROBE_CAP:
                    continue

                kept[name] = path.read_bytes().decode("utf-8")
                helpers += 1
                grew = True

            except (OSError, UnicodeDecodeError):
                continue

    return kept


def _read_by(name: str, probes: Mapping[str, str]) -> bool:
    """Whether a file at *name* under `probes/` is one *probes* read: its
    module or file name in a probe's text, or a `conftest.py` pytest loads
    for a probe at or below its directory."""

    folder, base = posixpath.split(name)

    if base == "conftest.py":
        return any(posixpath.normpath(probe).startswith(folder + "/") for probe in probes)

    stem = re.compile(rf"\b{re.escape(posixpath.splitext(base)[0])}\b")

    return any(stem.search(text) for text in probes.values())


# ....................... #


def run_critic(
    root: Path,
    worktree: Path,
    target: Task,
    review: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    diff_text: str,
    evidence: EvidencePackage | None,
    config_digest: str,
    broker: Broker | None = None,
    broker_handle: BrokerHandle | None = None,
    trigger: str = "task_gated",
    revision: Revision | None = None,
    attempt: int = 1,
    reproduce: Reproduce | None = None,
    earlier: Sequence[Mapping[str, Any]] | None = None,
    acceptance: Sequence[Probe] = (),
    revisions_left: bool = True,
) -> CriticVerdict:
    """One critic call over one candidate (S-0122/D-6). Everything the critic
    reads is composed here, before the copy exists; the copy is then scrubbed
    of the author's channels and given the inputs; the engine decides.

    A revision attempt also stages its own `revision.diff` and the earlier
    rounds' `blockers.yaml`, and its findings are read against them
    (S-0125/D-1, S-0125/D-2). A review round's *attempt* contributes the
    rejected spots `decide` carries findings on (S-0126/D-1, S-0126/D-2);
    they are read from the worktree's task log after staging, and nothing of
    them is staged for the critic.

    *reproduce* re-runs the repros `decide` asks for (S-0127/D-1), after the
    critic's session has ended, so nothing it measures reaches the critic
    (S-0122/D-2); with none, nothing is re-run and no finding that needs a
    re-run blocks. *earlier* is every earlier round's blockers a blocker
    re-raises, when they are not the revision's own (S-0127/D-4).
    *acceptance* is the unit's compiled probes, which `decide` re-runs and a
    revision's recheck re-runs again (S-0129/D-4); nothing of them is staged
    for the critic."""

    manifest_path = layout.gates_file(root)
    manifest = load_manifest(manifest_path) if manifest_path.is_file() else None
    elided = elided_prefixes(manifest)

    package = evidence or EvidencePackage(
        candidate=target.id,
        base=None,
        head="",
        notes=["the engine built no evidence package for this candidate"],
    )
    rows = package_rows(target, list(_changed_lines(diff_text)))
    sha = prompt_sha()
    # The tree under judgment, which the next round's `revision.diff` starts from.
    judged = tree_snapshot(worktree)

    # The whole input, as text, before any copy exists (S-0121/D-28).
    staged: dict[str, str] = {
        "diff.patch": elide_diff_bulk(diff_text, elided),
        "evidence.yaml": render_evidence(package),
        "rows.yaml": yaml.safe_dump(_rows_document(rows), sort_keys=False, allow_unicode=True),
        "critic.schema.json": SCHEMA_FILE.read_text(encoding="utf-8"),
        **{f"logs/{name}": text for name, text in package.logs.items()},
    }

    if revision is not None:
        staged["revision.diff"] = elide_diff_bulk(revision.diff, elided)
        # The kind and row a revision fact records are the engine's to read
        # for a re-raise (S-0127/D-4); the critic is staged what it was.
        staged["blockers.yaml"] = yaml.safe_dump(
            [
                {key: value for key, value in blocker.items() if key not in ("kind", "row")}
                for blocker in revision.blockers
            ],
            sort_keys=False,
            allow_unicode=True,
        )

    system = critic_prompt()
    prompt = build_critic_prompt(
        target, package.candidate, rows, package.base is not None, revision is not None
    )

    def stage(copy: Path) -> None:
        scrub_copy(copy)
        review_dir = copy / REVIEW_DIR

        for name, text in staged.items():
            path = review_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        if package.base is not None:
            problem = archive_tree(worktree, package.base, review_dir / "base", BASE_TIMEOUT)

            if problem is not None:
                (review_dir / "base.missing").write_text(problem + "\n", encoding="utf-8")

    session = run_seat(
        root,
        worktree,
        target,
        review,
        config,
        runtime,
        agent,
        prompt,
        stage=stage,
        read=read_critic,
        harvest=partial(_keep_probes, worktree=worktree),
        system=system,
        broker=broker,
        broker_handle=broker_handle,
    )

    document, refusal = session.verdict, session.refusal
    unparseable = document is None and refusal is None
    seat_error = seat_api_error(session.result.output) if unparseable else None
    grades = {row.id: row.grade for row, _ in rows}
    harvested: dict[str, str] = session.harvested or {}
    # S-0126/D-2: read after staging, from the worktree's log, never from
    # anything the critic was given.
    rejected = rejected_spots(target, worktree, attempt)
    decision = (
        decide(
            document,
            {row.id: owned for row, owned in rows},
            worktree,
            config.review.blocks_at,
            revision=revision,
            rejected=rejected,
            locked={row.id for row, _ in rows if row.grade == "LOCKED"},
            reproduce=reproduce,
            probes=harvested,
            earlier=earlier,
            acceptance=acceptance,
            revisions_left=revisions_left,
        )
        if document is not None
        else None
    )

    blockers: list[Finding] = []
    kept: list[Finding] = []
    probes: dict[str, str] = {}

    if decision is not None and document is not None:
        blockers, kept = verdict_findings(decision, grades, worktree)
        # Every probe a kept finding's re-run is handed, the ones its command
        # alone names and the critic's helpers included, so a revision's
        # recheck re-runs it whole (S-0127/D-10, S-0128/D-3); a compiled probe
        # by its stored text.
        named = {
            name
            for f in decision.kept
            if f.repro is not None
            for name in _probe_names(f.repro.command, f.repro.file)
        }
        available = {**harvested, **{probe.file: probe.text for probe in acceptance}}
        probes = {name: text for name, text in available.items() if name in named}
        probes |= _helpers(harvested, document.findings)
        _store_probes(root, review.id, probes)

    if refusal is not None:
        fact = f"critic output refused: {refusal}"
    elif seat_error is not None:
        fact = f"the critic's seat gave no verdict: {seat_error}"
    elif unparseable:
        fact = "critic output unparseable — no verdict recorded"
    elif decision is not None and decision.blocked:
        fact = (
            f"critic blocked: {len(decision.blockers)} finding(s) at or above "
            f"{config.review.blocks_at}, {len(decision.violated)} row(s) violated"
        )
    elif kept:
        fact = f"critic recorded {len(kept)} non-blocking finding(s)"
    else:
        fact = "critic clean"

    record = review_record(
        session,
        target,
        review,
        agent,
        config_digest,
        trigger,
        {
            "mode": "critic",
            "blocks_at": config.review.blocks_at,
            "prompt": PROMPT_VERSION,
            "prompt_sha": sha,
            "findings": _recorded_findings(decision) if decision else [],
            "discarded": decision.discarded if decision else [],
            "rows": decision.rows if decision else [],
            "blocked": bool(decision and decision.blocked),
            "contradiction": (
                document.contradiction.model_dump()
                if document is not None and document.contradiction is not None
                else None
            ),
            "unknowns": document.unknowns if document is not None else [],
            "probes": sorted(probes),
            "acceptance": decision.acceptance if decision else [],
            "evidence": {
                "base": package.base,
                "head": package.head,
                "rows": {row.row: row.status for row in package.rows},
            },
            "unparseable": unparseable,
            "refusal": refusal,
        },
        broker,
    )
    close_review(root, worktree, session, record, fact)

    verdict = CriticVerdict(
        review_id=review.id,
        fact=fact,
        blockers=blockers,
        kept=kept,
        discarded=decision.discarded if decision else [],
        unparseable=unparseable,
        refusal=refusal,
        second_ask=session.second_ask,
        seat_error=seat_error,
        decision=decision,
        document=document,
        prompt_sha=sha,
        probes=probes,
        measured=package.failing_items() or None,
        head=judged,
        cost_usd=session.result.cost_usd,
        token_counts=session.token_counts,
        provider=session.tier.provider or None,
        model=session.tier.model or None,
    )
    verdict.threads = revision_threads(verdict, worktree)

    return verdict


def _store_probes(root: Path, review_id: str, probes: Mapping[str, str]) -> None:
    """Keep the probes a surviving finding names, under the review's own
    directory, so the revision and the record can cite them after the copy
    is gone."""

    kept = (layout.task_dir(root, review_id) / "probes").resolve()

    for name, text in probes.items():
        # Under its path below `probes/`, so two of one basename are two files.
        path = (kept / posixpath.normpath(name).removeprefix("probes/")).resolve()

        if not path.is_relative_to(kept):
            continue  # a name that leaves `probes/` is no probe a re-run takes

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ....................... #


def triage_threads(
    root: Path,
    checkout: Path,
    branch: str,
    prompt: str,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    *,
    taken: Iterable[str] = (),
    broker: Broker | None = None,
) -> TriageDocument | None:
    """One critic call over a pull-request head's unresolved bot threads
    (S-0122/D-15): the seat reads a scrubbed copy of *checkout*, the head's
    tree, and answers `torve.triage/v1`. A dismissal stands on its evidence:
    one whose evidence is not a leading `path:line` citation that locates in
    that tree is read as apply. None when the seat gave no document that reads.

    *taken* is the record's task ids, so the review's own id is none the board
    holds; *broker* routes the seat as it routes any review (S-0021)."""

    from torve.application.dispatch import run_routing
    from torve.application.ports import BrokerBudget
    from torve.application.threads import APPLIED, parse_triage

    document_id = branch.rsplit("/", 1)[-1]
    target = Task(
        id=document_id,
        spec=document_id,
        intent=f"The unresolved bot threads on {branch}.",
        decisions=[],
    )
    review = mint_review_task(
        root, target, intent=f"Triage the unresolved bot threads on {branch}.", taken=taken
    )
    broker_handle = (
        broker.open(
            review.id,
            run_routing(config, review, review_on=False),
            BrokerBudget(tokens=review.budget.tokens),
        )
        if broker is not None
        else None
    )

    try:
        session = run_seat(
            root,
            checkout,
            target,
            review,
            config,
            runtime,
            agent,
            prompt,
            stage=scrub_copy,
            read=lambda output: (parse_triage(output), None),
            broker=broker,
            broker_handle=broker_handle,
        )
        document = session.verdict

        if document is not None:
            for verdict in document.threads:
                leading = verdict.evidence.split(maxsplit=1)[0] if verdict.evidence.strip() else ""
                located = CITATION.match(leading) and locate(verdict.evidence, checkout) is None

                if verdict.verdict not in APPLIED and not located:
                    verdict.verdict = "apply"

        verdicts = [verdict.model_dump() for verdict in document.threads] if document else []
        fact = (
            f"triaged {len(verdicts)} thread(s)"
            if document is not None
            else "triage output unparseable — no verdict recorded"
        )
        record = review_record(
            session,
            target,
            review,
            agent,
            "",
            "pr_threads",
            {"mode": "triage", "verdicts": verdicts, "unparseable": document is None},
            broker,
        )
        # The host's manifest names where telemetry goes, never the head's.
        close_review(root, root, session, record, fact)

    finally:
        if broker is not None and broker_handle is not None:
            broker.close(broker_handle)

    return document


def judge(run: Dispatch, review: Task, state: RunState | None = None) -> CriticVerdict:
    """The critic over the run's gate-green candidate: the patch, the
    evidence package and the digest the last gate pass produced. *state* is
    the run's history, from which a revision attempt reads the head it last
    judged and the blockers it was asked to fix (S-0125/D-2), and over which
    `runner.reproduce` re-runs the findings' repros against the candidate
    (S-0127/D-1). The battery's judge checkout is gone by then: the gate pass
    removes it before its verdict returns, and the re-run opens its own. The
    rounds *state* has spent say whether a revision is left for a weak test
    to buy (S-0127/A-14)."""

    from torve.application.acceptance import load as stored_acceptance
    from torve.application.review import _blocker_revisions_spent
    from torve.application.runner import reproduce

    revision = revision_for(run, state) if state is not None else None
    # S-0127/D-4: with no revision diff to build, every round's blockers are
    # still what a repeat re-raises.
    earlier = (
        mapped_blocker_anchors(run.worktree, state)
        if state is not None and revision is None
        else None
    )
    # S-0129/D-4: the unit's compiled probes, read from its store, so every
    # candidate and every round is judged against the same ones.
    compiled = (
        stored_acceptance(run.root, run.task.id)
        if run.config.review.compiles(run.task.compile)
        else None
    )

    return run_critic(
        run.root,
        run.worktree,
        run.task,
        review,
        run.config,
        run.deps.runtime,
        reviewer_for(run),
        run.last_pass.patch,
        run.last_pass.evidence,
        run.last_pass.digest,
        broker=run.deps.broker,
        broker_handle=run.broker_handle,
        revision=revision,
        attempt=state.attempts if state is not None else 1,
        reproduce=partial(reproduce, run, state) if state is not None else None,
        earlier=earlier,
        acceptance=compiled.kept if compiled is not None else (),
        revisions_left=state is None
        or _blocker_revisions_spent(state) < run.config.review.resolved_blocker_revisions(),
    )


# ----------------------- #
# The corpus (S-0122/D-19): a seeded case judged as a candidate would be.


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        [
            "git",
            *NO_HOOKS,
            "-c",
            "user.name=torve corpus",
            "-c",
            "user.email=corpus@torve.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _tree_copy(tree: Path, destination: Path) -> None:
    """The case's tree, without the staged prompt a past replay left in it."""

    shutil.copytree(
        tree, destination, symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git")
    )
    shutil.rmtree(destination / layout.TORVE_DIR / "tmp", ignore_errors=True)


def corpus_repository(case_dir: Path, repo: Path) -> str | None:
    """A git repository at *repo* whose `main` is the case's base and whose
    checked-out branch is its head: the tree is the head when the patch
    reverses onto it, the base when the patch applies to it. None when it
    was built; otherwise why the base is the tree itself."""

    patch = (case_dir / "diff.patch").resolve()
    _tree_copy(case_dir / "tree", repo)
    _git(repo, "init", "-q", "-b", "main")
    note: str | None = None

    reverse = _applies(repo, patch, "-R")
    forward = not reverse and _applies(repo, patch)

    if reverse:
        _git(repo, "apply", "-R", str(patch))
    elif not forward:
        note = "the case's patch neither reverses onto its tree nor applies to it"

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "candidate")

    # Forward over the base in both cases: copying the tree back over it
    # would keep a file the patch deletes.
    if reverse or forward:
        _git(repo, "apply", str(patch))

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "candidate")

    return note


def _applies(repo: Path, patch: Path, *flags: str) -> bool:
    import subprocess

    return (
        subprocess.run(
            ["git", *NO_HOOKS, "-C", str(repo), "apply", "--check", *flags, str(patch)],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def replay_case(
    root: Path,
    case_dir: Path,
    target: Task,
    review: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    *,
    tests_run: str | None = None,
    broker: Broker | None = None,
    broker_handle: BrokerHandle | None = None,
) -> CriticVerdict:
    """One corpus case through the critic: the case is rebuilt as a base and
    a candidate in a scratch repository, its evidence package is measured in
    a sandbox over it as a gate pass would, and the critic judges it, its
    repros re-run in sandboxes of that evidence spec, each over a fresh copy
    of the candidate, so a case reads `blocked` the way a night would
    (S-0127/D-1). The scratch repository is removed afterwards, whatever
    happened."""

    from torve.application.runner import SandboxExecutor

    # Under the root's worktree directory, which the reaper already sweeps
    # and a sandbox runtime already mounts from.
    (root / naming.WORKTREE_DIR).mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"{review.id}.corpus-", dir=root / naming.WORKTREE_DIR))

    try:
        repo = scratch / "repo"
        note = corpus_repository(case_dir, repo)
        manifest = Manifest(gates=[], tests=TestsConfig(run=tests_run or DEFAULT_TEST_RUN))
        ctx = build_context(repo, manifest, base="main")
        ctx.task = target
        tier = tier_for(config, review.tier)
        spec = SandboxSpec(
            name=naming.sandbox_name(review.id, "corpus") + "-evidence",
            image=image_for(config, tier),
            labels=naming.labels(review.id, "corpus", root),
            timeout_s=config.runtime.sandbox_timeout,
        )
        executor = SandboxExecutor(runtime, spec, repo)

        try:
            ctx.execute = executor
            package = build_evidence(ctx, [], candidate=target.id)

        finally:
            executor.close()

        if note is not None:
            package.notes.append(note)

        return run_critic(
            root,
            repo,
            target,
            review,
            config,
            runtime,
            agent,
            (case_dir / "diff.patch").read_text(encoding="utf-8"),
            package,
            "corpus",
            broker=broker,
            broker_handle=broker_handle,
            reproduce=partial(
                _replay_reproduce, runtime, spec, repo, config.runtime.sandbox_timeout
            ),
        )

    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _replay_reproduce(
    runtime: Runtime,
    spec: SandboxSpec,
    repo: Path,
    timeout: float,
    commands: Sequence[str],
    probes: Mapping[str, str | None],
) -> list[CommandRun]:
    """A night's re-run over a corpus case's scratch repository (S-0127/D-1,
    S-0127/D-10): each repro gets a fresh copy of the candidate *repo*, where
    `runner.reproduce_over` writes its probes and runs its commands under the
    live re-run's controls — the same probe refusals, the read-only mount and
    `REPRO_ENV`, the one writable run a denied write earns — and the copy
    goes after, so nothing one repro wrote reaches the next."""

    from torve.application.runner import reproduce_over

    copy = Path(tempfile.mkdtemp(prefix="repro-", dir=repo.parent))

    try:
        checkout = copy / repo.name
        # Links stay links, so a probe path through one is refused as a
        # judge checkout's would be.
        shutil.copytree(repo, checkout, symlinks=True)

        return reproduce_over(runtime, spec, checkout, commands, probes, timeout)

    finally:
        shutil.rmtree(copy, ignore_errors=True)
