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
into block or pass (`decide`, S-0122/D-1): a finding that survives the
location filter at or above `review.blocks_at` blocks, a row `violated`
with evidence that locates blocks, and `unknown` never does.

A probe the critic wrote survives only when a surviving finding names it as
its repro; everything else dies with the copy.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application.dispatch import Dispatch
from torve.application.ports import Agent, Broker, BrokerHandle, Runtime, SandboxSpec
from torve.application.review import (
    STOPS_AT,
    ReviewOutcome,
    SchemaRefusal,
    _changed_lines,
    close_review,
    elide_diff_bulk,
    elided_prefixes,
    last_document,
    mint_review_task,
    review_record,
    reviewer_for,
    run_seat,
    schema_refusal,
    seat_api_error,
)
from torve.base import naming
from torve.config import layout
from torve.config.manifest import DEFAULT_TEST_RUN, Manifest, TestsConfig, load_manifest
from torve.config.runconfig import RunnerConfig, image_for, tier_for
from torve.domain.attempt import Finding
from torve.domain.task import InheritedDecision, Task
from torve.domain.vocabulary import FindingSeverity
from torve.gates.context import NO_HOOKS, build_context
from torve.gates.evidence import CITATION, filter_findings, locate
from torve.gates.evidence_package import (
    EvidencePackage,
    build_evidence,
    package_rows,
    render_evidence,
)
from torve.gates.suite import archive_tree

if TYPE_CHECKING:
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
# The engine decides (S-0122/D-1).


@dataclass(frozen=True)
class Decision:
    """What the engine made of a critic document: the findings that survived
    the location filter, the ones that block, the rows that block, and every
    row's verdict as recorded — a row the critic did not answer is `unknown`."""

    blocked: bool
    blockers: list[CriticFinding]
    violated: list[RowVerdict]
    kept: list[CriticFinding]
    discarded: list[str]
    rows: list[dict[str, Any]]


def decide(
    document: CriticDocument,
    rows: Mapping[str, bool],
    root: Path,
    blocks_at: Literal["blocker", "major"],
) -> Decision:
    """Block or pass, from the findings and never from the critic's words
    (S-0122/D-1, LOCKED).

    *rows* maps each row the critic was given to whether the unit owns it; a
    row not owned is a LOCKED row the diff touches, given as a constraint,
    and binds as one. A finding blocks when its evidence locates under
    *root* and its severity is at or above *blocks_at*. A row blocks when
    the critic calls it `violated` with evidence that locates. `unknown`
    never blocks, and neither does a verdict on a row nobody gave it."""

    kept, discarded = filter_findings(document.findings, root)
    stopping = STOPS_AT[blocks_at]
    blockers = [finding for finding in kept if finding.severity in stopping]

    def weight(verdict: RowVerdict) -> tuple[bool, bool]:
        located = bool(verdict.evidence) and locate(verdict.evidence, root) is None
        return verdict.verdict == "violated", located

    # A row answered twice keeps its gravest verdict: a later `holds` never
    # erases a located `violated`.
    answered = {verdict.row: verdict for verdict in sorted(document.rows, key=weight)}
    violated: list[RowVerdict] = []
    recorded: list[dict[str, Any]] = []

    for row, owned in rows.items():
        verdict = answered.get(row)

        if verdict is None:
            recorded.append({"row": row, "owned": owned, "verdict": "unknown", "answered": False})
            continue

        located = bool(verdict.evidence) and locate(verdict.evidence, root) is None
        blocks = verdict.verdict == "violated" and located

        if blocks:
            violated.append(verdict)

        elif verdict.verdict == "violated":
            discarded.append(f"row {row}: violated without evidence that locates")

        recorded.append(
            {
                "row": row,
                "owned": owned,
                "verdict": verdict.verdict,
                "located": located,
                "blocks": blocks,
            }
        )

    return Decision(
        blocked=bool(blockers or violated),
        blockers=blockers,
        violated=violated,
        kept=kept,
        discarded=discarded,
        rows=recorded,
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


def _as_finding(finding: CriticFinding) -> Finding:
    return Finding(severity=finding.severity, claim=finding.claim, evidence=finding.evidence)


def _row_finding(verdict: RowVerdict, grades: Mapping[str, str]) -> Finding:
    """A violated row in the classic shape: a LOCKED row's breach is a
    blocker, any other row's a major — the critic's own calibration."""

    severity: FindingSeverity = "blocker" if grades.get(verdict.row) == "LOCKED" else "major"
    note = f": {verdict.note}" if verdict.note else ""

    return Finding(
        severity=severity, claim=f"{verdict.row} is violated{note}", evidence=verdict.evidence
    )


def revision_threads(verdict: CriticVerdict) -> list[dict[str, Any]]:
    """The blocking findings and rows as the feedback record's threads
    (S-0122/D-8): each claim with its evidence, its repro and the probe that
    shows it, anchored where the evidence's citation points. Never the
    critic's trace, which no revision reads."""

    decision = verdict.decision
    threads: list[dict[str, Any]] = []

    if decision is None:
        return threads

    for finding in decision.blockers:
        lines = [finding.claim, "", finding.evidence, ""]
        lines.append(
            f"kind: {finding.kind} · severity: {finding.severity} · verified: {finding.verified}"
        )

        if finding.repro is not None:
            lines.append(f"repro: `{finding.repro.command}`")
            probe = verdict.probes.get(finding.repro.file or "")

            if probe is not None:
                lines += ["", f"{finding.repro.file}:", "```", probe.rstrip(), "```"]

        if finding.fix_hint:
            lines += ["", f"fix: {finding.fix_hint}"]

        threads.append(_anchored(finding.evidence, verdict.review_id, "\n".join(lines)))

    for row in decision.violated:
        body = f"{row.row} is violated" + (f": {row.note}" if row.note else "")
        threads.append(_anchored(row.evidence, verdict.review_id, f"{body}\n\n{row.evidence}"))

    return threads


def _anchored(evidence: str, author: str, body: str) -> dict[str, Any]:
    thread: dict[str, Any] = {"comments": [{"author": author, "body": body}]}
    citation = CITATION.match(evidence.split(" — ")[0].split(" - ")[0].strip())

    if citation:
        thread["path"] = citation.group("path")
        thread["line"] = int(citation.group("start"))

    return thread


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
    target: Task, candidate: str, rows: list[tuple[InheritedDecision, bool]], based: bool
) -> str:
    """The message beside the frozen prompt: the candidate's intent, its rows
    and where its inputs are. The rows ride inline because they are short and
    the critic must answer each; everything else is a file it reads."""

    listed = (
        "\n".join(
            f"- {row.id} [{row.grade}] {'owned' if owned else 'constraint'}: {row.text}"
            for row, owned in rows
        )
        or "- none: no row governs these paths; judge the change on its own terms"
    )
    base = (
        "- `.torve/review/base/` — the tree the candidate started from"
        if based
        else "- no base tree could be built for this candidate; the evidence says why"
    )

    return f"""# Candidate {candidate}

## Intent

{target.intent or "(the contract states no intent)"}

## Rows

{listed}

## Inputs

- `.torve/review/diff.patch` — the change under judgment
{base}
- `.torve/review/evidence.yaml` and `.torve/review/logs/` — the engine's measurements
- `.torve/review/rows.yaml` — the rows above, with their paths and checks
- `.torve/review/critic.schema.json` — the shape of your answer
"""


def _keep_probes(copy: Path, document: CriticDocument | None) -> dict[str, str]:
    """The probe files the critic's findings name as repros, read before the
    copy dies; a path that leaves the copy, a link or a file over the cap is
    left there."""

    if document is None:
        return {}

    root = copy.resolve()
    kept: dict[str, str] = {}

    for finding in document.findings:
        name = finding.repro.file if finding.repro is not None else None

        if not name or name in kept:
            continue

        path = copy / name

        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue

            if path.is_file() and path.stat().st_size <= PROBE_CAP:
                kept[name] = path.read_text(encoding="utf-8", errors="replace")

        except OSError:
            continue

    return kept


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
) -> CriticVerdict:
    """One critic call over one candidate (S-0122/D-6). Everything the critic
    reads is composed here, before the copy exists; the copy is then scrubbed
    of the author's channels and given the inputs; the engine decides."""

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

    # The whole input, as text, before any copy exists (S-0121/D-28).
    staged: dict[str, str] = {
        "diff.patch": elide_diff_bulk(diff_text, elided),
        "evidence.yaml": render_evidence(package),
        "rows.yaml": yaml.safe_dump(_rows_document(rows), sort_keys=False, allow_unicode=True),
        "critic.schema.json": SCHEMA_FILE.read_text(encoding="utf-8"),
        **{f"logs/{name}": text for name, text in package.logs.items()},
    }
    system = critic_prompt()
    prompt = build_critic_prompt(target, package.candidate, rows, package.base is not None)

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
        harvest=_keep_probes,
        system=system,
        broker=broker,
        broker_handle=broker_handle,
    )

    document, refusal = session.verdict, session.refusal
    unparseable = document is None and refusal is None
    seat_error = seat_api_error(session.result.output) if unparseable else None
    grades = {row.id: row.grade for row, _ in rows}
    decision = (
        decide(document, {row.id: owned for row, owned in rows}, worktree, config.review.blocks_at)
        if document is not None
        else None
    )

    blockers: list[Finding] = []
    kept: list[Finding] = []
    probes: dict[str, str] = {}

    if decision is not None:
        violated = [_row_finding(verdict, grades) for verdict in decision.violated]
        blockers = [_as_finding(finding) for finding in decision.blockers] + violated
        kept = [_as_finding(finding) for finding in decision.kept] + violated
        named = {f.repro.file for f in decision.kept if f.repro is not None and f.repro.file}
        harvested: dict[str, str] = session.harvested or {}
        probes = {name: text for name, text in harvested.items() if name in named}
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
            "findings": [
                finding.model_dump(exclude_none=True)
                for finding in (decision.kept if decision else [])
            ],
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
    )
    verdict.threads = revision_threads(verdict)

    return verdict


def _store_probes(root: Path, review_id: str, probes: Mapping[str, str]) -> None:
    """Keep the probes a surviving finding names, under the review's own
    directory, so the revision and the record can cite them after the copy
    is gone."""

    for name, text in probes.items():
        path = layout.task_dir(root, review_id) / "probes" / Path(name).name
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


def judge(run: Dispatch, review: Task) -> CriticVerdict:
    """The critic over the run's gate-green candidate: the patch, the
    evidence package and the digest the last gate pass produced."""

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
    a sandbox over it as a gate pass would, and the critic judges it. The
    scratch repository is removed afterwards, whatever happened."""

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
        )

    finally:
        shutil.rmtree(scratch, ignore_errors=True)
