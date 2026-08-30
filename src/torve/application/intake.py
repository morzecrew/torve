"""Intake and the drafting run (RFC 0020): a commander's request becomes
draft task contracts through a run — role `draft`, the planner tier's seat,
read-only workspace — whose gate is the deterministic contract lint
(D-20.3). Drafts carry request-local `DRAFT-n` refs; task ids exist only
from adoption, minted under the engine lock atomically with the commit
that makes the contracts real (D-20.4). Adoption is the human signature
RFC 0007 §6 requires — relocated, never removed (D-20.1). The planner
module stays model-free (D-7.1): everything here runs under the runner's
machinery, the same boundary review-as-a-run proved out.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application import sizing
from torve.application.ports import (
    Agent,
    AgentContext,
    Broker,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    Runtime,
    SandboxSpec,
)
from torve.application.runstate import RunState
from torve.application.telemetry import broker_block, engine_event
from torve.base import naming
from torve.config import layout
from torve.config.runconfig import RunnerConfig, configured_images, image_for, tier_for
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import SCHEMA_VERSION, Budget, Scope, Task

# ----------------------- #

DRAFTS_FILE = "drafts.json"
DRAFT_REF = re.compile(r"^DRAFT-(\d+)$")
# One row per claimed request (phase 2): {issue, task, at} — the leg's
# scope. CLI-minted drafting runs never enter it, so the tick and an
# operator's terminal cannot race one run.
INTAKE_LEDGER = "intake.jsonl"
# Adoption's terminal marker: with state and drafts both consumed, this
# is what tells a fresh mint from an adopted one.
ADOPTED_FILE = "adopted.json"
RFC_LINE = re.compile(r"^rfc:\s*(\S+)\s*$", re.MULTILINE)


# ....................... #


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    intent: str = ""
    scope: Scope = Field(default_factory=Scope)
    acceptance: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


# ....................... #


class DraftsDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    drafts: list[Draft]
    rationale: str = ""


# ....................... #

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ....................... #


def parse_drafts(output: str) -> DraftsDocument | None:
    """The last JSON document with a `drafts` key anywhere in the output —
    the findings-parse discipline (D-5.4's sibling): unparseable is None,
    recorded by the caller, never invented as an empty batch."""

    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last: object | None = None

    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())

        except json.JSONDecodeError:
            continue

        if isinstance(document, dict) and "drafts" in document:
            last = cast("dict[str, Any]", document)

    if last is None:
        return None

    try:
        return DraftsDocument.model_validate(last)

    except ValidationError:
        return None


# ....................... #


# The contract lint (D-20.3): deterministic, engine-side, no model. A red
# lint is a red attempt; every error names the draft and the field.
def _glob_errors(ref: str, tree_paths: list[Path], globs: list[str], kind: str) -> list[str]:
    errors: list[str] = []

    for pattern in globs:
        if pattern.startswith("/") or ".." in pattern.split("/"):
            errors.append(f"{ref}: {kind} glob {pattern!r} escapes the tree")
            continue

        if any(ch in pattern for ch in "*?[") and not any(
            _matched(p, [pattern]) for p in tree_paths
        ):
            errors.append(
                f"{ref}: {kind} glob {pattern!r} matches nothing in the tree "
                "— a wildcard that can never match checks nothing"
            )

    return errors


# ....................... #


def _tree_paths(tree: Path) -> list[Path]:
    return [p.relative_to(tree) for p in tree.rglob("*") if p.is_file() and ".git" not in p.parts]


# ....................... #


def _matched(path: Path, globs: list[str]) -> bool:
    # fnmatch treats ** and * alike over the whole string, so "src/**"
    # covers depth the right-anchored Path.match cannot; both run because
    # each catches shapes the other misses.
    return any(path.match(g) or fnmatch(str(path), g) for g in globs)


# ....................... #


def lint_drafts(
    tree: Path,
    document: DraftsDocument,
    max_drafts: int,
    *,
    allow_dependency_order: bool = False,
) -> list[str]:
    """Every mechanical check a human should never have to make (D-20.3).
    The T-0113 rule is the first learned rule: a draft touching an existing
    module must allow that module's existing test file — the escalation
    that produced it burned a full poison ceiling on exactly this.

    `allow_dependency_order` relaxes the pairwise-scope check for a
    decomposition batch (RFC 0026 D-26.3): two drafts may overlap when an
    explicit `depends_on` edge orders them, instead of requiring every pair
    disjoint the way an ordinary intake batch — dispatched in parallel —
    must."""

    from torve.application.planner import globs_intersect

    errors: list[str] = []
    tree_paths = _tree_paths(tree)
    drafts = document.drafts

    if not drafts:
        return ["the document holds no drafts — an empty batch is a refusal, not a result"]

    if len(drafts) > max_drafts:
        errors.append(
            f"{len(drafts)} drafts exceed the ceiling of {max_drafts} (intake.max_drafts, D-20.8)"
        )

    refs = [d.ref for d in drafts]

    if len(set(refs)) != len(refs):
        errors.append("draft refs are not unique")

    for draft in drafts:
        ref = draft.ref

        if not DRAFT_REF.match(ref):
            errors.append(f"{ref!r}: refs are DRAFT-<n> — ids exist only from adoption (D-20.4)")

        if not draft.intent.strip():
            errors.append(f"{ref}: intent is empty")

        if not draft.acceptance:
            errors.append(f"{ref}: acceptance is empty — nothing would judge the work")

        for command in draft.acceptance:
            try:
                if not shlex.split(command):
                    raise ValueError

            except ValueError:
                errors.append(f"{ref}: acceptance command {command!r} does not shell-parse")

        if not draft.scope.allow:
            errors.append(
                f"{ref}: scope.allow is empty — an unconstrained draft contends with everything"
            )

        errors.extend(_glob_errors(ref, tree_paths, draft.scope.allow, "allow"))

        for pattern in draft.scope.allow:
            if pattern in draft.scope.deny:
                errors.append(f"{ref}: {pattern!r} is both allowed and denied")

        for dep in draft.depends_on:
            if dep == ref:
                errors.append(f"{ref}: depends on itself")
            elif dep not in refs:
                errors.append(f"{ref}: depends on unknown draft {dep!r}")

        # The T-0113 rule: existing modules bring their existing tests.
        for path in tree_paths:
            if (
                path.suffix == ".py"
                and "tests" not in path.parts
                and _matched(path, draft.scope.allow)
            ):
                test_file = Path("tests") / f"test_{path.stem}.py"

                if (tree / test_file).is_file() and not _matched(test_file, draft.scope.allow):
                    errors.append(
                        f"{ref}: allows existing module {path} but not its "
                        f"existing test file {test_file} — the T-0113 rule"
                    )

    for i, one in enumerate(drafts):
        for other in drafts[i + 1 :]:
            if not globs_intersect(one.scope.allow, other.scope.allow):
                continue

            if allow_dependency_order and (
                other.ref in one.depends_on or one.ref in other.depends_on
            ):
                continue

            errors.append(
                f"{one.ref} and {other.ref}: scopes intersect — a batch "
                "must be dispatchable in parallel"
                if not allow_dependency_order
                else f"{one.ref} and {other.ref}: scopes intersect with "
                "neither depending on the other"
            )

    return errors


# ....................... #


def _escapes_parent_scope(
    child_allow: list[str], parent_allow: list[str], tree_paths: list[Path]
) -> str | None:
    """The first path a child's allow-set reaches that the parent's does
    not — checked over every file the tree already holds plus every literal
    (non-wildcard) path the child names, since a decomposition may allow a
    file the drafter's read-only tree does not have yet."""

    literals = [Path(p) for p in child_allow if not any(ch in p for ch in "*?[")]

    for path in [*tree_paths, *literals]:
        if _matched(path, child_allow) and not _matched(path, parent_allow):
            return str(path)

    return None


# ....................... #


def lint_decomposition(
    tree: Path, document: DraftsDocument, parent: Task, max_drafts: int
) -> list[str]:
    """The decomposition batch's own four rules (RFC 0026 §5.2), layered on
    the ordinary contract lint: D-26.2 (a child never escapes the parent's
    allow-set), D-26.3 (`lint_drafts`' own relaxed pairwise check —
    overlap only where a `depends_on` edge orders it), D-26.4 (the parent's
    acceptance battery is distributed across the children, never dropped),
    and D-26.12's sibling rule — every child is itself right-sized; a
    decomposition that yields an oversized child has not decomposed. The
    depth bound (D-26.12) is enforced earlier, at the drafting run's mint."""

    errors = lint_drafts(tree, document, max_drafts, allow_dependency_order=True)
    drafts = document.drafts

    if not drafts:
        return errors  # lint_drafts already refused the empty batch

    tree_paths = _tree_paths(tree)

    for draft in drafts:
        escaped = _escapes_parent_scope(draft.scope.allow, parent.scope.allow, tree_paths)

        if escaped is not None:
            errors.append(
                f"{draft.ref}: {escaped!r} is outside {parent.id}'s scope.allow "
                "— a child scope is a grant the parent's scope did not sign"
            )

    carried = {command for draft in drafts for command in draft.acceptance}

    for command in parent.acceptance:
        if command not in carried:
            errors.append(
                f"{parent.id}: acceptance command {command!r} is dropped — no "
                "child carries it (the battery may be distributed, never dropped)"
            )

    for draft in drafts:
        verdict = sizing.estimate_scope(draft.scope, draft.acceptance)

        if verdict.size == "too_large":
            errors.append(
                f"{draft.ref}: too large ({'; '.join(verdict.reasons)}) — a "
                "decomposition that yields an oversized child has not decomposed"
            )

    return errors


# ....................... #

# RFC 0027 §5.1/§5.3: the committed configuration tree a configuration
# drafting run proposes changes to — sandbox definitions and the tier
# blocks live in `.torve/config.yaml`, nowhere else (D-13.3).
CONFIGURATION_SURFACES = [".torve/sandbox/**", ".torve/config.yaml"]


def _configuration_paths(allow: list[str]) -> list[str]:
    return [p for p in allow if _matched(Path(p), CONFIGURATION_SURFACES)]


def lint_configuration_change(
    tree: Path, document: DraftsDocument, config: RunnerConfig, runtime: Runtime
) -> list[str]:
    """RFC 0027 D-27.6: the drafting gate for a configuration drafting run —
    deterministic, no model, fires only when a draft's scope names a
    configuration surface, so an ordinary intake batch pays nothing extra
    and no new task field distinguishes the two (D-27.4's "no config-specific
    verb"). Confines the diff to `CONFIGURATION_SURFACES` (never mixed with
    application code in one draft), then grounds the proposal in a *working*
    baseline: the committed configuration parses under its schema, every
    sandbox definition a draft names still builds clean, and every
    configured image still resolves (`torve doctor`'s image check, D-17.2).
    Building here is the drafting gate's own deterministic check — the same
    act as an operator running `torve sandbox build` before adopting, never
    a mid-run build on the dispatch path (D-17.3 untouched)."""

    errors: list[str] = []
    touched_names: set[str] = set()
    any_configuration = False

    for draft in document.drafts:
        config_paths = _configuration_paths(draft.scope.allow)

        if not config_paths:
            continue

        any_configuration = True

        if len(config_paths) != len(draft.scope.allow):
            errors.append(
                f"{draft.ref}: mixes configuration surface(s) "
                f"({', '.join(config_paths)}) with other path(s) — a configuration "
                "change is confined to CONFIGURATION_SURFACES, never bundled with "
                "application code (D-27.6)"
            )
            continue

        for path in config_paths:
            parts = Path(path).parts

            if len(parts) >= 3 and parts[0] == ".torve" and parts[1] == "sandbox":
                touched_names.add(parts[2])

    if not any_configuration:
        return errors  # no draft names a configuration surface — an ordinary batch

    config_path = tree / ".torve" / "config.yaml"

    if config_path.is_file():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            RunnerConfig.model_validate(raw or {})

        except (yaml.YAMLError, ValidationError) as exc:
            errors.append(f"configuration-change lint: {config_path} does not parse: {exc}")

    for name in sorted(touched_names):
        definition = tree / ".torve" / "sandbox" / name

        if not definition.is_dir():
            continue  # a newly proposed definition does not exist yet to build

        try:
            runtime.build_image(definition, f"torve-agent:{name}")

        except Exception as exc:  # the build tool's own failure is the message
            errors.append(f"configuration-change lint: image {name!r} failed to build: {exc}")

    from torve.application.migrate import check_forze_pin

    pin_ok, pin_message = check_forze_pin()

    if not pin_ok:
        errors.append(f"configuration-change lint: torve doctor is red — {pin_message}")

    for image in configured_images(config):
        if runtime.resolve_image(image) is None:
            errors.append(
                f"configuration-change lint: torve doctor is red — image {image!r} "
                "is configured but not present in the runtime"
            )

    return errors


# ....................... #


def lint_contract(tree: Path, contract: Path, max_drafts: int = 1) -> list[str]:
    """The standalone face: the same protection for a hand-minted contract
    (RFC 0020 §5.2) — the operator path stays legal and gets safer."""

    try:
        raw = yaml.safe_load(contract.read_text(encoding="utf-8"))

    except yaml.YAMLError as exc:
        return [f"{contract.name}: not YAML ({exc})"]

    if not isinstance(raw, dict):
        return [f"{contract.name}: not a mapping"]

    data = cast("dict[str, Any]", raw)

    try:
        task = Task.model_validate({**data, "decisions": data.get("decisions", [])})

    except ValidationError as exc:
        return [f"{contract.name}: {exc.errors()[0]['msg']}"]

    if task.role != "implement":
        # A review carries no acceptance by contract law (D-5.10), a draft
        # none by D-20.3 — the batch checks below would misread both.
        return []

    document = DraftsDocument(
        drafts=[
            Draft(ref="DRAFT-1", intent=task.intent, scope=task.scope, acceptance=task.acceptance)
        ]
    )

    return [e.replace("DRAFT-1", task.id) for e in lint_drafts(tree, document, max_drafts)]


# ....................... #


# The drafting run.
def mint_intake_task(
    root: Path, request: str, config: RunnerConfig, rfc: str | None = None
) -> Task:
    """Engine-minted at request time, like a review at gated — the id here
    names the drafting run itself, never its output (D-20.4)."""

    from torve.application.planner import next_task_number

    task = Task(
        id=f"T-{next_task_number(root):04d}",
        rfc=rfc,
        role="draft",
        intent=request,
        decisions=[],
        budget=Budget(iterations=config.intake.iterations),
        tier="planner",
    )

    contract_dir = root / layout.TORVE_DIR / "tasks" / task.id
    contract_dir.mkdir(parents=True, exist_ok=True)
    document = task.model_dump(exclude_defaults=True)
    document["schema_version"] = SCHEMA_VERSION
    document["decisions"] = []

    (contract_dir / "contract.yaml").write_text(
        "# Minted by the engine at intake — drafting follows the request.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    return task


# ....................... #


def _decomposition_depth(root: Path, task_id: str) -> int:
    """Hops up a task's `parent` chain (RFC 0026 D-26.12): 0 for an
    undecomposed contract, 1 for a first-round child, 2 for a
    second-round grandchild — the point past which a third round refuses."""

    from torve.gates.context import load_task

    depth = 0
    seen = {task_id}
    current = task_id

    while True:
        contract = layout.task_file(root, current)

        if not contract.is_file():
            return depth

        parent = load_task(contract).parent

        if not parent or parent in seen:
            return depth

        depth += 1
        seen.add(parent)
        current = parent


# ....................... #


# The decomposition run (RFC 0026 §5.2): a draft-role run exactly RFC
# 0020's shape, pointed at a contract instead of a commander's prose.
def mint_decomposition_task(root: Path, parent_id: str, config: RunnerConfig) -> Task:
    """Refuses a third decomposition round by name (D-26.12) before any
    drafting compute is spent; otherwise mints the drafting run the same
    way `mint_intake_task` does, its single target naming what it
    decomposes."""

    from torve.application.planner import next_task_number
    from torve.gates.context import load_task

    parent_contract = layout.task_file(root, parent_id)

    if not parent_contract.is_file():
        raise ValueError(f"no contract at {parent_contract} to decompose")

    parent = load_task(parent_contract)
    depth = _decomposition_depth(root, parent_id)

    if depth >= 2:
        raise ValueError(
            f"{parent_id} is already {depth} decomposition round(s) deep — a "
            "third round is refused; the source document's phasing needs an "
            "amendment instead"
        )

    task = Task(
        id=f"T-{next_task_number(root):04d}",
        rfc=parent.rfc,
        role="draft",
        intent=f"Decompose {parent_id}: {parent.intent}",
        targets=[parent_id],
        decisions=[],
        budget=Budget(iterations=config.intake.iterations),
        tier="planner",
    )

    contract_dir = root / layout.TORVE_DIR / "tasks" / task.id
    contract_dir.mkdir(parents=True, exist_ok=True)
    document = task.model_dump(exclude_defaults=True)
    document["schema_version"] = SCHEMA_VERSION
    document["decisions"] = []

    (contract_dir / "contract.yaml").write_text(
        "# Minted by the engine — a decomposition drafting run.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    return task


# ....................... #


def _harness_facts(root: Path, config: RunnerConfig) -> list[str]:
    """RFC 0027 D-27.5: one line per configured tier — including a variant
    nothing uses, denominator zero and visible — plus the quasi-experiment
    caveat printed verbatim beside them (RFC 0004 §6a, D-22.7's rule applied
    a fourth time: never paraphrased, never carrying a corpus coordinate a
    prompt reader has no corpus to resolve)."""

    from torve.application.projections import QUASI_EXPERIMENT_CAVEAT, harness_populations

    populations = harness_populations(root, config)
    lines: list[str] = []

    for pop in populations:
        cost_parts: list[str] = []

        if pop["cost_usd_broker_n"]:
            cost_parts.append(
                f"${pop['cost_usd_broker']:.2f} broker-measured (n={pop['cost_usd_broker_n']})"
            )

        if pop["cost_usd_self_reported_n"]:
            cost_parts.append(
                f"${pop['cost_usd_self_reported']:.2f} self-reported "
                f"(n={pop['cost_usd_self_reported_n']})"
            )

        escalations = (
            ", ".join(f"{reason} ({n})" for reason, n in sorted(pop["escalations_by_reason"].items()))
            or "none"
        )

        lines.append(
            f"harness tier {pop['tier']}: {pop['attempts']} run(s), "
            f"cost {' + '.join(cost_parts) or 'none recorded'}, escalations: {escalations}, "
            f"unparseable reviews: {pop['unparseable_reviews']}, "
            f"digest {pop['current_digest'] or 'unresolved'}"
        )

    if lines:
        lines.append(QUASI_EXPERIMENT_CAVEAT)

    return lines


# ....................... #


def execution_facts(root: Path, config: RunnerConfig) -> str:
    """RFC 0020 phase 3: what the loop knows that a fresh drafter cannot —
    the live escalation queue, contended paths, recent landings, and (RFC
    0027 D-27.5) the harness populations every configured tier has produced.
    Bounded reads (telemetry tail), empty string when there is nothing to
    say."""

    lines: list[str] = []

    escalated = [
        (s.task_id, s.escalation.reason)
        for s in RunState.load_all(root / naming.WORKTREE_DIR)
        if s.state is TaskState.ESCALATED and s.escalation
    ]

    if escalated:
        lines.append("escalated now: " + "; ".join(f"{t} ({r})" for t, r in escalated[:6]))

    telemetry = root / layout.TORVE_DIR / "telemetry.jsonl"

    if telemetry.is_file():
        contended: dict[str, int] = {}
        landed: list[str] = []

        for line in telemetry.read_text(encoding="utf-8").splitlines()[-500:]:
            try:
                record = cast("dict[str, Any]", json.loads(line))

            except json.JSONDecodeError:
                continue

            if record.get("event") == "blocked_dispatch":
                path = str(record.get("path", ""))

                if path:
                    contended[path] = contended.get(path, 0) + 1
            elif record.get("event") == "lane_landed":
                landed.append(str(record.get("task", "")))

        if contended:
            top = sorted(contended.items(), key=lambda kv: -kv[1])[:4]

            lines.append(
                "contended paths (avoid scoping over these): "
                + "; ".join(f"{p} ({n}x)" for p, n in top)
            )

        if landed:
            lines.append("recently landed: " + ", ".join(landed[-8:]))

        lines.extend(_harness_facts(root, config))

    return "\n".join(f"- {entry}" for entry in lines)


# ....................... #


def build_intake_prompt(
    request: str,
    tree: Path,
    max_drafts: int,
    lint_errors: list[str] | None = None,
    feedback: str | None = None,
    facts: str = "",
    parent: Task | None = None,
) -> str:
    """The drafter's whole input: the request, the tree, the ceiling, and —
    on a retry — the lint's exact refusals. The calibration paragraph
    matters as much as review's: one honest draft beats a decomposition
    performed to look thorough.

    `parent` turns this into a decomposition run's prompt (RFC 0026
    D-26.10): the tree listing narrows to the parent's own scope.allow, and
    the parent's contract — scope, acceptance, the rules a decomposition
    must satisfy — rides beside the request. The parent's inherited
    decision rows do not: children re-inherit them from the governing
    document at adoption (D-20.9's existing rule, unchanged), so carrying
    a second copy into the prompt would buy nothing but tokens."""

    paths = _tree_paths(tree)

    if parent is not None:
        paths = [p for p in paths if _matched(p, parent.scope.allow)]

    listing = "\n".join(sorted(str(p) for p in paths)[:400])
    retry_block = ""

    if lint_errors:
        joined = "\n".join(f"- {e}" for e in lint_errors)

        retry_block = (
            f"\n## Your previous batch was refused by the lint\n\n"
            f"{joined}\n\nFix exactly these; do not reshuffle what passed.\n"
        )

    feedback_block = ""

    if feedback and feedback.strip():
        feedback_block = (
            "\n## The commander's feedback on your previous "
            f"drafts\n\n{feedback.strip()}\n\nRevise the batch "
            "to answer it; keep what the feedback does not "
            "touch.\n"
        )

    facts_block = ""

    if facts:
        facts_block = f"\n## Recent execution facts (read-only context)\n\n{facts}\n"

    parent_block = ""
    rules_block = (
        "Decompose the request into at most {max_drafts} draft contract(s) — one is\n"
        "the normal, frequent answer; split only where the pieces are genuinely\n"
        "independent and their file scopes are disjoint."
    )

    if parent is not None:
        parent_block = (
            f"\n## The contract you are decomposing ({parent.id})\n\n"
            f"- scope.allow: {', '.join(parent.scope.allow)}\n"
            "- acceptance:\n"
            + "\n".join(f"  - {command}" for command in parent.acceptance)
            + "\n"
        )
        rules_block = (
            f"Split {parent.id} into at most {{max_drafts}} draft contract(s). Every\n"
            "child's scope.allow must fit inside the parent's scope.allow above —\n"
            "a child may narrow it, never widen it. Children must be pairwise\n"
            "scope-disjoint, or may overlap only where an explicit depends_on edge\n"
            "orders them. Every acceptance command listed above must be carried by\n"
            "at least one child — the battery may be distributed, never dropped.\n"
            "Each child must be right-sized on its own; do not draft an oversized\n"
            "child."
        )

    return f"""# {"Decompose a contract" if parent is not None else "Draft task contracts"}

You are drafting contracts for work, not doing the work. The workspace is
read-only; read it to write honest file scopes and acceptance commands.

## The request

{request}
{parent_block}{feedback_block}{facts_block}
## The repository tree

```text
{listing}
```
{retry_block}
## What to produce

{rules_block.format(max_drafts=max_drafts)} Each draft carries: `ref`
("DRAFT-1", "DRAFT-2", …), `intent` (one paragraph: what changes and why —
never steps), `scope` with `allow`/`deny` file globs (every file the work
may touch, including test files — a draft touching an existing module must
allow that module's existing test file), `acceptance` (shell commands that
exit 0 when the work is done), and `depends_on` (refs of drafts that must
land first; usually empty). Never invent task ids — refs only.

Your final output must be exactly one JSON document, nothing after it:

{{"drafts": [{{"ref": "DRAFT-1", "intent": "...",
  "scope": {{"allow": ["src/x.py", "tests/test_x.py"], "deny": []}},
  "acceptance": ["python3 -m unittest discover -s tests -v"],
  "depends_on": []}}],
 "rationale": "one paragraph: how the request decomposed, what was excluded"}}
"""


# ....................... #


@dataclass
class IntakeOutcome:
    task_id: str
    fact: str
    drafts: list[Draft] = field(default_factory=list)
    rationale: str = ""
    attempts: int = 0
    lint_errors: list[str] = field(default_factory=list)
    unparseable: bool = False


# ....................... #


def drafts_file(root: Path, task_id: str) -> Path:
    return root / layout.TORVE_DIR / "tasks" / task_id / DRAFTS_FILE


# ....................... #


def run_intake(
    root: Path,
    worktree: Path,
    task: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    config_digest: str,
    broker: Broker | None = None,
) -> IntakeOutcome:
    """The draft-lint loop: attempt, parse, lint; red iterates within the
    budget with the lint's refusals in the next prompt; green persists the
    drafts and the run goes ready — drafts awaiting adoption, dispatching
    nothing (D-20.1). The drafting run's provider credential rides the same
    broker as any run (RFC 0021): the sandbox never holds the key.

    A drafting run whose contract names a single target (RFC 0026 §5.2) is
    a decomposition: the prompt and the lint both route on the parent
    contract that target names, in place of the free-text request path."""

    parent: Task | None = None

    if task.targets:
        from torve.gates.context import load_task

        parent_contract = layout.task_file(root, task.targets[0])

        if not parent_contract.is_file():
            raise ValueError(f"no contract at {parent_contract} to decompose")

        parent = load_task(parent_contract)

    tier = tier_for(config, task.tier)
    state_path = naming.state_file(root, task.id)

    if state_path.exists():
        # A re-queued run (D-20.6): the commander's revise put it back to
        # QUEUED with its feedback written; the history continues.
        state = RunState.load(state_path)

        if state.state is not TaskState.QUEUED:
            raise ValueError(
                f"{task.id} is {state.state} — a drafting run resumes only from queued"
            )
    else:
        state = RunState(task_id=task.id, path=state_path)

    state.transition(TaskState.CLAIMED, "engine-minted at intake")
    state.save()

    # The drafting run's provider credential rides the same broker as any
    # run (RFC 0021): the sandbox sees the broker's URL and the run-scoped
    # token, never a key (D-21.4 — the planner tier's provider is the
    # drafting run's routing).
    broker_handle: BrokerHandle | None = None

    if broker is not None:
        routing: list[BrokerRoute] = []

        if tier.adapter != "fake" and tier.provider:
            provider = config.broker.providers.get(tier.provider)

            if provider is None:
                raise ValueError(
                    f"tier {task.tier!r} uses provider {tier.provider!r} but the broker "
                    "configuration routes no such provider — add it under broker.providers"
                )

            routing.append(
                BrokerRoute(
                    provider=tier.provider,
                    upstream=provider.upstream,
                    key_env=provider.key_env,
                )
            )

        broker_handle = broker.open(
            task.id, BrokerRouting(routes=tuple(routing)), BrokerBudget(tokens=task.budget.tokens)
        )

    def broker_block_now() -> dict[str, Any] | None:
        if broker is None or broker_handle is None:
            return None

        return broker_block(broker.name, broker.usage(broker_handle))

    from torve.application.feedback import feedback_file

    feedback_path = feedback_file(root, task.id)
    feedback = feedback_path.read_text(encoding="utf-8") if feedback_path.is_file() else None

    budget = task.budget.iterations or config.intake.iterations
    lint_errors: list[str] = []
    unparseable = False
    document: DraftsDocument | None = None

    try:
        for _ in range(budget):
            state.transition(TaskState.RUNNING, f"drafting attempt {state.attempts + 1}")
            state.save()

            spec = SandboxSpec(
                name=naming.sandbox_name(task.id, state.run_id) + f"-a{state.attempts}",
                image=image_for(config, tier),
                labels=naming.labels(task.id, state.run_id, root),
                timeout_s=config.runtime.sandbox_timeout,
                env_passthrough=tuple(tier.api_key_env),
                workspace_read_only=True,
            )

            prompt = build_intake_prompt(
                task.intent,
                worktree,
                config.intake.max_drafts,
                lint_errors or None,
                feedback,
                facts=execution_facts(root, config),
                parent=parent,
            )

            handle = runtime.create(spec, worktree)
            state.sandbox_id = handle.id
            state.save()

            try:
                result = agent.run(
                    AgentContext(
                        task=task,
                        attempt=state.attempts,
                        workspace=worktree,
                        handle=handle,
                        runtime=runtime,
                        workdir=spec.workdir,
                        timeout_s=config.runtime.agent_timeout,
                        prompt=prompt,
                        broker=broker_handle,
                    )
                )

            finally:
                runtime.destroy(handle)
                state.sandbox_id = None
                state.save()

            document = parse_drafts(result.output)

            if document is None:
                unparseable = True
                state.transition(
                    TaskState.GATED, "drafter output unparseable — recorded, not empty"
                )
                state.save()
                continue

            unparseable = False
            lint_errors = (
                lint_decomposition(worktree, document, parent, config.intake.max_drafts)
                if parent is not None
                else lint_drafts(worktree, document, config.intake.max_drafts)
            )

            if not lint_errors:
                # D-27.6: the configuration-change lint layers onto the
                # ordinary contract lint — a no-op for any batch that names
                # no configuration surface, so this costs an ordinary
                # drafting run nothing.
                lint_errors = lint_configuration_change(worktree, document, config, runtime)

            if lint_errors:
                state.transition(TaskState.GATED, f"lint red: {len(lint_errors)} refusal(s)")
                state.save()
                continue

            fact = f"{len(document.drafts)} draft(s) lint-green"
            state.transition(TaskState.GATED, "drafts produced; lint green")
            state.transition(TaskState.REVIEWED, fact)

            drafts_file(root, task.id).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "request": task.intent,
                        "rfc": task.rfc,
                        "rationale": document.rationale,
                        "drafts": [d.model_dump() for d in document.drafts],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            state.transition(TaskState.READY, fact + " — awaiting adoption")
            state.save()

            _append_intake_record(
                root,
                task,
                config_digest,
                tier.adapter,
                result_model=result.model_version,
                cost=result.cost_usd,
                trace=result.trace_ref,
                drafts=len(document.drafts),
                attempts=state.attempts,
                unparseable=False,
                broker=broker_block_now(),
            )

            engine_event(
                root,
                "intake_drafted",
                {"task": task.id, "drafts": len(document.drafts), "attempts": state.attempts},
            )

            return IntakeOutcome(
                task.id, fact, list(document.drafts), document.rationale, state.attempts
            )

        detail = (
            "drafter output unparseable"
            if unparseable
            else f"lint red after {state.attempts} attempt(s): " + "; ".join(lint_errors[:3])
        )

        state.escalate(EscalationReason.BUDGET_EXHAUSTED, detail[:300])

        _append_intake_record(
            root,
            task,
            config_digest,
            tier.adapter,
            result_model=None,
            cost=None,
            trace=None,
            drafts=0,
            attempts=state.attempts,
            unparseable=unparseable,
            broker=broker_block_now(),
        )

        return IntakeOutcome(
            task.id,
            detail,
            attempts=state.attempts,
            lint_errors=lint_errors,
            unparseable=unparseable,
        )

    finally:
        if broker is not None and broker_handle is not None:
            broker.close(broker_handle)


# ....................... #


def _append_intake_record(
    root: Path,
    task: Task,
    config_digest: str,
    adapter: str,
    *,
    result_model: str | None,
    cost: float | None,
    trace: str | None,
    drafts: int,
    attempts: int,
    unparseable: bool,
    broker: dict[str, Any] | None = None,
) -> None:
    """The drafting run's telemetry — same stream, its own kind, so
    drafting quality is a query (D-20.8's settling evidence)."""

    from torve.application.telemetry import append_record

    manifest = layout.gates_file(root)

    if not manifest.is_file():
        return

    from torve.config.manifest import load_manifest

    append_record(
        root / load_manifest(manifest).telemetry,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "intake",
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config_hash": config_digest,
            "task_id": task.id,
            "drafts": drafts,
            "attempts": attempts,
            "unparseable": unparseable,
            "agent": {
                "tier": task.tier,
                "adapter": adapter,
                "model_version": result_model,
                "cost_usd": cost,
                "trace_ref": trace,
                # The drafting run's broker counts, beside the adapter's
                # report (D-21.5) — present when a broker was in force.
                **({"broker": broker} if broker is not None else {}),
            },
        },
    )


# ----------------------- #
# Adoption (D-20.1, D-20.4): the human signature, and the only moment ids
# exist — read the counter, rewrite refs, write contracts, commit, all
# under the engine lock so nothing races the minting.


def _inherit_decisions(root: Path, rfc: str) -> list[dict[str, Any]]:
    """The planner's rows, not a second copy of them (D-20.9, A-47): grades
    and paths as they stand at adoption, from an accepted document only —
    the same admission torve plan enforces (D-7.7)."""

    from torve.application.planner import PlanError, inherit_decisions
    from torve.config import rfc_parse

    doc_path = (root / rfc).resolve()

    if not doc_path.is_file():
        raise ValueError(f"no document at {rfc}")

    text = doc_path.read_text(encoding="utf-8")
    frontmatter = rfc_parse.parse_frontmatter(text)

    if not frontmatter or frontmatter.get("status") != "accepted":
        raise ValueError(
            f"{rfc} is not accepted — a draft has no settled decisions to inherit (D-7.7)"
        )

    try:
        rows = inherit_decisions(text, doc_path.name)

    except PlanError as exc:
        raise ValueError(str(exc)) from exc

    return [row.model_dump() for row in rows]


# ....................... #


def adopted_file(root: Path, task_id: str) -> Path:
    return root / layout.TORVE_DIR / "tasks" / task_id / ADOPTED_FILE


# ....................... #


def adopt(root: Path, task_id: str, config: RunnerConfig, assume_lock: bool = False) -> list[str]:
    """Adopt every draft the run produced: ids minted here and nowhere
    else, contracts committed as engine records on base, the loop left to
    dispatch them like hand-minted work (D-20.7). Returns the new ids.
    `assume_lock` is for a caller already inside the tick — the board's
    adopt command applies under the lock the tick holds."""

    from torve.application.loop import acquire_lock, release_lock
    from torve.application.planner import next_task_number

    marker = adopted_file(root, task_id)

    if marker.is_file():
        prior = cast("dict[str, Any]", json.loads(marker.read_text(encoding="utf-8")))
        raise ValueError(f"{task_id} was already adopted as {', '.join(prior.get('adopted', []))}")

    source = drafts_file(root, task_id)

    if not source.is_file():
        raise ValueError(f"{task_id} holds no drafts — nothing to adopt")

    state_path = naming.state_file(root, task_id)
    state = RunState.load(state_path) if state_path.exists() else None

    if state is not None and state.state is not TaskState.READY:
        raise ValueError(f"adopt needs a ready drafting run; {task_id} is {state.state}")
    # An absent state with drafts present is adoptable: the drafts file
    # only ever persists from a green run, and a reaper may have swept
    # the READY state before this human arrived (D-20.10).

    record = cast("dict[str, Any]", json.loads(source.read_text(encoding="utf-8")))
    drafts: list[Draft] = [Draft.model_validate(d) for d in record["drafts"]]
    rfc = record.get("rfc")
    decisions = _inherit_decisions(root, str(rfc)) if rfc else []

    # A decomposition run names the contract it decomposes as its single
    # target (RFC 0026 §5.2); an ordinary intake names none.
    from torve.gates.context import load_task

    source_contract = layout.task_file(root, task_id)
    source_targets = load_task(source_contract).targets if source_contract.is_file() else []
    parent_id = source_targets[0] if source_targets else None

    if not assume_lock and not acquire_lock(root, config.loop.tick_budget):
        raise RuntimeError(
            "the engine lock is held — a tick is running; adoption retries when it releases"
        )

    try:
        start = next_task_number(root)
        ids = {d.ref: f"T-{start + i:04d}" for i, d in enumerate(drafts)}
        written: list[Path] = []

        for draft in drafts:
            new_id = ids[draft.ref]
            contract_dir = root / layout.TORVE_DIR / "tasks" / new_id
            contract_dir.mkdir(parents=True, exist_ok=True)

            document: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "id": new_id,
                "role": "implement",
                "intent": draft.intent,
                "depends_on": [ids[ref] for ref in draft.depends_on],
                "scope": draft.scope.model_dump(),
                "acceptance": list(draft.acceptance),
                "decisions": decisions,
                "tier": "executor",
            }

            if rfc:
                document["rfc"] = rfc

            if parent_id:
                document["parent"] = parent_id

            path = contract_dir / "contract.yaml"

            path.write_text(
                f"# Adopted from {task_id}'s drafts (RFC 0020) — "
                "ids minted at adoption, D-20.4.\n" + yaml.safe_dump(document, sort_keys=False),
                encoding="utf-8",
            )

            written.append(path)

        if parent_id:
            # The parent becomes the integration task (RFC 0026 D-26.6):
            # depends_on gains every child; scope and the full battery
            # stay exactly as authored. Header comments precede the YAML
            # by convention here, so they are preserved rather than lost
            # to a wholesale rewrite.
            parent_contract = layout.task_file(root, parent_id)
            original = parent_contract.read_text(encoding="utf-8")
            header = "\n".join(line for line in original.splitlines() if line.startswith("#"))
            parent_record = cast("dict[str, Any]", yaml.safe_load(original))
            existing_deps = list(parent_record.get("depends_on", []))
            grown = existing_deps + [i for i in ids.values() if i not in existing_deps]
            parent_record["depends_on"] = grown

            parent_contract.write_text(
                (header + "\n" if header else "")
                + f"# {parent_id} becomes the integration task at adoption — "
                f"depends_on grown with {', '.join(ids.values())}.\n"
                + yaml.safe_dump(parent_record, sort_keys=False),
                encoding="utf-8",
            )

            written.append(parent_contract)

        proc = subprocess.run(
            ["git", "-C", str(root), "add", "--"] + [str(p.relative_to(root)) for p in written],
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode == 0:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "commit",
                    "-m",
                    (f"🧪 chore: adopt {', '.join(ids.values())} from {task_id} (RFC 0020)"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        if proc.returncode != 0:
            raise RuntimeError(
                "adoption commit failed: " + (proc.stderr.strip() or proc.stdout.strip())
            )

    finally:
        if not assume_lock:  # a borrowed lock is the tick's to release
            release_lock(root)

    # Adoption is the disposal (D-20.10): the run's purpose is consumed,
    # so its state goes with it — nothing is left for a reaper to judge.
    # The marker survives as the audit line telling adopted from fresh.
    adopted_file(root, task_id).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adopted": list(ids.values()),
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    state_path.unlink(missing_ok=True)
    source.unlink()
    engine_event(root, "intake_adopted", {"task": task_id, "adopted": list(ids.values())})

    return list(ids.values())


# ....................... #


# The board's intake leg (RFC 0020 §5.4): claim commander-filed requests,
# run the drafting they ask for, and project the lint-green drafts back
# onto the thread the request lives on. Ledger-scoped: a run this leg did
# not claim is the operator's, and the leg never touches it.
@dataclass
class IntakeDeps:
    """The leg's wiring, built by the tick: the tracker is the surface,
    the agent factory is lazy so the drafter's harness is constructed
    only when a request actually needs it."""

    tracker: Any
    runtime: Runtime
    agent_factory: Callable[[], Agent]
    worktree_at: Callable[[Path, str, Path], None]
    remove_worktree: Callable[[Path, Path], None]
    base_tip: Callable[[], str | None]
    config_digest: str
    # The egress broker adapter in force, shared with live runs (RFC 0021):
    # the drafting run's sandbox never holds a provider key either.
    broker: Broker | None = None


# ....................... #


def ledger_file(root: Path) -> Path:
    return root / layout.TORVE_DIR / INTAKE_LEDGER


# ....................... #


def _title_for(root: Path, task_id: str) -> str:
    from torve.application.tracker import _title  # pyright: ignore[reportPrivateUsage]

    return _title(root, task_id)


# ....................... #


def _ledger_rows(root: Path) -> list[dict[str, Any]]:
    path = ledger_file(root)

    if not path.is_file():
        return []

    return [
        cast("dict[str, Any]", json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ....................... #


def _drafts_comment(record: dict[str, Any], task_id: str) -> str:
    lines = [
        (
            f"drafts from {task_id} — adopt with `/torve adopt`, revise "
            "with `/torve revise` (your comment's own text reaches the "
            "drafter as feedback), refuse with `/torve abandon`"
        ),
        "",
    ]

    for draft in cast("list[dict[str, Any]]", record["drafts"]):
        scope = cast("dict[str, Any]", draft.get("scope", {}))

        lines += [
            f"**{draft['ref']}** — {draft['intent']}",
            "",
            f"- allow: `{'`, `'.join(cast('list[str]', scope.get('allow', [])))}`",
            f"- acceptance: `{'`; `'.join(cast('list[str]', draft.get('acceptance', [])))}`",
        ]

        deps = cast("list[str]", draft.get("depends_on", []))

        if deps:
            lines.append(f"- depends on: {', '.join(deps)}")

        lines.append("")

    if record.get("rationale"):
        lines += [str(record["rationale"]), ""]

    lines.append("authority: the run store; this comment is a projection")

    return "\n".join(lines)


# ....................... #


def intake_leg(
    root: Path, config: RunnerConfig, deps: IntakeDeps, commanders: tuple[str, ...]
) -> tuple[str, bool]:
    """Claim, run, project — each bounded to this tick. Authorization
    precedes claiming (D-20.5, D-8.9's list): a request from outside the
    commander list is left unclaimed and counted, never interpreted."""

    from torve.application.outbox import Effect, stage

    claimed = ran = staged = skipped = 0

    for request in deps.tracker.intake_requests():
        if request.author not in commanders:
            skipped += 1
            continue

        text = request.title + ("\n\n" + request.body if request.body else "")
        found = RFC_LINE.search(request.body or "")
        task = mint_intake_task(root, text, config, rfc=found.group(1) if found else None)
        deps.tracker.retitle(request.number, f"{task.id}: {request.title}")

        with ledger_file(root).open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "issue": request.number,
                        "task": task.id,
                        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
                + "\n"
            )

        engine_event(
            root,
            "intake_claimed",
            {"issue": request.number, "task": task.id, "actor": request.author},
        )

        claimed += 1

    for row in _ledger_rows(root):
        task_id = str(row["task"])

        if adopted_file(root, task_id).is_file():
            # The consumed thread closes itself (T-0093): keyed, so the
            # sync leg delivers it exactly once.
            if stage(
                root,
                Effect(
                    key=f"{task_id}:adopted-close",
                    kind="state",
                    payload={
                        "task": task_id,
                        "state": "adopted",
                        "title": _title_for(root, task_id),
                    },
                ),
            ):
                staged += 1

            continue

        state_path = naming.state_file(root, task_id)
        state = RunState.load(state_path) if state_path.exists() else None
        fresh = state is None and not drafts_file(root, task_id).is_file()
        queued = state is not None and state.state is TaskState.QUEUED

        if fresh or queued:
            contract = layout.task_file(root, task_id)

            if not contract.is_file():
                continue

            from torve.gates.context import load_task

            task = load_task(contract)
            tip = deps.base_tip()

            if tip is None:
                continue

            workdir = root / naming.WORKTREE_DIR / f"{task_id}.intake"
            deps.worktree_at(root, tip, workdir)

            try:
                run_intake(
                    root,
                    workdir,
                    task,
                    config,
                    deps.runtime,
                    deps.agent_factory(),
                    deps.config_digest,
                    broker=deps.broker,
                )

            finally:
                deps.remove_worktree(root, workdir)

            ran += 1
            state = RunState.load(state_path) if state_path.exists() else None

        if (
            state is not None
            and state.state is TaskState.READY
            and drafts_file(root, task_id).is_file()
        ):
            record = cast(
                "dict[str, Any]", json.loads(drafts_file(root, task_id).read_text(encoding="utf-8"))
            )

            if stage(
                root,
                Effect(
                    key=f"{task_id}:drafts:a{state.attempts}",
                    kind="drafts",
                    payload={"task": task_id, "body": _drafts_comment(record, task_id)},
                ),
            ):
                staged += 1

    parts = [f"claimed {claimed}", f"ran {ran}", f"projected {staged}"]

    if skipped:
        parts.append(f"skipped {skipped} non-commander request(s)")

    moved = bool(claimed or ran or staged)

    return (", ".join(parts) if moved or skipped else "no intake activity", moved)
