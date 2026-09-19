"""Intake and the drafting run (S-0020): a commander's request becomes
draft task contracts through a run — role `draft`, the planner tier's seat,
read-only workspace — whose gate is the deterministic contract lint
(S-0020/D-3). Drafts carry request-local `DRAFT-n` refs; task ids exist only
from adoption, minted under the engine lock atomically with the commit
that makes the contracts real (S-0020/D-4). Adoption is the human signature
S-0007/the-loop-closed requires — relocated, never removed (S-0020/D-1). The planner
module stays model-free (S-0007/D-1): everything here runs under the runner's
machinery, the same boundary review-as-a-run proved out.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from torve.application import sizing
from torve.application.ports import (
    Agent,
    AgentContext,
    AgentResult,
    Broker,
    BrokerBudget,
    BrokerHandle,
    BrokerRoute,
    BrokerRouting,
    Runtime,
    SandboxSpec,
)
from torve.application.review import SchemaRefusal, schema_refusal
from torve.application.runstate import RunState
from torve.application.telemetry import RECORD_SCHEMA_VERSION, broker_block, engine_event
from torve.base import naming
from torve.base.clock import stamp
from torve.base.model import STRICT
from torve.config import layout
from torve.config.runconfig import (
    RunnerConfig,
    TierConfig,
    broker_in_force,
    configured_images,
    credential_names,
    image_for,
    tier_for,
)
from torve.domain.attempt import SizeVerdict
from torve.domain.spec import document_id
from torve.domain.states import EscalationReason, TaskState
from torve.domain.task import (
    CONTRACT_SCHEMA_VERSION,
    Budget,
    InheritedDecision,
    Scope,
    Task,
)

# ----------------------- #

DRAFTS_FILE = "drafts.json"
DRAFT_REF = re.compile(r"^DRAFT-(\d+)$")
# Adoption's terminal marker: with state and drafts both consumed, this
# is what tells a fresh mint from an adopted one.
ADOPTED_FILE = "adopted.json"


# ....................... #


class Draft(BaseModel):
    model_config = STRICT

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
    the findings-parse discipline (S-0005/D-4's sibling): unparseable is None,
    recorded by the caller, never invented as an empty batch."""

    text = ANSI.sub("", output)
    decoder = json.JSONDecoder()
    last: object | None = None
    envelopes: list[str] = []

    for brace in re.finditer(r"\{", text):
        try:
            document, _ = decoder.raw_decode(text, brace.start())

        except json.JSONDecodeError:
            continue

        if isinstance(document, dict) and "drafts" in document:
            last = cast("dict[str, Any]", document)

        elif isinstance(document, dict):
            # A harness result envelope (`claude -p --output-format json`)
            # carries the agent's answer as the `result` string — the drafts
            # document is inside it, escaped, invisible to this scan.
            result: Any = cast("dict[str, Any]", document).get("result")

            if isinstance(result, str) and "drafts" in result:
                envelopes.append(result)

    if last is None:
        for enveloped in envelopes:
            nested = parse_drafts(enveloped)

            if nested is not None:
                return nested

    if last is None:
        return None

    try:
        return DraftsDocument.model_validate(last)

    except ValidationError as exc:
        # S-0054/D-15: a document the drafter wrote but the model refuses is a
        # lint refusal naming the field, and rides the next prompt as one.
        raise SchemaRefusal(schema_refusal(exc, "drafts document")) from None


# ....................... #


# The contract lint (S-0020/D-3): deterministic, engine-side, no model. A red
# lint is a red attempt; every error names the draft and the field.
def _glob_errors(
    ref: str, tree_paths: list[Path], globs: list[str], kind: str, planning: bool = False
) -> list[str]:
    errors: list[str] = []

    for pattern in globs:
        if pattern.startswith("/") or ".." in pattern.split("/"):
            errors.append(f"{ref}: {kind} glob {pattern!r} escapes the tree")
            continue

        # A pattern with no wildcard is a literal path, and the scope gate
        # matches it against files. One that names a directory therefore
        # matches no file at all: the contract reads as if it allowed the
        # directory's contents and allows nothing, so the attempt is refused
        # on writes its own scope appears to permit. T-0409 spent an attempt
        # on exactly that, declaring `.torve/harnesses` and being unable to
        # write under it. The check above never saw it, because it asks only
        # about wildcards.
        if not any(ch in pattern for ch in "*?["):
            bare = pattern.rstrip("/")
            # `tree_paths` holds files, so a directory is what has files under
            # it and is not one itself.
            under = any(str(p).startswith(bare + "/") for p in tree_paths)
            itself = any(str(p) == bare for p in tree_paths)

            if under and not itself:
                errors.append(
                    f"{ref}: {kind} glob {pattern!r} names a directory, which matches "
                    f"no file — write {bare}/** for its contents"
                )

            continue

        if not planning and not any(_matched(p, [pattern]) for p in tree_paths):
            # A `dir/**` over a directory the tree does not hold is the phase's
            # to create (bloomery S-0008 phase 1 builds `fuzz/` from nothing),
            # not a wildcard that can never match: it matches everything the
            # phase puts there. A wildcard inside an existing directory that
            # matches nothing stays a refusal.
            if pattern.endswith("/**") and not any(
                str(p).startswith(pattern[:-3] + "/") for p in tree_paths
            ):
                continue

            errors.append(
                f"{ref}: {kind} glob {pattern!r} matches nothing in the tree "
                "— a wildcard that can never match checks nothing"
            )

    return errors


# ....................... #


# Directories that are not the tree under judgment: git's own store, the
# engine's worktree scratch, and the environment. `.wt/` matters most — it
# holds whole copies of the repository, and `Path.match` is right-anchored,
# so `.wt/T-0281/src/…/feedback.py` matches the glob `src/…/feedback.py`.
# Every lint then reported each finding once per live worktree (T-0282).
NOT_THE_TREE = frozenset({".git", ".wt", ".venv", ".repowise", "__pycache__", "node_modules"})


def _needs_git(words: list[str]) -> bool:
    """Whether this acceptance command can only run in a repository.

    A sandbox mounts the worktree and not the repository, so `git` there
    fails outright — and `torve gates run` computes a diff against base, so
    it fails the same way one call in. T-0282 burned its whole poison
    ceiling on that, three attempts whose own tests passed every time, and
    nine phases across seven documents still carry the same command.
    """

    if words[0] == "git":
        return True

    # `uv run torve gates run`, `torve gates check`, and anything wrapping
    # them: the verb is what matters, not what precedes it.
    return "torve" in words and "gates" in words[words.index("torve") :]


# ....................... #


# The `torve` verbs that read nothing but the worktree they run over, and so
# are the only ones a sandbox can judge (S-0071/D-5). An allow-list rather
# than a list of the host-reading verbs: a verb nobody classified is refused
# at mint, which costs one drafting round, where the miss the other way costs
# the poison ceiling.
TREE_ONLY_VERBS = frozenset(
    {"spec", "source", "decisions", "size", "lint-contract", "init", "equip", "log"}
)


def _needs_host(words: list[str]) -> bool:
    """Whether this acceptance command reads the host rather than the tree.

    `torve doctor` is the one that burned T-0377: it reports on the Docker
    daemon, `$TORVE_PG_DSN` and the provider credentials, none of which a
    sandbox has by design, so all three attempts failed on the same two
    lines and the contract could never have gone green (S-0071/D-5).
    """

    if words[0] in {"docker", "podman"}:
        return True

    if "torve" not in words:
        return False

    rest = words[words.index("torve") + 1 :]
    verb = next((w for w in rest if not w.startswith("-")), None)

    return verb is not None and verb not in TREE_ONLY_VERBS


# ....................... #


def _tree_paths(tree: Path) -> list[Path]:
    return [
        p.relative_to(tree)
        for p in tree.rglob("*")
        if p.is_file() and NOT_THE_TREE.isdisjoint(p.parts)
    ]


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
    planning: bool = False,
) -> list[str]:
    """Every mechanical check a human should never have to make (S-0020/D-3).
    The T-0113 rule is the first learned rule: a draft touching an existing
    module must allow that module's existing test file — the escalation
    that produced it burned a full poison ceiling on exactly this.

    `planning` is the minting path (S-0052/A-3), where two of these rules do
    not hold. A drafted contract is written against the tree as it stands,
    so a glob matching nothing is a mistake; a *planned* one describes work
    that does not exist yet, and `spec check` already warns about exactly that
    as "intended modules awaiting implementation" (S-0001/D-32). And a phase's
    `acceptance` is optional by the RFC schema, where a draft's is not.
    Everything else — the shell parse, the git rule, the T-0113 test-file
    rule, a glob escaping the tree — holds on both paths.

    `allow_dependency_order` relaxes the pairwise-scope check for a
    decomposition batch (S-0026 S-0026/D-3): two drafts may overlap when an
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
            f"{len(drafts)} drafts exceed the ceiling of {max_drafts} (intake.max_drafts, S-0020/D-8)"
        )

    refs = [d.ref for d in drafts]

    if len(set(refs)) != len(refs):
        errors.append("draft refs are not unique")

    for draft in drafts:
        ref = draft.ref

        if not DRAFT_REF.match(ref):
            errors.append(
                f"{ref!r}: refs are DRAFT-<n> — ids exist only from adoption (S-0020/D-4)"
            )

        if not draft.intent.strip():
            errors.append(f"{ref}: intent is empty")

        if not draft.acceptance and not planning:
            errors.append(f"{ref}: acceptance is empty — nothing would judge the work")

        for command in draft.acceptance:
            try:
                words = shlex.split(command)

                if not words:
                    raise ValueError

            except ValueError:
                errors.append(f"{ref}: acceptance command {command!r} does not shell-parse")
                continue

            if _needs_git(words):
                errors.append(
                    f"{ref}: acceptance command {command!r} needs git, and a sandbox mounts "
                    "the worktree without a repository — `.git` there points at a host path "
                    "the container never sees, so this can only ever fail (S-0052/A-2). Use the "
                    "commands that judge this work without a repository instead — the tests "
                    "it touches, `uv run lint-imports --config pyproject.toml`, `uv run torve "
                    "spec check` — and drop this one: the gate battery runs outside the sandbox "
                    "on the candidate already"
                )
            elif _needs_host(words):
                errors.append(
                    f"{ref}: acceptance command {command!r} reads the host, and a sandbox has "
                    "no Docker daemon, no database and no credentials — it reports on the "
                    "machine the engine runs from, not on this work, so this can only ever "
                    "fail. Use the commands that judge the tree instead — the tests it "
                    "touches, `uv run mypy src`, `uv run lint-imports --config "
                    "pyproject.toml`, `uv run torve spec check`"
                )

        if not draft.scope.allow:
            errors.append(
                f"{ref}: scope.allow is empty — an unconstrained draft contends with everything"
            )

        errors.extend(_glob_errors(ref, tree_paths, draft.scope.allow, "allow", planning))

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
    """The decomposition batch's own four rules (S-0026/the-decomposition-run), layered on
    the ordinary contract lint: S-0026/D-2 (a child never escapes the parent's
    allow-set), S-0026/D-3 (`lint_drafts`' own relaxed pairwise check —
    overlap only where a `depends_on` edge orders it), S-0026/D-4 (the parent's
    acceptance battery is distributed across the children, never dropped),
    and S-0026/D-12's sibling rule — every child is itself right-sized; a
    decomposition that yields an oversized child has not decomposed. The
    depth bound (S-0026/D-12) is enforced earlier, at the drafting run's mint."""

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

# S-0027/tier-variants: the committed configuration tree a configuration
# drafting run proposes changes to — sandbox definitions and the tier
# blocks live in `.torve/config.yaml`, nowhere else (S-0013/D-3).
CONFIGURATION_SURFACES = ["sandboxes/**", ".torve/sandbox/**", ".torve/config.yaml"]


def _configuration_paths(allow: list[str]) -> list[str]:
    return [p for p in allow if _matched(Path(p), CONFIGURATION_SURFACES)]


def lint_configuration_change(
    tree: Path, document: DraftsDocument, config: RunnerConfig, runtime: Runtime
) -> list[str]:
    """S-0027 S-0027/D-6: the drafting gate for a configuration drafting run —
    deterministic, no model, fires only when a draft's scope names a
    configuration surface, so an ordinary intake batch pays nothing extra
    and no new task field distinguishes the two (S-0027/D-4's "no config-specific
    verb"). Confines the diff to `CONFIGURATION_SURFACES` (never mixed with
    application code in one draft), then grounds the proposal in a *working*
    baseline: the committed configuration parses under its schema, every
    sandbox definition a draft names is a target the build knows about, and
    every configured image resolves *in this runtime*.

    Narrower than `torve doctor`'s image check, which it used to claim to
    be (S-0017/D-2): doctor falls back to the registry for a reference this host
    has not pulled, and this gate cannot — the fallback is network I/O
    belonging to an adapter, and nothing here may reach one. So the two
    answer different questions, and the refusal below says which one it
    asked instead of asserting doctor's verdict (T-0194).
    The definition leg used to build each touched image. It cannot any more and
    should not: `RuntimePort.build_image` retired with S-0063/D-11, so the engine
    has no way to build at all, which is S-0017/D-3 made structural. What it
    checks instead is that `bake.hcl` names a target for the definition — the
    realistic drafting error once a build file exists, caught by reading a file
    rather than by a build nothing on this path may run."""

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
                "application code (S-0027/D-6)"
            )
            continue

        for path in config_paths:
            parts = Path(path).parts

            if len(parts) >= 2 and parts[0] == "sandboxes":
                touched_names.add(parts[1])

            elif len(parts) >= 3 and parts[0] == ".torve" and parts[1] == "sandbox":
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

    bake = tree / "bake.hcl"
    baked = bake.read_text(encoding="utf-8") if bake.is_file() else ""

    for name in sorted(touched_names):
        definition = next(
            (
                one
                for one in (tree / "sandboxes" / name, tree / ".torve" / "sandbox" / name)
                if one.is_dir()
            ),
            None,
        )

        if definition is None:
            continue  # a newly proposed definition does not exist yet

        if baked and f'target "{name}"' not in baked:
            errors.append(
                f"configuration-change lint: definition {name!r} exists and {bake.name} "
                f"names no target for it — nothing would build it (`just images`)"
            )

    from torve.application.migrate import check_forze_pin

    pin_ok, pin_message = check_forze_pin()

    if not pin_ok:
        errors.append(f"configuration-change lint: torve doctor is red — {pin_message}")

    for image in configured_images(config):
        if runtime.resolve_image(image) is None:
            errors.append(
                f"configuration-change lint: image {image!r} is configured but not "
                "present in this runtime — a drafting run grounds its proposal in a "
                "baseline this host can actually run, so pull or build it first "
                "(`torve doctor` may still pass: it can ask the registry, and this "
                "cannot)"
            )

    return errors


# ....................... #


def lint_contract(tree: Path, contract: Path, max_drafts: int = 1) -> list[str]:
    """The standalone face: the same protection for a hand-minted contract
    (S-0020/the-contract-lint) — the operator path stays legal and gets safer."""

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
        # A review carries no acceptance by contract law (S-0005/D-10), a draft
        # none by S-0020/D-3 — the batch checks below would misread both.
        return []

    return lint_task(tree, task, max_drafts)


# ....................... #


def lint_task(tree: Path, task: Task, max_drafts: int = 1, *, planning: bool = False) -> list[str]:
    """The same protection for a Task already in hand — what `lint_contract`
    does once the file is parsed, and what `torve plan` runs over every
    contract it is about to mint (S-0052/A-3). A review or draft role carries no
    acceptance by contract law, so the batch checks would misread it."""

    if task.role != "implement":
        return []

    document = DraftsDocument(
        drafts=[
            Draft(ref="DRAFT-1", intent=task.intent, scope=task.scope, acceptance=task.acceptance)
        ]
    )

    return [
        e.replace("DRAFT-1", task.id)
        for e in lint_drafts(tree, document, max_drafts, planning=planning)
    ]


# ....................... #


def standing_warnings(tree: Path, contract: Path, rfc_dir: Path | None = None) -> list[str]:
    """The lint-contract advisory (S-0030/standing-inheritance): the standing rows whose
    declared paths intersect the contract's scope.allow but that the
    contract does not carry, each named. Advisory, never a refusal — a
    hand-minted contract is already a human's signature (S-0030/D-4); the
    consuming CLI renders these as warnings."""

    try:
        raw = yaml.safe_load(contract.read_text(encoding="utf-8"))

    except yaml.YAMLError:
        return []

    if not isinstance(raw, dict):
        return []

    data = cast("dict[str, Any]", raw)

    try:
        task = Task.model_validate({**data, "decisions": data.get("decisions", [])})

    except ValidationError:
        return []

    if task.role != "implement":
        return []

    from torve.application.planner import standing_decisions

    standing = standing_decisions(rfc_dir or tree / layout.SPECS_DIR, task.scope.allow)
    carried = {row.id for row in task.decisions}
    missing = [row for row in standing if row.id not in carried]

    return [
        f"{row.id} ({row.grade}): its declared paths intersect this "
        "contract's scope.allow, and the contract does not carry it — copy "
        "the row into decisions to inherit it"
        for row in missing
    ]


# ....................... #


@dataclass(frozen=True)
class ThresholdVerdict:
    """S-0030/the-threshold-verdict: `rides` or `document_required`, with the reasons that
    drove it — named so both enforcement surfaces can render the same
    evidence without recomputing it."""

    verdict: Literal["rides", "document_required"]
    reasons: list[str] = field(default_factory=list)


def _document_owners(rfc_dir: Path) -> dict[str, str]:
    """Which accepted document owns each decision id, resolved through the
    same corpus parse `inherit_decisions` already reads (S-0030/D-3) — never
    from the identifier's own shape. A family of ids is not a document: RFC
    0001 alone carries S-0001/D-10, S-0001/D-19 and D-A.* as one, and counting families
    would overcount that single document as several — the mistake this
    resolution exists to rule out."""

    from torve.application.planner import PlanError, inherit_decisions, load_corpus

    owners: dict[str, str] = {}

    try:
        corpus = load_corpus(rfc_dir)
    except PlanError:
        return owners

    for doc in corpus.standing():
        if doc.superseded_by:
            continue

        for row in inherit_decisions(doc):
            owners[row.id] = Path(doc.path).name

    return owners


def document_threshold(
    standing: list[InheritedDecision],
    size: SizeVerdict,
    documents: dict[str, str],
    min_documents: int,
) -> ThresholdVerdict:
    """S-0030/the-threshold-verdict (S-0030/D-2/D-30.3): deterministic arithmetic over the
    standing rows a scope crosses and the size verdict already computed
    elsewhere — no model opinion of risk anywhere in the routing.
    `documents` maps a row's id to the accepted document that owns it
    (`_document_owners`); a row missing from the map counts as its own
    document rather than vanishing from the count."""

    by_document: dict[str, set[str]] = {}

    for row in standing:
        if row.grade == "LOCKED":
            by_document.setdefault(documents.get(row.id, row.id), set()).add(row.id)

    reasons: list[str] = []

    if len(by_document) >= min_documents:
        crossings = "; ".join(
            f"{doc} ({', '.join(sorted(ids))})" for doc, ids in sorted(by_document.items())
        )
        reasons.append(f"crosses locked decisions from {len(by_document)} documents: {crossings}")

    if size.size == "too_large":
        reasons.append(f"too large ({'; '.join(size.reasons)})")

    return ThresholdVerdict(verdict="document_required" if reasons else "rides", reasons=reasons)


def _threshold_for_scope(
    rfc_dir: Path, scope: Scope, acceptance: list[str], min_documents: int
) -> ThresholdVerdict:
    """Wires §5.1's standing rows and the size verdict into `document_
    threshold` for one scope — the shape both the intake lint and adoption
    need, and the lint-contract advisory too."""

    from torve.application.planner import standing_decisions

    standing = standing_decisions(rfc_dir, scope.allow)
    size = sizing.estimate_scope(scope, acceptance)
    documents = _document_owners(rfc_dir)

    return document_threshold(standing, size, documents, min_documents)


def lint_document_threshold(
    tree: Path, document: DraftsDocument, config: RunnerConfig
) -> list[str]:
    """S-0030/the-threshold-verdict (S-0030/D-4): the intake lint's enforcement surface — a
    draft whose scope crosses the document threshold is a lint error naming
    the crossings, so the drafter's next iteration can narrow scope, or the
    commander routes the request to authoring instead."""

    rfc_dir = tree / config.specs.path
    errors: list[str] = []

    for draft in document.drafts:
        verdict = _threshold_for_scope(
            rfc_dir, draft.scope, draft.acceptance, config.intake.document_threshold
        )

        if verdict.verdict == "document_required":
            errors.append(
                f"{draft.ref}: needs its own document — {'; '.join(verdict.reasons)} — "
                "split it into a document with `torve spec new`, or narrow scope.allow"
            )

    return errors


def document_threshold_warnings(
    tree: Path, contract: Path, config: RunnerConfig, rfc_dir: Path | None = None
) -> list[str]:
    """The lint-contract advisory (S-0030 S-0030/D-4): when a hand-minted
    contract's scope already crosses the document threshold, named —
    advisory, never a refusal, since a hand-minted contract is already a
    human's signature."""

    try:
        raw = yaml.safe_load(contract.read_text(encoding="utf-8"))

    except yaml.YAMLError:
        return []

    if not isinstance(raw, dict):
        return []

    data = cast("dict[str, Any]", raw)

    try:
        task = Task.model_validate({**data, "decisions": data.get("decisions", [])})

    except ValidationError:
        return []

    if task.role != "implement":
        return []

    verdict = _threshold_for_scope(
        # The configuration names where the corpus lives; hardcoding it made
        # this advisory glob a directory that need not exist, silently drop
        # the crossings clause, and disagree with the two surfaces that do
        # read `specs.path` — the drafting lint and adopt (T-0209).
        rfc_dir or tree / config.specs.path,
        task.scope,
        task.acceptance,
        config.intake.document_threshold,
    )

    if verdict.verdict != "document_required":
        return []

    return [f"needs its own document — {'; '.join(verdict.reasons)}"]


# ....................... #


def resolve_source(root: Path, config: RunnerConfig, identifier: str) -> str:
    """One source identifier, checked against the tree (S-0060/D-5): a
    document the corpus holds, or a source filed under `.torve/sources/`.
    Raises `ValueError` naming what was not found — a provenance nobody can
    open is worse than none, because it reads as an answer."""

    from torve.config import spec as corpus
    from torve.config.sources import load_sources
    from torve.domain.source import FILED_ID, is_source_id

    if not is_source_id(identifier):
        raise ValueError(
            f"{identifier!r} is not a source identifier — a document `S-NNNN`, "
            "or `<kind>/<slug>` for one that is filed"
        )

    if FILED_ID.match(identifier):
        if identifier not in load_sources(root):
            raise ValueError(
                f"no source {identifier!r} under {root / layout.TORVE_DIR / 'sources'} — "
                "`torve source new` files one"
            )
    elif corpus.document_dir(root / config.specs.path, identifier) is None:
        raise ValueError(f"no document {identifier!r} under {config.specs.path}")

    return identifier


# ....................... #


# The drafting run.
def mint_intake_task(
    root: Path,
    request: str,
    config: RunnerConfig,
    spec: str | None = None,
    source: str | None = None,
) -> Task:
    """Engine-minted at request time, like a review at gated — the id here
    names the drafting run itself, never its output (S-0020/D-4).

    *source* is what asked (S-0060/D-5), refused here if the tree holds no
    such source — before a model is called, since a run that names a
    provenance nobody can open is a run whose contracts would name it too."""

    from torve.application.planner import next_task_number

    task = Task(
        id=f"T-{next_task_number(root):04d}",
        spec=document_id(spec) if spec else None,
        source=resolve_source(root, config, source) if source else None,
        role="draft",
        intent=request,
        decisions=[],
        budget=Budget(iterations=config.intake.iterations),
        tier="planner",
    )

    contract_dir = root / layout.TORVE_DIR / "tasks" / task.id
    contract_dir.mkdir(parents=True, exist_ok=True)
    document = task.model_dump(exclude_defaults=True)
    document["schema_version"] = CONTRACT_SCHEMA_VERSION
    document["decisions"] = []

    (contract_dir / "contract.yaml").write_text(
        "# Minted by the engine at intake — drafting follows the request.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    return task


# ....................... #


def _decomposition_depth(root: Path, task_id: str) -> int:
    """Hops up a task's `parent` chain (S-0026 S-0026/D-12): 0 for an
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


# The decomposition run (S-0026/the-decomposition-run): a draft-role run exactly RFC
# 0020's shape, pointed at a contract instead of a commander's prose.
def mint_decomposition_task(root: Path, parent_id: str, config: RunnerConfig) -> Task:
    """Refuses a third decomposition round by name (S-0026/D-12) before any
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
        spec=parent.spec,
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
    document["schema_version"] = CONTRACT_SCHEMA_VERSION
    document["decisions"] = []

    (contract_dir / "contract.yaml").write_text(
        "# Minted by the engine — a decomposition drafting run.\n"
        + yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    return task


# ....................... #


def _harness_facts(root: Path, config: RunnerConfig) -> list[str]:
    """S-0027 S-0027/D-5: one line per configured tier — including a variant
    nothing uses, denominator zero and visible — plus the quasi-experiment
    caveat printed verbatim beside them (S-0004/measurement-defects-to-fix-before-trusting-a-number, S-0022/D-7's rule applied
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
            ", ".join(
                f"{reason} ({n})" for reason, n in sorted(pop["escalations_by_reason"].items())
            )
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
    """S-0020 phase 3: what the loop knows that a fresh drafter cannot —
    the live escalation queue, contended paths, recent landings, and (RFC
    0027 S-0027/D-5) the harness populations every configured tier has produced.
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
    pack_index: str = "",
) -> str:
    """The drafter's whole input: the request, the tree, the ceiling, and —
    on a retry — the lint's exact refusals. The calibration paragraph
    matters as much as review's: one honest draft beats a decomposition
    performed to look thorough.

    `parent` turns this into a decomposition run's prompt (S-0026
    S-0026/D-10): the tree listing narrows to the parent's own scope.allow, and
    the parent's contract — scope, acceptance, the rules a decomposition
    must satisfy — rides beside the request. The parent's inherited
    decision rows do not: children re-inherit them from the governing
    document at adoption (S-0020/D-9's existing rule, unchanged), so carrying
    a second copy into the prompt would buy nothing but tokens."""

    # S-0054/D-16: the pack's index stands where 400 filenames used to; the
    # drafter reads the tree itself and asks `torve spec paths` what rows a
    # candidate scope would cross. Without a pack (a caller composing the
    # prompt bare) the top of the tree is named, and nothing more.
    if pack_index:
        tree_block = (
            "## What the engine knows\n\n"
            f"{pack_index.rstrip()}\n\n"
            "The tree is yours to read. Before settling a draft's scope, run\n"
            "`torve spec paths <path>` on each directory it would touch: the\n"
            "answer is the decisions and invariants governing it, and a split\n"
            "is proposed against the rows it would cross, not a list of names.\n"
        )
    else:
        top = sorted({p.parts[0] for p in _tree_paths(tree)})
        tree_block = "## The repository tree\n\n" + "\n".join(f"- {name}" for name in top) + "\n"

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
            "- acceptance:\n" + "\n".join(f"  - {command}" for command in parent.acceptance) + "\n"
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
{tree_block}{retry_block}
## What to produce

{rules_block.format(max_drafts=max_drafts)} Each draft carries: `ref`
("DRAFT-1", "DRAFT-2", …), `intent` (one paragraph: what changes and why —
never steps), `scope` with `allow`/`deny` file globs (every file the work
may touch, including test files — a draft touching an existing module must
allow that module's existing test file), `acceptance` (shell commands that
exit 0 when the work is done, and that run with no repository — the
acceptance battery runs inside a sandbox holding the working tree alone, so
no command may use git, and `torve gates` needs a diff against base; the
battery runs outside the sandbox on the candidate anyway), and `depends_on`
(refs of drafts that must land first; usually empty). Never invent task ids
— refs only.

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


def _decomposition_parent(root: Path, task: Task) -> Task | None:
    """S-0026/the-decomposition-run: a drafting run whose contract names a single target
    is a decomposition — the contract that target names is its parent."""

    if not task.targets:
        return None

    from torve.gates.context import load_task

    parent_contract = layout.task_file(root, task.targets[0])

    if not parent_contract.is_file():
        raise ValueError(f"no contract at {parent_contract} to decompose")

    return load_task(parent_contract)


# ....................... #


def _claimed_or_resumed_state(task: Task, state_path: Path) -> RunState:
    """A fresh mint starts a state; a re-queued run (S-0020/D-6) — the
    commander's revise put it back to QUEUED with its feedback written —
    resumes one, its history continuing. Either way the drafting run
    claims from here."""

    if state_path.exists():
        state = RunState.load(state_path)

        if state.state is not TaskState.QUEUED:
            raise ValueError(
                f"{task.id} is {state.state} — a drafting run resumes only from queued"
            )
    else:
        state = RunState(task_id=task.id, path=state_path)

    state.transition(TaskState.CLAIMED, "engine-minted at intake")
    state.save()
    return state


# ....................... #


def _open_intake_broker(
    task: Task, tier: TierConfig, config: RunnerConfig, broker: Broker | None
) -> BrokerHandle | None:
    """The drafting run's provider credential rides the same broker as any
    run (S-0021): the sandbox sees the broker's URL and the run-scoped
    token, never a key (S-0021/D-4 — the planner tier's provider is the
    drafting run's routing)."""

    if broker is None or not broker_in_force(config):
        return None

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
                via_proxy=provider.via_proxy,
            )
        )

    return broker.open(
        task.id, BrokerRouting(routes=tuple(routing)), BrokerBudget(tokens=task.budget.tokens)
    )


# ....................... #


def _attempt_intake_draft(
    root: Path,
    worktree: Path,
    task: Task,
    config: RunnerConfig,
    runtime: Runtime,
    agent: Agent,
    tier: TierConfig,
    state: RunState,
    lint_errors: list[str],
    feedback: str | None,
    parent: Task | None,
    broker_handle: BrokerHandle | None,
) -> tuple[AgentResult, DraftsDocument | None]:
    """One drafting attempt: sandbox up, agent run, sandbox down (always),
    output parsed — the lint's own refusals from the prior attempt, if
    any, ride the next prompt."""

    state.transition(TaskState.RUNNING, f"drafting attempt {state.attempts + 1}")
    state.save()

    spec = SandboxSpec(
        name=naming.sandbox_name(task.id, state.run_id) + f"-a{state.attempts}",
        image=image_for(config, tier),
        labels=naming.labels(task.id, state.run_id, root),
        timeout_s=config.runtime.sandbox_timeout,
        env_passthrough=credential_names(config, tier),
        workspace_read_only=True,
    )

    from torve.application.contextpack import build as build_pack
    from torve.application.contextpack import materialize as materialize_pack

    pack = build_pack(root, root / config.specs.path, task, layout.gates_file(root))
    materialize_pack(worktree, pack)

    prompt = build_intake_prompt(
        task.intent,
        worktree,
        config.intake.max_drafts,
        lint_errors or None,
        feedback,
        facts=execution_facts(root, config),
        parent=parent,
        pack_index=pack["index.md"],
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

    return result, parse_drafts(result.output)


# ....................... #


def _lint_intake_batch(
    worktree: Path,
    document: DraftsDocument,
    parent: Task | None,
    config: RunnerConfig,
    runtime: Runtime,
) -> list[str]:
    """The contract lint, routed on whether this is a decomposition, then
    the document-threshold lint (S-0030/D-4), then (S-0027/D-6) the configuration-
    change lint layered on top — each a no-op unless its own condition
    fires, so this costs an ordinary drafting run nothing."""

    lint_errors = (
        lint_decomposition(worktree, document, parent, config.intake.max_drafts)
        if parent is not None
        else lint_drafts(worktree, document, config.intake.max_drafts)
    )

    if not lint_errors:
        lint_errors = lint_document_threshold(worktree, document, config)

    if not lint_errors:
        lint_errors = lint_configuration_change(worktree, document, config, runtime)

    return lint_errors


# ....................... #


def _finish_intake_success(
    root: Path,
    task: Task,
    state: RunState,
    document: DraftsDocument,
    config_digest: str,
    tier: TierConfig,
    result: AgentResult,
    broker_block_now: Callable[[], dict[str, Any] | None],
) -> IntakeOutcome:
    """Lint-green: persist the drafts and go ready — awaiting adoption,
    dispatching nothing (S-0020/D-1)."""

    fact = f"{len(document.drafts)} draft(s) lint-green"
    state.transition(TaskState.GATED, "drafts produced; lint green")
    state.transition(TaskState.REVIEWED, fact)

    drafts_file(root, task.id).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request": task.intent,
                "spec": task.spec,
                "source": task.source,
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

    return IntakeOutcome(task.id, fact, list(document.drafts), document.rationale, state.attempts)


# ....................... #


def _finish_intake_exhausted(
    root: Path,
    task: Task,
    state: RunState,
    config_digest: str,
    tier: TierConfig,
    unparseable: bool,
    lint_errors: list[str],
    broker_block_now: Callable[[], dict[str, Any] | None],
) -> IntakeOutcome:
    """The budget spent with nothing lint-green: escalate, record, and
    hand back what the last attempt showed."""

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
    nothing (S-0020/D-1). The drafting run's provider credential rides the same
    broker as any run (S-0021): the sandbox never holds the key.

    A drafting run whose contract names a single target (S-0026/the-decomposition-run) is
    a decomposition: the prompt and the lint both route on the parent
    contract that target names, in place of the free-text request path."""

    parent = _decomposition_parent(root, task)
    tier = tier_for(config, task.tier)
    state_path = naming.state_file(root, task.id)
    state = _claimed_or_resumed_state(task, state_path)
    broker_handle = _open_intake_broker(task, tier, config, broker)

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

    try:
        for _ in range(budget):
            try:
                result, document = _attempt_intake_draft(
                    root,
                    worktree,
                    task,
                    config,
                    runtime,
                    agent,
                    tier,
                    state,
                    lint_errors,
                    feedback,
                    parent,
                    broker_handle,
                )
            except SchemaRefusal as exc:
                # S-0054/D-15: refused by the field it fails on, and told so on
                # the next attempt exactly as a lint refusal is.
                unparseable = False
                lint_errors = [str(exc)]
                state.transition(TaskState.GATED, f"drafts refused: {exc}"[:200])
                state.save()
                continue

            if document is None:
                unparseable = True
                state.transition(
                    TaskState.GATED, "drafter output unparseable — recorded, not empty"
                )
                state.save()
                continue

            unparseable = False
            lint_errors = _lint_intake_batch(worktree, document, parent, config, runtime)

            if lint_errors:
                state.transition(TaskState.GATED, f"lint red: {len(lint_errors)} refusal(s)")
                state.save()
                continue

            return _finish_intake_success(
                root, task, state, document, config_digest, tier, result, broker_block_now
            )

        return _finish_intake_exhausted(
            root, task, state, config_digest, tier, unparseable, lint_errors, broker_block_now
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
    drafting quality is a query (S-0020/D-8's settling evidence)."""

    from torve.application.telemetry import append_record

    manifest = layout.gates_file(root)

    if not manifest.is_file():
        return

    from torve.config.manifest import load_manifest

    append_record(
        root / load_manifest(manifest).telemetry,
        {
            "schema_version": RECORD_SCHEMA_VERSION,
            "kind": "intake",
            "at": stamp(),
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
                # report (S-0021/D-5) — present when a broker was in force.
                **({"broker": broker} if broker is not None else {}),
            },
        },
    )


# ----------------------- #
# Adoption (S-0020/D-1, S-0020/D-4): the human signature, and the only moment ids
# exist — read the counter, rewrite refs, write contracts, commit, all
# under the engine lock so nothing races the minting.


def _inherit_decisions(root: Path, config: RunnerConfig, document: str) -> list[dict[str, Any]]:
    """The planner's rows, not a second copy of them (S-0020/D-9, S-0007/A-3): grades
    and paths as they stand at adoption, from an accepted document only —
    the same admission torve plan enforces (S-0007/D-7). *document* is the
    identifier the drafts file carries (S-0059/D-1), found by the one lookup."""

    from torve.application.planner import PlanError, inherit_decisions
    from torve.config import spec

    doc_path = spec.document_dir(root / config.specs.path, document)

    if doc_path is None:
        raise ValueError(f"no document {document} under {config.specs.path}")

    try:
        doc = spec.load_document(doc_path)
    except spec.SpecError as exc:
        raise ValueError(f"{document} does not load — {'; '.join(exc.problems)}") from None

    if doc.status != "accepted":
        raise ValueError(
            f"{document} is not accepted — a draft has no settled decisions to inherit (S-0007/D-7)"
        )

    try:
        rows = inherit_decisions(doc)

    except PlanError as exc:
        raise ValueError(str(exc)) from exc

    return [row.model_dump() for row in rows]


# ....................... #


def _merged_decisions(
    root: Path,
    config: RunnerConfig,
    scope_allow: list[str],
    rfc_line: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The adoption merge (S-0030/D-1): every adopted contract carries the
    standing rows its scope crosses, merged with the cited document's copy
    and deduplicated by identifier — the cited copy wins on conflict, since
    it is the one the request was written against."""

    from torve.application.planner import PlanError, standing_decisions

    try:
        standing = standing_decisions(root / config.specs.path, scope_allow)

    except PlanError as exc:
        raise ValueError(str(exc)) from exc

    merged: dict[str, dict[str, Any]] = {}

    for row in rfc_line:
        merged[row["id"]] = row

    for standing_row in standing:
        merged.setdefault(standing_row.id, standing_row.model_dump())

    return list(merged.values())


# ....................... #


def adopted_file(root: Path, task_id: str) -> Path:
    return root / layout.TORVE_DIR / "tasks" / task_id / ADOPTED_FILE


# ....................... #


def adopt(root: Path, task_id: str, config: RunnerConfig, assume_lock: bool = False) -> list[str]:
    """Adopt every draft the run produced: ids minted here and nowhere
    else, contracts committed as engine records on base, the loop left to
    dispatch them like hand-minted work (S-0020/D-7). Returns the new ids.
    `assume_lock` is for a caller already inside the tick — the board's
    adopt command applies under the lock the tick holds."""

    from torve.application.enginelock import acquire_lock, release_lock
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
    # the READY state before this human arrived (S-0020/D-10).

    record = cast("dict[str, Any]", json.loads(source.read_text(encoding="utf-8")))
    drafts: list[Draft] = [Draft.model_validate(d) for d in record["drafts"]]
    cited = record.get("spec")
    asked = record.get("source")
    decisions = _inherit_decisions(root, config, str(cited)) if cited else []

    # S-0030/D-4: adoption refuses document_required before anything is
    # written — the same check the intake lint already ran, re-run here
    # since a hand-minted drafts file never passed it.
    rfc_dir = root / config.specs.path

    for draft in drafts:
        verdict = _threshold_for_scope(
            rfc_dir, draft.scope, draft.acceptance, config.intake.document_threshold
        )

        if verdict.verdict == "document_required":
            raise ValueError(
                f"{draft.ref}: needs its own document — {'; '.join(verdict.reasons)} — "
                "split it into a document with `torve spec new`, or narrow scope.allow"
            )

    # A decomposition run names the contract it decomposes as its single
    # target (S-0026/the-decomposition-run); an ordinary intake names none.
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
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "id": new_id,
                "role": "implement",
                "intent": draft.intent,
                "depends_on": [ids[ref] for ref in draft.depends_on],
                "scope": draft.scope.model_dump(),
                "acceptance": list(draft.acceptance),
                # S-0030/D-1: adoption always merges the standing rows the
                # draft's own scope crosses with the cited document's copy,
                # deduplicated by identifier — never the cited document alone.
                "decisions": _merged_decisions(root, config, draft.scope.allow, decisions),
                "tier": "executor",
            }

            if cited:
                document["spec"] = cited

            if asked:
                document["source"] = asked

            if parent_id:
                document["parent"] = parent_id

            path = contract_dir / "contract.yaml"

            path.write_text(
                f"# Adopted from {task_id}'s drafts (S-0020) — "
                "ids minted at adoption, S-0020/D-4.\n" + yaml.safe_dump(document, sort_keys=False),
                encoding="utf-8",
            )

            written.append(path)

        if parent_id:
            # The parent becomes the integration task (S-0026 S-0026/D-6):
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
                    (f"🧪 chore: adopt {', '.join(ids.values())} from {task_id} (S-0020)"),
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

    # Adoption is the disposal (S-0020/D-10): the run's purpose is consumed,
    # so its state goes with it — nothing is left for a reaper to judge.
    # The marker survives as the audit line telling adopted from fresh.
    adopted_file(root, task_id).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adopted": list(ids.values()),
                "at": stamp(),
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
