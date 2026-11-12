"""Review as a run (S-0005): the runner mints the review task when its
target's gates go green (S-0005/D-11), composes the reviewer's input — the diff,
the target's contract, its inherited decisions and the gate results, never
the author's session trace (S-0005/D-3) — and drives one attempt in a disposable
copy of the target worktree (S-0005/D-2 as reworded by S-0005/A-4). The reviewer runs the
battery it is judging inside that copy (S-0005/D-16); nothing written in it
survives the review. Findings come back as data; unlocatable evidence is
discarded before anyone sees it (S-0005/D-4), a surviving blocker escalates the
target as blocker_finding, and configuration — never the model — decided
that consequence (S-0001/D-10).

v1 drives a single attempt (`budget: iterations: 1`, S-0005/the-review-contract) with the
same sandbox mechanics and telemetry shape as any run; the multi-attempt
loop for reviews arrives when a reviewer earns retries.
"""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from torve.application import threads
from torve.application.dispatch import Dispatch, emit, run_routing
from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Broker,
    BrokerBudget,
    BrokerHandle,
    BrokerUsage,
    PrScm,
    PrVcs,
    Runtime,
    SandboxSpec,
)
from torve.application.runstate import RunState
from torve.base import naming
from torve.base.clock import stamp
from torve.config import layout
from torve.config.manifest import Manifest, load_manifest
from torve.config.runconfig import (
    RunnerConfig,
    TierConfig,
    agent_timeout_for,
    credential_names,
    effective_skill_sets,
    image_for,
    sandbox_timeout_for,
    tier_for,
)
from torve.domain.attempt import Finding, GateResult
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import CONTRACT_SCHEMA_VERSION, Budget, InheritedDecision, Task
from torve.gates.context import NO_HOOKS, GitError, git
from torve.gates.evidence import CITATION, CITED, citations, filter_findings, locate

# ----------------------- #

# A seat's verdict, whatever shape its reader parses: the classic reviewer's
# findings list or the critic's document.
V = TypeVar("V")


def mint_review_task(
    root: Path, target: Task, intent: str | None = None, taken: Iterable[str] = ()
) -> Task:
    """Runner-minted at gated (S-0005/D-11): the same contract shape with a
    different role (S-0005/D-9), decisions inherited from the target, one
    iteration. The planner never mints these — review follows execution.

    `taken` is the record's task ids when a store holds the tasks (S-0056/D-9):
    counted from the files alone, the review took the id of a task the record
    had queued, and staging its copy removed that task's worktree."""

    from torve.application.planner import next_task_number

    review = Task(
        id=f"T-{next_task_number(root, taken):04d}",
        spec=target.spec,
        role="review",
        targets=[target.id],
        intent=intent
        or (f"Review {target.id}'s diff against its contract and inherited decisions."),
        decisions=target.decisions,
        budget=Budget(iterations=1),
        tier="reviewer",
    )

    contract_dir = root / layout.TORVE_DIR / "tasks" / review.id
    contract_dir.mkdir(parents=True, exist_ok=True)
    document = review.model_dump(exclude_defaults=True)
    document["schema_version"] = CONTRACT_SCHEMA_VERSION
    document["decisions"] = [d.model_dump() for d in review.decisions]

    (contract_dir / "contract.yaml").write_text(
        "# Minted by the runner at gated — review follows execution.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    return review


# ....................... #


def _inherited_decisions(decisions: list[InheritedDecision]) -> str:
    """Each inherited row beside its `check` and `check_twin` (S-0106/D-5),
    then the audit questions when any row carries a check. A row without a
    check is listed without either and draws no finding for lacking one."""

    lines: list[str] = []
    checked = False

    for row in decisions:
        lines.append(f"- {row.id} [{row.grade}] {row.text}")
        if row.check:
            checked = True
            lines.append(f"  check: {row.check}")
            lines.append(f"  check_twin: {row.check_twin or 'none'}")

    if not lines:
        return "- none"

    if checked:
        lines.append("")
        lines.append(
            "For each row above that carries a check, ask two questions: does the "
            "check test what the row says, and would a plausible wrong "
            "implementation make it fail? A check that fails either question is a "
            "finding at `major`. A row with no check draws no finding for lacking one."
        )

    return "\n".join(lines)


def build_review_prompt(
    target: Task,
    diff_text: str,
    gate_results: list[GateResult],
    degraded: bool = False,
    *,
    elided: tuple[str, ...] | None = None,
) -> str:
    """The reviewer's whole input (S-0005/D-3: no author trace, ever). The
    calibration paragraph is deliberate — this reviewer sees a diff after
    green gates, where clean is the normal outcome; without permission to
    say so it manufactures work.

    The workspace paragraph is the other deliberate one: the reviewer is told
    what its tree actually is — a copy nobody reads back — and that running
    the change is allowed and expected, on this attempt's own clock."""

    gates_summary = (
        "\n".join(f"- {r.name}: {r.outcome}" for r in gate_results) or "- (none recorded)"
    )
    decisions = _inherited_decisions(target.decisions)

    # The battery by name: told what the change was judged green on, the
    # reviewer can run that same battery rather than guess at it (S-0005/D-16).
    acceptance = "\n".join(f"- {command}" for command in target.acceptance) or "- none declared"

    # S-0005/A-5: the prompt points at the staged diff instead of embedding it —
    # a diff carrying vendored bulk exceeds any context, and a reviewer with
    # tools reads lazily. A short head rides along as orientation only,
    # elided the same way the staged file is.
    head_lines = elide_diff_bulk(
        diff_text, ELIDED_DIFF_PATHS if elided is None else elided
    ).splitlines()[:80]
    diff_head = "\n".join(head_lines)
    diff_head_lines = len(head_lines)

    # S-0104/D-7: the contingent paths this diff actually wrote beyond the
    # declared globs. Shown to the reviewer so a widening is read, never
    # discovered after the fact. A `scope.derived` rider is not listed: the
    # review holds no manifest, and the scope gate's note still names it.
    promotions = promoted_paths(target, diff_text)
    promotions_block = (
        "\n\ncontingent paths this change widened beyond its task's scope:\n"
        + "\n".join(f"- {path}" for path in promotions)
        if promotions
        else ""
    )

    spec_block = (
        "No task contract exists for this change: you are reviewing in "
        "degraded mode. You have no scope and no inherited decisions — do "
        "NOT invent a specification to check against; spec-drift findings "
        "are unavailable, and that is expected."
        if degraded
        else f"## The task under review\n\nintent:\n{target.intent}\n\n"
        f"acceptance commands, yours to run in this copy:\n{acceptance}\n\n"
        f"inherited decisions:\n{decisions}{promotions_block}"
    )

    return f"""# Review

You are reviewing a change, not landing one. This workspace is a copy of the
tree the change lives in — a copy that is destroyed when this review ends.
Nothing you write here reaches the branch, the candidate, or anyone's view of
them, so an edit of yours cannot fix what you found, and cannot count as
evidence for it either.

Run what you are judging. The target's acceptance commands are yours to
execute in this copy, and a change you have executed is worth more than a
change you have read: run the tests, not only the diff. That execution
spends this attempt's own budget and timeout — the review never waits,
never restarts, and never extends itself. A battery too slow to finish
inside the window is a finding about the battery; say so, and say what you
did not get to run.

The gate battery is not among them here. This copy is staged without a
repository, so `torve gates` and anything else needing git fails at the
door — the battery runs outside the sandbox, on the candidate, after the
attempt. Do not spend the window discovering that.

{spec_block}

## Gate results

{gates_summary}

## What the engine knows

`.torve/context/index.md` in this workspace lists what the engine wrote
for this review from the record and the tree, with no model and nothing
the author of the change wrote. `touched.json` there names the decision
rows whose paths this diff intersects and the last battery's coverage of
the changed lines. Nothing in it outranks the contract above.

## The diff

The complete diff under review is staged at `.torve/tmp/review.diff` in
this workspace. Read it before judging anything — every finding must be
grounded in it. Pull surrounding context from the tree where a hunk
alone is ambiguous. Generated or vendored bulk in the diff (minified
bundles, lockfiles) may be skimmed by filename; the source-side hunks
carry the reviewable substance.

The first {diff_head_lines} lines, as orientation:

```diff
{diff_head}
```

## What to produce

Judge the change: is it wrong, unsafe, or contradicting an inherited
decision marked LOCKED? A small diff after green gates is often clean —
"no findings" is a normal, frequent outcome, not a failure to work.
Severities: blocker (the change is wrong, unsafe, or contradicts a LOCKED
decision), major (a reviewer would insist before merge), minor or nit
(preferences; at most two).

Every finding needs evidence that locates: a leading `path:line` citation
(against the files as they arrived here, not as you left them) followed by
" — " and one sentence, or a backticked command with the output it printed —
what you ran here is evidence exactly like a path:line is, and the output is
the part that locates. A finding whose evidence does not locate is discarded
unread.

Your final output must be exactly one JSON document, nothing after it:

{{"findings": [{{"severity": "major", "claim": "...", "evidence": "path.py:12 — ..."}}]}}

Each finding is validated against `.torve/context/schema/finding.json`; a
document that fails it is refused by the field it fails on, not read.

An empty list is a valid, complete review: {{"findings": []}}
"""


# ....................... #

# Vendored and generated paths whose diff sections are unreviewable bulk: a
# minified bundle line is bigger than the model's whole context, and reading
# it destroyed a review from inside the session (T-0241). The section is
# replaced with a one-line marker naming the path; the actual bytes sit in
# the copy's tree for a reviewer that truly needs them.
ELIDED_DIFF_PATHS = ("src/torve/_web/", "uv.lock")


def elided_prefixes(manifest: Manifest | None) -> tuple[str, ...]:
    """Every path whose diff section the reviewer collapses to a marker
    (S-0110/D-10): the vendored prefix and lock file the engine always elides,
    plus each rider file the manifest's `scope.derived` map names — a lock
    beside a nested manifest is bulk no reviewer reads line by line, whatever
    depth it sits at. A manifest that declares no map leaves the engine's own
    defaults in place (S-0104/D-5)."""

    prefixes = list(ELIDED_DIFF_PATHS)

    if manifest is not None:
        riders = {rider for riders in manifest.scope.derived.values() for rider in riders}
        prefixes += sorted(riders - set(prefixes))

    return tuple(prefixes)


def elide_diff_bulk(diff_text: str, prefixes: tuple[str, ...] = ELIDED_DIFF_PATHS) -> str:
    """Every per-file section of a unified diff whose path is an elided file,
    or sits under an elided directory (an entry ending in `/`), collapses to a
    marker line; all other sections pass through byte-for-byte. A file entry
    matches exactly, so `web/package-lock.json` never hides
    `web/package-lock.json.audit.py`. Deterministic, path-keyed,
    content-blind."""

    kept: list[str] = []
    eliding = False

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            path = line.split(" b/")[-1].strip()
            eliding = any(
                path == prefix or (prefix.endswith("/") and path.startswith(prefix))
                for prefix in prefixes
            )

            if eliding:
                kept.append(
                    f"[vendored/generated: {path} — section elided; the file is in the tree]\n"
                )
                continue

        if not eliding:
            kept.append(line)

    return "".join(kept)


# ....................... #


def seat_api_error(output: str) -> str | None:
    """The provider's error when the seat's last envelope is one and nothing
    else — is_error, with an API Error as its result or an `api_error` as its
    terminal reason — else None. Judged from the recorded output alone, so
    what the escalation says is auditable. A seat with no credential ends on
    "Not logged in" and `api_error`, which read as unparseable before."""

    for line in reversed(output.strip().splitlines()):
        try:
            document: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(document, dict):
            return None

        envelope = cast("dict[str, Any]", document)
        result = str(envelope.get("result", ""))

        api_error = "API Error" in result or envelope.get("terminal_reason") == "api_error"

        return result if envelope.get("is_error") and api_error else None

    return None


def transient_api_failure(output: str) -> bool:
    """A seat error that waiting can change: a provider-side 5xx, or a 429
    rate limit. A 4xx otherwise is the same refusal every time."""

    # The status sits after the colon ("API Error: 529 ...") or in brackets
    # ("API Error: Request rejected (429) ..."), depending on the harness.
    return bool(re.search(r"API Error:.*\b(429|5\d\d)\b", seat_api_error(output) or ""))


# ....................... #


def stage_review_copy(worktree: Path, copy: Path) -> Path:
    """The reviewer's workspace: a copy of the target worktree it may write in
    and run (S-0005/D-2 as reworded by S-0005/A-4), destroyed with its sandbox.

    Staged only once the review's whole input exists, so nothing read into the
    judgment can be something the reviewer then wrote. And `.git` does not
    travel with the copy: a worktree's gitfile is a pointer into the host's
    repository, so a copy that carried it could
    write through the pointer into the very history the review exists to
    leave alone. The tree travels; the repository does not.
    """

    if copy.exists():
        shutil.rmtree(copy, ignore_errors=True)

    copy.parent.mkdir(parents=True, exist_ok=True)

    # symlinks are copied as links: the sandbox saw links, not targets, before
    # the copy existed, and resolving them here would reach outside the tree.
    return shutil.copytree(worktree, copy, ignore=shutil.ignore_patterns(".git"), symlinks=True)


# ....................... #


def destroy_review_copy(copy: Path) -> None:
    """Nothing written in the copy survives the review (S-0005/D-2): the reviewer's
    edits, its build output, its scratch all die with its sandbox.

    `ignore_errors` is the point, not the omission of one: a cleanup that
    raises would replace the review's own verdict with a filesystem complaint,
    and a copy left behind by a dying runner is a `.wt` directory the reaper
    collects like any other worktree of a run that is no longer alive.
    """

    shutil.rmtree(copy, ignore_errors=True)


# ....................... #


class _FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    findings: list[Finding]


# ....................... #

# Which grades stop a promotion, per `review.blocks_at`. `major` is the
# default because three consecutive reviews of this engine graded a
# pass-killing defect `major` and nothing `blocker`, so the bar that stopped
# promotion sat above everything the reviewer actually found.
STOPS_AT: dict[str, frozenset[str]] = {
    "blocker": frozenset({"blocker"}),
    "major": frozenset({"blocker", "major"}),
}

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ....................... #


class SchemaRefusal(ValueError):
    """A document the engine found but the model refused (S-0054/D-15): the
    message names the field and the rule, as a lint refusal does — never
    recorded as unparseable, which is the word for no document at all."""


def schema_refusal(exc: ValidationError, root: str) -> str:
    """`findings[0].severity: Input should be 'blocker', ...` — the first
    error, located by its path, is what the agent can act on; the count
    says whether fixing it is enough."""

    errors = exc.errors()
    first = errors[0]
    where = ".".join(str(part) for part in first["loc"])
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""

    return f"{root}.{where}: {first['msg']}{more}" if where else f"{root}: {first['msg']}{more}"


# ....................... #


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: What the engine writes under `.torve/` and no revision produced: the
#: feedback, the staged prompt, the run's task bookkeeping. A revision's diff
#: keeps every other `.torve` path — the manifest, the configuration, the
#: specifications — because those are reviewable changes like any other.
REVISION_BOOKKEEPING = (
    ":(exclude).torve/tasks",
    ":(exclude).torve/context",
    ":(exclude).torve/regimes",
    ":(exclude).torve/traces",
    ":(exclude).torve/skills",
    ":(exclude).torve/tmp",
    ":(exclude).torve/review",
    ":(exclude).torve/telemetry.jsonl",
    ":(exclude).torve/feedback.jsonl",
    ":(exclude).torve/feedback.md",
)


def _changed_lines(diff_text: str) -> dict[str, set[int]]:
    """New-side line numbers the diff adds or changes, by path.

    Only a hunk's `+` lines and the positions its `-` lines left are
    changed: the context a hunk carries three lines either side is code the
    revision did not write, and a finding there must be carried, not blocked
    (S-0125/D-1). A deletion marks where the removed code stood, so a
    revision that only removes a guard is still judged on it."""

    changed: dict[str, set[int]] = {}
    path = ""
    line_no = 0
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1] if " b/" in line else ""
            changed.setdefault(path, set())
            in_hunk = False
        elif line.startswith("@@ ") and path:
            hunk = _HUNK_HEADER.match(line)

            if hunk is not None:
                line_no = int(hunk.group(3))
                in_hunk = True
        elif in_hunk and line.startswith("+"):
            changed[path].add(line_no)
            line_no += 1
        elif in_hunk and line.startswith("-"):
            changed[path].add(line_no)
        elif in_hunk and not line.startswith("\\"):
            line_no += 1

    return changed


def _diff_hunks(diff_text: str, path: str) -> list[dict[str, Any]]:
    """The hunks a revision diff applies to *path*, in order, each with its
    old and new ranges and its body lines."""

    hunks: list[dict[str, Any]] = []
    current = ""
    hunk: dict[str, Any] | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[-1] if " b/" in line else ""
            hunk = None
            continue

        if current and posixpath.normpath(current) != posixpath.normpath(path):
            continue

        header = _HUNK_HEADER.match(line)

        if header is not None:
            hunk = {
                "old_start": int(header.group(1)),
                "old_count": int(header.group(2) or "1"),
                "new_start": int(header.group(3)),
                "new_count": int(header.group(4) or "1"),
                "lines": [],
            }
            hunks.append(hunk)
        elif hunk is not None:
            hunk["lines"].append(line)

    return hunks


def _mapped_line(diff_text: str, path: str, line: int) -> int:
    """Where old-side *line* of *path* sits in the revision's new tree. A
    line the revision deleted or replaced maps to the position the deletion
    left, where its replacement or the code that slid up now stands. Lines a revision inserted above an
    earlier blocker are what move its anchor; matching the recorded number
    against the new citation would miss the blocker once the shift passes
    `threads.WINDOW` (S-0124/D-3)."""

    offset = 0

    for hunk in _diff_hunks(diff_text, path):
        old_start = int(hunk["old_start"])
        old_count = int(hunk["old_count"])
        new_start = int(hunk["new_start"])
        new_count = int(hunk["new_count"])

        if old_count == 0:
            if line <= old_start:
                return line + offset

            offset += new_count - old_count
            continue

        if line < old_start:
            return line + offset

        if line >= old_start + old_count:
            offset += new_count - old_count
            continue

        old, new = old_start, new_start

        for body in hunk["lines"]:
            if body.startswith("\\"):
                continue

            if body.startswith("+"):
                new += 1
            elif body.startswith("-"):
                if old == line:
                    return new

                old += 1
            else:
                if old == line:
                    return new

                old += 1
                new += 1

        return new

    return line + offset


@dataclass(frozen=True)
class Revision:
    """A revision attempt's own inputs (S-0125/D-2): what changed since the
    head the previous review judged, and every earlier round's blocker
    anchors and claims, read from the run's history."""

    diff: str
    blockers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> dict[str, set[int]]:
        """The new-side lines *diff* adds or changes, by path — the lines a
        kept finding must sit near to block the revision (S-0125/D-1)."""

        return _changed_lines(self.diff)


def promoted_paths(target: Task, diff_text: str) -> list[str]:
    """The contract's contingent literals *diff_text* wrote beyond the task's
    declared globs (S-0104/D-7), sorted. Read from the diff, so the reviewer is
    shown what the change actually widened rather than what the plan wished;
    an unconstrained scope widens nothing, so the answer is empty."""

    if not target.scope.allow:
        return []

    from pathspec import GitIgnoreSpec

    allow = GitIgnoreSpec.from_lines(target.scope.allow)
    contingent = set(target.scope.contingent)

    return sorted(
        path
        for path in set(_changed_lines(diff_text))
        if path in contingent and not allow.match_file(path)
    )


def touched_file(root: Path, target: Task, diff_text: str) -> dict[str, Any]:
    """`touched.json` (S-0054/D-13): the rows whose paths the diff intersects
    and the last battery's coverage of the changed lines — engine facts about
    the diff, none of them the author's voice.

    Earlier reviews' findings on this target are not among them (S-0122/D-6):
    handed to the next review, they anchored it to the last one's reading
    instead of the tree in front of it."""

    from xml.etree import ElementTree

    from pathspec import GitIgnoreSpec

    changed = _changed_lines(diff_text)
    rows = [
        {
            "id": row.id,
            "grade": row.grade,
            "text": row.text,
            "check": row.check,
            "files": sorted(hits),
        }
        for row in target.decisions
        if row.paths
        for hits in [[p for p in changed if GitIgnoreSpec.from_lines(row.paths).match_file(p)]]
        if hits
    ]

    coverage: list[dict[str, Any]] = []
    report = root / "coverage.xml"

    if report.is_file():
        try:
            tree = ElementTree.parse(report)
        except ElementTree.ParseError:
            tree = None

        if tree is not None:
            # S-0110/D-10: the prefix comes from the report's own `<sources>`,
            # the layout's source root when it declares none (S-0110/D-8).
            from torve.application.contextpack import coverage_prefix

            source = coverage_prefix(root, tree)

            for cls in tree.iter("class"):
                filename = cls.get("filename") or ""
                path = filename if filename.startswith(source) else f"{source}{filename}"
                lines = changed.get(path)

                if not lines:
                    continue

                hit = {
                    int(ln.get("number") or 0)
                    for ln in cls.iter("line")
                    if ln.get("hits") not in (None, "0")
                }
                coverage.append({"path": path, "changed": len(lines), "covered": len(lines & hit)})

    return {
        "schema_version": 1,
        "changed_paths": sorted(changed),
        "decisions": rows,
        "coverage": coverage,
    }


# ....................... #


def last_document(output: str, key: str = "findings") -> dict[str, Any] | None:
    """The last JSON object carrying *key* anywhere in a seat's output, or
    None. Harness output is hostile ground: ANSI escapes are stripped and the
    document may span lines or be followed by session chatter, so balanced
    decoding wins over line splitting."""

    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    envelopes: list[str] = []

    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())

        except json.JSONDecodeError:
            continue

        if isinstance(document, dict) and key in document:
            last = cast("dict[str, Any]", document)

        elif isinstance(document, dict):
            # A harness result envelope (`claude -p --output-format json`)
            # carries the reviewer's answer as the `result` string — the
            # findings document is inside it, escaped, invisible to this
            # scan (parse_drafts' envelope discipline, learned again when
            # an opus review's two blockers were dropped as unparseable).
            result: Any = cast("dict[str, Any]", document).get("result")

            if isinstance(result, str) and key in result:
                envelopes.append(result)

    if last is None:
        for enveloped in envelopes:
            nested = last_document(enveloped, key)

            if nested is not None:
                return nested

    return last


def parse_findings(output: str) -> list[Finding] | None:
    """The last JSON document with a `findings` key anywhere in the output,
    parsed and validated; None when no such document exists — recorded as
    unparseable, never invented as clean. A document that exists but fails
    the model raises `SchemaRefusal` naming the field (S-0054/D-15)."""

    last = last_document(output)

    if last is None:
        return None

    try:
        return _FindingsDocument.model_validate(last).findings

    except ValidationError as exc:
        raise SchemaRefusal(schema_refusal(exc, "findings document")) from None


# ....................... #


@dataclass
class ReviewOutcome:
    review_id: str
    fact: str
    blockers: list[Finding] = field(default_factory=list)
    kept: list[Finding] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    unparseable: bool = False
    refusal: str | None = None
    # S-0086/D-1: this verdict took a second ask, because the first was
    # unreadable. False is the ordinary case, and a readable verdict is never
    # re-asked.
    second_ask: bool = False
    # The provider's error when the seat itself failed and gave no verdict
    # (an unparseable outcome whose cause is known): a 429 at a usage limit
    # clears on its own, and whoever reads the escalation should know that.
    seat_error: str | None = None
    # What a revision is handed when this review blocks (S-0122/D-8): the
    # threads, when the reviewer composed richer ones than the blockers'
    # claim and evidence, and the measurements the candidate failed. None
    # leaves the classic shape, `blocker_threads` over the blockers.
    threads: list[dict[str, Any]] | None = None
    measured: dict[str, Any] | None = None
    # The tree this review judged, as a git tree object (S-0125/D-2), written
    # into the revision fact so the next round's `revision.diff` starts where
    # this review ended. Empty for a reviewer that recorded none.
    head: str = ""


class JudgedFinding(Finding):
    """A critic's finding or violated row in the classic shape, with what the
    engine read of it (S-0127/D-4, S-0127/D-14). The record's items and the
    revision fact read these fields rather than the evidence, so a fact
    written where no tree is at hand still names each blocker's kind, row and
    anchor."""

    kind: str = ""
    """The finding's kind as the critic gave it; `contract` for a violated row."""
    row: str | None = None
    """The row the finding names, or the violated row itself."""
    path: str = ""
    """The anchor's path, normalised: the first citation that resolves in the
    tree the critic judged; empty when none does."""
    line: int | None = None
    """The anchor's first line."""
    rerun_command: str = ""
    """The command the engine re-ran (S-0127/D-1); empty when it re-ran none."""
    rerun_exit: int | None = None
    """The re-run's exit code; None for a timeout, a repro refused or not run,
    and a finding never re-run."""


# ....................... #


def read_verdict(output: str) -> tuple[list[Finding] | None, str | None]:
    """(findings, refusal) for one reviewer answer. Findings None with no
    refusal is no document at all; None with a refusal is a document the
    schema refused by field (S-0054/D-15). Either way the verdict is
    unreadable, which is what `findings is None` says."""

    try:
        return parse_findings(output), None

    except SchemaRefusal as exc:
        return None, str(exc)


# ....................... #


def blocker_threads(blockers: list[Finding], review_id: str) -> list[dict[str, Any]]:
    """A revision's critique, in the feedback record's thread shape (RFC
    0043 S-0043/D-2): one thread per blocker, the review id as its single
    comment's author, the claim and its evidence as the body. Evidence that
    resolves to a path:line anchors the thread there; a backticked command
    with its output (S-0005/D-16) locates without a filesystem coordinate at
    all, so the thread carries no path/line and renders anchor-less — the
    same honest "?:-" `render_feedback` already gives an address-less
    thread."""

    threads: list[dict[str, Any]] = []

    for finding in blockers:
        citation = CITATION.match(finding.evidence.split(" — ")[0].split(" - ")[0].strip())
        thread: dict[str, Any] = {
            "comments": [{"author": review_id, "body": f"{finding.claim}\n\n{finding.evidence}"}]
        }

        if citation:
            thread["path"] = citation.group("path")
            thread["line"] = int(citation.group("start"))

        threads.append(thread)

    return threads


# ....................... #


def review_items(findings: list[Finding], root: Path | None = None) -> list[dict[str, Any]]:
    """The review's findings as the record carries them (S-0101/D-4): severity,
    claim and evidence as the reviewer gave them, beside the finding's anchor
    (`_finding_anchor` under *root*) — empty for evidence that cites no line,
    a backticked command with its output.

    A critic's finding also carries its kind and, when the engine re-ran its
    repro, the command and its exit code (S-0127/D-14)."""

    items: list[dict[str, Any]] = []

    for finding in findings:
        anchor = _finding_anchor(finding, root)
        item: dict[str, Any] = {
            "severity": finding.severity,
            "path": anchor["path"] if anchor else "",
            "line": anchor["line"] if anchor else None,
            "claim": finding.claim,
            "evidence": finding.evidence,
        }

        if isinstance(finding, JudgedFinding):
            item.update(
                kind=finding.kind,
                rerun_command=finding.rerun_command,
                rerun_exit=finding.rerun_exit,
            )

        items.append(item)

    return items


# ....................... #


@dataclass
class SeatSession(Generic[V]):
    """One reviewing seat's whole session in its disposable copy: the run
    state it advanced, the verdict it gave (None when it gave none readable),
    and the facts the record carries about how it was given. Shared by the
    classic reviewer and the critic (S-0122/D-6), so the copy, the clock, the
    retry and the second ask are one mechanism with two prompts."""

    state: RunState
    tier: TierConfig
    image: str
    verdict: V | None
    refusal: str | None
    # The last answer, rebuilt with the trace reference the record cites.
    result: AgentResult
    trace_refs: list[str]
    token_counts: dict[str, int]
    broker_usage: BrokerUsage | None
    started_at: str
    ended_at: str
    wall_time_s: float
    second_ask: bool
    # What the caller's `harvest` took from the copy before it died.
    harvested: Any = None


def _session_stream(root: Path, result: AgentResult) -> bytes | None:
    """The bytes at the trace *result* cites, read as soon as its ask returns:
    every ask of one review runs as the same attempt in the same copy, so the
    harness writes the second ask's session over the first's path."""

    if result.trace_ref is None:
        return None

    path = Path(result.trace_ref)
    path = path if path.is_absolute() else root / path

    return path.read_bytes() if path.is_file() else None


def run_seat(
    root: Path,
    worktree: Path,
    target: Task,
    review: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    prompt: str,
    *,
    stage: Callable[[Path], None],
    read: Callable[[str], tuple[V | None, str | None]],
    harvest: Callable[[Path, V | None], Any] | None = None,
    system: str | None = None,
    broker: Broker | None = None,
    broker_handle: BrokerHandle | None = None,
) -> SeatSession[V]:
    """Drive one reviewing seat over a disposable copy of *worktree*, which
    it may run and write in (S-0005/D-2 as reworded by S-0005/A-4).

    The caller composed *prompt* before this is called, and the executor's
    trace is taken in hand before the copy exists, so no write the seat makes
    can reach anything that was read into the judgment. *stage* writes the
    caller's inputs into the fresh copy; *read* turns an answer into
    (verdict, refusal); *harvest*, when given, reads what the copy still
    holds once the verdict is in and the sandbox is gone, before the copy dies."""

    from torve.application.telemetry import agent_token_counts

    tier = tier_for(config, review.tier)
    state = RunState(task_id=review.id, path=naming.state_file(root, review.id))
    state.transition(TaskState.CLAIMED, "runner-minted review")
    state.transition(TaskState.RUNNING, f"reviewing {target.id}")
    state.save()

    image = image_for(config, tier)

    # T-0172: the harness names the session trace after the workspace it is
    # given. The executor's trace is taken in hand here, before the copy is
    # staged, and put back whatever the review does — its record cites those
    # bytes, and the review exists to look at them, not to become them.
    executor_trace = naming.trace_file(worktree, 1)
    saved_executor_trace = executor_trace.read_bytes() if executor_trace.is_file() else None

    # Only now does the reviewer get a tree: a copy of the target worktree at
    # the review's own conventional address (S-0003/D-4), writable, and destroyed
    # with its sandbox.
    copy = stage_review_copy(worktree, naming.worktree(root, review.id))
    harvested: Any = None

    try:
        stage(copy)

        spec = SandboxSpec(
            name=naming.sandbox_name(review.id, state.run_id) + "-a1",
            image=image,
            labels=naming.labels(review.id, state.run_id, root),
            # The reviewer seat carries its own clock when its tier names one
            # (S-0035/the-tier-clock, S-0035/D-6): absent falls through to the globals. That
            # clock is also the bound on whatever the reviewer executes (S-0005/D-16) —
            # no budget or timeout of its own exists to widen it.
            timeout_s=sandbox_timeout_for(config, tier),
            env_passthrough=credential_names(config, tier),
        )

        handle = runtime.create(spec, copy)

    except BaseException:
        destroy_review_copy(copy)
        raise

    state.sandbox_id = handle.id
    state.save()

    started_at = stamp()
    review_clock = time.monotonic()
    answered = False

    try:
        # A transient provider failure (500/529 through the gateway, or a 429
        # rate limit) is not a verdict: two green-gated runs died as gate_infrastructure_failure
        # over upstream blips, each costing the whole task a restart. One
        # in-run retry absorbs the blip; a second failure escalates as before.
        context = AgentContext(
            task=review,
            attempt=1,
            workspace=copy,
            handle=handle,
            runtime=runtime,
            workdir=spec.workdir,
            timeout_s=agent_timeout_for(config, tier),
            prompt=prompt,
            system=system,
            broker=broker_handle,
        )
        result = agent.run(context)

        if transient_api_failure(result.output):
            time.sleep(15)
            result = agent.run(context)

        verdict, refusal = read(result.output)

        # S-0086/D-1: an unreadable verdict — no document at all, or one the
        # schema refused — is asked once more of the same reviewer in this
        # same staged copy, which is why the verdict is read here and not
        # after the copy is gone. A green-gated task was escalated because the
        # reviewer's envelope carried no document; a reviewer that cannot
        # answer twice is broken, and a readable verdict is never re-asked.
        asks = [result]
        streams = [_session_stream(root, result)]

        if verdict is None:
            result = agent.run(context)
            verdict, refusal = read(result.output)
            asks.append(result)
            streams.append(_session_stream(root, result))

        answered = True

    finally:
        runtime.destroy(handle)
        state.sandbox_id = None
        state.save()

        try:
            # The harvest reads the copy only once the sandbox is gone: a
            # process the seat left running could otherwise swap a path it
            # checked for a link before it reads.
            if answered and harvest is not None:
                harvested = harvest(copy, verdict)

        finally:
            # The copy goes with the sandbox: whatever the reviewer wrote, built
            # or broke reaches no one, because it is no longer anywhere (S-0005/D-2).
            destroy_review_copy(copy)

            # The reviewer's session may have been written over the executor's
            # trace path before anything else could fail — put the executor's
            # evidence back either way (or drop the review's session from a path
            # that never held one before).
            if saved_executor_trace is not None:
                executor_trace.write_bytes(saved_executor_trace)
            else:
                executor_trace.unlink(missing_ok=True)

    # The review's session lives under its own id — which, since the copy sits
    # at the review's own worktree address, is the name the harness gives it
    # here. The write keeps that true for an adapter that named its trace
    # somewhere else, and the record's root-relative trace_ref (S-0039/D-1) then
    # points at evidence that survives the review and the reap alike, until
    # the store's own retention takes it. This writer reaches the store
    # through `trace_file`, the one helper that ensures the directory exists
    # — as the harness does; neither may depend on the other having run
    # first. Only an adapter that actually wrote a trace gets the relocation
    # (T-0176): an adapter that wrote none left no file, and a record citing
    # a harness-shaped path nothing produced is a fabricated coordinate — as
    # misleading as a missing one.
    # One trace per ask, numbered by it (S-0086/D-1): a second ask's session is
    # its own evidence, and both refs ride the record below. What lands there
    # is the stream the adapter wrote, verbatim (S-0039/D-5); the result's
    # output stands in only for an adapter that cites a trace it did not write,
    # because that output is clipped like every exec result — a critic's tool
    # calls cut down to its first line and its verdict.
    review_trace_refs: list[str] = []

    for ask_number, (ask, stream) in enumerate(zip(asks, streams, strict=True), start=1):
        if ask.trace_ref is None:
            continue

        review_worktree = naming.worktree(root, review.id)
        trace = naming.trace_file(review_worktree, ask_number)

        if stream is not None:
            trace.write_bytes(stream)
        else:
            trace.write_text(ask.output, encoding="utf-8")

        review_trace_refs.append(naming.trace_ref(review_worktree, ask_number))

    # T-0186: the reviewer's self-reported token counts must survive the
    # rebuild below — the rebuilt AgentResult is the base shape and carries
    # no token fields, so read them off the original result first.
    token_counts = agent_token_counts(result)

    rebuilt = AgentResult(
        exit_code=result.exit_code,
        output=result.output,
        cost_usd=result.cost_usd,
        model_version=result.model_version,
        trace_ref=review_trace_refs[-1] if review_trace_refs else None,
    )

    return SeatSession(
        state=state,
        tier=tier,
        image=image,
        verdict=verdict,
        refusal=refusal,
        result=rebuilt,
        trace_refs=review_trace_refs,
        token_counts=token_counts,
        broker_usage=(
            broker.usage(broker_handle)
            if broker is not None and broker_handle is not None
            else None
        ),
        started_at=started_at,
        ended_at=stamp(),
        wall_time_s=round(time.monotonic() - review_clock, 3),
        # The verdict was read in the copy, so a second ask could still reach
        # the same reviewer there (S-0086/D-1).
        second_ask=len(asks) > 1,
        harvested=harvested,
    )


# ....................... #


def review_record(
    session: SeatSession[Any],
    target: Task,
    review: Task,
    agent: Agent,
    config_digest: str,
    trigger: str,
    verdict_fields: dict[str, Any],
    broker: Broker | None = None,
) -> dict[str, Any]:
    """The review's telemetry record: who judged what, under which trigger,
    and how the seat ran, around the verdict's own fields."""

    from torve.application.telemetry import RECORD_SCHEMA_VERSION, broker_block

    tier, result = session.tier, session.result

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": "review",
        "at": stamp(),
        "config_hash": config_digest,
        "task_id": review.id,
        "target": target.id,
        # Which trigger produced this review, because the two have different
        # lifecycles and the record could not tell them apart (S-0005/A-9): a
        # task-gated blocker is revised in-run or escalates, a
        # pull-request one is reported and stops nothing.
        "trigger": trigger,
        **verdict_fields,
        # Which reviews needed a second ask (S-0086/D-1), so the ledger can
        # count how often a harness fails to produce a findings document.
        "second_ask": session.second_ask,
        "agent": {
            "tier": review.tier,
            "adapter": getattr(agent, "kind", tier.adapter),
            "provider": tier.provider or None,
            "model": tier.model or None,
            "model_version": result.model_version,
            "cost_usd": result.cost_usd,
            "trace_ref": result.trace_ref,
            # Both asks' traces when there were two (S-0086/D-1): the verdict
            # that escalated is `trace_ref`, and the unreadable answer before
            # it is evidence for whoever asks why the harness failed.
            **({"trace_refs": session.trace_refs} if session.second_ask else {}),
            # The review's own span (the broker clock covers the whole run,
            # so it can never answer "how long was the review"): two wall
            # stamps for the humans, the monotonic duration as the truth.
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "wall_time_s": session.wall_time_s,
            # The reviewer's token counts, flat beside cost (T-0186) — only
            # the reported ones, absent keys omitted (S-0004/D-6).
            **session.token_counts,
            # The image tag beside the adapter (S-0017/D-4) — the cost table's
            # harness column reads it; the attempt records already carry it.
            "image": session.image,
            "shadow": False,
            # The reviewer spends the run's budget on the same handle; its
            # counts ride the record beside the adapter's report (S-0021/D-5).
            **(
                {"broker": broker_block(broker.name, session.broker_usage)}
                if broker is not None and session.broker_usage is not None
                else {}
            ),
        },
    }


def close_review(
    root: Path, worktree: Path, session: SeatSession[Any], record: dict[str, Any], fact: str
) -> None:
    """Append the record where the target's manifest says telemetry goes,
    and walk the review's own state to its end."""

    from torve.application.telemetry import append_record

    manifest_file = layout.gates_file(worktree)

    if manifest_file.is_file():
        append_record(root / load_manifest(manifest_file).telemetry, record)

    state = session.state
    state.transition(TaskState.GATED, "findings produced")
    state.transition(TaskState.REVIEWED, fact)
    state.transition(TaskState.READY, fact)
    state.save()


# ....................... #


def run_review(
    root: Path,
    worktree: Path,
    target: Task,
    review: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    diff_text: str,
    gate_results: list[GateResult],
    config_digest: str,
    degraded: bool = False,
    broker: Broker | None = None,
    broker_handle: BrokerHandle | None = None,
    trigger: str = "task_gated",
) -> ReviewOutcome:
    """One review attempt in a disposable copy of the target worktree, which
    the reviewer may run and write in (S-0005/D-2 as reworded by S-0005/A-4). Produces the
    review's run state and telemetry record; the caller applies the consequence
    to the target.

    The order is the guarantee: the input, the executor's evidence and the
    mount point are all settled before the copy exists, so no write the
    reviewer makes can reach anything that was read into the judgment."""

    # S-0110/D-10: the elision set is the manifest's own rider files, so a
    # nested lock rides out of the reviewer's view exactly as the root one does.
    manifest_path = layout.gates_file(root)
    manifest = load_manifest(manifest_path) if manifest_path.is_file() else None
    elided = elided_prefixes(manifest)

    # The reviewer's whole input, composed first (S-0005/D-3: no author trace,
    # ever) — after this line the diff under judgment is text in a string, and
    # no state the copy can reach has any part in it. The diff itself is
    # staged as a file in the copy below (S-0005/A-5): a diff carrying a vendored
    # bundle exceeds any prompt, and a reviewer with tools reads lazily.
    prompt = build_review_prompt(target, diff_text, gate_results, degraded=degraded, elided=elided)

    def stage(copy: Path) -> None:
        # S-0005/A-5: the diff rides the copy as a file, not the prompt —
        # composed above, before the copy existed, so the guarantee is
        # unchanged; the prompt names the path and the reviewer reads what it
        # needs. Vendored bulk is elided even here: reading a minified bundle
        # killed a review from inside the session, and the real bytes sit in
        # the tree anyway.
        diff_path = copy / ".torve" / "tmp" / "review.diff"
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(elide_diff_bulk(diff_text, elided), encoding="utf-8")

        # S-0054/D-13: the reviewer reads the pack too — built for the target, as a
        # replay, so no model-authored entry enters — plus `touched.json`, the
        # engine's facts about this diff. The copy carried the executor's skill
        # set; the review role's own replaces it (S-0054/D-14).
        from torve.application.contextpack import build as build_pack
        from torve.application.contextpack import materialize as materialize_pack
        from torve.application.skills import materialize as materialize_skills

        shutil.rmtree(copy / ".torve" / "skills", ignore_errors=True)
        materialize_skills(
            review.role,
            copy / ".torve" / "skills",
            effective_skill_sets(tier_for(config, review.tier), review.role, config.skills.sets),
            layout.skills_vendor_dir(copy),
        )
        pack = build_pack(
            root, root / config.specs.path, target, layout.gates_file(root), replay=True
        )
        pack["touched.json"] = (
            json.dumps(touched_file(root, target, diff_text), indent=2, sort_keys=True) + "\n"
        )
        pack["index.md"] += (
            "- `touched.json` — the rows this diff intersects and coverage of the changed lines\n"
        )
        materialize_pack(copy, pack)

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
        read=read_verdict,
        broker=broker,
        broker_handle=broker_handle,
    )

    findings, refusal, result = session.verdict, session.refusal, session.result
    unparseable = findings is None and refusal is None
    # No verdict because the seat itself failed: the record and the escalation
    # name the provider's error, not an unreadable answer nobody gave.
    seat_error = seat_api_error(result.output) if unparseable else None
    kept: list[Finding] = []
    discarded: list[str] = []

    if findings is not None:
        # Evidence resolves against the tree under judgment, never against the
        # copy: a `path:line` the reviewer could only produce by writing there
        # is a coordinate about its own scratch, not about the change (S-0005/D-4,
        # S-0005/D-2). A backticked command with its output locates without a
        # filesystem at all — that is the shape execution evidence takes
        # inside the copy (S-0005/D-16).
        kept, discarded = filter_findings(findings, worktree)

    # What stops a promotion is configuration's, not the reviewer's (S-0001/D-10):
    # `review.blocks_at` says which grades count, and `blocker` alone is the
    # behaviour every reading before it had. The findings themselves keep the
    # grade the reviewer gave them, here and in the record.
    stopping = STOPS_AT[config.review.blocks_at]
    blockers = [f for f in kept if f.severity in stopping]

    if refusal is not None:
        fact = f"review output refused: {refusal}"
    elif seat_error is not None:
        fact = f"the reviewer's seat gave no verdict: {seat_error}"
    elif unparseable:
        fact = "review output unparseable — no findings recorded"
    elif blockers:
        fact = f"review found {len(blockers)} finding(s) at or above {config.review.blocks_at}"
    elif kept:
        fact = f"review recorded {len(kept)} non-blocking finding(s)"
    else:
        fact = "review clean"

    record = review_record(
        session,
        target,
        review,
        agent,
        config_digest,
        trigger,
        {
            "mode": "classic",
            "blocks_at": config.review.blocks_at,
            "findings": [f.model_dump() for f in kept],
            "discarded": discarded,
            "unparseable": unparseable,
            "refusal": refusal,
        },
        broker,
    )
    close_review(root, worktree, session, record, fact)

    return ReviewOutcome(
        review_id=review.id,
        fact=fact,
        blockers=blockers,
        kept=kept,
        discarded=discarded,
        unparseable=unparseable,
        refusal=refusal,
        second_ask=session.second_ask,
        seat_error=seat_error,
    )


# ----------------------- #
# The pull-request trigger (S-0005/triggers): review on open and update,
# including pull requests no agent wrote.

PR_LEDGER = "pr-reviews.jsonl"


# ....................... #


@dataclass
class PrReviewOutcome:
    action: str  # reviewed | skipped | already reviewed
    detail: str = ""
    review_id: str | None = None
    findings: int = 0
    blockers: int = 0
    comment: str = ""


# ....................... #


def _reviewed_heads(root: Path) -> set[tuple[int, str]]:
    path = root / layout.TORVE_DIR / PR_LEDGER

    if not path.is_file():
        return set()

    heads: set[tuple[int, str]] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = cast("dict[str, Any]", json.loads(line))
            heads.add((int(row["pr"]), str(row["head"])))

    return heads


# ....................... #


def _pr_comment(review_id: str, head_sha: str, outcome: ReviewOutcome, degraded: bool) -> str:
    """Composed from the review's records, never the reviewer's prose —
    and posted by the runner: the reviewer holds no forge credential
    (S-0005/D-2)."""

    lines = [f"torve review — {review_id} · head {head_sha[:12]} · {outcome.fact}"]

    if degraded:
        lines.append(
            "reviewed without a task contract — degraded input: no "
            "scope, no inherited decisions; spec-drift findings "
            "unavailable (S-0005/D-8)"
        )

    ordered = outcome.blockers + [f for f in outcome.kept if f.severity != "blocker"]
    lines.extend(f"- [{f.severity}] {f.claim} ({f.evidence})" for f in ordered)
    lines += ["", "authority: the run store; this comment is a projection"]

    return "\n".join(lines)


# ....................... #


def review_pull_request(
    root: Path,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    scm: PrScm,
    vcs: PrVcs,
    number: int,
    token: str | None = None,
    broker: Broker | None = None,
    taken: Iterable[str] = (),
) -> PrReviewOutcome:
    """S-0005/triggers: skip rules first (draft, zero changed files, configured
    authors, not open); one review per head — the pull regime's debounce,
    since rapid pushes collapse into whatever head is current when the
    trigger fires and a head reviews at most once; then degraded or
    task-informed input (S-0005/D-8) and the findings posted back as one
    marker-deduped comment. Task state is never mutated here — blockers on
    a task-gated run escalate on that path; this one reports."""

    info = scm.pr_info(number)

    if info.state != "open":
        return PrReviewOutcome("skipped", f"pull request is {info.state or 'unknown'}")

    if info.draft:
        return PrReviewOutcome("skipped", "draft")

    if info.changed_files == 0:
        return PrReviewOutcome("skipped", "no changed files")

    if info.author in config.review.skip_authors:
        return PrReviewOutcome("skipped", f"author {info.author} is skipped by configuration")

    if (number, info.head_sha) in _reviewed_heads(root):
        return PrReviewOutcome("already reviewed", f"head {info.head_sha[:12]}")

    base_sha, head_sha = vcs.fetch_pr(root, number, info.base_ref, token)
    degraded, target = True, None

    for task_id in vcs.landed_tasks(root, base_sha, head_sha):
        contract = layout.task_file(root, task_id)

        if contract.is_file():
            from torve.gates.context import load_task

            degraded, target = False, load_task(contract)
            break

    if target is None:
        target = Task(
            id=f"PR-{number}",
            role="implement",
            intent=f"Pull request #{number}: {info.title}",
            decisions=[],
        )

    review = mint_review_task(
        root,
        target,
        intent=(
            f"Review pull request #{number} at {head_sha[:12]} — no task contract, degraded input."
            if degraded
            else None
        ),
        taken=taken,
    )

    workdir = root / naming.WORKTREE_DIR / f"{review.id}.pr"
    vcs.worktree_at(root, head_sha, workdir)

    # The reviewer's provider rides the same broker as any run (S-0021):
    # the review sandbox sees the broker's URL and the run-scoped token,
    # never a key.
    broker_handle: BrokerHandle | None = None

    if broker is not None:
        broker_handle = broker.open(
            review.id,
            run_routing(config, review, review_on=False),
            BrokerBudget(tokens=review.budget.tokens),
        )

    try:
        diff_text = vcs.diff(root, base_sha, head_sha)
        from torve.application.telemetry import config_hash, engine_event

        digest = config_hash(layout.gates_file(root), root, config)

        outcome = run_review(
            root,
            workdir,
            target,
            review,
            config,
            runtime,
            agent,
            diff_text,
            [],
            digest,
            degraded=degraded,
            broker=broker,
            broker_handle=broker_handle,
            # This path reports and never touches task state, so nothing
            # escalates and nothing revises: the record has to say so, or a
            # blocker found here is indistinguishable from one the
            # task-gated loop already dealt with (S-0005/A-9).
            trigger="pull_request",
        )

    finally:
        if broker is not None and broker_handle is not None:
            broker.close(broker_handle)

        vcs.remove_worktree(root, workdir)

    url = scm.comment(
        number,
        _pr_comment(review.id, head_sha, outcome, degraded),
        f"review:{number}:{head_sha[:12]}",
    )

    ledger = root / layout.TORVE_DIR / PR_LEDGER

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "pr": number,
                    "head": head_sha,
                    "review": review.id,
                    "at": stamp(),
                }
            )
            + "\n"
        )

    engine_event(
        root,
        "pr_review",
        {
            "pr": number,
            "head": head_sha,
            "review": review.id,
            "findings": len(outcome.kept),
            "blockers": len(outcome.blockers),
            "degraded": degraded,
        },
    )

    return PrReviewOutcome(
        "reviewed", outcome.fact, review.id, len(outcome.kept), len(outcome.blockers), url
    )


# ....................... #


# A surviving blocker's revision fact (S-0043 S-0043/D-1) starts with this —
# `_blocker_revisions_spent` is the only internal reader, so generation and
# counting can never drift apart. Public because the next attempt's tier
# selection reads the same fact (S-0117/D-1). Mirrors `_WALLCLOCK_MARKER` above.
BLOCKER_REVISION_MARKER = "review blocker (revision "


def is_blocker_revision_fact(fact: str) -> bool:
    """S-0117/D-1: whether a history fact is the one a surviving blocker's
    revision appends — the revision that sends the next attempt to the
    seat's `revision_variant`."""

    return fact.startswith(BLOCKER_REVISION_MARKER)


def _blocker_revisions_spent(state: RunState) -> int:
    """S-0043/D-1: how much of the run's `blocker_revisions` budget a surviving
    blocker has already spent — every revision fact `review_hook_fn` appends
    without a transition (the state stays GATED, retried), counted across
    the run's whole history, never just the last attempt."""

    return sum(1 for h in state.history if is_blocker_revision_fact(h["fact"]))


# The anchors a revision fact carries (S-0124/D-3): `path` and `line` of every
# blocking finding the round revised, as JSON after the human-readable fact.
# A critic thread has no reply address and leaves no `threads_file`, so the
# run's own history is where the next round finds what the last one named.
BLOCKER_ANCHORS_MARKER = " anchors="


def _citation_locates(path: str, start: int, end: int, root: Path) -> bool:
    """Whether one citation is a line or range `locate` accepts under *root*.

    The whole *path* must be read: `locate` splits a span at ` — ` or ` - ` and
    would accept a prefix, which is not the path the evidence cited."""

    span = f"{path}:{start}" if end == start else f"{path}:{start}-{end}"

    if span.startswith("`") or span.split(" — ")[0].split(" - ")[0].strip() != span:
        return False

    return locate(span, root) is None


def located_spans(evidence: str, root: Path) -> list[tuple[str, int, int]]:
    """Every citation in *evidence* that resolves under *root*, in order, each
    path normalised the way `locate` resolves it, so `./src/app.py:12` and
    `src/app.py:12` name one file (S-0127/D-4). The first is the finding's
    anchor; all of them are the spans a re-raise compares."""

    return [
        (posixpath.normpath(path), start, end)
        for path, start, end in citations(evidence, root)
        if _citation_locates(path, start, end, root)
    ]


def _finding_anchor(finding: Finding, root: Path | None = None) -> dict[str, Any] | None:
    """The `path`/`line` a finding is anchored at (S-0127/D-4): the first
    citation in its evidence that resolves under *root*, wherever it sits, or
    None when none does (a backticked command with its output).

    A critic's finding carries the anchor its own tree gave it. With no tree
    to resolve in, the first citation the evidence makes stands in."""

    if isinstance(finding, JudgedFinding):
        return {"path": finding.path, "line": finding.line} if finding.path else None

    if root is None:
        cited = CITED.search(finding.evidence)

        if cited is None:
            return None

        return {"path": posixpath.normpath(cited.group("path")), "line": int(cited.group("start"))}

    spans = located_spans(finding.evidence, root)

    return {"path": spans[0][0], "line": spans[0][1]} if spans else None


def _blocker_anchors(blockers: list[Finding], root: Path | None) -> list[dict[str, Any]]:
    """The anchors a revision round records for the findings it revises."""

    return [
        anchor for finding in blockers if (anchor := _finding_anchor(finding, root)) is not None
    ]


def _anchors_suffix(anchors: list[dict[str, Any]]) -> str:
    return f"{BLOCKER_ANCHORS_MARKER}{json.dumps(anchors, separators=(',', ':'))}"


# The judged head and every blocker's claim, beside the anchors (S-0125/D-2).
# They sit before `anchors=` so a reader that only wants anchors can take the
# tail of the fact unchanged. Claims are per blocker, because the anchorless
# ones have no `path` to pair a `detail` sentence with.
BLOCKER_HEAD_MARKER = " head="
BLOCKER_CLAIMS_MARKER = " blockers="


def _head_suffix(head: str) -> str:
    judged = head.strip()

    return f"{BLOCKER_HEAD_MARKER}{judged}" if judged else ""


def _claims_suffix(blockers: list[Finding], root: Path | None) -> str:
    if not blockers:
        return ""

    payload: list[dict[str, Any]] = []

    for finding in blockers:
        one: dict[str, Any] = {"claim": finding.claim}

        # S-0127/D-4: a critic's blocker records its kind and row, which the
        # next round's re-raise reads.
        if isinstance(finding, JudgedFinding):
            if finding.kind:
                one["kind"] = finding.kind

            if finding.row:
                one["row"] = finding.row

        anchor = _finding_anchor(finding, root)

        if anchor is not None:
            one.update(anchor)

        payload.append(one)

    return f"{BLOCKER_CLAIMS_MARKER}{json.dumps(payload, separators=(',', ':'))}"


def blocker_fact(
    review_id: str,
    round_no: int,
    budget: int,
    blockers: list[Finding],
    head: str = "",
    root: Path | None = None,
) -> str:
    """The `review blocker (revision k of n)` fact: its claims, each critic
    blocker's kind and row (S-0127/D-4), the head this review judged and its
    anchors, from which the next round reads both its `revision.diff` base
    and `blockers.yaml` (S-0125/D-2). *root* is the tree the blockers'
    citations resolve in."""

    detail = "; ".join(f.claim for f in blockers)

    return (
        f"{BLOCKER_REVISION_MARKER}{round_no} of {budget}): "
        f"{review_id}: {detail[:300]}"
        f"{_head_suffix(head)}"
        f"{_claims_suffix(blockers, root)}"
        f"{_anchors_suffix(_blocker_anchors(blockers, root))}"
    )


def _marker_json(
    text: str, marker: str, follower: str | None = None, end: int | None = None
) -> tuple[int | None, list[Any] | None]:
    """The first *marker* whose payload decodes as one complete JSON list and
    is followed by *follower* (or ends the text), and where the marker starts;
    (None, None) when there is none.

    A blocker's own claim can carry ` head=`, ` blockers=` or ` anchors=` as
    text, and the claim is itself inside the claims JSON. Splitting on a
    marker occurrence would then land inside a JSON string and lose the
    claims, so each candidate is decoded whole and only the one the engine
    wrote — the one whose value ends at the next marker — is taken
    (S-0125/D-2)."""

    limit = len(text) if end is None else end
    decoder = json.JSONDecoder()
    start = 0

    while True:
        position = text.find(marker, start, limit)

        if position < 0:
            return None, None

        payload = text[position + len(marker) : limit]

        try:
            value, consumed = decoder.raw_decode(payload)

        except ValueError:
            start = position + len(marker)
            continue

        rest = payload[consumed:]
        follows = not rest.strip() if follower is None else False

        if isinstance(value, list) and follower is not None:
            tail = rest.lstrip()
            lead = follower.strip()

            if tail.startswith(lead):
                try:
                    tail_value, tail_consumed = decoder.raw_decode(tail[len(lead) :])

                except ValueError:
                    tail_value, tail_consumed = None, 0

                follows = (
                    isinstance(tail_value, list) and not tail[len(lead) + tail_consumed :].strip()
                )

        if isinstance(value, list) and follows:
            return position, value

        start = position + len(marker)


def _blocker_rounds(state: RunState) -> list[dict[str, Any]]:
    """Every revision round the run recorded, oldest first, each with its
    number, the head it judged and the blockers it asked to fix (S-0125/D-2).
    A fact written before the head or per-blocker claims existed still reads:
    it has no head, and its anchors stand in for the claims."""

    rounds: list[dict[str, Any]] = []

    for entry in state.history:
        fact = str(entry.get("fact", ""))

        if not is_blocker_revision_fact(fact):
            continue

        anchor_position, anchors = _marker_json(fact, BLOCKER_ANCHORS_MARKER)
        claim_position, blockers = _marker_json(
            fact, BLOCKER_CLAIMS_MARKER, follower=BLOCKER_ANCHORS_MARKER
        )
        limit = (
            claim_position
            if claim_position is not None
            else (anchor_position if anchor_position is not None else len(fact))
        )
        head = ""
        start = 0

        while True:
            position = fact.find(BLOCKER_HEAD_MARKER, start, limit)

            if position < 0:
                break

            candidate = fact[position + len(BLOCKER_HEAD_MARKER) : limit].strip()

            if re.fullmatch(r"[0-9a-f]{7,64}", candidate):
                head = candidate
                break

            start = position + len(BLOCKER_HEAD_MARKER)

        if blockers is None:
            blockers = [
                one
                for one in (anchors or [])
                if isinstance(one, dict) and isinstance(one.get("path"), str)
            ]

        match = re.match(r"review blocker \(revision (\d+) of \d+\)", fact)
        number = int(match.group(1)) if match is not None else len(rounds) + 1

        rounds.append(
            {
                "round": number,
                "head": head,
                "blockers": [dict(one) for one in blockers if isinstance(one, dict)],
            }
        )

    return rounds


def tree_snapshot(worktree: Path) -> str:
    """The tree *worktree* holds, tracked and untracked files alike, as a git
    tree object written through a throwaway index — the engine commits an
    attempt only when it lands, so `HEAD` is never what a review judged
    (S-0125/D-2). Empty when *worktree* is not the top of a repository git
    can read, where the tree's paths would not be the worktree's."""

    try:
        if git(worktree, "rev-parse", "--show-prefix").strip():
            return ""

    except GitError:
        return ""

    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        for args in (("read-tree", "HEAD"), ("add", "-A"), ("write-tree",)):
            proc = subprocess.run(
                ["git", *NO_HOOKS, "-C", str(worktree), *args],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            if proc.returncode != 0:
                return ""

        return proc.stdout.strip()


def revision_for(run: Dispatch, state: RunState) -> Revision | None:
    """The revision attempt's inputs (S-0125/D-2), from the tree the most
    recent revision fact judged to the tree the worktree holds now, and every earlier round's
    blockers. None for a first attempt — no round recorded yet — and for a
    round whose head was never recorded, where no revision diff can be built
    and the attempt is decided as a first one."""

    rounds = _blocker_rounds(state)

    if not rounds:
        return None

    head = str(rounds[-1].get("head") or "")

    if not head:
        return None

    current = tree_snapshot(run.worktree)

    if not current:
        return None

    diff = _revision_diff(run.worktree, head, current)

    if diff is None:
        return None

    return Revision(diff=diff, blockers=_mapped_rounds(run.worktree, rounds, current))


def _revision_diff(worktree: Path, head: str, current: str) -> str | None:
    """The diff from judged tree *head* to tree *current*; None when git
    cannot build it.

    The engine's own files under `.torve/` (the feedback, the staged prompt,
    the run's bookkeeping) change between rounds and are no part of what the
    revision wrote. Every other `.torve` path stays: the manifest, the
    configuration and the specifications are reviewed artefacts, and a
    revision that changes one must be judged on it (S-0125/D-1)."""

    try:
        return git(
            worktree,
            "diff-tree",
            "-p",
            "-M",
            "--no-color",
            head,
            current,
            "--",
            ".",
            *REVISION_BOOKKEEPING,
        )

    except GitError:
        return None


def _mapped_rounds(
    worktree: Path, rounds: list[dict[str, Any]], current: str
) -> list[dict[str, Any]]:
    """Every round's blockers, each tagged with its round, its line mapped
    from the head that round judged to tree *current*. An anchor recorded
    two rounds back sits in that round's coordinates, so it is mapped
    through the diff from its own head, never the latest round's: code a
    revision inserted above an unresolved blocker moves the line the critic
    cites, and matching the number as recorded would carry a blocker no
    revision fixed (S-0125/D-1, S-0124/D-3). A round with no head, or one
    git cannot diff, keeps its lines as recorded."""

    diffs: dict[str, str] = {}
    mapped: list[dict[str, Any]] = []

    for one in rounds:
        head = str(one.get("head") or "")

        if head and current and head not in diffs:
            diffs[head] = _revision_diff(worktree, head, current) or ""

        diff = diffs.get(head, "")

        for blocker in one["blockers"]:
            entry = {**blocker, "round": one["round"]}
            path, line = entry.get("path"), entry.get("line")

            if diff and isinstance(path, str) and isinstance(line, int):
                entry["line"] = _mapped_line(diff, posixpath.normpath(path), line)

            mapped.append(entry)

    return mapped


def mapped_blocker_anchors(worktree: Path, state: RunState) -> list[dict[str, Any]]:
    """Every earlier round's blockers, their anchors in the coordinates of the
    tree *worktree* holds now, what a revision's findings re-raise against
    (S-0124/D-3, S-0127/D-4). A blocker with no anchor is kept, since its row
    alone can be repeated."""

    return _mapped_rounds(worktree, _blocker_rounds(state), tree_snapshot(worktree))


def _anchor_reraised(
    blockers: list[Finding], previous: list[dict[str, Any]], root: Path | None = None
) -> tuple[str, int | None] | None:
    """The finding the classic reviewer's round re-raises (S-0124/D-3): one
    anchored in the same file within `threads.WINDOW` lines of an anchor the
    previous round recorded, or in the same file when either has no line. A
    re-raise stops the run instead of buying another round. A critic's
    re-raise is `decide`'s, by `repeats_blocker` (S-0127/D-4).

    *previous* is in the current tree's coordinates when a revision's
    anchors were mapped there (`mapped_blocker_anchors`); *root* is the tree
    the findings' citations resolve in."""

    for finding in blockers:
        anchor = _finding_anchor(finding, root)

        if anchor is None:
            continue

        path, line = anchor["path"], anchor["line"]

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

            if line is None or recorded is None or abs(recorded - line) <= threads.WINDOW:
                return path, line

    return None


# S-0127/D-11: the words a claim is compared by. Function words say nothing
# about which defect a claim names.
_CLAIM_WORD = re.compile(r"[a-z0-9_]+")
_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "does", "for",
        "from", "has", "have", "in", "into", "is", "it", "its", "not", "of", "on", "or", "so",
        "than", "that", "the", "then", "there", "this", "to", "was", "when", "where", "which",
        "while", "with",
    }
)  # fmt: skip


def _claim_stems(claim: str) -> set[str]:
    """The stems a claim names: each word of three letters or more that is no
    function word, a plural `s` dropped, cut to its first five letters, so
    `counts`, `counter` and `count` read alike."""

    stems: set[str] = set()

    for word in _CLAIM_WORD.findall(claim.lower()):
        if word in _FUNCTION_WORDS:
            continue

        stem = word[:-1] if len(word) > 3 and word.endswith("s") else word

        if len(stem) >= 3:
            stems.add(stem[:5])

    return stems


def claims_match(claim: str, earlier: str) -> bool:
    """Whether *claim* names the defect *earlier* named (S-0127/D-11): the two
    claims' stems are the same set, or they share at least two stems and at
    least half the stems of the shorter one.

    S-0127/D-11 left the measure to the phase that builds it, and this is the
    choice: word overlap, so the engine and no model decides what repeats
    (S-0122/D-1), taken against the shorter claim, so a terse claim and a
    wordy one about the same bound and the same branch still match, and
    needing two shared stems, so two claims that share one identifier — a new
    defect cited on the line after an earlier one, T-0804's shape — do not.
    Too loose a measure brings that false re-raise back; too strict a one
    lets a repeated defect spend a revision, which the budget then bounds."""

    ours, theirs = _claim_stems(claim), _claim_stems(earlier)

    if ours and ours == theirs:
        return True

    shared = len(ours & theirs)

    return shared >= 2 and 2 * shared >= min(len(ours), len(theirs))


def repeats_blocker(
    kind: str,
    row: str | None,
    spans: Iterable[tuple[str, int, int]],
    claim: str,
    earlier: Iterable[Mapping[str, Any]],
    match: Callable[[str, str], bool] = claims_match,
) -> bool:
    """Whether a revision's blocker repeats one of *earlier* (S-0127/D-4): it
    names the same row, or it has the same *kind*, a citation in *spans* that
    overlaps the earlier blocker's anchor as `_mapped_rounds` maps it into the
    current tree, and a claim *match* reads as the earlier one's (D-11).

    A blocker beside an earlier anchor that repeats neither is a new defect,
    which spends the revision budget as any blocker does. A blocker recorded
    before kinds were (no `kind`) is repeated only by its row."""

    cited = [(posixpath.normpath(path), start, end) for path, start, end in spans]

    for one in earlier:
        if row is not None and one.get("row") == row:
            return True

        path, line = one.get("path"), one.get("line")

        if one.get("kind") != kind or not isinstance(path, str) or not isinstance(line, int):
            continue

        recorded = posixpath.normpath(path)

        overlaps = any(
            cited_path == recorded and start <= line <= end for cited_path, start, end in cited
        )

        if overlaps and match(claim, str(one.get("claim", ""))):
            return True

    return False


def _reraised_at(outcome: ReviewOutcome, worktree: Path, state: RunState) -> str | None:
    """Where *outcome* re-raises an earlier round's blocker, or None. A critic
    verdict's re-raise is the one `decide` read by row, kind, span and claim
    (S-0127/D-4); the classic reviewer's is still an anchor near an earlier
    one (S-0124/D-3)."""

    from torve.application.critic import CriticVerdict

    if isinstance(outcome, CriticVerdict):
        repeated = outcome.decision.reraised if outcome.decision is not None else []

        if not repeated:
            return None

        spans = located_spans(repeated[0].evidence, worktree)

        return f"{spans[0][0]}:{spans[0][1]}" if spans else str(repeated[0].row)

    found = _anchor_reraised(outcome.blockers, mapped_blocker_anchors(worktree, state), worktree)

    if found is None:
        return None

    path, line = found

    return path if line is None else f"{path}:{line}"


# ....................... #


def reviewer_for(run: Dispatch) -> Agent:
    """The reviewer this run judges with (S-0005). Resolved before the
    broker opens as well as at the step itself, so a run configured for
    review with no reviewer wired fails at setup rather than after an
    attempt has already been paid for."""

    if run.deps.review_agent is None:
        raise ValueError(
            "review is configured (review.on: task_gated) but no reviewer agent was provided"
        )

    return run.deps.review_agent


# ....................... #


async def review_step(run: Dispatch, state: RunState) -> str | None:
    """Judge the gate-green candidate (S-0005/D-11). Returns the fact for the
    reviewed transition, None when the run must not land — a blocker inside
    its revision budget retries in place (S-0043/D-1), and a spent budget, an
    unparseable verdict or a broker refusal escalates the target."""

    taken = run.deps.taken() if run.deps.taken is not None else ()
    outcome: ReviewOutcome

    if run.preselected_review is not None:
        # S-0122/D-7: the critic judged this candidate while selection chose
        # it; its verdict is this review's, never asked for twice. Its own
        # review id is the one that ran, so no task is minted here
        # (S-0123/D-4).
        outcome, run.preselected_review = run.preselected_review, None

    elif run.config.review.mode == "critic":
        # S-0122/D-6: the critic judges the candidate from the evidence the
        # gate pass measured; what follows treats its verdict as any review's.
        from torve.application.critic import judge

        review_task = mint_review_task(run.root, run.task, taken=taken)
        outcome = await asyncio.to_thread(judge, run, review_task, state)

    else:
        review_task = mint_review_task(run.root, run.task, taken=taken)
        outcome = await asyncio.to_thread(
            run_review,
            run.root,
            run.worktree,
            run.task,
            review_task,
            run.config,
            run.deps.runtime,
            reviewer_for(run),
            run.last_pass.patch,
            list(run.last_pass.results),
            run.last_pass.digest,
            broker=run.deps.broker,
            broker_handle=run.broker_handle,
        )

    # S-0101/D-4: the findings ride the record, not only this host's stream, so
    # a host that did not run the review reads them from the board. Emitted
    # before the branches below so a refusal or an unreadable verdict is
    # recorded as the review it was — with no findings, which is what it holds.
    emit(
        run,
        "review_recorded",
        1,
        review_id=outcome.review_id,
        findings=len(outcome.kept),
        blockers=len(outcome.blockers),
        items=review_items(outcome.kept, run.worktree),
        trigger="task_gated",
    )

    # The reviewer spends the same run budget: a refusal there is the
    # same cost_anomaly, stopped on the run that overspent (S-0021/D-6).
    broker, handle = run.deps.broker, run.broker_handle

    if broker is not None and handle is not None:
        usage = broker.usage(handle)

        if usage.refusals.get("budget"):
            state.escalate(
                EscalationReason.COST_ANOMALY,
                f"broker refused {usage.refusals['budget']} request(s) "
                "past the run's token budget during review",
            )

            return None

    if outcome.refusal is not None:
        # S-0054/D-15: a document that fails the schema is refused by name —
        # the reviewer was asked twice (S-0086/D-1), so the refusal escalates as
        # the verdict it could not deliver, never as a clean review.
        state.escalate(
            EscalationReason.GATE_INFRASTRUCTURE_FAILURE,
            f"{outcome.review_id}: review output refused: {outcome.refusal}",
        )

        return None

    if outcome.unparseable:
        # Fail closed (S-0005/D-4): a verdict that cannot be read must
        # not promote — "no findings recorded" once waved a review
        # carrying two blockers straight to ready.
        state.escalate(
            EscalationReason.GATE_INFRASTRUCTURE_FAILURE,
            f"{outcome.review_id}: the reviewer's seat gave no verdict "
            f"({outcome.seat_error}) — requeue once the seat answers; no verdict "
            "is never a clean review"
            if outcome.seat_error
            else f"{outcome.review_id}: review output unparseable — an "
            "unreadable verdict is a review infrastructure failure, "
            "never a clean review",
        )

        return None

    if outcome.blockers:
        from torve.application.feedback import capture_feedback

        detail = "; ".join(f.claim for f in outcome.blockers)
        spent = _blocker_revisions_spent(state)
        budget = run.config.review.resolved_blocker_revisions()

        if spent < budget:
            where = _reraised_at(outcome, run.worktree, state)

            if where is not None:
                state.escalate(
                    EscalationReason.BLOCKER_FINDING,
                    f"{outcome.review_id}: blocker re-raised at {where}: {detail[:300]}",
                )

                return None

            # S-0043 S-0043/D-1/D-43.2: the blockers and the convicted
            # candidate diff ride the S-0005/the-revision-loop-added-by-a-32-2026-08-24 feedback record —
            # the existing S-0005/D-13 plant/frame mechanics carry it into
            # the next attempt with no new delivery path.
            # S-0122/D-8: a critic's revision is handed its blocking findings
            # with their repros and the measurements the candidate failed —
            # never the critic's trace, which no revision reads.
            capture_feedback(
                run.root,
                run.task.id,
                run.last_pass.patch,
                outcome.threads
                if outcome.threads is not None
                else blocker_threads(outcome.blockers, outcome.review_id),
                measured=outcome.measured,
            )
            state.history.append(
                {
                    "at": state.heartbeat,
                    "from": str(state.state),
                    "to": str(state.state),
                    "fact": blocker_fact(
                        outcome.review_id,
                        spent + 1,
                        budget,
                        outcome.blockers,
                        outcome.head,
                        run.worktree,
                    ),
                }
            )
            state.save()
            return None

        state.escalate(EscalationReason.BLOCKER_FINDING, f"{outcome.review_id}: {detail[:300]}")

        return None

    # The verdict the lane's require_review predicate reads
    # (S-0006/D-14, S-0006/A-3); cleared on the next entry to running.
    state.reviewed_by = outcome.review_id

    return f"{outcome.fact} ({outcome.review_id})"
